"""Step 3 — rank/prune the pool, and optionally PROPOSE net-new tests.

Semi-deterministic:
- Catalog picks (objectiveAssessments + VALD) are always validated ⊆ the candidate
  pool — no hallucinated catalog tests.
- With USE_LLM_RANKER, Gemini Flash (temp=0) orders the pool AND may propose up to
  MAX_PROPOSED_TESTS tests that are NOT in any catalog, when the condition needs a
  test the pool lacks. Proposals are clearly flagged (source=agent_proposed,
  objectId=None) for the clinician to review/add to the catalog — never auto-added.
- No LLM → deterministic priority ordering, zero proposals.
"""

from __future__ import annotations

import json

from app import config
from app.pipeline.models import SOURCE_PROPOSED, Candidate, Ranked


def _deterministic(candidates: list[Candidate]) -> list[Ranked]:
    ordered = sorted(candidates, key=lambda c: (-c.priority, c.test_name.lower()))
    out: list[Ranked] = []
    for i, c in enumerate(ordered, start=1):
        out.append(
            Ranked(
                candidate=c,
                rank=i,
                confidence=round(min(c.priority / 10.0, 1.0), 2),
                rationale=c.test_detail or f"Standard test for this presentation (priority {c.priority}).",
            )
        )
    return out


def _llm(candidates: list[Candidate], patient_context: str) -> list[Ranked] | None:
    if not (config.USE_LLM_RANKER and config.GEMINI_API_KEY):
        return None
    try:
        from google import genai  # type: ignore
    except Exception:  # noqa: BLE001
        return None

    by_name = {c.test_name.lower(): c for c in candidates}
    pool_lines = "\n".join(
        f"- {c.test_name} [{c.source}]: {c.test_detail}" for c in candidates
    )
    prompt = (
        "You are a musculoskeletal physiotherapy assistant selecting objective tests.\n"
        "1) From the ALLOWED POOL, choose and order the tests most appropriate for this patient.\n"
        f"2) You MAY additionally propose up to {config.MAX_PROPOSED_TESTS} tests that are NOT in "
        "the pool ONLY if the condition clearly needs a test the pool lacks. Mark proposals with "
        '"proposed": true and name the target muscle and joint.\n'
        'Return STRICT JSON: [{"testName": str, "proposed": bool, "muscle": str, "joint": str, '
        '"confidence": 0-1, "rationale": str}]. Use exact pool names for non-proposed items. No prose.\n\n'
        f"PATIENT CONTEXT:\n{patient_context}\n\nALLOWED POOL:\n{pool_lines}\n"
    )
    try:
        client = genai.Client(api_key=config.GEMINI_API_KEY)
        resp = client.models.generate_content(
            model=config.GEMINI_MODEL,
            contents=prompt,
            config={"temperature": 0, "response_mime_type": "application/json"},
        )
        parsed = json.loads(resp.text)
    except Exception as e:  # noqa: BLE001
        print(f"[ranker] LLM failed, falling back to deterministic: {e}")
        return None

    out: list[Ranked] = []
    proposed_count = 0
    rank = 1
    for item in parsed:
        name = str(item.get("testName", "")).strip()
        if not name:
            continue
        is_proposed = bool(item.get("proposed"))
        cand = by_name.get(name.lower())

        if cand and not is_proposed:
            chosen = cand                                   # validated: in-pool
        elif is_proposed and proposed_count < config.MAX_PROPOSED_TESTS:
            chosen = Candidate(                             # net-new, flagged, will be learned
                object_id=None, test_name=name,
                test_detail=str(item.get("rationale", "")),
                unit_name="", data_type="", calculation_type="MANUAL",
                normative_text="", priority=4, source=SOURCE_PROPOSED,
                muscle=str(item.get("muscle", "")), joint=str(item.get("joint", "")),
                needs_review=True,
            )
            proposed_count += 1
        else:
            continue  # out-of-pool and not a valid proposal → drop (no hallucinations)

        out.append(
            Ranked(
                candidate=chosen, rank=rank,
                confidence=float(item.get("confidence", chosen.priority / 10.0)),
                rationale=str(item.get("rationale", chosen.test_detail or "")),
            )
        )
        rank += 1
    return out or None


def rank(candidates: list[Candidate], patient_context: str) -> list[Ranked]:
    # LLM path may PROPOSE tests even when the pool is empty, so don't short-circuit.
    llm = _llm(candidates, patient_context)
    if llm is not None:
        return llm
    return _deterministic(candidates)
