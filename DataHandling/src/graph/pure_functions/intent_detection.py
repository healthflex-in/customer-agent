"""
Pure intent-detection helpers extracted from HealthAgent.
No LLM calls. No side effects. Fully unit-testable.
"""
import re


def should_check_for_correction(user_input: str, awaiting_confirmation: bool = False) -> bool:
    """
    High-precision legacy helper for explicit corrections.

    The active graph does not use this during interviewing: ordinary answers,
    including self-corrections and speech disfluencies, must be extracted. It is
    retained for the legacy HealthAgent API and deliberately requires a clear
    reference to previously supplied information.
    """
    simple_confirmations = {
        'yes', 'no', 'ok', 'okay', 'sure', 'correct', 'right',
        'yep', 'yeah', 'nope', 'nah', 'fine', 'good', 'yup',
        'alright', 'affirmative', 'negative',
    }
    input_lower = user_input.lower().strip().strip('.,!?;:')

    if input_lower in simple_confirmations:
        return False

    explicit_patterns = (
        r"\b(?:i|we)\s+(?:previously\s+)?(?:said|mentioned|told you)\b",
        r"\b(?:meant to say|i meant|misspoke)\b",
        r"\blet me correct\b",
        r"\b(?:please\s+)?(?:change|update|correct|fix)\s+(?:that|this|my|the)\b",
        r"\b(?:that|this)\s+(?:is|was)\s+(?:wrong|incorrect)\b",
        r"\b(?:wrong|incorrect)\b.{0,80}\b(?:correct|should be)\b",
    )
    return any(re.search(pattern, input_lower) for pattern in explicit_patterns)


def is_simple_confirmation(user_input: str) -> bool:
    """True if the user's input is a simple yes/no/ok confirmation."""
    confirmations = {
        'yes', 'yeah', 'yep', 'sure', 'ok', 'okay', 'yup', 'fine',
        'good', 'alright', 'ready', "i am ready", "i'm ready",
        'lets go', "let's go", 'start', 'begin',
    }
    return user_input.lower().strip().strip('.,!?') in confirmations


_BARE_NO = frozenset({
    "no", "nope", "nah", "no thanks", "no i don't", "no i dont", "i don't", "i dont",
    "no i do not", "i do not", "not really", "none", "nothing", "no nothing",
})
_BARE_YES = frozenset({"yes", "yeah", "yep", "yup", "yes i do", "yes i have", "i do", "i have"})


def bare_yes_no(user_input: str | None) -> str | None:
    """"No" or "Yes" when the whole reply is only a short yes or no, else None.

    A bare "no" answers the question it fits. It is never a refusal, so a reply
    like this is not read as a skip. "No reports" is not bare: it names what it is about.
    """

    text = " ".join(str(user_input or "").lower().replace("\u2019", "'").split()).strip(" .,!?")
    if text in _BARE_NO:
        return "No"
    if text in _BARE_YES:
        return "Yes"
    return None


_SKIP_ALL = re.compile(
    r"^(?:(?:ok|okay|yes|yeah|please|just)\s+)*"
    r"(?:skip (?:all|everything|them all|all of them|the rest|the lot)"
    r"|(?:i )?(?:want to |would like to |wanna )?skip (?:all|everything)"
    r"|none of (?:these|them|those)|leave (?:all|everything|them all) (?:out|for now))"
    r"(?: please| thanks| thank you)?$"
)


def is_skip_all(user_input: str | None) -> bool:
    """True only when the patient says to skip every remaining question ("skip all").

    A bare "skip" is not this: it asks which question is meant. Only an explicit
    "all", "everything", "the rest" or "none of these" exits every question at once.
    """

    text = " ".join(str(user_input or "").lower().replace("\u2019", "'").split()).strip(" .,!?")
    return bool(_SKIP_ALL.match(text))


def is_simple_rejection(user_input: str) -> bool:
    """True if the user's input is a simple no/rejection."""
    rejections = {'no', 'nope', 'nah', 'incorrect', 'wrong'}
    return user_input.lower().strip().strip('.,!?') in rejections


_FAREWELL = re.compile(
    r"^(?:(?:ok|okay|well|so|hey|thanks|thank you|please)\s+)*"
    r"(?:good ?bye|bye(?: bye)?|see you(?: later)?|i quit"
    r"|i(?:'m| am| m) (?:done|finished)"
    r"(?: now| here| for now| with (?:my |the )?(?:responses|answers|questions|form|interview))?)"
    r"(?:\s+(?:thanks|thank you|now|please|then))*$"
)
_END_REQUEST = re.compile(
    r"\b(?:end|stop|quit|finish|close|exit|leave)\b(?: (?:the|this|my|these))? "
    r"(?:interv\w*|chat|conversation|form|assessment|session|questions)\b"
    r"|\bno more questions\b|\bstop asking\b"
)


def is_end_request(user_input: str) -> bool:
    """True when the patient is asking to end the interview.

    Deliberately narrow: a farewell must be the whole message, so an answer
    such as "I'm done with running" is never read as a request to leave.
    """
    text = re.sub(r"[^a-z' ]", " ", str(user_input or "").lower().replace("\u2019", "'"))
    text = " ".join(text.split())
    if not text:
        return False
    return bool(_FAREWELL.match(text) or _END_REQUEST.search(text))


_YES_WORDS = frozenset({"yes", "yeah", "yep", "yup", "sure", "ok", "okay", "definitely", "absolutely"})
_NO_WORDS = frozenset({"no", "nope", "nah"})
_CARRY_ON = re.compile(
    r"\b(?:continue|carry on|keep going|go on|more questions|not yet|"
    r"(?:don'?t|do not) (?:end|stop|finish))\b"
)


def end_confirmation_reply(user_input: str) -> str | None:
    """Read a reply to "are you sure you want to end the interview?".

    Returns "yes", "no", or None when the reply is neither, in which case it is
    read as an ordinary answer. Only short replies count, so a long answer that
    happens to start with "no" is never taken as a refusal to end.
    """
    text = re.sub(r"[^a-z' ]", " ", str(user_input or "").lower().replace("\u2019", "'"))
    words = text.split()
    if not words:
        return None
    if is_end_request(user_input):
        return "yes"
    if len(words) <= 6 and words[0] in _YES_WORDS:
        return "yes"
    if len(words) <= 8 and (words[0] in _NO_WORDS or _CARRY_ON.search(" ".join(words))):
        return "no"
    return None
