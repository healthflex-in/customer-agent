"""Deterministic replies when a patient asks what an intake question means."""

from __future__ import annotations

import re


_CLARIFICATION_PATTERNS = (
    r"\bwhat (?:do|did|would) you (?:want|like|need) to know\b",
    r"\bwhat (?:exactly|basically) (?:do|did|would) you (?:want|mean|need)\b",
    r"\bwhat (?:information|details) (?:do|would) you (?:want|need)\b",
    r"\bwhich (?:information|details) (?:do|would) you (?:want|need)\b",
    r"\bcan you (?:clarify|explain) (?:that|the question|what you mean)\b",
    r"\bwhy (?:do|are) you (?:need|asking|ask)\b",
)

_ACTIVITY_CLEARANCE = re.compile(
    r"\b(?:can i|could i|should i|may i|am i (?:able|allowed)|"
    r"is it (?:safe|okay|ok)|when (?:can|could|should) i|"
    r"when i (?:can|could|should))\b"
    r".{0,55}\b(?:return|get back|go back|resume|start|continue|play|do)\b"
    r".{0,35}\b(?:sports?|exercise|workouts?|gym|running|training|football|cricket|"
    r"cycling|swimming|play\w*|palay\w*)\b",
    re.I,
)


def pending_intake_question(history: list[dict] | None) -> str:
    """Return the latest real intake prompt, skipping prior boundary replies."""
    for entry in reversed(history or []):
        if entry.get("role") != "agent":
            continue
        message = str(entry.get("message") or "").strip()
        if message and not message.startswith(OUT_OF_FLOW_RESPONSE):
            return message
    return ""


def boundary_response_with_pending_question(
    response: str, pending_question: str | None
) -> str:
    """Decline advice and repeat the unanswered intake question in one turn."""
    question = str(pending_question or "").strip()
    if not question or question.startswith(OUT_OF_FLOW_RESPONSE):
        return response
    return (
        response
        + "\n\nTo continue the intake, please answer this question:\n"
        + question
    )

OUT_OF_FLOW_RESPONSE = (
    "I'm here to collect information for your clinical assessment, so I'm unable "
    "to answer unrelated questions, provide medical advice, recommend medication, "
    "or confirm when it's safe to return to activity. A clinician or physiotherapist "
    "can assess your condition and advise you on what's appropriate."
)

_QUESTION_OPENING = re.compile(
    r"^(?:can|could|should|may|would|will|do|does|did|is|are|am|what|when|where|"
    r"why|how|which|who|tell me|explain|recommend)\b",
    re.I,
)
_MEDICATION_ADVICE = re.compile(
    r"\b(?:can|could|should|may|do|would|what|which|how much|how many)\b"
    r".{0,60}\b(?:medicine|medication|painkillers?|tablets?|dose|dosage|"
    r"paracetamol|acetaminophen|ibuprofen|aspirin|naproxen|diclofenac)\b",
    re.I,
)
_MEDICAL_ADVICE_PHRASES = (
    "what do you think", "your opinion", "what could it be", "what could this",
    "what might it be", "what might this",
    "what is wrong", "what's wrong", "likely diagnosis", "can you diagnose",
    "is it serious", "should i be worried", "how bad is it", "what should i do",
    "what can i do", "how do i treat", "how can i treat", "what treatment",
    "what are the causes", "what causes", "what are the symptoms", "why does",
    "do i need surgery", "which exercise should", "what exercise should",
)
_BRAND_OR_UNRELATED_TERMS = (
    "stance health", "about stance", "your clinic", "the clinic", "clinic location",
    "your services", "physiotherapy service", "how does stance", "about you",
    "who are you", "what do you do", "what does stance", "stance team",
    "stance location", "where are you located", "how many center", "how many clinic",
    "how many branch", "book a session", "what do you offer",
    "weather", "news", "politics", "stock price", "crypto", "sports score",
    "tell me a joke", "movie", "song", "recipe", "homework",
)
_IN_FLOW_OPERATIONAL = re.compile(
    r"\b(?:upload|attach|document|report|scan|repeat|skip|change|correct|update|"
    r"increase|decrease|raise|lower|rating|score|include|mention|note down|answer|"
    r"previous question|next question|summary|share (?:the )?(?:information|details)|"
    r"information (?:you )?(?:wrote|recorded)|what is required|"
    r"complete (?:this|the)|form)\b",
    re.I,
)

_INTAKE_ANSWER_SIGNALS = re.compile(
    r"\b(?:pain|injur|symptom|doctor|physio|hospital|surgery|fracture|"
    r"week|month|year|sudden|gradual|exercise|work|smok|alcohol|"
    r"worse|better|relief|goal|report|mri|x-?ray|ct scan)\b",
    re.I,
)


