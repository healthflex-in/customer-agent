"""Compose the non-test sections of the assessment draft.

- `clinicalDetails` + `subjectiveAssessment` are built DETERMINISTICALLY from the
  intake form — they only restate what the patient reported (safe, no LLM).
- `plan` + `patientAdvice` come from condition-keyed templates in tests_pool.json
  (curated, growable). All of it is advisory — the clinician edits before saving.

Output keys match what `mapAgentFormDataToAssessment` reads:
  clinicalDetails / subjectiveAssessment / plan / patientAdvice
"""

from __future__ import annotations

import re

from app import config, pool
from app.pipeline.models import SOURCE_VALD

# Exact intake section/field names (from customer-info.form_data).
S_COMPLAINT = "Present Complaint"
S_PREV = "Previous Consultations"
S_PAIN = "Pain Assessment"
S_HISTORY = "History & Diagnostics"
S_GOALS = "Treatment Goals"


def _g(form_data: dict, section: str, field: str) -> str:
    sec = form_data.get(section, {}) if isinstance(form_data, dict) else {}
    return str(sec.get(field, "")).strip() if isinstance(sec, dict) else ""


def _parse_nprs(severity: str):
    """'7' / '7/10' → 7; non-numeric ('Severe') → None."""
    m = re.search(r"\b(\d{1,2})\b", severity or "")
    if not m:
        return None
    val = int(m.group(1))
    return val if 0 <= val <= 10 else None


def _clinical_details(form_data: dict) -> dict | None:
    chief = _g(form_data, S_COMPLAINT, "Primary Complaint")
    history_bits = [
        ("Duration", _g(form_data, S_COMPLAINT, "Duration of the Issue")),
        ("Onset", _g(form_data, S_COMPLAINT, "Onset (Gradual or Sudden)")),
        ("Mechanism", _g(form_data, S_COMPLAINT, "Mechanism of Injury or Cause")),
        ("Previous treatment", _g(form_data, S_PREV, "Previous Diagnosis or Advice and Prescribed Treatment Taken")),
        ("Systemic/surgical history", _g(form_data, S_HISTORY, "Systemic Illness and Surgical History")),
        ("Lifestyle", _g(form_data, S_HISTORY, "Current Lifestyle")),
    ]
    history = ". ".join(f"{label}: {val}" for label, val in history_bits if val)
    nprs = _parse_nprs(_g(form_data, S_PAIN, "Severity (1-10)"))
    if not (chief or history or nprs is not None):
        return None
    out = {"chiefComplaint": chief, "clinicalHistory": history}
    if nprs is not None:
        out["nprs"] = nprs
    return out


def _subjective_lines(form_data: dict) -> list[str]:
    lines: list[str] = []
    chief = _g(form_data, S_COMPLAINT, "Primary Complaint")
    if chief:
        lines.append(f"Patient reports {chief.lower()}.")

    loc = _g(form_data, S_PAIN, "Primary Location of Pain")
    sev = _g(form_data, S_PAIN, "Severity (1-10)")
    if loc or sev:
        lines.append(
            "Pain" + (f" located at {loc}" if loc else "") + (f", severity {sev}" if sev else "") + "."
        )
    agg = _g(form_data, S_PAIN, "Aggravating Factors")
    rel = _g(form_data, S_PAIN, "Relieving Factors")
    if agg:
        lines.append(f"Aggravated by {agg}.")
    if rel:
        lines.append(f"Relieved by {rel}.")

    status = _g(form_data, S_PREV, "Current Status of Issue (Improved, Same, Worse)")
    if status:
        lines.append(f"Current status: {status}.")

    # Goals are surfaced in the dedicated first-assessment Goals section, not here.
    return lines


def _patient_goals(form_data: dict) -> dict | None:
    """Map the intake 'Treatment Goals' into the first-assessment Goals section
    shape: { shortTermGoals: [{goal}], longTermGoals: [{goal}] }. Only if the
    patient actually provided them."""
    short = _g(form_data, S_GOALS, "Short-Term Goals (within 3 months)")
    longg = _g(form_data, S_GOALS, "Long-Term Goals (after 3 months)")
    exp = _g(form_data, S_GOALS, "Specific Expectations from Treatment")

    short_goals = [{"goal": short}] if short else []
    long_goals = [{"goal": longg}] if longg else []
    # Expectations are a long-horizon aim → group with long-term goals.
    if exp:
        long_goals.append({"goal": exp})

    if not (short_goals or long_goals):
        return None
    return {"shortTermGoals": short_goals, "longTermGoals": long_goals}


def _extra_tests_block(extra_tests) -> str:
    """VALD + proposed/learned tests can't be picked in the objective dropdown, so
    surface them here as a bulleted list of recommended additional tests."""
    if not extra_tests:
        return ""
    shown = extra_tests[: config.MAX_EXTRA_TESTS]
    lines = ["Recommended additional tests to consider:"]
    for r in shown:
        c = r.candidate
        tag = "VALD" if c.source == SOURCE_VALD else "suggested"
        lines.append(f"• {c.test_name} ({tag})")
    more = len(extra_tests) - len(shown)
    if more > 0:
        lines.append(f"• …and {more} more")
    return "\n".join(lines)


def compose(form_data: dict, condition_label: str, extra_tests=None) -> dict:
    """Return the extra assessment sections (only non-empty ones)."""
    out: dict = {}

    cd = _clinical_details(form_data)
    if cd:
        out["clinicalDetails"] = cd

    narrative = " ".join(_subjective_lines(form_data)).strip()
    extra_block = _extra_tests_block(extra_tests)
    parts = [p for p in (narrative, extra_block) if p]
    if parts:
        out["subjectiveAssessment"] = {"assessment": "\n\n".join(parts)}

    entry = pool.get_condition(condition_label) or {}

    goals = _patient_goals(form_data)
    if goals:
        out["patientGoals"] = goals

    plan_exercises = entry.get("plan") or []
    if plan_exercises:
        out["plan"] = {
            "advice": "",
            "plans": [
                {"exercise": p.get("exercise", ""), "comments": p.get("comments", ""),
                 "set": [], "duration": {"value": 0, "unit": ""}}
                for p in plan_exercises if p.get("exercise")
            ],
        }
    return out
