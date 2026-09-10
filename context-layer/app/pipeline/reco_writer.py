"""Step 5 — build the FE-compatible payload and persist it.

Emits `{ objectiveAssessment: { tests: [ObjectiveTest] } }` in the exact shape the
dashboard's `mapAgentFormDataToAssessment` consumes. Measurement fields
(value/left/right/positiveNegative) are intentionally left blank — the clinician
measures. We NEVER write reports / AgentReport / clinician_agent_form_data.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app import config, db
from app.pipeline.models import SOURCE_PROPOSED, Ranked


def _to_objective_test(r: Ranked) -> dict:
    c = r.candidate
    return {
        "objectId": c.object_id,            # string catalog id (JSON-safe); None for proposed
        "testName": c.test_name,
        "exerciseId": c.object_id,          # catalog/vald id; None for proposed
        "unitName": c.unit_name,
        # measurements left empty for the clinician:
        "value": None,
        "left": None,
        "right": None,
        "positiveNegative": None,
        "comments": "",
        "testDetail": c.test_detail,
        "textForNormativeData": c.normative_text,
        "dataType": c.data_type,
        "calculationType": c.calculation_type,
        # advisory metadata (ignored by the form, useful for the panel/eval):
        "_recoRank": r.rank,
        "_recoConfidence": r.confidence,
        "_recoRationale": r.rationale,
        "_source": c.source,               # objective | vald | agent_proposed
        "_deviceType": c.device_type or None,
        "_isProposed": c.source == SOURCE_PROPOSED,  # clinician must review before use
    }


def build_payload(ranked: list[Ranked], condition: str, pool_version: str) -> dict:
    tests = [_to_objective_test(r) for r in ranked[: config.MAX_TESTS]]
    return {
        "objectiveAssessment": {"tests": tests},
        "condition": condition,
        "poolVersion": pool_version,
    }


def upsert(user_id, appointment_id, report_id, payload: dict) -> dict:
    now = datetime.now(timezone.utc)
    doc = {
        "userId": db.as_object_id(user_id),
        "appointmentId": db.as_object_id(appointment_id),
        "reportId": db.as_object_id(report_id) if report_id else None,
        **payload,
        "updatedAt": now,
    }
    db.coll(config.COLL_RECO).update_one(
        {"appointmentId": doc["appointmentId"], "userId": doc["userId"]},
        {"$set": doc, "$setOnInsert": {"createdAt": now}},
        upsert=True,
    )
    return payload
