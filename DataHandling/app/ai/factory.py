"""Provider-agnostic LLM construction for the HealthAgent rotation path.

app/ai/completer.py builds simple prompt -> text callables for the LangGraph
reasoning path. This module builds objects that expose `.complete(prompt) ->
response` (a `.text` attribute, matching what `ensure_string()` and the
rotation loop in src/llm/functionalities.py already expect) so that path
keeps working unchanged no matter what LLM_CONFIG.provider resolves to.

Add a new provider by adding one branch to create_llm() — nothing that
calls create_llm() needs to change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from app.ai.models import LLM_CONFIG
from app.ai.pricing_guard import guard_prompt_length
from app.observability.ai_usage import anthropic_usage, bedrock_usage, gemini_usage

# app/observability/ai_usage.py predates LLM_CONFIG and labels Bedrock calls
# "aws_bedrock" (matching its pricing catalog keys), not "bedrock". Keep
# LLM_PROVIDER short for humans; translate to the billing label only here.
_TELEMETRY_PROVIDER_LABELS = {
    "google_genai": "google_genai",
    "anthropic": "anthropic",
    "bedrock": "aws_bedrock",
}


def telemetry_provider_label(provider: str) -> str:
    """Return the provider string app/observability/ai_usage.py expects."""

    return _TELEMETRY_PROVIDER_LABELS.get(provider, provider)


def usage_extractor_for(provider: str):
    """Return the ai_usage.py usage parser for the given LLM_CONFIG.provider."""

    if provider == "anthropic":
        return anthropic_usage
    if provider == "bedrock":
        return bedrock_usage
    return gemini_usage


@dataclass
class _TextResponse:
    """Duck-types llama-index's CompletionResponse.

    `ensure_string()` (src/graph/pure_functions/form_validation.py) reads
    `.text`; `anthropic_usage`/`bedrock_usage` read `.raw` for token counts.
    """

    text: str
    raw: object = field(default=None, repr=False)


class AnthropicChatLLM:
    """Wraps the Anthropic Messages API behind a `.complete(prompt)` call."""

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        max_tokens: int = 4096,
        temperature: float | None = None,
    ):
        import anthropic

        self.model = model
        self.api_key = (
            api_key
            or os.environ.get("LLM_API_KEY")
            or os.environ.get("ANTHROPIC_API_KEY")
            or ""
        ).strip()
        if not self.api_key:
            raise ValueError("No Anthropic API key configured (set LLM_API_KEY)")
        self._client = anthropic.Anthropic(api_key=self.api_key)
        self._max_tokens = max_tokens
        # anthropic>=2 dropped `temperature` from messages.create(); kept as a
        # constructor argument only so every provider class shares one shape.
        self._temperature = temperature

    def complete(self, prompt: str) -> _TextResponse:
        guard_prompt_length(self.model, prompt)
        response = self._client.messages.create(
            model=self.model,
            max_tokens=self._max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        text = "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        )
        return _TextResponse(text=text.strip(), raw=response)


class BedrockConverseLLM:
    """Wraps the Bedrock Converse API behind a `.complete(prompt)` call.

    Converse is already provider-agnostic on AWS's side — the same call
    shape works for Claude, Llama, Nova, or GPT-OSS models on Bedrock. Only
    `model` (the modelId) changes; this class does not.
    """

    def __init__(
        self,
        model: str,
        region: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.3,
    ):
        import boto3
        from botocore.config import Config

        self.model = model
        region_name = region or os.environ.get("AWS_REGION", "us-east-1")
        self._client = boto3.client(
            "bedrock-runtime",
            region_name=region_name,
            config=Config(retries={"max_attempts": 3, "mode": "standard"}),
        )
        self._max_tokens = max_tokens
        self._temperature = temperature

    def complete(self, prompt: str) -> _TextResponse:
        response = self._client.converse(
            modelId=self.model,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            inferenceConfig={"maxTokens": self._max_tokens, "temperature": self._temperature},
        )
        content = response.get("output", {}).get("message", {}).get("content", [])
        text = next((block["text"] for block in content if "text" in block), "")
        return _TextResponse(text=text.strip(), raw=response)


def create_llm(model: str, api_key: str | None = None):
    """Build an LLM exposing `.complete(prompt)` for LLM_CONFIG.provider.

    `api_key` is only consulted on the google_genai and anthropic paths;
    Bedrock always resolves credentials through the ambient AWS IAM chain.
    """

    provider = LLM_CONFIG.provider

    if provider == "anthropic":
        return AnthropicChatLLM(model=model, api_key=api_key)

    if provider == "bedrock":
        return BedrockConverseLLM(model=model)

    # google_genai — identical construction to the pre-migration behaviour.
    from llama_index.llms.google_genai import GoogleGenAI

    resolved_key = api_key or os.environ.get("GEMINI_API_KEY")
    return GoogleGenAI(model=model, api_key=resolved_key)
