#this is not used anywhere in the present code, written only to the legacy agents history list
WELCOME_PROMPT = """
Hey,
Welcome to Stance Health, India's first technology-enabled MSK health platform.
I'll need about 5 minutes to understand your clinical history so I can share it with your primary clinician and make your discussion with Stance Health more productive.
Please note that this information is critical to proceed with your session.
"""

# The FRM-01 interview opens with one simple question.  The patient's reply is
# then processed by the normal extraction/question-selection flow, so we do not
# front-load a long batch of clinical questions before they have said why they
# are visiting.
OPENING_GREETING = "Hey, I'm glad you're here!"
OPENING_PURPOSE = (
    "This is your clinical intake form. Your answers help your clinician prepare for your visit."
)
OPENING_QUESTION = "What is the main problem bothering you today?"
INITIAL_INTAKE_PROMPT = f"{OPENING_GREETING} {OPENING_PURPOSE} {OPENING_QUESTION}"

READY_TO_START_PROMPT = """
Are you ready to get started now?
If not, you can exit for now, but please come back and complete this before your session.
"""

INTERVIEW_DECLINED_PROMPT = """
No problem. You can return to this interview whenever you're ready.
Your health information is important, and I'm here to assist you when it's convenient for you.
"""

DEFAULT_ANSWER = """
I understand. Let's continue with the next question to gather more information about your health.
"""

# ── Form definition — loaded from data/forms/FRM-01.md ───────────────────────
# Edit questions, sections, and visit rules in that file; no Python changes needed.

from src.forms.loader import load_form as _load_form

# Backward-compat exports (imported by functionalities.py, server.py, state.py)
PREDEFINED_QUESTIONS = _load_form("FRM-01").predefined_as_tuples()
MEDICAL_FORM_TEMPLATE = _load_form("FRM-01").empty_form()
GENERAL_VISIT_Q1 = _load_form("FRM-01").general_visit_q1


def get_medical_form_template() -> dict:
    """Return a fresh blank form dict. Always call this — never mutate the module-level constant."""
    return _load_form("FRM-01").empty_form()

SYSTEM_PROMPT = """You are an intelligent medical assistant tasked with gathering comprehensive information about a patient's medical history, pain assessment, and treatment goals.
Your goal is to ask relevant questions, record responses accurately, and ensure all critical details are collected in a structured format.
Be empathetic and professional at all times. Keep your responses focused on the medical interview.
If the user says something wildly irrelevant or uses profanity, politely guide them back to the interview process.
Always be extremely professional and considerate.

IMPORTANT: You must also detect when users want to correct previously provided information. Listen for phrases like:
- "I made a mistake" / "Actually, it was..." / "No, I meant..." / "Sorry, I said X but it's actually Y"
When you detect a correction, identify what needs to be changed and update the form accordingly.
"""

QUERY_TASK_PROMPT = """
Only reply with either the modified or non-modified question, nothing extra and definitely not both.
Make sure to phrase the question in a way that feels natural and conversational, while still collecting
the necessary medical information.
"""

TEMPLATE_PROMPT = """
{}

Extract the information from the user's response and structure it according to the following form template:

{}

Example output format:
{}
"""

# ──────────────────────────────────────────────────────────────
# PROM EXTRACTOR — used for FRM-02 / tagged-questions sessions
# Extracts patient-reported outcome measure responses from conversation
# ──────────────────────────────────────────────────────────────
PROM_EXTRACTOR_PROMPT = """You are a clinical data analyst extracting patient-reported outcome measure (PROM) responses from a conversation.

Read the entire conversation below and fill each PROM field with the patient's answer.

## Full Conversation
{conversation}

## Current PROM Form (partially filled)
{current_form}

## Instructions

Each section is a validated clinical outcome scale:
- **Oxford Hip Score (OHS)** / **Oxford Shoulder Score (OSS)**: Joint function over the past 4 weeks. Typical responses: none / very mild / mild / moderate / severe — or — never / rarely / sometimes / very often / always.
- **PHQ-9 (Depression)**: Frequency of symptoms over the past 2 weeks: not at all / several days / more than half the days / nearly every day.
- **GAD-7 (Anxiety)**: Same frequency scale as PHQ-9.
- **Numeric Pain Scale (NPS)**: Patient rates pain 0–10.
- **RMDQ (Back Disability)**: Yes/No — whether back pain limits each activity.
- **Other scales**: Extract the patient's response as stated.

Rules:
- Store the patient's actual response verbatim or as a concise paraphrase (e.g. "Moderate", "Sometimes", "7/10", "Yes — I avoid stairs").
- If the patient answered a question conversationally, map it to the right field.
- Leave as empty string "" if genuinely not answered yet.
- Never fabricate answers. Never copy from already-filled fields.
- Return ONLY valid JSON matching the exact structure below. No explanation, no markdown.

Fill this structure:
{form_structure}"""

