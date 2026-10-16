"""Patient text normalization and prompt-injection neutralization."""

import re

MAX_PATIENT_INPUT_CHARS = 2000

_CONTROL_CHARS = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')
_PROMPT_INJECTION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r'ignore\s+(all\s+)?(previous|prior)?\s*instructions?',
        r'you are now',  r'forget everything',  r'system prompt',  r'jailbreak',
        r'act as',  r'pretend (you are|to be)',
    )
)


def normalize_user_text(text: str) -> str:
    """Limit length and strip control characters (tab and newline are kept)."""
    if not text:
        return text
    return _CONTROL_CHARS.sub('', text[:MAX_PATIENT_INPUT_CHARS])


def neutralize_prompt_injection(text: str) -> str:
    """Replace known instruction-override phrases with ``[filtered]``."""
    if not text:
        return text
    for pattern in _PROMPT_INJECTION_PATTERNS:
        text = pattern.sub('[filtered]', text)
    return text


def sanitize_patient_input(text: str) -> str:
    """Strip control chars, limit length, neutralize prompt injection."""
    if not text:
        return text
    return neutralize_prompt_injection(normalize_user_text(text)).strip()
