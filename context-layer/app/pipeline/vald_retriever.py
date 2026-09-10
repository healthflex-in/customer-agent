"""Step 2b — VALD tests from the `vald-exercise-data` collection.

The VALD pool is the set of UNIQUE exercises actually recorded under
`savedResponse.data.forceFrame` and `savedResponse.data.forceDeck` (not the noisy
`vald-exercise-details` catalog). We clean out internal data keys / cryptic codes,
dedupe by base movement, and cache the result (global data — computed once).

- forceFrame = joint-specific strength tests → filtered by the condition's body part.
- forceDeck  = functional/plate tests (jumps, squats, IMTP, push-ups) → included by
  body region (lower-limb vs upper-limb condition), since they aren't joint-named.
"""

from __future__ import annotations

import re
import threading

from app import config, db
from app.pipeline.models import SOURCE_VALD, Candidate

# condition label → body-part tokens matched against forceFrame exercise names.
_CONDITION_BODYPART: dict[str, list[str]] = {
    "knee_pain": ["knee"],
    "shoulder_pain": ["shoulder"],
    "low_back_pain": ["hip", "trunk", "back"],
    "neck_pain": ["neck"],
    "ankle_pain": ["ankle"],
    "hip_pain": ["hip"],
}

_LOWER_LIMB = {"knee_pain", "hip_pain", "ankle_pain", "low_back_pain"}
_UPPER_LIMB = {"shoulder_pain"}

_cache_lock = threading.Lock()
_cache: dict | None = None  # {"forceFrame": [names], "forceDeck": [names]}


def _clean(name: str) -> str | None:
    """Drop internal data keys / cryptic codes; keep human-readable exercise names."""
    n = (name or "").strip()
    if not n or "_" in n:                       # e.g. "kneeExtensionData_..._prone"
        return None
    if re.search(r"[a-z][A-Z]", n):             # camelCase internal key
        return None
    if re.fullmatch(r"[A-Z0-9]{2,10}", n):      # cryptic code e.g. ABCMJ, ISOPU
        return None
    return n


def _base(name: str) -> str:
    """Collapse variants: 'Knee Extension - Seated (90)' → 'Knee Extension'."""
    return re.split(r"\s+-\s+|\s*\(", name)[0].strip()


def _distinct_keys(field: str) -> list[str]:
    pipe = [
        {"$match": {f"savedResponse.data.{field}": {"$type": "object"}}},
        {"$project": {"k": {"$map": {
            "input": {"$objectToArray": f"$savedResponse.data.{field}"},
            "as": "e", "in": "$$e.k"}}}},
        {"$unwind": "$k"},
        {"$group": {"_id": "$k"}},
    ]
    return [d["_id"] for d in db.coll(config.COLL_VALD_DATA).aggregate(pipe, allowDiskUse=True)]


def _pool() -> dict:
    """Distinct, cleaned, base-deduped forceFrame/forceDeck names. Cached per process."""
    global _cache
    if _cache is not None:
        return _cache
    with _cache_lock:
        if _cache is not None:
            return _cache
        result: dict[str, list[str]] = {}
        for field in ("forceFrame", "forceDeck"):
            bases: dict[str, str] = {}
            try:
                keys = _distinct_keys(field)
            except Exception as e:  # noqa: BLE001
                print(f"[vald] {field} aggregation failed: {e}")
                keys = []
            for k in keys:
                c = _clean(k)
                if not c:
                    continue
                b = _base(c)
                bases.setdefault(b.lower(), b)
            result[field] = sorted(bases.values())
        _cache = result
    return _cache


def _fd_region(name: str) -> str:
    n = name.lower()
    return "upper" if ("push up" in n or "shoulder" in n) else "lower"


def _candidate(name: str, device: str, priority: int) -> Candidate:
    return Candidate(
        object_id=None,
        test_name=name,
        test_detail=f"VALD {device} assessment",
        unit_name="",
        data_type="",
        calculation_type="MANUAL",
        normative_text="",
        priority=priority,
        source=SOURCE_VALD,
        device_type=device,
    )


def retrieve(condition_labels: list[str]) -> list[Candidate]:
    tokens: set[str] = set()
    for label in condition_labels:
        tokens.update(_CONDITION_BODYPART.get(label, []))

    region = None
    if any(l in _LOWER_LIMB for l in condition_labels):
        region = "lower"
    elif any(l in _UPPER_LIMB for l in condition_labels):
        region = "upper"

    if not tokens and not region:
        return []

    pool = _pool()
    out: list[Candidate] = []

    # forceFrame: joint-specific strength tests, matched by body-part token.
    if tokens:
        for name in pool.get("forceFrame", []):
            if any(t in name.lower() for t in tokens):
                out.append(_candidate(name, "forceframe", config.VALD_DEFAULT_PRIORITY))

    # forceDeck: functional tests, included by body region.
    if region:
        for name in pool.get("forceDeck", []):
            if _fd_region(name) == region:
                out.append(_candidate(name, "forcedeck", config.VALD_DEFAULT_PRIORITY - 1))

    return out
