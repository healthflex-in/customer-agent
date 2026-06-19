"""
Pure intent-detection helpers extracted from HealthAgent.
No LLM calls. No side effects. Fully unit-testable.
"""
import re


def should_check_for_correction(user_input: str, awaiting_confirmation: bool = False) -> bool:
    """
    Heuristic: decide whether to run LLM correction detection.
    Returns False quickly (no LLM) for simple answers that are clearly not corrections.

    Extracted from HealthAgent.should_check_for_correction().
    """
    simple_confirmations = {
        'yes', 'no', 'ok', 'okay', 'sure', 'correct', 'right',
        'yep', 'yeah', 'nope', 'nah', 'fine', 'good', 'yup',
        'alright', 'affirmative', 'negative',
    }
    input_lower = user_input.lower().strip().strip('.,!?;:')

    if input_lower in simple_confirmations:
        return False

    if awaiting_confirmation:
        words = set(w.lower().strip('.,!?;:') for w in user_input.split())
        if len(user_input.split()) <= 3 and words & simple_confirmations:
            return False

    correction_keywords = [
        'actually', 'sorry', 'correction', 'correct', 'wrong', 'mistake',
        'meant', 'meant to say', 'i said', 'i meant', 'let me correct',
        'change', 'update', 'to clarify', 'clarify', 'misspoke',
        'instead', 'rather',
    ]
    correction_patterns = [
        "was not", "wasn't", "it was not", "it wasn't",
        "it's not", "its not", "is not", "isn't",
        "not that", "not the",
        "sorry, not", "actually, not", "i meant", "i said",
        "change", "update", "correct that", "fix that",
    ]

    has_correction = any(kw in input_lower for kw in correction_keywords)
    if not has_correction:
        has_correction = any(p in input_lower for p in correction_patterns)

    # "not X, but Y" pattern (only if a clarifying word is nearby)
    words_list = input_lower.split()
    if not has_correction and 'not' in words_list:
        idx = words_list.index('not')
        context = ' '.join(words_list[max(0, idx - 2):min(len(words_list), idx + 5)])
        if any(w in context for w in ["but", "actually", "it's", "its", "i meant", "i said", "sorry"]):
            has_correction = True

    # Exclude status descriptions that contain "not" but are not corrections
    status_descriptions = [
        'not improved', 'not getting worse', 'not better', 'not worse',
        'remained the same', 'stayed the same', 'is the same', 'has been the same',
        "condition is not", "status is not", "it's not improved", "it's not worse",
    ]
    for desc in status_descriptions:
        if desc in input_lower:
            has_correction = False
            break

    return has_correction


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
