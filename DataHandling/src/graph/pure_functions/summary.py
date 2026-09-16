"""
LLM-powered summary generation and intent classification.
Extracted from HealthAgent — no self.* references.
"""
import json
import re
import time as _time
from typing import Callable, Optional

from app.observability.privacy import error_type
from src.graph.pure_functions.form_validation import extract_json_from_response


def generate_interview_summary(
    form: dict,
    history: list,
    llm_complete: Callable[[str], str],
) -> str:
    """
    Generate a patient-friendly narrative summary of the filled form.
    Extracted from HealthAgent.generate_summary().
    """
    form_json = json.dumps(form, indent=2, ensure_ascii=False)
    prompt = f"""You are an empathetic medical assistant. Convert the structured medical intake
form below into a short, patient-friendly summary.

FORM DATA (JSON):
{form_json}

Write the summary as if you are explaining it to the patient in simple, everyday language:
1. Start with 1-2 short paragraphs narrating the overall story (main problem, duration, onset,
   pain details, past consultations, health history, goals).
2. Add a Key points section with 3-7 bullet points.
3. Avoid technical language. Prefer plain phrases.
4. NEVER leave raw field names in brackets.
5. Do NOT use markdown bold (**) or backticks.
6. Use "- " for bullet points.
7. End with exactly: "Is this information correct, or would you like to make any changes?"
8. NEVER say "The form is complete" or any variation.
9. Do not add, assume, or infer any detail that is not explicitly present in FORM DATA.

CRITICAL — handle empty/negative field values honestly:
- Fields with values like "None", "No specific goals", "Not mentioned", "Not applicable", "No past surgeries", "No goals mentioned", "nothing" → summarize as ABSENCE, not presence.
  e.g. "You haven't mentioned any specific treatment goals" NOT "you have clear goals"
  e.g. "No past surgeries or health conditions were mentioned" NOT "your health history is clear"
- NEVER infer positive attributes from empty or negative field values.
- Only state something as a fact if the form field contains a real positive value.

Return ONLY the summary text."""

    try:
        _t0 = _time.perf_counter()
        summary = llm_complete(prompt).strip().replace("**", "")
        print(f"[timing] generate_summary_llm={(_time.perf_counter() - _t0) * 1000:.0f}ms")
        forbidden = ["the form is complete", "no further questions are needed",
                     "all fields are filled", "the interview is finished"]
        if any(p in summary.lower() for p in forbidden):
            summary = _fallback_summary(form)
        return summary or _fallback_summary(form)
    except Exception as e:
        print(f"[generate_interview_summary] Failed: {error_type(e)}")
        return _fallback_summary(form)


def _fallback_summary(form: dict) -> str:
    parts = ["Here's a summary of the information you've provided:\n"]
    for section, fields in form.items():
        if isinstance(fields, dict):
            non_empty = {k: v for k, v in fields.items() if v and str(v).strip()}
            if non_empty:
                parts.append(f"\n{section}:")
                for k, v in non_empty.items():
                    parts.append(f"- {k}: {v}")
    parts.append("\nIs this information correct, or would you like to make any changes?")
    return "\n".join(parts)


