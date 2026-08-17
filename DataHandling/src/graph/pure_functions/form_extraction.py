"""
LLM-powered form extraction, correction detection/application.
Each function takes explicit parameters (form, llm_fn, prompts) — no self.* references.
"""
import json
import copy
import time as _time
from typing import Callable, Optional

from src.graph.pure_functions.form_validation import extract_json_from_response
from src.prompts import TEMPLATE_PROMPT, CORRECTION_DETECTION_PROMPT, CORRECTION_APPLY_PROMPT


def _llm_check_no_prior_consultations(user_input: str, llm_complete: Callable[[str], str] = None) -> bool:
    """
    Fast keyword check: returns True if the user clearly indicated NO prior consultations.
    No LLM call needed — saves ~1-2s per turn in the Previous Consultations section.
    """
    lowered = user_input.lower().strip()
    no_consult_signals = [
        "have not seen", "haven't seen", "not seen anyone", "not seen any",
        "have not consulted", "haven't consulted", "have not visited", "haven't visited",
        "did not visit", "didn't visit", "never consulted", "never seen",
        "no doctor", "no hospital", "no one", "no physiotherapist",
        "i have not", "i haven't", "nope", "nah", "no not",
        "none", "nothing", "not yet", "haven't gone",
    ]
    consult_signals = [
        "doctor", "physiotherapist", "hospital", "clinic", "consulted",
        "visited", "went to", "prescribed", "diagnosed", "treatment",
    ]
    has_no = any(s in lowered for s in no_consult_signals)
    has_yes = any(s in lowered for s in consult_signals)
    # Only return True if the user clearly negated AND didn't also mention a visit
    return has_no and not has_yes


def extract_form_data_from_text(
    user_input: str,
    form: dict,
    prompt_template: str,
    llm_complete: Callable[[str], str],
    current_section: str = "",
    last_question: str = "",
) -> dict:
    """
    Parse free-text user response and return an updated form dict.
    Extracted from HealthAgent.formatter(). Pure — returns new form, does not mutate input.
    """
    updated_form = copy.deepcopy(form)
    example_output = '{"Patient Information": {"Age": "35", "Gender": "male"}}'

    # Include the last agent question when available — critical for short/denial
    # responses like "nothing", "no", "none" where the LLM must know WHAT was asked
    # to correctly fill (or negate) the relevant fields.
    if last_question:
        patient_input_block = (
            f'Question that was asked:\n"{last_question}"\n\n'
            f'Patient\'s response:\n"{user_input}"\n\n{prompt_template}'
        )
    else:
        patient_input_block = f'Patient\'s response:\n"{user_input}"\n\n{prompt_template}'

    enhanced_prompt = TEMPLATE_PROMPT.format(
        patient_input_block, json.dumps(updated_form, indent=2), example_output
    )

    try:
        _t0 = _time.perf_counter()
        llm_response = llm_complete(enhanced_prompt)
        _extract_ms = (_time.perf_counter() - _t0) * 1000
        print(f"[timing] form_extraction_llm={_extract_ms:.0f}ms section={current_section!r}")
        print("LLM FORMATTING RESPONSE:\n", llm_response)
        json_str = extract_json_from_response(llm_response)

        if not json_str:
            fallback = (
                f"Convert this patient response to a JSON object matching the form structure:\n\n"
                f"PATIENT RESPONSE: {user_input}\n"
                f"FORM STRUCTURE: {json.dumps(updated_form, indent=2)}\n\n"
                f"Respond ONLY with valid JSON."
            )
            _t0 = _time.perf_counter()
            json_str = extract_json_from_response(llm_complete(fallback))
            print(f"[timing] form_extraction_llm_fallback={(_time.perf_counter() - _t0) * 1000:.0f}ms")
            if not json_str:
                return updated_form

        formatted_data = json.loads(json_str)

        # Apply extracted data section by section
        for section, fields in updated_form.items():
            if section in formatted_data:
                for key in fields:
                    new_val = formatted_data.get(section, {}).get(key, "")
                    if new_val:
                        updated_form[section][key] = new_val

        # ── POST-PROCESSING ──────────────────────────────────────────────────
        updated_form = _post_process_previous_consultations(
            updated_form, formatted_data, user_input, current_section, llm_complete
        )
        updated_form = _post_process_history_diagnostics(
            updated_form, formatted_data, user_input
        )
        updated_form = _post_process_referral(updated_form)
        # Always run — "How did you hear about us?" is asked in Round 0 while
        # current_section is still on another section, so we can't gate on "Referral".
        # The function guards itself: if Source is already filled it returns immediately.
        updated_form = _post_process_referral_from_input(updated_form, user_input)

        print("Updated form sections after formatting:")
        for section, fields in updated_form.items():
            non_empty = {k: v for k, v in fields.items() if v}
            if non_empty:
                print(f"  {section}: {non_empty}")

        return updated_form

    except Exception as e:
        print(f"Error in extract_form_data_from_text: {e}")
        return updated_form


