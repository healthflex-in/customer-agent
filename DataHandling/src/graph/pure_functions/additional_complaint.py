"""Capture additional symptoms without replacing an existing complaint."""
import copy
import re


_BODY_SITE = re.compile(
    r"\b(?:(?:left|right)\s+)?(?:head|neck|back|shoulder|arm|hand|wrist|elbow|"
    r"forearm|hip|leg|knee|ankle|foot|feet|chest|abdomen|stomach|jaw)s?\b",
    re.I,
)
_SYMPTOM = re.compile(r"\b(?:pain|stiffness|weakness|swelling|ache|discomfort)\b", re.I)


def _concise_complaint(text):
    """Extract only the newly reported symptom from an addition sentence."""
    candidate = re.sub(
        r"^.*?\b(?:also|additionally)\s+(?:have|feel|experience)\s+",
        "",
        str(text),
        flags=re.I,
    )
    candidate = re.split(r"\bbut\b|[.;]", candidate, maxsplit=1, flags=re.I)[0]
    candidate = candidate.strip(" ,.'\"")
    return candidate or str(text).strip()


def is_explicit_symptom_addition(text):
    lowered = text.lower()
    return bool(
        re.search(r"\b(?:pain|stiffness|weakness|swelling)\b", lowered)
        and re.search(r"\b(?:as well|aswell|also|additional|forgot|add)\b", lowered)
        and not re.search(r"\b(?:no pain|not pain|dont have|don't have|do not have)\b", lowered)
    )


def split_update_and_addition(text):
    """Split `change X and I also have Y` without losing either action."""
    match = re.search(
        r"\b(?:and\s+)?(?:i\s+)?(?:also|additionally)\s+(?:have|feel|experience)\b",
        text,
        re.I,
    )
    if not match:
        return None
    update_text = text[:match.start()].strip(" ,.;")
    addition_text = text[match.start():].strip(" ,.;")
    if not update_text or not is_explicit_symptom_addition(addition_text):
        return None
    if not re.search(
        r"\b(?:update|change|correct|make|set|increase|decrease|raise|lower|"
        r"should be|instead)\b",
        update_text,
        re.I,
    ):
        return None
    return update_text, _concise_complaint(addition_text)


def additional_complaint_replacement(text):
    """Return a corrected complaint label, or None for an ordinary addition."""
    lowered = str(text).lower()
    correction_signal = re.search(
        r"\b(?:change|correct|update|replace|not .*additional|"
        r"additional (?:complaint|concern|point).{0,35}"
        r"(?:is|should be|write|written|record|mention))\b",
        lowered,
    )
    if not correction_signal:
        return None

    scoped = str(text)
    explicit = re.search(
        r"\badditional (?:complaint|concern|point) (?:is|should be)\s+(.+?)(?:\bbut\b|[.;]|$)",
        scoped,
        re.I,
    )
    if explicit:
        scoped = explicit.group(1)
    matches = list(_BODY_SITE.finditer(scoped))
    if not matches:
        return None
    sites = list(dict.fromkeys(match.group(0).strip().lower() for match in matches))
    site = " and ".join(sites)
    symptom_matches = list(_SYMPTOM.finditer(scoped))
    symptom = symptom_matches[-1].group(0).lower() if symptom_matches else "pain"
    return f"{site} {symptom}".strip()


def is_additional_complaint_cancellation(text):
    """Recognize an explicit retraction/skip of the active added complaint."""
    normalized = " ".join(str(text).lower().replace("’", "'").split())
    if re.search(
        r"\b(?:there (?:is|are)|i have|i don't have|i dont have|no)\s+"
        r"(?:(?:no|any)\s+)?additional (?:complaint|concern|pain)s?\b",
        normalized,
    ):
        return True
    if re.search(r"\b(?:remove|delete)\b", normalized) and re.search(
        r"\b(?:this|that|additional)\b.{0,30}\b(?:complaint|concern|pain)\b"
        r"|\b(?:complaint|concern|pain)\b.{0,30}\b(?:remove|delete)\b",
        normalized,
    ):
        return True
    return bool(
        re.fullmatch(
            r"(?:let'?s\s+)?skip (?:this|it)|"
            r"no need(?:\s+of|\s+for)?(?:\s+(?:this|that|the))?"
            r"(?:\s+additional)?(?:\s+(?:compl\w*|compa\w*|concern|pain))?"
            r"(?:\s+by mistake)?(?:\s+i told (?:you|u))?"
            r"|no need(?:,? enough)?(?: bye)?|"
            r"remove (?:this|that)(?: additional)? (?:complaint|concern)",
            normalized.strip(" .,!"),
        )
    )


def additional_complaint_removal_target(text, form, active_section=None):
    """Resolve an explicit removal to one additional-complaint section only."""
    normalized = " ".join(str(text).lower().replace("’", "'").split())
    removal = bool(re.search(r"\b(?:remove|delete|drop)\b", normalized))
    negated_pain = bool(re.search(
        r"\b(?:i\s+)?(?:do not|don't|dont|no longer)\s+have\b.{0,45}"
        r"\b(?:pain|stiffness|weakness|swelling|ache|discomfort)\b",
        normalized,
    ))
    if not removal and not negated_pain and not is_additional_complaint_cancellation(text):
        return None

    sections = [
        name for name, fields in form.items()
        if name.startswith("Additional Complaint ") and isinstance(fields, dict)
    ]
    if not sections:
        return None

    mentioned_sites = {
        match.group(0).strip().lower() for match in _BODY_SITE.finditer(normalized)
    }
    if mentioned_sites:
        matches = []
        for section in sections:
            complaint = str(form[section].get("Primary Complaint", "")).lower()
            complaint_sites = {
                match.group(0).strip().lower() for match in _BODY_SITE.finditer(complaint)
            }
            if mentioned_sites & complaint_sites:
                matches.append(section)
        if len(matches) == 1:
            return matches[0]
        # A body site was explicitly named but it does not uniquely identify an
        # additional complaint. Never guess: the patient may be correcting the
        # primary complaint instead.
        return None

    if active_section in sections:
        return active_section
    if re.search(r"\b(?:this|that|additional)\s+(?:complaint|concern|point)\b", normalized):
        return sections[-1]
    if len(sections) == 1:
        return sections[0]
    return None


def capture_additional_complaint(form, text):
    updated = copy.deepcopy(form)
    # Keep the exact patient wording as the source of truth. Details about the
    # original complaint must not be reused for this additional symptom.
    existing = [name for name in updated if name.startswith("Additional Complaint ")]
    for name in existing:
        if updated[name].get("Primary Complaint") == text.strip():
            return updated, name
    name = f"Additional Complaint {len(existing) + 1}"
    updated[name] = {
        "Primary Complaint": text.strip(),
        "Duration of the Issue": "",
        "Onset (Gradual or Sudden)": "",
        "Mechanism of Injury or Cause": "",
        "Severity (1-10)": "",
        "Aggravating Factors": "",
        "Relieving Factors": "",
    }
    return updated, name
