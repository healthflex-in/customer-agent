"""Central, validated model registry for every Gemini call path."""

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


# Production-approved stable IDs, verified against Google's model lifecycle on
# 2026-09-02. Adding a new model is an intentional reviewed change because model
# migrations can alter clinical extraction behaviour and cost.
SUPPORTED_GEMINI_MODELS = frozenset({
    "gemini-2.5-flash-lite",
    "gemini-2.5-flash",
})

RETIRED_GEMINI_MODELS = frozenset({
    "gemini-1.5-flash",
    "gemini-1.5-flash-8b",
    "gemini-1.5-pro",
    "gemini-2.0-flash",
    "gemini-2.0-flash-001",
    "gemini-2.0-flash-lite",
    "gemini-2.0-flash-lite-001",
})


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
    if model in RETIRED_GEMINI_MODELS:
        raise ValueError(f"{setting} references retired Gemini model {model}")
    if model not in SUPPORTED_GEMINI_MODELS:
        approved = ", ".join(sorted(SUPPORTED_GEMINI_MODELS))
        raise ValueError(
            f"{setting} references unapproved Gemini model {model}; "
            f"approved stable models: {approved}"
        )
    return model


def build_model_registry(environment: Mapping[str, str]) -> GeminiModelRegistry:
    """Build and validate model configuration without contacting a provider."""

    general = _normalize_model_name(
        environment.get("GEMINI_GENERAL_MODEL", "gemini-2.5-flash-lite"),
        "GEMINI_GENERAL_MODEL",
    )
    reasoning = _normalize_model_name(
        environment.get("GEMINI_REASONING_MODEL", "gemini-2.5-flash"),
        "GEMINI_REASONING_MODEL",
    )
    audio = _normalize_model_name(
        environment.get("GEMINI_AUDIO_MODEL", reasoning),
        "GEMINI_AUDIO_MODEL",
    )
    search = _normalize_model_name(
        environment.get("GEMINI_SEARCH_MODEL", reasoning),
        "GEMINI_SEARCH_MODEL",
    )

    raw_fallbacks = environment.get("GEMINI_FALLBACK_MODELS")
    fallback_values = (
        [reasoning]
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