def _post_process_previous_consultations(
    form: dict, formatted_data: dict, user_input: str,
    current_section: str = "", llm_complete: Optional[Callable[[str], str]] = None
) -> dict:
    prev_field = "Previous Diagnosis or Advice and Prescribed Treatment Taken"
    status_field = "Current Status of Issue (Improved, Same, Worse)"
    pc = form.get("Previous Consultations", {})
    prev_val = pc.get(prev_field, "").strip()
    status_val = pc.get(status_field, "").strip()

    # Also clear a falsely-extracted value: when the LLM extracts "physio" from
    # "no i have not seen any physio", the negation is missed. Override if the
    # extracted value is just a provider-type keyword without real content.
    _false_positive_keywords = {"physio", "physiotherapist", "doctor", "hospital", "clinic",
                                "orthopedic", "surgeon", "specialist", "consultant"}
    if prev_val and prev_val.lower().strip() in _false_positive_keywords:
        prev_val = ""
        form["Previous Consultations"][prev_field] = ""

    # If the field is empty (or was just cleared) and we're on the Previous Consultations
    # section, use the keyword check to determine whether the user said no prior consultations.
    if not prev_val and current_section == "Previous Consultations" and llm_complete:
        if _llm_check_no_prior_consultations(user_input, llm_complete):
            form["Previous Consultations"][prev_field] = (
                "None — patient has not consulted any doctor, physiotherapist, or hospital for this issue"
            )
            form["Previous Consultations"][status_field] = (
                "Not applicable (no previous consultations for this issue)"
            )
            return form

    if "Previous Consultations" not in formatted_data:
        return form

    user_lower = user_input.strip().lower()
    consult_kws = ["doctor", "physiotherapist", "hospital", "consulted", "visited",
                   "diagnosis", "prescribed", "treatment", "medicine", "injection", "physio", "clinic"]
    user_has_consult = any(kw in user_lower for kw in consult_kws)
    prev_has_consult = any(kw in prev_val.lower() for kw in consult_kws)

    if status_val and not prev_val and not prev_has_consult and not user_has_consult:
        form["Previous Consultations"][status_field] = ""

    if prev_val:
        no_consult_inds = ["no consultation", "never consulted", "have not consulted",
                           "haven't consulted", "did not visit", "didn't visit",
                           "no doctor", "no hospital", "none", "nothing",
                           "no previous consultations", "not applicable"]
        if any(ind in prev_val.lower() for ind in no_consult_inds):
            form["Previous Consultations"][status_field] = (
                "Not applicable (no previous consultations for this issue)"
            )
    return form


