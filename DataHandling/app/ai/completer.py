"""Text completion entry point for the interview.

Graph nodes and handlers receive a function from prompt text to response
text. The configured provider is selected here, via LLM_PROVIDER — switching
providers (Anthropic today, Bedrock tomorrow, anything added later) is an
env-only change. See app/ai/models.py (LLM_CONFIG) for the resolved provider
and model names, and app/ai/factory.py for the construction logic shared
with the HealthAgent rotation path.

Speech transcription is unaffected and continues to use Gemini regardless of
LLM_PROVIDER — see app/audio/stt.py.
"""

from __future__ import annotations

from collections.abc import Callable

from app.ai.models import LLM_CONFIG


def build_reasoning_completer() -> Callable[[str], str] | None:
    """Return the form-extraction completer for the configured LLM_PROVIDER."""

    if LLM_CONFIG.provider == "anthropic":
        from app.ai.anthropic_completer import build_anthropic_reasoning_completer
        return build_anthropic_reasoning_completer()

    if LLM_CONFIG.provider == "bedrock":
        from app.ai.bedrock import build_bedrock_reasoning_completer
        return build_bedrock_reasoning_completer()

    from app.ai.gemini import build_gemini_reasoning_completer
    return build_gemini_reasoning_completer()


def build_interview_completer() -> Callable[[str], str] | None:
    """Return a plain interview-turn completer for the configured LLM_PROVIDER.

    HealthAgent.llm_complete (src/llm/functionalities.py) is the interview
    completer actually wired into the graph today (server.py) — kept here,
    symmetric with build_reasoning_completer, for a caller that wants a
    plain callable instead of HealthAgent's rotation wrapper.
    """

    if LLM_CONFIG.provider == "anthropic":
        from app.ai.anthropic_completer import build_anthropic_interview_completer
        return build_anthropic_interview_completer()

    if LLM_CONFIG.provider == "bedrock":
        from app.ai.bedrock import build_bedrock_interview_completer
        return build_bedrock_interview_completer()

    return None
