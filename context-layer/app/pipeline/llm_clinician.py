"""The intelligent clinician — one LLM pass that READS the whole case and fills
the assessment with judgment, grounded by the real catalogs.

Unlike the deterministic pipeline (keyword condition + curated pool + templates),
this reads the full intake and reasons: it determines the condition, selects the
most appropriate objective tests FROM THE REAL CATALOG (so they still pre-select in
the dropdown), chooses relevant VALD tests, and writes a tailored subjective
assessment, plan, and advice for THIS patient.

Grounding / safety:
- Objective tests are validated against the objectiveAssessments catalog; anything
  the model invents there is dropped (no hallucinated catalog tests).
- VALD picks are validated against the real vald-exercise-data pool.
- Truly novel test ideas come back as `additionalTests` (advisory, clinician-review).
Returns None on any failure so the caller falls back to the deterministic pipeline.
"""

from __future__ import annotations

import json
import time

from app import config, db
from app.pipeline import vald_retriever

_catalog_cache: list | None = None

# Gemini prompt/context cache handle for the static catalog + instructions.
# Reused across patients (the catalog is identical) and refreshed before its TTL,
# so we never resend/reprocess the ~2k-token catalog per call. One handle at a time
# → no pileup; expired caches are auto-deleted by Gemini.
_STATIC_TTL_SECONDS = 3600
_prompt_cache_name: str | None = None
_prompt_cache_expiry: float = 0.0
_prompt_cache_disabled: bool = False


def _output_contract() -> str:
    return (
        "When given a PATIENT INTAKE, read the WHOLE case and reason clinically, then "
        "return STRICT JSON, no prose:\n"
        "{\n"
        '  "condition": "concise clinical impression (e.g. \'right rotator cuff tendinopathy\')",\n'
        '  "objectiveTests": ["exact catalog test name", ...],   // 6-12, ordered most→least important\n'
        '  "valdTests": ["exact VALD name", ...],                // 0-6 relevant instrumented tests\n'
        '  "additionalTests": ["clinically useful test NOT in the catalog", ...],  // 0-3, optional\n'
        '  "subjectiveAssessment": "a synthesised clinical narrative of this patient\'s presentation",\n'
        '  "clinicalDetails": {"chiefComplaint": "...", "clinicalHistory": "...", "nprs": <int 0-10 or null>},\n'
        '  "plan": [{"exercise": "name", "comments": "dosage/notes tailored to this patient"}],  // 3-6\n'
        '  "advice": "specific patient advice for this presentation"\n'
        "}\n"
        "Use EXACT names from the catalogs; only choose objective tests from the catalog."
    )


def _system_instruction() -> str:
    catalog_lines = "\n".join(
        f"- {d['testName']}: {d.get('testDetail', '')}" for d in _catalog() if d.get("testName")
    )
    vald_pool = vald_retriever._pool()
    vald_names = sorted(set(vald_pool.get("forceFrame", []) + vald_pool.get("forceDeck", [])))
    vald_lines = "\n".join(f"- {n}" for n in vald_names)
    return (
        "You are an experienced musculoskeletal physiotherapist preparing a patient's "
        "objective assessment from their intake.\n\n"
        f"OBJECTIVE TEST CATALOG (choose the most clinically appropriate tests for the patient):\n"
        f"{catalog_lines}\n\n"
        f"VALD INSTRUMENTED TESTS (optional, pick any relevant):\n{vald_lines}\n\n"
        + _output_contract()
    )


def _get_prompt_cache(client) -> str | None:
    """Create or reuse the cached static content (catalog + instructions)."""
    global _prompt_cache_name, _prompt_cache_expiry, _prompt_cache_disabled
    if _prompt_cache_disabled:
        return None
    now = time.time()
    if _prompt_cache_name and now < _prompt_cache_expiry:
        return _prompt_cache_name
    try:
        from google.genai import types  # type: ignore

        cache = client.caches.create(
            model=config.GEMINI_MODEL,
            config=types.CreateCachedContentConfig(
                system_instruction=_system_instruction(),
                ttl=f"{_STATIC_TTL_SECONDS}s",
            ),
        )
        _prompt_cache_name = cache.name
        _prompt_cache_expiry = now + _STATIC_TTL_SECONDS - 300  # refresh before expiry
        print(f"[llm_clinician] prompt cache ready: {cache.name}")
        return _prompt_cache_name
    except Exception as e:  # noqa: BLE001
        # Too-small content / unsupported → disable caching, fall back to inline.
        print(f"[llm_clinician] prompt caching unavailable, using inline prompt: {e}")
        _prompt_cache_disabled = True
        return None


