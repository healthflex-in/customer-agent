"""Anthropic text completion used by the interview and form extraction.

Mirrors app/ai/gemini.py and app/ai/bedrock.py: the rest of the application
calls a plain prompt -> text function. This module is the only place that
constructs an Anthropic client for that completer shape (app/ai/factory.py
covers the separate `.complete()`-object shape used by the HealthAgent
rotation path in src/llm/functionalities.py).
"""

from __future__ import annotations

import os
from collections.abc import Callable

from app.ai.models import LLM_CONFIG
from app.ai.pricing_guard import guard_prompt_length
from app.observability.ai_usage import anthropic_usage, tracked_ai_call
from app.observability.privacy import error_type


def _anthropic_api_key() -> str:
    return (
        os.environ.get("LLM_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or ""
    ).strip()


def build_anthropic_text_completer(
    model_name: str,
    *,
    operation: str,
    temperature: float | None = None,
    max_tokens: int = 4096,
    json_output: bool = False,
) -> Callable[[str], str] | None:
    """Return a prompt completer for one Claude model, or None without a key.

    `temperature` is accepted for symmetry with build_gemini_text_completer
    and build_bedrock_text_completer, but is not sent on the wire: the
    installed anthropic SDK (2.x) no longer exposes it on messages.create().
    """

    api_key = _anthropic_api_key()
    print(f"[anthropic] {operation} completer — API key present: {'yes' if api_key else 'NO'}")
    if not api_key:
        print("[anthropic] WARNING: No LLM_API_KEY/ANTHROPIC_API_KEY — completer disabled")
        return None

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=api_key)
    except Exception as sdk_error:
        print(f"[anthropic] SDK init failed with {error_type(sdk_error)}")
        return None

    # The Messages API has no "force JSON" response mode like Gemini's
    # response_mime_type; callers that need JSON already parse it defensively
    # out of free text (see
    # src/graph/pure_functions/form_validation.extract_json_from_response), so
    # json_output only nudges the prompt via a system instruction.
    system = "Respond with a single valid JSON object and no other text." if json_output else None

    def complete(prompt: str) -> str:
        guard_prompt_length(model_name, prompt)
        request: dict[str, object] = {
            "model": model_name,
            "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            request["system"] = system

        response = tracked_ai_call(
            provider="anthropic",
            model=model_name,
            operation=operation,
            call=lambda: client.messages.create(**request),
            usage_extractor=anthropic_usage,
        )
        return "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ).strip()

    print(f"[anthropic] Completer ready — model={model_name} op={operation}")
    return complete


def build_anthropic_reasoning_completer() -> Callable[[str], str] | None:
    """Build the form-extraction completer for the configured Claude model.

    Every caller of this completer expects a JSON object back.
    """

    try:
        return build_anthropic_text_completer(
            LLM_CONFIG.reasoning,
            operation="form_extraction",
            temperature=0.1,
            json_output=True,
        )
    except Exception as error:
        print(f"[anthropic] Reasoning completer init failed: {error_type(error)}")
        return None


def build_anthropic_interview_completer() -> Callable[[str], str] | None:
    """Build the interview Q&A completer for the configured Claude model."""

    try:
        return build_anthropic_text_completer(
            LLM_CONFIG.general,
            operation="interview_text",
            temperature=0.3,
        )
    except Exception as error:
        print(f"[anthropic] Interview completer init failed: {error_type(error)}")
        return None
