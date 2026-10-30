"""Gemini text completion used by the interview.

The rest of the application calls a function that accepts a prompt and
returns text. This module is the only place that constructs a Gemini client
for form extraction.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from app.ai.models import MODEL_REGISTRY
from app.ai.thinking import bounded_thinking
from app.config import GEMINI_REQUEST_TIMEOUT_MS
from app.observability.ai_usage import gemini_usage, tracked_ai_call
from app.observability.privacy import error_type


def _gemini_api_key() -> str:
    api_key = (
        os.getenv("GEMINI_API_KEY")
        or os.getenv("GOOGLE_API_KEY")
        or os.getenv("GOOGLE_GEMINI_API_KEY")
    )
    if not api_key:
        try:
            from src.llm.utils import load_gemini_key

            api_key = load_gemini_key()
        except Exception:
            api_key = None
    return (api_key or "").strip()


def build_gemini_text_completer(
    model_name: str,
    *,
    operation: str,
    temperature: float,
    timeout_ms: int = GEMINI_REQUEST_TIMEOUT_MS,
    json_output: bool = False,
) -> Callable[[str], str] | None:
    """Return a prompt completer for one Gemini model, or None when no key exists.

    The extraction request runs in parallel with question generation. Its
    network wait is bounded so one unavailable connection cannot keep a
    patient response pending. The caller then uses the interview model.
    """

    api_key = _gemini_api_key()
    print(f"[graph] Reasoning LLM — API key present: {'yes' if api_key else 'NO'}")
    if not api_key:
        print("[graph] WARNING: No API key — reasoning LLM disabled, using flash-lite fallback")
        return None

    thinking = bounded_thinking(model_name)
    try:
        from google import genai
        from google.genai import types

        client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=timeout_ms),
        )
        generation_config = types.GenerateContentConfig(
            temperature=temperature,
            thinking_config=types.ThinkingConfig(**thinking.as_kwargs()),
            response_mime_type="application/json" if json_output else None,
        )

        def complete(prompt: str) -> str:
            response = tracked_ai_call(
                provider="google_genai",
                model=model_name,
                operation=operation,
                call=lambda: client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=generation_config,
                ),
                usage_extractor=gemini_usage,
            )
            return (response.text or "").strip()

        print(
            f"[graph] Reasoning LLM ({model_name}, {thinking.describe()}) "
            "initialized for form extraction"
        )
        return complete
    except Exception as sdk_error:
        print(f"[graph] Direct SDK init failed with {error_type(sdk_error)}; using fallback")

    from src.llm.utils import init_reasoning_llm

    fallback_llm = init_reasoning_llm(api_key)

    def complete_with_fallback(prompt: str) -> str:
        response = tracked_ai_call(
            provider="google_genai",
            model=model_name,
            operation=operation,
            call=lambda: fallback_llm.complete(prompt),
            usage_extractor=gemini_usage,
        )
        text = getattr(response, "text", None) or str(response)
        return text.strip()

    print(f"[graph] Reasoning LLM ({model_name}) initialized for form extraction")
    return complete_with_fallback


def build_gemini_reasoning_completer() -> Callable[[str], str] | None:
    """Build the form-extraction completer for the configured Gemini model.

    Every caller of this completer expects a JSON object back.
    """

    try:
        return build_gemini_text_completer(
            MODEL_REGISTRY.reasoning,
            operation="form_extraction",
            temperature=0.1,
            json_output=True,
        )
    except Exception as error:
        print(f"[graph] Reasoning LLM initialization failed: {error_type(error)}")
        return None
