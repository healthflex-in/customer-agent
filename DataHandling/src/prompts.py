WELCOME_PROMPT = """
Hey,
Welcome to Stance Health, India's first technology-enabled MSK health platform.
I'll need about 5 minutes to understand your clinical history so I can share it with your primary clinician and make your discussion with Stance Health more productive.
Please note that this information is critical to proceed with your session.
"""

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

SYSTEM_PROMPT = """
You are an intelligent medical assistant tasked with gathering comprehensive information about a patient's medical history, pain assessment, and treatment goals.
Your goal is to ask relevant questions, record responses accurately, and ensure all critical details are collected in a structured format.
Be empathetic and professional at all times. Keep your responses focused on the medical interview.
If the user says something wildly irrelevant or uses profanity, politely guide them back to the interview process.
Always be extremely professional and considerate.

IMPORTANT: You must also detect when users want to correct previously provided information. Listen for phrases like:
- "I made a mistake"
- "Actually, it was..."
- "No, I meant..."
- "Let me correct that"
- "Sorry, I said X but it's actually Y"
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
# HIGH-YIELD 3 QUESTIONS → FILLS 95%+ OF FORM ACCURATELY
# ──────────────────────────────────────────────────────────────
PREDEFINED_QUESTIONS = [
    (
        "Initial Comprehensive Interview",
        "To help me understand your situation, I'd like to ask you a few questions. Please share as much detail as you can:\n\n"
        "• What exactly is bothering you right now (pain, stiffness, weakness, swelling, etc.), where do you feel it, and how severe is it on a scale of 0 to 10?\n"
        "• How long have you been experiencing this, and how did it start (suddenly after an injury or gradually)?\n"
        "• What makes it worse or better (any movement, position, time of day, rest, etc.)?\n"
        "• Have you consulted any doctor, physiotherapist, or hospital for this before? If yes, what did they say and what treatment was prescribed?\n"
        "• Do you have any other health conditions, past surgeries, or do you smoke or drink? Also, do you have any MRI, X-ray, CT scan, or blood reports related to this issue?\n"
        "• What are your goals with treatment - what would you like to be able to do in the next 3 months and long-term? Also, how did you come to know about us?",
    ),
    (
        "Past Treatment & History/Diagnostics",
        "Have you consulted any doctor, physiotherapist, or hospital for this same problem before?\n"
        "If yes:\n"
        "• What did they diagnose or say was the issue?\n"
        "• What treatment, medicines, injections, or exercises were prescribed?\n"
        "• Did it help at all, and what is the current status of your issue (improved, same, or worse)?\n\n"
        "Now about your overall health, lifestyle, and any reports:\n"
        "• Do you have any other health conditions like diabetes, high blood pressure, thyroid, heart issues, or any past surgeries or fractures?\n"
        "• Do you smoke or drink alcohol regularly?\n"
        "• Do you exercise or have a physically active/demanding job?\n"
        "• Do you have any MRI, X-ray, CT scan, or blood reports related to this issue?",
    ),
    (
        "Goals & Referral",
        "What are your goals with treatment?\n"
        "• In the next 3 months, what would you like to be able to do?\n"
        "• Long-term, what is your ultimate goal (e.g., walk without pain, play sports, climb stairs easily, etc.)?\n"
        "• What are your specific expectations from this treatment?\n\n"
        "How did you come to know about us? (Friend/family referral, Google, Instagram, Facebook, YouTube, etc.)",
    ),
]

# ──────────────────────────────────────────────────────────────
# EXACT SAME TEMPLATE STRUCTURE (unchanged keys)
# ──────────────────────────────────────────────────────────────
def get_medical_form_template():
    """
    Return a fresh copy of the medical form template.
    This function ensures we always get a clean template that hasn't been mutated.
    CRITICAL: Always use this function instead of accessing MEDICAL_FORM_TEMPLATE directly
    to prevent data leakage between users.
    """
    import copy
    return copy.deepcopy(_MEDICAL_FORM_TEMPLATE_BASE)

# Base template (private - should not be accessed directly)
_MEDICAL_FORM_TEMPLATE_BASE = {
    "Present Complaint": {
        "Primary Complaint": "",
        "Duration of the Issue": "",
        "Onset (Gradual or Sudden)": "",
        "Mechanism of Injury (If Any)": "",
    },
    "Previous Consultations": {
        "Previous Diagnosis or Advice and Prescribed Treatment Taken": "",
        "Current Status of Issue (Improved, Same, Worse)": "",
    },
    "Pain Assessment": {
        "Primary Location of Pain": "",
        "Severity (1-10)": "",
        "Aggravating Factors": "",
        "Relieving Factors": "",
    },
    "History & Diagnostics": {
        "Systemic Illness and Surgical History": "",
        "Current Lifestyle": "",
        "Reports": "",
    },
    "Treatment Goals": {
        "Short-Term Goals (within 3 months)": "",
        "Long-Term Goals (after 3 months)": "",
        "Specific Expectations from Treatment": "",
    },
    "Referral": {
        "Source": "",
    },
}

# Public constant for backward compatibility (but use get_medical_form_template() instead)
MEDICAL_FORM_TEMPLATE = _MEDICAL_FORM_TEMPLATE_BASE

# ──────────────────────────────────────────────────────────────
# ENHANCED EXTRACTION PROMPTS (critical for accuracy with speech-to-text)
# ──────────────────────────────────────────────────────────────
FORMAT_PROMPT = """
You are an expert medical data extractor. Extract EVERY piece of information the patient said into the exact JSON structure below.

CRITICAL RULES:
- Be extremely forgiving with transcription errors:
   → "cost" → "cause", "ligament" → "ligament", "back pain" → "backbone", "eight" or "ate" → "8", "ten" → "10"
   → "since two months", "for 3 weeks", "last year" → extract duration
   → "after fall", "twisted ankle", "lifting weight" → Mechanism of Injury
   → Any number said near "pain" or "hurts" → assume it's pain scale (0–10)
- If patient mentions MRI, X-ray, scan, report even once → put in "Reports"
- Never leave a field blank if any clue exists — use clinical judgment
- Combine all answers from the entire conversation
- Most recent or most specific answer overrides earlier ones

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
→ "I have diabetes", "my sugar is high", "BP medicine", "old knee surgery", "MRI done last month", "friend referred", etc.

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