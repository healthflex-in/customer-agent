"""Form and section progress calculations shared by API and WebSocket paths."""

SECTION_ORDER = (
    "Present Complaint",
    "Previous Consultations",
    "Pain Assessment",
    "History & Diagnostics",
    "Treatment Goals",
    "Referral",
)

PREVIOUS_CONSULTATIONS_FIELD = (
    "Previous Diagnosis or Advice and Prescribed Treatment Taken"
)
CURRENT_STATUS_FIELD = "Current Status of Issue (Improved, Same, Worse)"

NO_CONSULTATION_INDICATORS = (
    "no previous",
    "didn't visit",
    "did not visit",
    "haven't consulted",
    "have not consulted",
    "no consultations",
    "no doctor",
    "no hospital",
    "never consulted",
    "not consulted",
    "none",
    "nothing",
)


def _has_text(value) -> bool:
    return bool(value and str(value).strip())


def _means_no_previous_consultation(value) -> bool:
    normalized = str(value).strip().lower()
    return any(marker in normalized for marker in NO_CONSULTATION_INDICATORS)


def calculate_form_progress(form_data):
    """Return individual-field completion as a percentage from 0 to 100."""
    if not form_data:
        return 0

    total_fields = 0
    filled_fields = 0

    for section_name in SECTION_ORDER:
        if section_name not in form_data:
            continue

        section_data = form_data[section_name]
        if not isinstance(section_data, dict):
            continue

        if section_name == "Previous Consultations":
            previous_value = section_data.get(PREVIOUS_CONSULTATIONS_FIELD, "")
            if _has_text(previous_value) and _means_no_previous_consultation(
                previous_value
            ):
                total_fields += 1
                filled_fields += 1
                continue

            total_fields += 2
            if _has_text(previous_value):
                filled_fields += 1
            if _has_text(section_data.get(CURRENT_STATUS_FIELD)):
                filled_fields += 1
            continue

        for field_name, field_value in section_data.items():
            total_fields += 1
            if not _has_text(field_value):
                continue

            normalized = str(field_value).strip().lower()
            if section_name == "History & Diagnostics":
                filled_fields += 1
            elif section_name == "Referral" and field_name == "Source":
                if normalized not in {"yes", "no", "none", "n/a", "na", ""}:
                    filled_fields += 1
            elif normalized not in {"none", "no", "nothing", "n/a", "na", ""}:
                filled_fields += 1

    if total_fields == 0:
        return 0

    progress = (filled_fields / total_fields) * 100
    return min(100, max(0, round(progress, 1)))


def calculate_section_completion_status(form_data):
    """Return canonical section ordering and all-fields completion status."""
    if not form_data:
        # Preserve the established WebSocket/API response contract.
        return {
            "totalSteps": 0,
            "currentStep": 0,
            "progress": 0,
            "steps": [],
        }

    completed_sections = []
    incomplete_sections = []

    for section_name in SECTION_ORDER:
        if section_name not in form_data:
            continue

        section_data = form_data[section_name]
        if not isinstance(section_data, dict):
            continue

        filled_fields = 0
        if section_name == "Previous Consultations":
            previous_value = section_data.get(PREVIOUS_CONSULTATIONS_FIELD, "")
            status_value = section_data.get(CURRENT_STATUS_FIELD, "")

            if _has_text(status_value) and not _has_text(previous_value):
                total_fields = 2
            elif _has_text(previous_value) and _means_no_previous_consultation(
                previous_value
            ):
                total_fields = 1
                filled_fields = 1
            elif _has_text(previous_value):
                total_fields = 2
                filled_fields = int(_has_text(previous_value)) + int(
                    _has_text(status_value)
                )
            else:
                total_fields = 2
        else:
            total_fields = len(section_data)
            if total_fields == 0:
                incomplete_sections.append(
                    {
                        "name": section_name,
                        "isComplete": False,
                        "completionPercentage": 0,
                        "filledFields": 0,
                        "totalFields": 0,
                    }
                )
                continue

            placeholder_only = {"n/a", "na", "nil", "tbd", "unknown"}
            for field_value in section_data.values():
                if _has_text(field_value):
                    normalized = str(field_value).strip().lower()
                    if normalized not in placeholder_only:
                        filled_fields += 1

        completion_percentage = (
            (filled_fields / total_fields) * 100 if total_fields > 0 else 0
        )
        is_complete = filled_fields == total_fields and total_fields > 0
        section_info = {
            "name": section_name,
            "isComplete": is_complete,
            "completionPercentage": round(completion_percentage, 1),
            "filledFields": filled_fields,
            "totalFields": total_fields,
        }
        (completed_sections if is_complete else incomplete_sections).append(
            section_info
        )

    # The frontend maps step state by canonical index, so completion must not
    # reorder the response.
    order_map = {name: index for index, name in enumerate(SECTION_ORDER)}
    all_sections = sorted(
        completed_sections + incomplete_sections,
        key=lambda section: order_map.get(section["name"], 99),
    )
    total_sections = len(all_sections)
    completed_count = len(completed_sections)
    progress = (
        (completed_count / total_sections) * 100 if total_sections > 0 else 0
    )

    return {
        "totalSteps": total_sections,
        "currentStep": completed_count + 1 if incomplete_sections else total_sections,
        "completedSteps": completed_count,
        "progress": round(progress, 1),
        "steps": all_sections,
    }
