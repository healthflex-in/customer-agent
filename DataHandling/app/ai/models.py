"""Central model registry for every Gemini call path (STT + brand search)."""

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Mapping

try:
    from dotenv import load_dotenv
except ImportError:  # Registry validation must work in minimal test/tool images.
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv()


@dataclass(frozen=True)
class GeminiModelRegistry:
    general: str
    reasoning: str
    audio: str
    search: str
    fallbacks: tuple[str, ...]

    @property
    def rotation(self) -> tuple[str, ...]:
        """Return general plus fallbacks once each, preserving order."""

        return tuple(dict.fromkeys((self.general, *self.fallbacks)))


def _normalize_model_name(value: str, setting: str) -> str:
    model = value.strip()
    if model.startswith("models/"):
        model = model[len("models/"):]
    if not model:
        raise ValueError(f"{setting} must not be empty")
    return model


def build_model_registry(environment: Mapping[str, str]) -> GeminiModelRegistry:
    """Build and validate model configuration without contacting a provider."""

    general = _normalize_model_name(
        environment.get("GEMINI_GENERAL_MODEL", "gemini-3.5-flash-lite"),
        "GEMINI_GENERAL_MODEL",
    )
    reasoning = _normalize_model_name(
        environment.get("GEMINI_REASONING_MODEL", "gemini-3.5-flash-lite"),
        "GEMINI_REASONING_MODEL",
    )
    audio = _normalize_model_name(
        environment.get("GEMINI_AUDIO_MODEL", "gemini-3.5-flash-lite"),
        "GEMINI_AUDIO_MODEL",
    )
    search = _normalize_model_name(
        environment.get("GEMINI_SEARCH_MODEL", "gemini-3.5-flash-lite"),
        "GEMINI_SEARCH_MODEL",
    )

    raw_fallbacks = environment.get("GEMINI_FALLBACK_MODELS")
    fallback_values = (
        [general]
        if raw_fallbacks is None
        else [item for item in raw_fallbacks.split(",") if item.strip()]
    )
    fallbacks = tuple(
        _normalize_model_name(item, "GEMINI_FALLBACK_MODELS")
        for item in fallback_values
    )

    return GeminiModelRegistry(
        general=general,
        reasoning=reasoning,
        audio=audio,
        search=search,
        fallbacks=fallbacks,
    )


# Importing application startup paths validates configuration before traffic is
# accepted. Provider reachability is intentionally left to deployment health
# checks so a transient network failure does not make configuration nondeterministic.
MODEL_REGISTRY = build_model_registry(os.environ)


# ── Provider-agnostic LLM role configuration ────────────────────────────────
# MODEL_REGISTRY above is Gemini-specific and still governs the `audio` and
# `search` roles — speech-to-text and Google Search brand grounding stay on
# Gemini, since there is no equivalent elsewhere today (see app/audio/stt.py
# and src/graph/nodes/extract.py, both unaffected by LLM_PROVIDER).
#
# The `general` and `reasoning` roles that drive the interview and form
# extraction are resolved separately below so they can move to another
# provider with an env change only. Add a new provider by adding one branch
# to app/ai/factory.py — nothing here needs to change for that.
SUPPORTED_LLM_PROVIDERS = frozenset({"google_genai", "anthropic", "bedrock"})


@dataclass(frozen=True)
class LLMRoleConfig:
    """Resolved provider + model names for the interview/reasoning roles."""

    provider: str
    general: str
    reasoning: str
    fallbacks: tuple[str, ...]

    @property
    def rotation(self) -> tuple[str, ...]:
        """Return general plus fallbacks once each, preserving order."""

        return tuple(dict.fromkeys((self.general, *self.fallbacks)))


def build_llm_role_config(
    environment: Mapping[str, str], gemini_registry: GeminiModelRegistry
) -> LLMRoleConfig:
    """Resolve LLM_PROVIDER plus its general/reasoning model names.

    LLM_PROVIDER has no default. An unconfigured deployment must fail at
    startup, not silently run the interview/reasoning roles on Gemini.
    Set LLM_PROVIDER=google_genai explicitly to opt into that provider —
    GEMINI_GENERAL_MODEL / GEMINI_REASONING_MODEL / GEMINI_FALLBACK_MODELS
    from `gemini_registry` are then reused as that provider's model names.
    Any other supported provider requires LLM_GENERAL_MODEL explicitly too —
    model names are provider-specific strings, so there is nothing stable to
    default to.
    """

    provider = (environment.get("LLM_PROVIDER") or "").strip()
    if not provider:
        approved = ", ".join(sorted(SUPPORTED_LLM_PROVIDERS))
        raise ValueError(
            f"LLM_PROVIDER is required (no default) — set it to one of {approved}. "
            "This must never silently fall back to Gemini."
        )
    if provider not in SUPPORTED_LLM_PROVIDERS:
        approved = ", ".join(sorted(SUPPORTED_LLM_PROVIDERS))
        raise ValueError(f"LLM_PROVIDER must be one of {approved}; got {provider!r}")

    raw_fallbacks = environment.get("LLM_FALLBACK_MODELS")

    if provider == "google_genai":
        fallbacks = (
            gemini_registry.fallbacks
            if raw_fallbacks is None
            else tuple(item.strip() for item in raw_fallbacks.split(",") if item.strip())
        )
        return LLMRoleConfig(
            provider=provider,
            general=gemini_registry.general,
            reasoning=gemini_registry.reasoning,
            fallbacks=fallbacks,
        )

    general = (environment.get("LLM_GENERAL_MODEL") or "").strip()
    if not general:
        raise ValueError(f"LLM_GENERAL_MODEL is required when LLM_PROVIDER={provider!r}")
    reasoning = (environment.get("LLM_REASONING_MODEL") or "").strip() or general
    fallbacks = (
        (general,)
        if raw_fallbacks is None
        else tuple(item.strip() for item in raw_fallbacks.split(",") if item.strip())
    )
    return LLMRoleConfig(provider=provider, general=general, reasoning=reasoning, fallbacks=fallbacks)


# Resolved once at import time, same fail-fast guarantee as MODEL_REGISTRY.
LLM_CONFIG = build_llm_role_config(os.environ, MODEL_REGISTRY)
