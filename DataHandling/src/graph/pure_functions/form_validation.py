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

    # Persisted draft reservations use {} and partial records may omit fields.
    # Missing schema keys are unanswered, never evidence of completion.
    from src.forms.loader import load_form
    expected_fields = load_form("FRM-01").sections.get(section, [])
    stored_section = form.get(section, {})
    if not isinstance(stored_section, dict):
        stored_section = {}
    section_data = {field: "" for field in expected_fields}
    section_data.update(stored_section)

    rules = load_form("FRM-01")
    for field, value in section_data.items():
        # Fields marked "(If Any)" or "(Optional)" are not required — skip them
        # when deciding section completion so they never block advancement.
        if "(If Any)" in field or "(Optional)" in field:
            continue
        # A dependent field is asked only once the field it depends on has an
        # answer; a denial of that field saves the dependent as not applicable.
        parent = rules.rule(field).applies_when_answered
        if parent and not str(section_data.get(parent) or "").strip():
            continue
        if field == "Current Lifestyle":
            from src.graph.pure_functions.lifestyle import missing_lifestyle_components
            missing.extend(missing_lifestyle_components(str(value or "")))
        elif value is None or (isinstance(value, str) and not value.strip()):
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


def missing_in_ask_order(
    form: dict, form_sections: list, visit_context: str = "unknown"
) -> list[tuple[str, str]]:
    """Return [(section, field), ...] still to ask, in the order they should be asked.

    The one place that decides what is missing and in what order, shared by live
    turns and resume. Fields stay grouped by section. Sections that still need a
    mandatory answer come first, the main intake comes before any additional
    complaint, and ties keep FRM-01 order. A field closed as Not provided or
    Declined is not missing.
    """
    from src.forms.loader import load_form

    definition = load_form("FRM-01")
    skipped = (
        definition.visit_types["general_assessment"].skip_sections
        if visit_context == "general_assessment"
        else frozenset()
    )
    by_section = {
        section: list(fields)
        for section, fields in get_all_missing_fields(form, form_sections).items()
    }

    def needs_mandatory(section: str) -> bool:
        return any(definition.is_mandatory(section, field) for field in by_section[section])

    sections = sorted(
        (section for section in by_section if section not in skipped),
        key=lambda section: (
            list(form_sections).index(section) if section in form_sections else len(form_sections)
        ),
    )
    sections.sort(
        key=lambda section: (section.startswith("Additional Complaint "), not needs_mandatory(section))
    )
    return [
        (section, field)
        for section in sections
        for field in sorted(by_section[section], key=lambda name: not definition.is_mandatory(section, name))
    ]


def first_section_batch(
    missing: list[tuple[str, str]], limit: int
) -> list[tuple[str, str]]:
    """The next questions to ask: the open fields of the first section only.

    A batch never mixes sections, so the patient is only asked what is left
    in the section being discussed.
    """
    if not missing:
        return []
    first_section = missing[0][0]
    return [item for item in missing if item[0] == first_section][:limit]


def is_form_complete(form: dict, form_sections: list) -> bool:
    """True when every field in every section is filled."""
    return not get_all_missing_fields(form, form_sections)
