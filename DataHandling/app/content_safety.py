"""Deterministic guards for internal instruction leakage."""

from __future__ import annotations

import re
from typing import Any


_INTERNAL_TRANSCRIPTION_PROMPT_MARKERS = (
    "you are a medical transcription assistant",
    "transcribe exactly what the patient says",
    "physiotherapy intake interview conducted in indian english",
    "do not paraphrase summarise or add any commentary",
    "return only the transcription text nothing else",
)


def _normalized_text(text: str) -> str:
    normalized = text.lower().replace("—", " ").replace("–", " ")
    normalized = re.sub(r"[^a-z0-9\s]", " ", normalized)
    return " ".join(normalized.split())


def contains_internal_transcription_prompt(text: str) -> bool:
    """Return whether text contains a distinctive internal STT instruction."""
    if not isinstance(text, str) or not text.strip():
        return False
    normalized = _normalized_text(text)
    return any(marker in normalized for marker in _INTERNAL_TRANSCRIPTION_PROMPT_MARKERS)


def scrub_internal_transcription_prompts(value: Any) -> tuple[Any, int]:
    """Blank contaminated strings recursively and return the removal count."""
    if isinstance(value, str):
        return ("", 1) if contains_internal_transcription_prompt(value) else (value, 0)
    if isinstance(value, dict):
        cleaned: dict = {}
        removed = 0
        for key, child in value.items():
            cleaned_child, child_removed = scrub_internal_transcription_prompts(child)
            cleaned[key] = cleaned_child
            removed += child_removed
        return cleaned, removed
    if isinstance(value, list):
        cleaned_list = []
        removed = 0
        for child in value:
            cleaned_child, child_removed = scrub_internal_transcription_prompts(child)
            cleaned_list.append(cleaned_child)
            removed += child_removed
        return cleaned_list, removed
    return value, 0
