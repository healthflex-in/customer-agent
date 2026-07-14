"""Input sanitization — strips control chars and neutralizes prompt injection."""
import re

def sanitize_patient_input(text: str) -> str:
    if not text:
        return text
    text = text[:2000]
    text = re.sub(r'[\x00-\x08\x0b-\x1f\x7f]', '', text)
    _injection = [
        r'ignore\s+(all\s+)?(previous|prior)?\s*instructions?',
        r'you are now', r'forget everything', r'system prompt', r'jailbreak',
        r'act as', r'pretend (you are|to be)',
    ]
    for pat in _injection:
        text = re.sub(pat, '[filtered]', text, flags=re.IGNORECASE)
    return text.strip()