def _post_process_history_diagnostics(form: dict, formatted_data: dict, user_input: str) -> dict:
    if "History & Diagnostics" not in formatted_data:
        return form
    hd = form.get("History & Diagnostics", {})
    user_lower = user_input.lower()

    # Reports field
    reports_val = str(hd.get("Reports", "") or "").strip()
    no_reports_inds = ["i dont have", "i don't have", "dont have", "don't have",
                       "no reports", "no mri", "no ct", "no x-ray", "no x ray",
                       "no scan", "no scans", "no imaging"]
    if not reports_val and any(ind in user_lower for ind in no_reports_inds):
        form["History & Diagnostics"]["Reports"] = (
            "No relevant diagnostic reports available for this issue"
        )
    else:
        placeholder_vals = {"none mentioned", "none reported", "not mentioned", "no details mentioned"}
        if reports_val.lower() in placeholder_vals and not any(ind in user_lower for ind in no_reports_inds):
            form["History & Diagnostics"]["Reports"] = ""

    # Systemic Illness field
    sys_val = str(hd.get("Systemic Illness and Surgical History", "") or "").strip()
    placeholder_hist = {"none mentioned", "not mentioned", "none reported"}
    explicit_no_hist = ["no major health issues", "no health issues", "no medical conditions",
                        "no medical history", "no past surgeries", "no previous surgeries",
                        "no surgeries", "haven't had any surgeries", "no chronic illness"]
    if sys_val and sys_val.lower() in placeholder_hist and not any(ind in user_lower for ind in explicit_no_hist):
        form["History & Diagnostics"]["Systemic Illness and Surgical History"] = ""

    return form


def _post_process_referral_from_input(form: dict, user_input: str) -> dict:
    """
    When the extraction LLM fails on short referral answers ('google', 'youtube',
    'yes my friend'), directly map the user_input to Source if it's still empty.
    Only runs when current_section == 'Referral'.
    """
    if "Referral" not in form:
        return form
    source = str(form["Referral"].get("Source", "") or "").strip()
    if source:
        return form  # Already filled by extraction — nothing to do

    user_lower = user_input.lower().strip()

    # Known referral/discovery channels — extract the CHANNEL NAME, not the raw sentence
    channel_map = {
        "youtube": "YouTube", "instagram": "Instagram", "facebook": "Facebook",
        "google": "Google", "twitter": "Twitter", "whatsapp": "WhatsApp",
        "social media": "Social media", "online": "Found online",
        "website": "Stance Health website", "ad ": "Online ad", "advert": "Online ad",
        "friend": "Friend/word of mouth", "family": "Family referral",
        "relative": "Family referral", "colleague": "Colleague referral",
        "word of mouth": "Word of mouth", "newspaper": "Newspaper",
        "doctor referred": "Doctor referral", "referred by": "Referral",
        "podcast": "Podcast", "blog": "Blog/article",
    }
    matched_channel = None
    for kw, label in channel_map.items():
        if kw in user_lower:
            matched_channel = label
            break

    if matched_channel:
        form["Referral"]["Source"] = matched_channel
        print(f"[referral_fill] Source extracted as channel: '{matched_channel}'")
    elif user_lower not in {"no", "nope", "nah", "none", "nothing", "n/a"}:
        # Any other non-negative answer: store as-is and let validate_section decide
        form["Referral"]["Source"] = user_input.strip()
        print(f"[referral_fill] Source filled from input (fallback): '{user_input.strip()}'")
    return form


def _post_process_referral(form: dict) -> dict:
    if "Referral" not in form:
        return form
    source = str(form["Referral"].get("Source", "") or "").strip()
    if not source:
        return form
    lower = source.lower()
    invalid = {"yes", "no", "none", "n/a", "na", "nil", "nothing", "0"}
    clinician_kws = ["doctor", "dr ", "dr.", "orthopedic", "orthopaedic", "ortho",
                     "physio", "physiotherapist", "surgeon", "consultant", "specialist",
                     "hospital", "clinic", "center", "centre"]
    referral_kws = ["friend", "family", "relative", "colleague", "google", "instagram",
                    "facebook", "youtube", "social media", "online", "website",
                    "ad ", "advert", "advertisement", "referral"]
    should_clear = lower in invalid
    if not should_clear:
        has_clinician = any(kw in lower for kw in clinician_kws)
        has_channel = any(kw in lower for kw in referral_kws)
        if has_clinician and not has_channel:
            should_clear = True
    if should_clear:
        form["Referral"]["Source"] = ""
    return form