def classify_summary_response(
    user_input: str,
    summary_text: str,
    llm_complete: Callable[[str], str],
) -> dict:
    """
    Classify what the user wants to do after seeing the summary.
    Extracted from HealthAgent.classify_summary_response().
    Returns dict with keys: intent, correction_text, has_reports, wants_upload.
    """
    normalized = re.sub(r"[^a-z0-9\s]", " ", user_input.lower())
    normalized = " ".join(normalized.split())

    if re.search(r"\b(?:have uploaded|already uploaded|just uploaded|i uploaded|uploaded my|uploaded the|done uploading)\b", normalized):
        return {
            "intent": "has_reports",
            "wants_upload": False,
            "upload_claimed": True,
            "correction_text": None,
        }

    if normalized in {
        "yes", "correct", "looks good", "that is right", "thats right",
        "done", "ok done", "okay done", "all done", "finished",
        "yes done", "yes correct",
    }:
        return {
            "intent": "confirm",
            "correction_text": None,
            "has_reports": None,
            "wants_upload": None,
        }

    from src.graph.pure_functions.correction_consistency import (
        detect_pain_location_correction,
    )

    if detect_pain_location_correction(user_input, {}):
        return {
            "intent": "request_change",
            "correction_text": user_input.strip(),
            "has_reports": None,
            "wants_upload": None,
        }

    # A direct channel answer at summary time commonly follows a referral
    # question that was displayed just before a reconnect/race. Treat it as a
    # concrete field update, never as an ambiguous generic correction.
    from src.graph.pure_functions.referral import normalize_referral_source

    if normalize_referral_source(user_input):
        return {
            "intent": "request_change",
            "correction_text": user_input.strip(),
            "has_reports": None,
            "wants_upload": None,
        }
    vague_change_responses = {
        "no", "nope", "incorrect", "wrong", "not correct", "not right",
        "this is incorrect", "this is wrong", "this is not correct",
        "that is incorrect", "that is wrong", "that is not correct",
        "it is incorrect", "it is wrong", "it is not correct",
        "i want to make changes", "i want some changes", "i want a change",
        "want to make changes", "want some changes", "make changes",
        "i would like to make changes", "i would like to make some changes",
        "i would like to make any changes", "i need to change something",
        "change something",
    }
    if normalized in vague_change_responses:
        return {
            "intent": "request_change",
            "correction_text": None,
            "has_reports": None,
            "wants_upload": None,
        }

    prompt = f"""You are assisting with a medical intake interview. The patient has just been shown
a summary and asked: "Is this information correct, or would you like to make any changes?"

SUMMARY SHOWN TO THE PATIENT:
{summary_text}

The patient responded: "{user_input}"

Classify their intent. Choose EXACTLY ONE intent from below:

- "confirm": patient says YES — the summary is correct (e.g. "yes", "correct", "looks good", "that's right", "ok", "sure", "yep")
- "new_complaint": patient reveals an ADDITIONAL specific health issue, pain, or symptom that was NOT in the summary
  Use when: patient says "i have neck pain", "actually my knee hurts", "i forgot to mention my back", "i have a complaint", "wait i have pain" etc.
  This means they want to ADD new medical information, not correct existing data.
- "request_change": patient wants to CORRECT or UPDATE something already stated in the summary
  CRITICAL: "no" in response to "Is this correct?" → request_change (no, it is NOT correct)
  Also: "not right", "wrong", "incorrect", "change X to Y", "actually it was X"
  Set correction_text to null when the patient only says that something is wrong or asks to make
  changes without identifying the information and its corrected value. Otherwise set it to the
  patient's exact correction request.
- "has_reports": patient mentions having diagnostic reports (MRI, X-ray, scans)
- "no_reports": patient confirms they do NOT have reports
- "question": patient is asking a question about the process

Priority: explicit replacement/correction language such as "not X, it is Y", "change X to Y",
or "the left knee, not the right" → "request_change", even when it mentions a body part.
Use "new_complaint" only when the information is additional rather than a replacement.

Respond ONLY with compact JSON:
{{
  "intent": "confirm" | "new_complaint" | "request_change" | "has_reports" | "no_reports" | "question",
  "correction_text": string | null,
  "has_reports": true | false | null,
  "wants_upload": true | false | null
}}"""
    try:
        _t0 = _time.perf_counter()
        raw = llm_complete(prompt)
        print(f"[timing] classify_summary_llm={(_time.perf_counter() - _t0) * 1000:.0f}ms")
        json_str = extract_json_from_response(raw)
        if not json_str:
            return {}
        data = json.loads(json_str)
        if not isinstance(data, dict):
            return {}
        valid_intents = {"confirm", "new_complaint", "request_change", "has_reports", "no_reports", "question"}
        if data.get("intent") not in valid_intents:
            return {}
        return {
            "intent": data["intent"],
            "correction_text": data.get("correction_text"),
            "has_reports": data.get("has_reports"),
            "wants_upload": data.get("wants_upload"),
        }
    except Exception as e:
        print(f"[classify_summary_response] Failed: {error_type(e)}")
        return {}


