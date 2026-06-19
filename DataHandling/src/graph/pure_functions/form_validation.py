"""
Pure form-validation helpers extracted from HealthAgent.validator().
No LLM calls. No side effects. Fully unit-testable.
"""
from typing import Optional


def ensure_string(text) -> str:
    """Convert any LLM response object to a plain string."""
    if hasattr(text, "text"):
        return text.text
    return str(text) if hasattr(text, "__str__") else "Response could not be processed"


def extract_json_from_response(response: Optional[str]) -> Optional[str]:
    """Extract the first valid JSON object from an LLM response string."""
    if not response:
        return None
    response = response.strip()
    if response.startswith("{") and response.endswith("}"):
        return response
    start, end = response.find("{"), response.rfind("}")
    if start != -1 and end != -1 and start < end:
        return response[start:end + 1]
    if "```json" in response:
        parts = response.split("```json")
        if len(parts) > 1:
            code_part = parts[1].split("```")[0].strip()
            if code_part.startswith("{") and code_part.endswith("}"):
                return code_part
    if "```" in response:
        parts = response.split("```")
        if len(parts) > 1:
            code_part = parts[1].strip()
            if code_part.startswith("{") and code_part.endswith("}"):
                return code_part
    return None


def validate_section(form: dict, section: str) -> list:
    """
    Return the list of missing (unfilled) fields for the given form section.
    An empty list means the section is complete.

    Extracted from HealthAgent.validator() — same logic, no self.* mutations.
    """
    missing: list = []

    if section not in form:
        return missing

    section_data = form[section]

    # ── Special handling: Previous Consultations ──────────────────────────────
    if section == "Previous Consultations":
        prev_field = "Previous Diagnosis or Advice and Prescribed Treatment Taken"
        status_field = "Current Status of Issue (Improved, Same, Worse)"

        previous_value = section_data.get(prev_field, "").strip()
        status_value = section_data.get(status_field, "").strip()

        consultation_keywords = [
            "doctor", "physiotherapist", "hospital", "consulted", "visited",
            "diagnosis", "diagnosed", "prescribed", "treatment", "medicine",
            "injection", "exercise", "physio", "clinic",
        ]
        has_consultation_mention = bool(previous_value) and any(
            kw in previous_value.lower() for kw in consultation_keywords
        )

        # Status filled but no actual consultation mentioned → treat as incomplete
        if status_value and not previous_value and not has_consultation_mention:
            return [prev_field, status_field]

        prev_lower = previous_value.lower()
        no_consult_indicators = [
            "no previous", "didn't visit", "did not visit", "haven't consulted",
            "have not consulted", "no consultations", "no doctor", "no hospital",
            "never consulted", "not consulted", "none", "nothing",
            "no previous consultations", "not applicable", "not seen",
            "haven't seen", "have not seen", "nope", "no one",
        ]
        has_no_consultations = any(ind in prev_lower for ind in no_consult_indicators)

        if has_no_consultations:
            if not previous_value:
                missing.append(prev_field)
        else:
            if not previous_value and status_value:
                return [prev_field, status_field]
            if not previous_value:
                missing.append(prev_field)
            elif not status_value:
                missing.append(status_field)

        return missing

    # ── All other sections: check every field ─────────────────────────────────
    for field, value in section_data.items():
        # Fields marked "(If Any)" or "(Optional)" are not required — skip them
        # when deciding section completion so they never block advancement.
        if "(If Any)" in field or "(Optional)" in field:
            continue
        if not value:
            missing.append(field)

    return missing


def get_all_missing_fields(form: dict, form_sections: list) -> dict:
    """Return {section: [missing_fields]} for every section that has gaps."""
    return {
        section: fields
        for section in form_sections
        for fields in [validate_section(form, section)]
        if fields
    }


def is_form_complete(form: dict, form_sections: list) -> bool:
    """True when every field in every section is filled."""
    return not get_all_missing_fields(form, form_sections)