# ──────────────────────────────────────────────────────────────
# ENHANCED EXTRACTION PROMPTS (kept for fallback)
# ──────────────────────────────────────────────────────────────
FORMAT_PROMPT = """
You are an expert medical data extractor. Extract EVERY piece of information the patient said into the exact JSON structure below.

CRITICAL RULES:
- ALWAYS correct spelling mistakes before storing — store the clean, correct English version:
   → "stright" → "straight", "recieve" → "receive", "swollén" → "swollen"
   → "brusing" → "bruising", "ligment" → "ligament", "phisio" → "physio"
   → "fourty" → "forty", "yrs" → "years", "mins" → "minutes"
   → Any obvious typo or phonetic spelling → correct it silently
- Be extremely forgiving with transcription/speech errors:
   → "cost" → "cause", "eight" or "ate" → "8", "ten" → "10"
   → "since two months", "for 3 weeks", "last year" → extract duration
   → "after fall", "twisted ankle", "lifting weight" → Mechanism of Injury or Cause
   → Any number said near "pain" or "hurts" → assume it's pain scale (0–10)
   → "stright" or "striaght" → "straight"
- Store CLEAN, READABLE text — not raw misspelled patient input
- If patient mentions MRI, X-ray, scan, report even once → put in "Reports"
- Never leave a field blank if any clue exists — use clinical judgment
- Combine all answers from the entire conversation
- Most recent or most specific answer overrides earlier ones

CRITICAL — NEGATIVE / DENIAL ANSWERS must be stored, not ignored:
- "i dont have any past surgeries" → "Systemic Illness and Surgical History": "No past surgeries"
- "no fractures" → "Systemic Illness and Surgical History": "No fractures"
- "i dont exercise" / "i dont do any exercise" → "Current Lifestyle": (add to existing or set) "No regular exercise"
- "I work as a teacher" → "Current Lifestyle": (add to existing or set) "Work: teacher"
- "no health conditions" / "i am healthy" → "Systemic Illness and Surgical History": "No known systemic illness"
- "no reports" / "i dont have any scans" → "Reports": "None"
- "no goals" / "nothing" (in response to a goals question) → fill the goals field with "No specific goals mentioned"
- Any clear denial or negation about a supported topic → store the negation as the field value, NEVER leave it blank
- For Current Lifestyle, support only work/occupation and activity/exercise. Do not independently extract unsupported lifestyle details.

CRITICAL: "Previous Consultations" section:
- ONLY fill "Previous Consultations" fields if the patient EXPLICITLY mentions:
  - Visiting a doctor, physiotherapist, or hospital for THIS problem
  - Receiving a diagnosis, prescription, or treatment advice
  - Previous consultations or medical visits
- DO NOT extract general status descriptions (like "getting worse", "improving", "staying the same") into "Previous Consultations" unless they explicitly relate to previous treatment
- If patient says "the pain is getting worse" or "it's been getting worse", this is about the CURRENT complaint, NOT previous consultations
- "Current Status of Issue (Improved, Same, Worse)" in "Previous Consultations" should ONLY be filled if the patient mentions how previous treatment affected their condition
- If no previous consultations are mentioned, leave BOTH "Previous Consultations" fields empty

- Output ONLY valid JSON. No extra text. No explanations.

Fill this exact structure:
{}
"""