def classify_reports_intent(
    user_input: str,
    llm_complete: Callable[[str], str],
    context_question: str = "",
) -> dict:
    """
    Determine whether the user has diagnostic reports and wants to upload them.
    context_question: the last question the agent asked (provides context for
    short replies like "yes", "yes i do").
    Returns dict with keys: has_reports, wants_upload.
    """
    import re as _re
    lowered = user_input.lower()
    report_words = ["report", "mri", "x-ray", "xray", "ct scan", "scan", "ultrasound",
                    "blood test", "lab", "x ray", "imaging", "film", "result"]
    _cq_str = str(context_question) if not isinstance(context_question, str) else context_question
    asking_about_reports = any(w in _cq_str.lower() for w in report_words) if _cq_str else False

    decline_upload_indicators = [
        "don't want to upload", "dont want to upload", "do not want to upload",
        "won't upload", "wont upload", "will not upload", "not upload here",
        "don't upload here", "dont upload here", "not comfortable uploading",
        "share directly with the doctor", "share directly with my doctor",
        "share directly with the clinician", "show directly to the doctor",
        "show it to the doctor", "bring it to the appointment",
        "bring them to the appointment", "share in person",
    ]
    accept_upload_indicators = [
        "want to upload", "will upload", "i'll upload", "ill upload",
        "can upload", "upload it here", "upload them here",
    ]
    deterministic_upload_preference = None
    if any(ind in lowered for ind in decline_upload_indicators):
        deterministic_upload_preference = False
    elif any(ind in lowered for ind in accept_upload_indicators):
        deterministic_upload_preference = True
    direct_share_with_clinician = bool(
        _re.search(
            r"\bshare\b.{0,60}\bdirectly\b.{0,40}\b(?:doctor|clinician)\b",
            lowered,
        )
    )

    # Use specific multi-word phrases only — short fragments like "i do" match
    # substrings of "i dont" and cause false positives.
    has_reports_indicators = [
        "i have reports", "i have an mri", "i have x-ray", "i have ct scan",
        "i have scans", "i got reports", "my reports", "my mri", "my x-ray",
        "reports are", "reports show", "mri shows", "x-ray shows", "i do have reports",
        "i do have scans", "i do have my reports",
    ]
    # Whole-word check for common short confirmations to avoid "i dont" → "i do"
    short_confirmation = " ".join(_re.sub(r"[^a-z0-9\s]", " ", lowered).split())
    confirms_reports_question = asking_about_reports and short_confirmation in {
        "yes", "yes i do", "yes i have", "yes i have one", "yes i have reports",
    }
    explicitly_has = (
        any(ind in lowered for ind in has_reports_indicators)
        or confirms_reports_question
        or bool(_re.search(
            r"\bi (?:do )?have (?:the |an? |some |my )?"
            r"(?:x[ -]?rays?|mri|ct scans?|scans?|reports?|imaging|films?)\b",
            lowered,
        ))
        or (
            asking_about_reports
            and (
                direct_share_with_clinician
                or any(
                    phrase in lowered
                    for phrase in (
                        "show it to the doctor",
                        "bring it to the appointment",
                        "bring them to the appointment",
                    )
                )
            )
        )
    )

    # Fast definite-negative check: if the user clearly has no reports, skip the LLM call
    # Use more specific phrases to avoid false positives like "we don't have to worry about it"
    no_reports_indicators = [
        "no reports", "no mri", "no x-ray", "no ct", "no scans", "no scan",
        "don't have any reports", "dont have any reports", "i don't have reports",
        "i dont have reports", "do not have any reports",
        "i don't have any", "i dont have any",
        "nothing", "none", "nope", "no i don't", "no i dont",
    ]
    explicitly_no = any(ind in lowered for ind in no_reports_indicators)

    if explicitly_has:
        return {
            "has_reports": True,
            "wants_upload": deterministic_upload_preference,
        }
    if explicitly_no and not context_question:
        # Only skip LLM when there's no question context (can't be a "yes" to a report question)
        return {"has_reports": False, "wants_upload": False}

    # If the user is clearly talking about symptoms/pain (no report words at all),
    # skip the LLM call — it would return null anyway.
    has_any_report_word = any(w in lowered for w in report_words)

    if not has_any_report_word and not asking_about_reports:
        # No report-related content in input or context → definitely null, skip LLM
        return {}

    context_line = f'\nThe assistant just asked: "{context_question}"\n' if context_question else ""

    prompt = f"""You are assisting with a medical intake interview.
{context_line}
The patient replied: "{user_input}"

Decide ONLY:
1. Whether they have diagnostic reports (MRI, X-ray, CT, scans, lab reports).
2. Whether they want to upload them now.

Respond ONLY with JSON:
{{
  "has_reports": true | false | null,
  "wants_upload": true | false | null
}}

RULES:
- has_reports = true ONLY if the patient explicitly mentions having MRI, X-ray, CT, scans, or lab reports.
- If the assistant asked about reports and the patient clearly confirms ("yes i do", "yes i have one") → has_reports = true.
- If the patient is talking about symptoms, pain, or general health (not reports) → has_reports = null.
- has_reports = false ONLY if they explicitly say they do NOT have any reports/scans.
- When in doubt → has_reports = null (never guess true from unrelated context).
- wants_upload = true only if they clearly want to upload now.
- wants_upload = false if they decline uploading or say they will share the report directly with their doctor/clinician.
"""
    try:
        raw = llm_complete(prompt)
        json_str = extract_json_from_response(raw)
        if not json_str:
            return {}
        data = json.loads(json_str)
        if not isinstance(data, dict):
            return {}
        has_reports = data.get("has_reports")
        wants_upload = data.get("wants_upload")
        if explicitly_has and has_reports is not False:
            has_reports = True
        if deterministic_upload_preference is not None:
            wants_upload = deterministic_upload_preference
        return {"has_reports": has_reports, "wants_upload": wants_upload}
    except Exception as e:
        print(f"[classify_reports_intent] Failed: {error_type(e)}")
        return {}
