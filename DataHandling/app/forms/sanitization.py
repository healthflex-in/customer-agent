"""Narrow form-data sanitizers shared by persistence boundaries."""

import copy


def strip_deprecated_referral_section(form_data: dict) -> dict:
    """Copy a customer form without the retired top-level Referral section.

    The exact-key removal leaves unrelated clinical prose and fields named
    ``Source`` intact.
    """
    clean_form = dict(form_data) if isinstance(form_data, dict) else {}
    clean_form.pop("Referral", None)
    return clean_form


def preserve_historical_referral_section(
    form_data: dict,
    existing_form_data: object,
) -> dict:
    """Preserve Referral exactly when it already exists in a stored document.

    New forms never gain the retired section. Existing Mongo records retain it
    during unrelated updates without exposing it to the active interview flow.
    """
    preserved_form = dict(form_data) if isinstance(form_data, dict) else {}
    if isinstance(existing_form_data, dict) and "Referral" in existing_form_data:
        preserved_form["Referral"] = copy.deepcopy(existing_form_data["Referral"])
    return preserved_form