FINAL_FORM_FILL_PROMPT = """
Given the complete conversation below and the current form, go through EVERY word the patient said again.

Patients often mention critical details casually:
→ "I have diabetes", "my sugar is high", "BP medicine", "old knee surgery", "MRI done last month", etc.

Correct ALL transcription errors using medical context (e.g., "physio" = physiotherapy, "injection" = steroid injection, etc.)

Update every single field possible. Be aggressive in extracting — your job is to fill as much as possible.

Conversation:
{}

Current form (for reference only):
{}

Return ONLY the final complete updated JSON matching this exact structure:
{}
"""

# ──────────────────────────────────────────────────────────────
# CORRECTION DETECTION AND HANDLING PROMPTS
# ──────────────────────────────────────────────────────────────

CORRECTION_DETECTION_PROMPT = """
Analyze the user's message to determine if they are trying to correct previously provided information.

CRITICAL: Only detect a correction if the user EXPLICITLY uses correction language. Do NOT treat answers to new questions as corrections, even if they mention similar concepts.

Look for EXPLICIT correction indicators like:
- "I made a mistake"
- "Actually, it was..."
- "No, I meant..."
- "Let me correct that"
- "Sorry, I said X but it's actually Y"
- "It wasn't X, it was Y"
- "I misspoke"
- "To clarify"
- "It was not X, it was Y"
- "Change X to Y"
- "Sorry, not X, it's Y"
- "I meant X, not Y"

IMPORTANT: If the user is just answering a question (even if it mentions diagnosis, pain, etc.), this is NOT a correction.
For example:
- "I am not sure what the diagnosis is" → NOT a correction, just an answer
- "I did not consult any doctor" → NOT a correction, just an answer
- "Sorry, not one and a half days, one day" → IS a correction (uses "sorry, not X, Y")

IMPORTANT MEDICAL CORRECTION EXAMPLES (only when explicit correction language is used):
- "Sorry, not ACL tear, it's actually an ACL pull" → Primary Complaint correction
- "Actually, it wasn't a fracture, it was a sprain" → Primary Complaint correction
- "I said pain level 8, but it's actually 6" → Severity correction
- "Sorry, not left knee, it's my right knee" → Location correction
- "Not 2 weeks, it's been 2 months" → Duration correction

User message: "{}"

Current form data:
{}

ANALYSIS INSTRUCTIONS:
1. FIRST: Check if the user uses EXPLICIT correction language (see list above)
2. If NO explicit correction language → set is_correction to false
3. If YES explicit correction language → identify what they're correcting:
   a. Look at the current form data to find what value they might be correcting
   b. Match the correction to the specific field and section
   c. Use medical knowledge to identify which field is most relevant
4. Only set is_correction to true if:
   - User uses explicit correction language AND
   - You can identify a specific field they're correcting with HIGH confidence
5. If you're uncertain which specific field to update, set needs_clarification to true

Respond with a JSON object in this exact format:
{{
    "is_correction": true/false,
    "confidence": "high"/"medium"/"low",
    "old_value": "what they said before (if identifiable)",
    "new_value": "what they want to change it to",
    "field_name": "exact field name from form (if identifiable)",
    "section_name": "exact section name from form (if identifiable)",
    "needs_clarification": true/false,
    "clarification_question": "question to ask if ambiguous (or empty string)"
}}

If is_correction is false, all other fields should be empty strings or false.
If you cannot identify which field to update with HIGH confidence, set needs_clarification to true and provide a clarification_question.
"""

CORRECTION_APPLY_PROMPT = """
The user wants to correct a field in the form.

CRITICAL INSTRUCTIONS:
- You MUST update ONLY this exact field: "{}" in section "{}"
- Change the value from "{}" to "{}"
- DO NOT modify any other fields, even if they contain similar values
- DO NOT search for the old value in other fields
- ONLY update the field specified above

Current form:
{}

STEPS TO FOLLOW:
1. Locate the section: "{}"
2. Find the field: "{}"
3. Change its value to: "{}"
4. Leave ALL other fields unchanged

Return the COMPLETE updated form as valid JSON.
Return ONLY the JSON, no explanations.
"""

CORRECTION_CONFIRMATION_PROMPT = """
I've updated the form based on your correction:

Changed "{}" from "{}" to "{}"

Is this correct? Please say yes to confirm, or let me know if you need any other changes.
"""