def _catalog() -> list:
    global _catalog_cache
    if _catalog_cache is None:
        _catalog_cache = list(
            db.coll(config.COLL_OBJECTIVE_ASSESSMENTS).find(
                {},
                {"testName": 1, "testDetail": 1, "dataType": 1, "unitName": 1,
                 "calculationType": 1, "textForNormativeData": 1},
            )
        )
    return _catalog_cache


def _catalog_by_name() -> dict:
    return {str(d.get("testName", "")).strip().lower(): d for d in _catalog()}


def _format_intake(form_data: dict) -> str:
    lines = []
    for section, fields in (form_data or {}).items():
        if isinstance(fields, dict):
            filled = {k: v for k, v in fields.items() if str(v).strip()}
            if filled:
                lines.append(f"## {section}")
                for k, v in filled.items():
                    lines.append(f"- {k}: {v}")
    return "\n".join(lines) or "(intake is sparse / mostly empty)"


def reason(form_data: dict) -> dict | None:
    if not (config.USE_LLM and config.GEMINI_API_KEY):
        return None
    try:
        from google import genai  # type: ignore
    except Exception:  # noqa: BLE001
        return None

    catalog = _catalog()
    if not catalog:
        return None

    intake_msg = f"PATIENT INTAKE:\n{_format_intake(form_data)}\n\nProduce the JSON now."

    try:
        from google.genai import types  # type: ignore

        client = genai.Client(api_key=config.GEMINI_API_KEY)
        cache_name = _get_prompt_cache(client)
        if cache_name:
            # Static catalog + instructions come from the cache; send only the intake.
            resp = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=intake_msg,
                config=types.GenerateContentConfig(
                    cached_content=cache_name,
                    temperature=0,
                    response_mime_type="application/json",
                ),
            )
        else:
            # Inline fallback: full prompt (system instruction + intake) in one shot.
            resp = client.models.generate_content(
                model=config.GEMINI_MODEL,
                contents=_system_instruction() + "\n\n" + intake_msg,
                config=types.GenerateContentConfig(
                    temperature=0, response_mime_type="application/json"
                ),
            )
        data = json.loads(resp.text)
    except Exception as e:  # noqa: BLE001
        print(f"[llm_clinician] failed, falling back to deterministic: {e}")
        return None

    if not isinstance(data, dict) or not data.get("condition"):
        return None

    by_name = _catalog_by_name()
    _vp = vald_retriever._pool()
    vald_set = {n.lower() for n in (_vp.get("forceFrame", []) + _vp.get("forceDeck", []))}

    # Objective tests → validated catalog picks (dropdown-eligible, carry objectId).
    objective = []
    for i, name in enumerate(data.get("objectiveTests", []) or [], start=1):
        doc = by_name.get(str(name).strip().lower())
        if not doc:
            continue  # not in catalog → drop (no hallucinations)
        objective.append({
            "objectId": str(doc.get("_id")),
            "testName": doc.get("testName", name),
            "unitName": doc.get("unitName", ""),
            "dataType": doc.get("dataType", ""),
            "calculationType": doc.get("calculationType", "MANUAL"),
            "testDetail": doc.get("testDetail", ""),
            "textForNormativeData": doc.get("textForNormativeData", ""),
            "rank": i,
        })

    # VALD + additional → advisory list surfaced in subjective.
    extras = []
    for name in data.get("valdTests", []) or []:
        if str(name).strip().lower() in vald_set:
            extras.append((str(name).strip(), "VALD"))
    for name in data.get("additionalTests", []) or []:
        if str(name).strip():
            extras.append((str(name).strip(), "suggested"))

    return {
        "condition": str(data.get("condition")),
        "objective": objective,
        "extras": extras,
        "subjectiveAssessment": str(data.get("subjectiveAssessment", "")).strip(),
        "clinicalDetails": data.get("clinicalDetails") or {},
        "plan": data.get("plan") or [],
        "advice": str(data.get("advice", "")).strip(),
    }
