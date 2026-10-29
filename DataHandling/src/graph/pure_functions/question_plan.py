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

# Questions put to the patient in one turn. A batch never leaves its section.
QUESTIONS_PER_TURN = 4

CLINICIAN_LATER = (
    "That's completely okay. You can cover what you skipped with your clinician later."
)
NOTED_COMPLAINT = "I've noted {labels}. We'll come to that after this."
MANDATORY_NOTICE = (
    "That one is important for your treatment plan, so I do need your answer. "
    "A short, approximate one is fine."
)
SKIP_WHICH = "That's completely okay. Which of these would you like to skip?"
SKIP_WHICH_KEEP_REQUIRED = (
    "Some of these help your clinician plan the right treatment, so I'll keep those. "
    "Which of the others would you like to skip?"
)
# A question asked again because the reply did not answer it.
RETRY_NOTICE = "Sorry, I didn't catch an answer to that. Could you please answer this one?"
RETRY_MANDATORY_NOTICE = (
    "Sorry, I didn't catch that. This one is required to complete your assessment, "
    "so please answer it."
)
# The patient asked to end the interview while a required question can still be asked.
END_NOT_YET_REQUIRED = (
    "You haven't answered the required questions yet. Please answer them before we end "
    "the interview, because they're needed for your treatment plan."
)
# Added when a share of the form must also be done before the patient can finish.
END_PERCENT_NOTE = (
    "We also need to complete at least {minimum}% of the form before we can finish, "
    "and you're at {current}% now."
)
END_NOT_YET_MORE = (
    "We're at {current}% and need at least {minimum}% before we can finish. "
    "Just a few more questions and we're done."
)
# Every required question is answered, so ending is allowed, but it is confirmed first.
END_CONFIRM = (
    "You've not answered all the questions. Are you sure you want to end the "
    "interview? Reply 'yes' to end it, or 'no' to carry on."
)
END_CONTINUE = "Great, let's carry on."
# A required question that went unanswered after the last ask. The interview moves on.
MANDATORY_LATER = (
    "I wasn't able to capture {labels}, so please tell your clinician about this later. "
    "Let's move on."
)


def question_lines(missing: list[tuple[str, str]], field_labels: dict[str, str]) -> list[str]:
    """The patient-facing question for each (section, field), in order."""

    return [
        REQUIRED_FIELD_QUESTIONS.get(
            field,
            LIFESTYLE_COMPONENT_QUESTIONS.get(field, field_labels.get(field, field)),
        )
        for _, field in missing
    ]


def question_for_missing_fields(
    missing: list[tuple[str, str]],
    field_labels: dict[str, str],
    *,
    max_fields: int = QUESTIONS_PER_TURN,
    intro: str = "",
    about: str = "",
) -> str:
    """Return an explicit question for each missing field in one small batch.

    ``intro`` opens a batch that starts a new section. Follow-up questions in the
    same section have none, and are asked with "Could you also tell me:". ``about``
    names the complaint the questions are for; it is given only when the patient
    has more than one, so they always know which problem they are answering for.
    """

    prompts = question_lines(missing[:max_fields], field_labels)
    if intro:
        if len(prompts) == 1:
            return f"{intro}\n\n{prompts[0]}"
        return f"{intro}\n" + "\n".join(f"- {prompt}" for prompt in prompts)
    if len(prompts) == 1:
        return f"About {about}: {prompts[0]}" if about else prompts[0]
    header = f"Could you also tell me about {about}:" if about else "Could you also tell me:"
    return f"{header}\n" + "\n".join(f"- {prompt}" for prompt in prompts)


def contextual_resume_question(form: dict, missing: list[tuple[str, str]], field_labels: dict[str, str]) -> str:
    """Briefly anchor resumed questions in saved facts, without an AI recap."""
    complaint = str(form.get("Present Complaint", {}).get("Primary Complaint", "") or "").strip()
    introduction = "Welcome back. Your previous answers for this assessment are saved."
    if complaint:
        introduction += f" You previously described: {complaint}"
    return introduction + "\n\nLet's continue with the information still needed.\n\n" + question_for_missing_fields(missing, field_labels)
