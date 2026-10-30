"""Wording the bot uses to acknowledge what the patient said.

Each situation has a few variants, so the bot doesn't sound identical every
turn. A variant is chosen from the turn number, which keeps the choice stable
for a given turn and easy to test. Only wording that does not change what the
patient must do lives here. The mandatory notice, the end confirmation and the
summary stay in question_plan.py as fixed, clinic-approved text.
"""

from __future__ import annotations

import re

KNOWN_TONES = frozenset({"neutral", "positive", "frustrated", "confused", "distressed"})
KNOWN_INTENTS = frozenset({"answer", "skip", "end", "form_question", "medical_advice", "off_topic"})

VARIANTS: dict[str, tuple[str, ...]] = {
    # The patient changed an answer they had already given.
    "revision": (
        "Got it, I've updated {changes}.",
        "Thanks, I've updated {changes}.",
        "Okay, I've changed {changes} for you.",
    ),
    "return_to_questions": (
        "Coming back to the questions, could you answer these?",
        "Let's go back to the questions. Could you answer these?",
    ),
    # The reply answered something new. {things} says what, in the form's own words.
    "saved": (
        "Thanks, I've noted {things}.",
        "Got it, I've written down {things}.",
        "Thanks, that tells me {things}.",
    ),
    # The patient asked what a question is about.
    "form_question": (
        "Good question. I'm asking about {complaint}.",
        "Of course. These questions are about {complaint}.",
    ),
    # The patient skipped an entire added complaint. Its name stays on the form.
    "section_skipped": (
        "Alright.",
        "Sure, we'll leave that one out.",
    ),
    # The patient gave details about another problem while the main one is being asked about.
    "other_complaint_saved": (
        "Thanks, I've noted {things} for {complaint}. Let's finish {active} first, then we'll come back to it.",
        "Got it, I've written down {things} for {complaint}. We'll finish {active} first and come back to it afterwards.",
    ),
    # The patient raised a separate problem.
    "noted": (
        "I've noted {labels}. We'll come to that after this.",
        "Thanks for mentioning {labels}. We'll come back to it after this.",
        "I've made a note of {labels}, and we'll look at it after your main problem.",
    ),
    # The patient skipped an optional question.
    "skip_accepted": (
        "That's completely okay. You can cover what you skipped with your clinician later.",
        "No problem, we'll leave that one. Your clinician can go over it with you later.",
        "That's fine. We'll leave it, and your clinician can pick it up with you.",
    ),
    # A question was asked again and the reply did not answer it.
    "retry": (
        "Sorry, I didn't catch an answer to that. Could you please answer this one?",
        "I'm not sure I followed that. Could you answer this one in your own words?",
    ),
}

# Shown first when the model reads the patient as frustrated, confused or distressed.
TONE_LEADS: dict[str, tuple[str, ...]] = {
    "frustrated": (
        "I know this is a lot of questions, and I appreciate your patience.",
        "Thank you for bearing with me, I know this can feel long.",
    ),
    "confused": (
        "Let me explain: this form helps your clinician plan your treatment before your visit.",
        "Of course, let me explain why we're asking. Your answers help your clinician prepare.",
    ),
    "distressed": (
        "I'm sorry this is hard to talk about. We can take it one question at a time.",
        "Thank you for telling me this. We'll go slowly and only ask what we need.",
    ),
}


def your(label: str) -> str:
    """A complaint name as a patient would say it mid-sentence: "your piriformis tightness"."""

    text = str(label or "").strip()
    if len(text) > 1 and not text[1].isupper():
        text = text[0].lower() + text[1:]
    return f"your {text}"


def first_word(text: str) -> str:
    """The first word of ``text``, lower case, or "" when it has none."""

    match = re.match(r"[A-Za-z']+", str(text or "").strip())
    return match.group(0).lower() if match else ""


def _choose(options: tuple[str, ...], turn: int, avoid: str) -> str:
    """The turn's variant, or the next one that doesn't start like ``avoid``.

    Two sentences in a row must not open with the same word ("Okay, that's
    helpful. Okay, let's dive deeper"), so the sentence that will sit in front
    of ``avoid`` is chosen to start differently. If every option starts the same
    way, the turn's own variant is used.
    """

    start = turn % len(options)
    word = first_word(avoid)
    for offset in range(len(options)):
        option = options[(start + offset) % len(options)]
        if not word or first_word(option) != word:
            return option
    return options[start]


def pick(key: str, turn: int, avoid: str = "") -> str:
    """The variant for ``key`` on this turn, the same for the same turn number.

    ``avoid`` is the text that will follow it; the variant won't start with the
    same word.
    """

    return _choose(VARIANTS[key], turn, avoid)


def tone_lead(tone: str | None, turn: int, avoid: str = "") -> str:
    """A sentence to acknowledge how the patient seems, or "" for a neutral reply."""

    options = TONE_LEADS.get(tone or "")
    if not options:
        return ""
    return _choose(options, turn, avoid)


def normalise_intent(value: object) -> str:
    """The model's intent label, or "answer" when it is missing or unknown."""

    text = str(value or "").strip().lower()
    return text if text in KNOWN_INTENTS else "answer"


def normalise_tone(value: object) -> str:
    """The model's tone label, or neutral when it is missing or unknown."""

    text = str(value or "").strip().lower()
    return text if text in KNOWN_TONES else "neutral"
