"""Deterministic required-field questions for the FRM-01 intake."""
REQUIRED_FIELD_QUESTIONS = {
    "Primary Complaint": "What is the main problem bothering you?",
    "Duration of the Issue": "How long have you been experiencing this issue?",
    "Onset (Gradual or Sudden)": "Did it start suddenly or gradually?",
    "Mechanism of Injury or Cause": "What caused it or what was happening when it started?",
    "Previous Diagnosis or Advice and Prescribed Treatment Taken": (
        "Have you seen a doctor, physiotherapist, or hospital for this problem before? "
        "If yes, what diagnosis, advice, or treatment did they give?"
    ),
    "Current Status of Issue (Improved, Same, Worse)": (
        "After that previous consultation or treatment, is the condition improved, "
        "the same, or worse?"
    ),
    "Primary Location of Pain": "Where exactly do you feel the pain or problem?",
    "Severity (1-10)": "On a scale of 0 to 10, how severe is it right now?",
    "Aggravating Factors": "What activities, movements, or situations make it worse?",
    "Relieving Factors": "What makes it feel better or gives relief?",
    "Systemic Illness and Surgical History": (
        "Do you have any other health conditions, past surgeries, or fractures?"
    ),
    "Current Lifestyle": (
        "What is your usual activity or exercise routine?"
    ),
    "Reports": "Do you have any related X-rays, MRI, CT scans, or blood reports?",
    "Short-Term Goals (within 3 months)": (
        "What would you like to achieve in the next three months?"
    ),
    "Long-Term Goals (after 3 months)": (
        "What is your longer-term health or activity goal after three months?"
    ),
    "Specific Expectations from Treatment": (
        "What are your specific expectations from treatment?"
    ),
}

LIFESTYLE_COMPONENT_QUESTIONS = {
    "Current Lifestyle — Work": "What is your work or usual occupation?",
    "Current Lifestyle — Activity or Exercise": (
        "What is your usual activity or exercise routine?"
    )
}

# Asked once, after the patient declines the original question. Never the same wording.
REFRAME_QUESTIONS = {
    "Primary Complaint": "In a few words, what is the one problem you most want help with?",
    "Duration of the Issue": "Roughly how long has this been going on — days, weeks, or months?",
    "Onset (Gradual or Sudden)": "Did it come on suddenly or gradually?",
    "Mechanism of Injury or Cause": "What set it off, or how did it start?",
    "Previous Diagnosis or Advice and Prescribed Treatment Taken": (
        "Have you seen anyone about this before? Yes or no."
    ),
    "Current Status of Issue (Improved, Same, Worse)": (
        "Since that visit, is it better, the same, or worse?"
    ),
    "Primary Location of Pain": "Where do you feel it most? A general area is enough.",
    "Severity (1-10)": (
        "Would you describe it as mild, moderate, or severe? "
        "If numbers are easier, 0 means no pain and 10 means the worst pain imaginable."
    ),
    "Aggravating Factors": "What tends to make it worse?",
    "Relieving Factors": "What eases it, even for a short time?",
    "Systemic Illness and Surgical History": (
        "Are there any conditions or recent surgeries that could affect exercising?"
    ),
    "Current Lifestyle — Work": "Do you mostly sit, stand, or move around for work? A short answer is fine.",
    "Current Lifestyle — Activity or Exercise": (
        "Are you active most days, sometimes, or rarely? A short answer is fine."
    ),
    "Reports": "Do you have any scans or reports for this? ",
    "Short-Term Goals (within 3 months)": "In the next few months, what would feel like progress?",
    "Long-Term Goals (after 3 months)": "Looking further ahead, what would you like to get back to after 3 months?",
    "Specific Expectations from Treatment": "What are you hoping we help with? ",
}

REFRAME_LEAD = "Of course — share only what you're comfortable with."
CLINICIAN_LATER = (
    "That's completely okay. You can cover what you skipped with your clinician later."
)
NOTED_COMPLAINT = "I've noted {labels}. We'll come to that after this."


def question_for_missing_fields(
    missing: list[tuple[str, str]],
    field_labels: dict[str, str],
    *,
    max_fields: int = 4,
    reframe_fields: set[str] | None = None,
) -> str:
    """Return an explicit question for each missing field in one small batch.

    ``reframe_fields`` uses the shorter second wording, after the patient
    declined the original question.
    """

    softer = reframe_fields or set()
    prompts = []
    for _, field in missing[:max_fields]:
        if field in softer and field in REFRAME_QUESTIONS:
            prompts.append(REFRAME_QUESTIONS[field])
            continue
        prompts.append(
            REQUIRED_FIELD_QUESTIONS.get(
                field,
                LIFESTYLE_COMPONENT_QUESTIONS.get(field, field_labels.get(field, field)),
            )
        )
    if len(prompts) == 1:
        return prompts[0]
    return "Could you also tell me:\n" + "\n".join(f"- {prompt}" for prompt in prompts)


def contextual_resume_question(form: dict, missing: list[tuple[str, str]], field_labels: dict[str, str]) -> str:
    """Briefly anchor resumed questions in saved facts, without an AI recap."""
    complaint = str(form.get("Present Complaint", {}).get("Primary Complaint", "") or "").strip()
    introduction = "Welcome back. Your previous answers for this assessment are saved."
    if complaint:
        introduction += f" You previously described: {complaint}"
    return introduction + "\n\nLet's continue with the information still needed.\n\n" + question_for_missing_fields(missing, field_labels)
