"""Stable, immutable representation of administered PROM instruments."""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any, Mapping, Sequence


PROM_SCHEMA_VERSION = 1

INSTRUMENTS: dict[str, tuple[str, str]] = {
    "prom_ohs": ("ohs", "Oxford Hip Score (OHS)"),
    "prom_oss": ("oss", "Oxford Shoulder Score (OSS)"),
    "prom_phq9": ("phq9", "PHQ-9 (Depression)"),
    "prom_gad7": ("gad7", "GAD-7 (Anxiety)"),
    "prom_nps": ("nps", "Numeric Pain Scale (NPS)"),
    "prom_rmdq": ("rmdq", "RMDQ (Back Disability)"),
    "prom_koos": ("koos", "KOOS (Knee)"),
    "prom_dash": ("dash", "DASH (Arm/Shoulder/Hand)"),
    "prom_odi": ("odi", "Oswestry Disability Index (ODI)"),
    "prom_whoqol": ("whoqol", "WHOQOL (Quality of Life)"),
}


def instrument_identity(question_id: Any) -> tuple[str, str] | None:
    qid = str(question_id or "").strip().lower()
    for prefix, identity in INSTRUMENTS.items():
        if qid == prefix or qid.startswith(prefix + "_"):
            return identity
    return None


def is_immutable_prom_question(question: Any) -> bool:
    return isinstance(question, Mapping) and instrument_identity(
        question.get("question_id")
    ) is not None


def _clean_options(value: Any) -> list[str] | None:
    if not isinstance(value, (list, tuple)):
        return None
    options = [str(option).strip() for option in value]
    return options if all(options) else None


def _definition_hash(questions: Sequence[Mapping[str, Any]]) -> str:
    canonical = [
        {
            "questionId": question["questionId"],
            "displayText": question["displayText"],
            "responseType": question["responseType"],
            "options": question["options"],
            "order": question["order"],
        }
        for question in questions
    ]
    encoded = json.dumps(
        canonical, ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def build_prom_snapshot(
    questions: Sequence[Any],
    *,
    source_form_id: str,
    legacy_form_data: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a content-addressed administered-definition and response snapshot.

    Scoring is intentionally disabled. A content hash proves which exact wording,
    order and options were administered, but it is not a publisher-approved
    instrument version or scoring licence.
    """

    grouped: dict[str, dict[str, Any]] = {}
    seen_ids: set[str] = set()
    for order, raw_question in enumerate(questions):
        if not isinstance(raw_question, Mapping):
            raise ValueError("Every assessment question requires a stable question ID")
        qid = str(raw_question.get("question_id") or "").strip()
        text = str(raw_question.get("text") or "").strip()
        if not qid:
            raise ValueError("Every assessment question requires a stable question ID")
        if not text:
            raise ValueError(f"Assessment question {qid} has no display text")
        identity = instrument_identity(qid) or ("custom", "Other Assessments")
        if qid in seen_ids:
            raise ValueError(f"Duplicate PROM question ID: {qid}")
        seen_ids.add(qid)

        instrument_id, display_name = identity
        declared_version = str(
            raw_question.get("instrument_version")
            or raw_question.get("instrumentVersion")
            or ""
        ).strip() or None
        entry = grouped.setdefault(
            instrument_id,
            {
                "instrumentId": instrument_id,
                "displayName": display_name,
                "declaredVersions": set(),
                "questions": [],
                "responses": [],
            },
        )
        if declared_version:
            entry["declaredVersions"].add(declared_version)

        response_type = str(raw_question.get("type") or "text").strip().lower()
        options = _clean_options(raw_question.get("options"))
        if instrument_id == "nps":
            if response_type not in {"scale", "linear_scale", "slider", "rating"}:
                raise ValueError(
                    f"Clinical PROM question {qid} requires explicit scale metadata"
                )
        elif instrument_id != "custom":
            if response_type not in {
                "single_choice", "dropdown", "likert", "boolean", "yes_no"
            } or not options:
                raise ValueError(
                    f"Clinical PROM question {qid} requires explicit response options"
                )
        question = {
            "questionId": qid,
            "displayText": text,
            "responseType": response_type,
            "options": options,
            "order": order,
        }
        entry["questions"].append(question)

        legacy_value = None
        if isinstance(legacy_form_data, Mapping):
            legacy_section = legacy_form_data.get(display_name)
            if isinstance(legacy_section, Mapping) and text in legacy_section:
                legacy_value = legacy_section.get(text)
        entry["responses"].append(
            {"questionId": qid, "value": legacy_value if legacy_value is not None else ""}
        )

    instruments: list[dict[str, Any]] = []
    for entry in grouped.values():
        versions = sorted(entry.pop("declaredVersions"))
        entry["definitionHash"] = _definition_hash(entry["questions"])
        entry["declaredVersion"] = versions[0] if len(versions) == 1 else None
        entry["versionStatus"] = (
            "declared" if len(versions) == 1 else
            "conflict" if len(versions) > 1 else
            "content_addressed_only"
        )
        entry["wordingPolicy"] = "immutable"
        entry["scoring"] = {
            "status": "not_configured",
            "score": None,
            "reason": "No clinically approved scoring definition is configured.",
        }
        instruments.append(entry)

    instruments.sort(
        key=lambda item: min(q["order"] for q in item["questions"])
    )
    return {
        "schemaVersion": PROM_SCHEMA_VERSION,
        "sourceFormId": source_form_id,
        "instruments": instruments,
    }


def questions_from_prom_snapshot(snapshot: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Reconstruct the exact administered questions for a resume operation."""

    if snapshot.get("schemaVersion") != PROM_SCHEMA_VERSION:
        raise ValueError("Unsupported PROM snapshot schema version")
    questions: list[dict[str, Any]] = []
    for instrument in snapshot.get("instruments") or []:
        if not isinstance(instrument, Mapping):
            continue
        version = instrument.get("declaredVersion")
        for question in instrument.get("questions") or []:
            if not isinstance(question, Mapping):
                continue
            questions.append(
                {
                    "question_id": question.get("questionId"),
                    "text": question.get("displayText"),
                    "type": question.get("responseType") or "text",
                    "options": copy.deepcopy(question.get("options")),
                    "instrument_version": version,
                    "definition_frozen": True,
                    "order": question.get("order", len(questions)),
                }
            )
    questions.sort(key=lambda item: item["order"])
    for question in questions:
        question.pop("order", None)
    return questions


def apply_prom_answers(
    snapshot: Mapping[str, Any], answers_by_question_id: Mapping[str, Any]
) -> dict[str, Any]:
    """Return a copied snapshot with stable-ID responses updated."""

    updated = copy.deepcopy(dict(snapshot))
    known_ids: set[str] = set()
    for instrument in updated.get("instruments") or []:
        for response in instrument.get("responses") or []:
            qid = str(response.get("questionId") or "")
            known_ids.add(qid)
            if qid in answers_by_question_id:
                response["value"] = answers_by_question_id[qid]
    unknown = set(answers_by_question_id) - known_ids
    if unknown:
        raise ValueError(f"Unknown PROM question IDs: {', '.join(sorted(unknown))}")
    return updated
