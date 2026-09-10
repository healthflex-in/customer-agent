"""Orchestration: intake form → condition → pool (objective + VALD) → rank → payload.

- Candidate pool = file-backed tests_pool.json (objective + agent-learned) + VALD.
- The LLM (when enabled) ranks and may PROPOSE net-new tests; proposals are persisted
  back into tests_pool.json (the pool grows / the system learns over time).
- One material-change-gated LLM call: fresh context (same form hash + condition + pool
  version) serves the stored recommendation without re-ranking. force=True bypasses it.
"""

from __future__ import annotations

from app import config, db, pool
from app.pipeline import assessment_composer as composer
from app.pipeline import candidate_retriever as retriever
from app.pipeline import condition_classifier as classifier
from app.pipeline import context_manager as ctxmgr
from app.pipeline import llm_clinician
from app.pipeline import ranker as ranker_mod
from app.pipeline import reco_writer as writer
from app.pipeline import vald_retriever
from app.pipeline.models import SOURCE_OBJECTIVE, SOURCE_PROPOSED


def _intake_score(doc) -> int:
    """Rank candidate intake docs: prefer ones with a real complaint / more filled fields."""
    fd = doc.get("form_data", {}) or {}
    pc = fd.get("Present Complaint", {}) if isinstance(fd, dict) else {}
    score = 0
    if isinstance(pc, dict) and pc.get("Primary Complaint"):
        score += 100
    if isinstance(fd, dict):
        score += sum(
            1 for s in fd.values() if isinstance(s, dict) for v in s.values() if v
        )
    return score


def _has_primary_complaint(doc) -> bool:
    fd = doc.get("form_data", {}) or {}
    pc = fd.get("Present Complaint", {}) if isinstance(fd, dict) else {}
    return bool(isinstance(pc, dict) and str(pc.get("Primary Complaint", "")).strip())


def _to_dt(value):
    """Coerce a Mongo date (datetime or ISO string) to a naive UTC datetime, or None."""
    from datetime import datetime

    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _appointment_date(appointment_id):
    if not appointment_id:
        return None
    doc = db.coll("appointments").find_one(
        {"_id": db.as_object_id(appointment_id)}, {"appointmentStartTime": 1}
    )
    return _to_dt(doc.get("appointmentStartTime")) if doc else None


def _intake_date(doc):
    return _to_dt(doc.get("createdAt")) or _to_dt(doc.get("updatedAt"))


def _load_intake(user_id, appointment_id=None):
    docs = list(db.coll(config.COLL_CUSTOMER_INFO).find(db.id_query("userId", user_id)))
    if not docs:
        return {}, None
    # Only clinical intakes (FRM-01, with a Present Complaint) are usable for the
    # assessment. PROM forms (FRM-02) are also appointment-stamped but have no complaint.
    pool = [d for d in docs if _has_primary_complaint(d)]
    if not pool:
        return {}, None

    # 1) An intake explicitly tagged to THIS appointment wins.
    if appointment_id:
        aoid = db.as_object_id(appointment_id)
        exact = [d for d in pool if d.get("appointmentId") in (aoid, str(appointment_id))]
        if exact:
            best = max(exact, key=_intake_score)
            return best.get("form_data", {}) or {}, best.get("category")

    # 2) Date-based: use the intake current AS OF the appointment's date. A tagging/
    #    intake dated T applies to appointments on/after T; a past appointment uses the
    #    latest intake that existed on/before it. Today/upcoming appointments get the
    #    most recent intake.
    appt_dt = _appointment_date(appointment_id)
    if appt_dt is not None:
        applicable = [d for d in pool if _intake_date(d) and _intake_date(d) <= appt_dt]
        if applicable:
            best = max(applicable, key=_intake_date)
            return best.get("form_data", {}) or {}, best.get("category")

        # No intake existed on/before the appointment date → nothing applies. Do NOT
        # back-fill a past appointment with a newer intake.
        return {}, None

    # 3) No appointment date available → richest clinical intake.
    best = max(pool, key=_intake_score)
    return best.get("form_data", {}) or {}, best.get("category")


def _applies_to(appointment_id) -> str:
    # First vs follow-up. Kept simple; refine with report history in a later phase.
    return "any"


def _dedupe(candidates):
    """Keep one candidate per test name, preferring the higher priority."""
    best: dict[str, object] = {}
    for c in candidates:
        key = c.test_name.strip().lower()
        if key not in best or c.priority > best[key].priority:  # type: ignore[attr-defined]
            best[key] = c
    return list(best.values())


