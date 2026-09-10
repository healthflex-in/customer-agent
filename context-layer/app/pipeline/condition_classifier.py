"""Step 1 — derive condition(s) from the patient's intake form.

Deterministic-first (reuse customer-info.category, else keyword map). LLM is an
optional last resort and is intentionally NOT wired here by default to keep the
common path free of LLM cost — classification from MSK intake text is easy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Condition:
    label: str
    confidence: float
    source: str  # "category" | "keyword" | "unknown"


# Minimal keyword → condition map. Extend as testRecommendationRules grow.
# Keys are condition labels that must match testRecommendationRules.condition.
_KEYWORD_MAP: dict[str, list[str]] = {
    "knee_pain": ["knee", "patella", "acl", "mcl", "meniscus", "kneecap"],
    "shoulder_pain": ["shoulder", "rotator cuff", "supraspinatus", "labrum"],
    "low_back_pain": ["low back", "lower back", "lumbar", "sciatica", "l4", "l5"],
    "neck_pain": ["neck", "cervical", "whiplash"],
    "ankle_pain": ["ankle", "achilles", "sprain foot", "plantar"],
    "hip_pain": ["hip", "groin", "glute"],
}


def _text_blob(form_data: dict) -> str:
    parts: list[str] = []
    for section in (form_data or {}).values():
        if isinstance(section, dict):
            parts.extend(str(v) for v in section.values() if v)
        elif section:
            parts.append(str(section))
    return " ".join(parts).lower()


def classify(form_data: dict, category: str | None = None) -> list[Condition]:
    """Return one or more conditions, most-confident first.

    Multi-condition (comorbidity) is supported: every keyword group that matches
    is returned so downstream retrieval can union their test batteries.
    """
    if category:
        return [Condition(label=category, confidence=0.95, source="category")]

    blob = _text_blob(form_data)
    hits: list[Condition] = []
    for label, keywords in _KEYWORD_MAP.items():
        matched = sum(1 for kw in keywords if re.search(r"\b" + re.escape(kw), blob))
        if matched:
            # More distinct keyword hits → higher confidence, capped.
            hits.append(Condition(label=label, confidence=min(0.6 + 0.1 * matched, 0.9),
                                  source="keyword"))

    if not hits:
        return [Condition(label="unknown", confidence=0.0, source="unknown")]

    hits.sort(key=lambda c: c.confidence, reverse=True)
    return hits