def detect_form_correction(
    user_input: str,
    form: dict,
    is_summary_mode: bool,
    llm_complete: Callable[[str], str],
) -> Optional[dict]:
    """
    Ask the LLM whether the user is correcting a previously filled field.
    Returns correction dict or None.
    Extracted from HealthAgent.detect_correction().
    """
    try:
        if is_summary_mode:
            prompt = f"""The user is reviewing their medical form summary and wants to make changes.

User message: "{user_input}"

Current form data:
{json.dumps(form, indent=2)}

Analyze their message to identify which field they want to change and the new value.

Respond with a JSON object:
{{
    "is_correction": true/false,
    "confidence": "high"/"medium"/"low",
    "old_value": "current value from form (if identifiable)",
    "new_value": "what they want to change it to",
    "field_name": "exact field name from form (if identifiable)",
    "section_name": "exact section name from form (if identifiable)",
    "needs_clarification": true/false,
    "clarification_question": "question to ask if ambiguous"
}}"""
        else:
            prompt = CORRECTION_DETECTION_PROMPT.format(user_input, json.dumps(form, indent=2))

        _t0 = _time.perf_counter()
        raw = llm_complete(prompt)
        print(f"[timing] correction_detect_llm={(_time.perf_counter() - _t0) * 1000:.0f}ms")
        json_str = extract_json_from_response(raw)
        if not json_str:
            return None
        data = json.loads(json_str)
        if data.get("is_correction", False):
            return data
        return None
    except Exception as e:
        print(f"[detect_form_correction] Error: {e}")
        return None


def apply_form_correction(
    correction_data: dict,
    form: dict,
    llm_complete: Callable[[str], str],
) -> tuple:
    """
    Apply a correction to the form.
    Returns (updated_form, success: bool, message: str).
    Extracted from HealthAgent.apply_correction().
    """
    updated_form = copy.deepcopy(form)
    field_name = correction_data.get("field_name", "")
    section_name = correction_data.get("section_name", "")
    old_value = correction_data.get("old_value", "")
    new_value = correction_data.get("new_value", "")

    if correction_data.get("needs_clarification", False):
        q = correction_data.get("clarification_question", "")
        return updated_form, False, q or "Could you please be more specific about what you'd like to change?"

    if not field_name or not section_name:
        return updated_form, False, "I'm not sure which field to update. Could you specify what you'd like to change?"

    if section_name not in updated_form or field_name not in updated_form[section_name]:
        return updated_form, False, f"I couldn't find '{field_name}' in '{section_name}'. Could you clarify?"

    try:
        prompt = CORRECTION_APPLY_PROMPT.format(
            field_name, section_name, old_value, new_value,
            json.dumps(updated_form, indent=2),
            section_name, field_name, new_value,
        )
        raw = llm_complete(prompt)
        json_str = extract_json_from_response(raw)
        if json_str:
            new_form = json.loads(json_str)
            if section_name in new_form and field_name in new_form[section_name]:
                if new_form[section_name][field_name] != new_value:
                    new_form[section_name][field_name] = new_value
                updated_form = new_form
            else:
                updated_form[section_name][field_name] = new_value
        else:
            updated_form[section_name][field_name] = new_value
    except Exception:
        updated_form[section_name][field_name] = new_value

    msg = f"I've updated '{field_name}' from '{old_value or '(empty)'}' to '{new_value}'."
    return updated_form, True, msg