def get_recommendations(user_id, appointment_id, report_id=None, *, force: bool = False) -> dict:
    form_data, category = _load_intake(user_id, appointment_id)
    ctx = ctxmgr.activate(user_id, appointment_id)
    source_hash = ctxmgr.form_hash(form_data)

    # Generic cache: nothing about the intake changed → serve the stored draft.
    if not force and ctx.get("sourceFormHash") == source_hash and ctx.get("provenance"):
        cached = db.coll(config.COLL_RECO).find_one(
            {"appointmentId": db.as_object_id(appointment_id), "userId": db.as_object_id(user_id)}
        )
        if cached:
            cached.pop("_id", None)
            return {"status": "cached", **_public(cached)}

    # No clinical intake applies to this appointment's date (e.g. a past appointment
    # that predates the patient's intake) → nothing to recommend.
    if not _blob(form_data).strip():
        return {
            "status": "no_intake",
            "condition": None,
            "objectiveAssessment": {"tests": []},
            "message": "No intake data applies to this assessment's date.",
        }

    # ── INTELLIGENT PATH: the LLM reads the whole case and fills the form ──────
    if config.USE_LLM and _blob(form_data).strip():
        llm = llm_clinician.reason(form_data)
        if llm:
            payload = _build_llm_payload(llm, form_data)
            writer.upsert(user_id, appointment_id, report_id, payload)
            ctxmgr.save(
                user_id, appointment_id,
                condition={"label": llm["condition"], "confidence": 0.9, "source": "llm"},
                source_hash=source_hash, pool_version="llm:v1",
                candidate_names=[t["testName"] for t in llm["objective"]],
                provenance=[{"testName": t["testName"], "rank": t["rank"], "source": "llm"}
                            for t in llm["objective"]],
            )
            return {"status": "generated", "engine": "llm", **_public(payload)}

    # ── DETERMINISTIC FALLBACK: keyword condition + curated pool + templates ───
    conditions = classifier.classify(form_data, category)
    primary = conditions[0]
    labels = [c.label for c in conditions if c.label != "unknown"]

    if not labels:
        return {
            "status": "no_condition",
            "condition": primary.label,
            "objectiveAssessment": {"tests": []},
            "message": "Could not derive a condition from intake — please confirm the condition.",
        }

    flags = retriever.detect_patient_flags(_blob(form_data))
    result = retriever.retrieve(labels, _applies_to(appointment_id), flags)
    vald = vald_retriever.retrieve(labels)
    combined = _dedupe(result.candidates + vald)
    pool_version = f"{result.pool_version}+vald{len(vald)}"

    # Empty pool is OK when the LLM ranker can propose; otherwise nothing to offer.
    if not combined and not config.USE_LLM_RANKER:
        return {
            "status": "no_tests",
            "condition": primary.label,
            "objectiveAssessment": {"tests": []},
            "unmatchedRuleTests": result.unmatched_test_names,
            "message": f"No curated tests found for '{primary.label}'.",
        }

    ranked = ranker_mod.rank(combined, _patient_context(form_data, labels, flags))

    # LEARNING STEP: persist freshly proposed tests into the pool so they become
    # deterministic candidates next time (the pool grows, LLM needed less over time).
    learned = 0
    for r in ranked:
        c = r.candidate
        if c.source == SOURCE_PROPOSED:
            if pool.add_learned_test(
                primary.label, test_name=c.test_name, muscle=c.muscle, joint=c.joint,
                rationale=r.rationale, priority=c.priority,
            ):
                learned += 1

    # Only catalog-backed tests populate the objective DROPDOWN (they carry a real
    # objectId that matches getAllTests). VALD + proposed/learned tests can't be
    # selected there, so they go into the subjective assessment text instead.
    objective_ranked = [
        r for r in ranked if r.candidate.source == SOURCE_OBJECTIVE and r.candidate.object_id
    ]
    extra_ranked = [
        r for r in ranked if not (r.candidate.source == SOURCE_OBJECTIVE and r.candidate.object_id)
    ]

    payload = {
        **writer.build_payload(objective_ranked, primary.label, pool_version),
        **composer.compose(form_data, primary.label, extra_ranked),
    }
    writer.upsert(user_id, appointment_id, report_id, payload)

    ctxmgr.save(
        user_id, appointment_id,
        condition={"label": primary.label, "confidence": primary.confidence, "source": primary.source},
        source_hash=source_hash,
        pool_version=pool_version,
        candidate_names=[c.test_name for c in combined],
        provenance=[
            {"testName": r.candidate.test_name, "rank": r.rank, "source": r.candidate.source,
             "confidence": r.confidence, "rationale": r.rationale}
            for r in ranked
        ],
    )
    return {
        "status": "generated",
        **_public(payload),
        "learnedTestsAdded": learned,
        "unmatchedRuleTests": result.unmatched_test_names,
    }


