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


def is_simple_rejection(user_input: str) -> bool:
    """True if the user's input is a simple no/rejection."""
    rejections = {'no', 'nope', 'nah', 'incorrect', 'wrong'}
    return user_input.lower().strip().strip('.,!?') in rejections