def _looks_like_detailed_intake_answer(text: str) -> bool:
    """Recognize pasted Q&A and detailed clinical replies before boundary checks."""

    words = text.split()
    return len(words) >= 12 and bool(_INTAKE_ANSWER_SIGNALS.search(text))


def build_activity_clearance_response(user_input: str | None) -> str | None:
    """Safely answer return-to-activity questions without pretending to clear care."""
    text = " ".join(str(user_input or "").split())
    if not _ACTIVITY_CLEARANCE.search(text):
        return None
    return OUT_OF_FLOW_RESPONSE


def build_out_of_flow_response(
    user_input: str | None,
    last_agent_question: str | None = None,
) -> str | None:
    """Return the global boundary message for questions outside intake collection.

    Plain clinical answers and requests to clarify the intake question are allowed
    through. Urgent-risk detection remains the caller's higher-priority concern.
    """
    text = " ".join(str(user_input or "").split())
    lowered = text.lower()
    if not text:
        return None
    if build_intake_clarification(text, last_agent_question) is not None:
        return None

    # Corrections, upload/navigation questions, and pasted clinical Q&A belong
    # to the intake even when their wording starts with "can" or "what".
    if _IN_FLOW_OPERATIONAL.search(text):
        return None

    if _ACTIVITY_CLEARANCE.search(text) or _MEDICATION_ADVICE.search(text):
        return OUT_OF_FLOW_RESPONSE
    if _looks_like_detailed_intake_answer(text):
        return None

    # Clinical answers often begin with a subordinate "when/while" clause.
    # They are statements, not questions, even though the first word is also a
    # question word (for example: "When I walk, the pain gets worse").
    if re.match(r"^(?:when|while)\s+(?:i|my|the)\b", lowered) and re.search(
        r"\b(?:pain|hurt|ache|worse|better|relief|reliev|massage|walk|move|bend)\b",
        lowered,
    ):
        return None

    is_question = "?" in text or bool(_QUESTION_OPENING.search(text))
    if not is_question:
        return None
    if any(phrase in lowered for phrase in _MEDICAL_ADVICE_PHRASES):
        return OUT_OF_FLOW_RESPONSE
    if any(term in lowered for term in _BRAND_OR_UNRELATED_TERMS):
        return OUT_OF_FLOW_RESPONSE

    # General requests for medical education or treatment guidance.
    if any(phrase in lowered for phrase in (
        "tell me about", "can you tell me", "can u tell me", "explain", "what is",
        "what are", "what can", "what happens", "how does", "how do i", "info on",
        "information on", "more about", "difference between",
    )):
        return OUT_OF_FLOW_RESPONSE
    # Remaining genuine questions are outside the data-collection contract.
    return OUT_OF_FLOW_RESPONSE if is_question else None


def build_intake_clarification(
    user_input: str | None,
    last_agent_question: str | None,
) -> str | None:
    """Return a focused explanation without advancing the interview.

    The last assistant question is included only to identify the topic. The
    patient message must itself contain a clear request for clarification, so
    ordinary clinical answers are never intercepted.
    """

    patient_text = " ".join(str(user_input or "").lower().split())
    if not patient_text or not any(
        re.search(pattern, patient_text) for pattern in _CLARIFICATION_PATTERNS
    ):
        return None

    topic_text = f"{patient_text} {str(last_agent_question or '').lower()}"

    if any(term in topic_text for term in ("surgery", "surgeries", "operation", "procedure")):
        return (
            "For each surgery, please tell me what procedure you had and which body part "
            "it involved, why it was needed, approximately when it happened, and whether "
            "you have any ongoing symptoms, complications, or activity restrictions. "
            "A short answer is completely fine."
        )
    if any(term in topic_text for term in ("exercise", "workout", "activity level", "physically active")):
        return (
            "Please share the type of exercise or activity you do, how many days per week "
            "you do it, and whether your current condition limits it. A brief answer is fine."
        )
    if any(term in topic_text for term in ("health condition", "illness", "medical condition")):
        return (
            "Please mention any ongoing or important past health condition, when it was "
            "diagnosed, and whether you currently take treatment or have any related limitations."
        )
    if any(term in topic_text for term in ("report", "x-ray", "x ray", "mri", "scan")):
        return (
            "Please tell me what report or scan you have, which body area it is for, when it "
            "was done, and whether you were told the main finding. You can upload it when prompted."
        )
    if any(term in topic_text for term in ("goal", "expect", "treatment")):
        return (
            "Please describe what improvement you want from treatment—for example less pain, "
            "better movement, returning to work, exercise, sport, or another daily activity."
        )
    if any(term in topic_text for term in ("pain", "complaint", "symptom")):
        return (
            "Please describe where the problem is, how severe it feels, when and how it started, "
            "and what makes it worse or better."
        )

    return (
        "Please share only what you are comfortable sharing. I am looking for the basic details "
        "requested in the previous question; a short, approximate answer is fine."
    )