def _build_llm_payload(llm: dict, form_data: dict) -> dict:
    """Turn the intelligent clinician's output into the FE-compatible payload."""
    tests = []
    for t in llm["objective"][: config.MAX_TESTS]:
        tests.append({
            "objectId": t["objectId"], "testName": t["testName"], "exerciseId": t["objectId"],
            "unitName": t.get("unitName", ""),
            "value": None, "left": None, "right": None, "positiveNegative": None,
            "comments": "", "testDetail": t.get("testDetail", ""),
            "textForNormativeData": t.get("textForNormativeData", ""),
            "dataType": t.get("dataType", ""), "calculationType": t.get("calculationType", "MANUAL"),
            "_recoRank": t["rank"], "_source": "objective", "_isProposed": False,
        })

    # Tailored subjective narrative + any VALD/suggested tests as a bulleted list.
    subj = llm.get("subjectiveAssessment", "").strip()
    if llm.get("extras"):
        shown = llm["extras"][: config.MAX_EXTRA_TESTS]
        lines = ["Recommended additional tests to consider:"]
        lines += [f"• {name} ({tag})" for name, tag in shown]
        more = len(llm["extras"]) - len(shown)
        if more > 0:
            lines.append(f"• …and {more} more")
        subj = (subj + "\n\n" + "\n".join(lines)).strip() if subj else "\n".join(lines)

    payload: dict = {
        "objectiveAssessment": {"tests": tests},
        "condition": llm["condition"],
        "poolVersion": "llm:v1",
    }
    if subj:
        payload["subjectiveAssessment"] = {"assessment": subj}

    cd = llm.get("clinicalDetails") or {}
    if cd.get("chiefComplaint") or cd.get("clinicalHistory") or cd.get("nprs") is not None:
        out_cd = {"chiefComplaint": cd.get("chiefComplaint", ""),
                  "clinicalHistory": cd.get("clinicalHistory", "")}
        if isinstance(cd.get("nprs"), int):
            out_cd["nprs"] = cd["nprs"]
        payload["clinicalDetails"] = out_cd

    plan = [p for p in (llm.get("plan") or []) if p.get("exercise")]
    if plan:
        payload["plan"] = {"advice": "", "plans": [
            {"exercise": p["exercise"], "comments": p.get("comments", ""),
             "set": [], "duration": {"value": 0, "unit": ""}} for p in plan
        ]}

    # Goals stay from the patient's own words (deterministic).
    goals = composer._patient_goals(form_data)
    if goals:
        payload["patientGoals"] = goals

    return payload


def _blob(form_data: dict) -> str:
    return " ".join(
        str(v) for s in (form_data or {}).values() if isinstance(s, dict) for v in s.values()
    )


def _patient_context(form_data: dict, labels: list[str], flags: set[str]) -> str:
    lines = [f"conditions: {', '.join(labels)}"]
    if flags:
        lines.append(f"flags: {', '.join(sorted(flags))}")
    for section, fields in (form_data or {}).items():
        if isinstance(fields, dict):
            filled = {k: v for k, v in fields.items() if v}
            if filled:
                lines.append(f"{section}: {filled}")
    return "\n".join(lines)


def _json_safe(value):
    """Recursively convert Mongo types (ObjectId, datetime) to JSON-serializable ones."""
    from datetime import datetime

    from bson import ObjectId

    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_json_safe(v) for v in value]
    return value


# Keys surfaced to the FE (all consumed by mapAgentFormDataToAssessment).
_PUBLIC_KEYS = (
    "condition", "poolVersion",
    "objectiveAssessment", "subjectiveAssessment",
    "clinicalDetails", "plan", "patientGoals",
)


def _public(payload: dict) -> dict:
    out = {k: payload.get(k) for k in _PUBLIC_KEYS if payload.get(k) is not None}
    out.setdefault("objectiveAssessment", {"tests": []})
    return _json_safe(out)
