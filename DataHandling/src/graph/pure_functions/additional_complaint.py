"""Capture additional symptoms without replacing an existing complaint."""
import copy
import re


_BODY_SITE = re.compile(
    r"\b(?:(?:left|right)\s+)?(?:head|neck|back|shoulder|arm|hand|wrist|elbow|"
    r"forearm|hip|leg|knee|ankle|foot|feet|chest|abdomen|stomach|jaw|"
    r"si[ -]?joint|sacroiliac joint)s?\b",
    re.I,
)
_SYMPTOM = re.compile(
    r"\b(?:pain|stiffness|weakness|swelling|ache|discomfort|headache|"
    r"numbness|numb|tingling|pins[- ]and[- ]needles|clicking|locking|tightness|"
    r"instability|soreness|burning|cramp(?:ing)?|sprain|strain|tear)\b",
    re.I,
)
_INSTRUCTION_LANGUAGE = re.compile(
    r"\b(?:please|pls|kindly)\b.*\b(?:add|include|record|write|update|change)\b|"
    r"\b(?:add|include|record|write|update|change)\b.*\b(?:additional|complaint|"
    r"concern|information|form)\b",
    re.I,
)


def concise_additional_complaint(text):
    """Extract only the symptom label, never the patient's UI instruction."""
    raw = str(text).strip()
    sites = list(dict.fromkeys(
        match.group(0).strip().lower() for match in _BODY_SITE.finditer(raw)
    ))
    symptoms = [match.group(0).lower() for match in _SYMPTOM.finditer(raw)]
    if sites and symptoms:
        return f"{' and '.join(sites)} {symptoms[-1]}"

    candidate = re.sub(
        r"^.*?\b(?:also|additionally)\s+(?:have|feel|experience|experiencing)\s+",
        "",
        raw,
        flags=re.I,
    )
    candidate = re.split(r"\bbut\b|[.;]", candidate, maxsplit=1, flags=re.I)[0]
    candidate = candidate.strip(" ,.'\"")
    return candidate or raw


def validated_additional_complaint(text):
    """Return a safe clinical label or None when instruction text remains."""
    raw = " ".join(str(text or "").split()).strip()
    needs_condensing = bool(
        _INSTRUCTION_LANGUAGE.search(raw)
        or re.search(
            r"\b(?:i(?:['’]ve| have)?\s+)?(?:also|additionally)\s+"
            r"(?:have|feel|experience|(?:been\s+)?experiencing)\b",
            raw,
            re.I,
        )
    )
    candidate = concise_additional_complaint(raw) if needs_condensing else raw
    complaint = " ".join(candidate.split()).strip(" .,!\"'")
    if not complaint or len(complaint) > 160 or "?" in complaint:
        return None
    if _INSTRUCTION_LANGUAGE.search(complaint):
        return None
    # An LLM may classify a treatment preference as a new complaint merely
    # because it contains words such as "pain pills". Require an actual symptom
    # label and reject expectation/request language unless the patient also
    # explicitly states that they experience a symptom.
    has_symptom = bool(_SYMPTOM.search(complaint))
    has_patient_assertion = bool(re.search(
        r"\b(?:i(?:['’]ve| have)?\s+)?(?:also\s+|additionally\s+)?"
        r"(?:have|feel|experience|experiencing|suffer(?:ing)? from|developed)\b",
        raw,
        re.I,
    ))
    preference_language = bool(re.search(
        r"\b(?:i\s+(?:also\s+)?expect|expectation|looking for|treatment plan|"
        r"pain pills?|painkillers?|medicine|medication|without surgery|"
        r"want (?:a|the) plan|would like)\b",
        raw,
        re.I,
    ))
    if not has_symptom or (preference_language and not has_patient_assertion):
        return None
    return complaint


def is_explicit_symptom_addition(text):
    lowered = text.lower()
    if re.search(r"\b(?:no pain|not pain|dont have|don't have|do not have)\b", lowered):
        return False
    symptom = r"(?:pain|stiffness|weakness|swelling|ache|discomfort)"
    site = _BODY_SITE.pattern
    return bool(
        re.search(
            rf"\b(?:i\s+)?(?:also\s+|additionally\s+)?(?:have|feel|experience|experiencing)\b"
            rf".{{0,45}}(?:{symptom}).{{0,12}}\b(?:too|as\s*well)\b",
            lowered,
            re.I,
        )
        or re.search(
            rf"\b(?:i\s+)?(?:also|additionally)\s+(?:have|feel|experience|experiencing)\b"
            rf".{{0,45}}(?:{symptom})\b",
            lowered,
            re.I,
        )
        or re.search(
            rf"\b(?:add|additional|forgot to mention)\b.{{0,45}}{site}.{{0,20}}(?:{symptom})\b",
            lowered,
            re.I,
        )
    )


def split_update_and_addition(text):
    """Split `change X and I also have Y` without losing either action."""
    match = re.search(
        r"\b(?:and\s+)?(?:i\s+)?(?:also|additionally)\s+"
        r"(?:have|feel|experience|experiencing)\b",
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
    return update_text, concise_additional_complaint(addition_text)


def additional_complaint_replacement(text):
    """Return a corrected complaint label, or None for an ordinary addition."""
    lowered = str(text).lower()
    # Never guess that a generic correction targets an additional complaint.
    # The patient must explicitly identify the additional complaint/point.
    if not re.search(r"\badditional (?:complaint|concern|point)\b", lowered):
        return None
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
        r"(?:(?:no|any)\s+)?(?:add\w{3,12}|other) "
        r"(?:compl\w*|concern|pain)s?\b",
        normalized,
    ):
        return True
    if re.search(
        r"\bthe\w*\s+is\s+(?:no|o)\s+add\w{3,12}\s+compl\w*\b",
        normalized,
    ):
        return True
    if re.search(
        r"\b(?:no|don'?t have|do not have)\s+(?:any\s+)?other\s+"
        r"(?:compl\w*|concerns?|pain|issues?)\b",
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
            r"remove (?:this|that)(?: additional)? (?:complaint|concern)|"
            r"(?:that'?s|that is) all|enough(?: for now)?(?: bye)?",
            normalized.strip(" .,!"),
        )
    )


# Words that name a complaint's kind, not where it is. "Back pain" and "lower
# back pain" differ only by a qualifier, so they are the same complaint.
_COMPLAINT_FILLER = frozenset({
    "pain", "ache", "aches", "aching", "issue", "issues", "problem", "problems",
    "injury", "discomfort", "soreness", "sore", "stiffness", "trouble",
    "complaint", "concern", "a", "an", "the", "my", "in", "of", "on", "at",
    "and", "with", "i", "have", "got", "is", "are",
})


def _complaint_words(text):
    return frozenset(
        word for word in re.findall(r"[a-z]+", str(text or "").lower())
        if word not in _COMPLAINT_FILLER
    )


def same_complaint(first, second):
    """True when two complaint labels describe one problem.

    One is the same problem as the other when its distinguishing words are
    all in the other: "back pain" and "lower back pain" match, "left knee pain"
    and "right knee pain" do not, and "knee pain" and "back pain" do not.
    """
    left, right = _complaint_words(first), _complaint_words(second)
    if not left or not right:
        return str(first or "").strip().lower() == str(second or "").strip().lower()
    return left <= right or right <= left


_SAME_ISSUE = re.compile(
    r"\bnot\b.{0,15}\b(?:different|another|new|separate|other|additional|second)\b"
    r".{0,12}\b(?:issue|concern|problem|complaint|pain)\b"
    r"|\b(?:it|that|this)(?:\s+is|'s)\s+the\s+same\b"
    r"|\bsame\s+(?:issue|concern|problem|complaint)\b"
)


def is_same_complaint_statement(text):
    """True when the patient says the extra complaint is the one they already gave."""
    normalized = " ".join(str(text or "").lower().replace("\u2019", "'").split())
    return bool(_SAME_ISSUE.search(normalized))


def same_complaint_section(text, form):
    """The additional complaint the patient says is really their main complaint, if any.

    Only a complaint that restates the main one is returned, so a genuine second
    problem is never removed by "it's the same".
    """
    if not is_same_complaint_statement(text):
        return None
    primary = (form.get("Present Complaint") or {}).get("Primary Complaint", "")
    matches = [
        name for name, fields in form.items()
        if name.startswith("Additional Complaint ") and isinstance(fields, dict)
        and same_complaint(fields.get("Primary Complaint", ""), primary)
    ]
    return matches[0] if len(matches) == 1 else None


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
    complaint = validated_additional_complaint(text)
    if complaint is None:
        return copy.deepcopy(form), None
    return add_complaint_section(form, complaint)


def add_complaint_section(form, complaint):
    """Add an Additional Complaint section for ``complaint``, reusing a match."""
    updated = copy.deepcopy(form)
    existing = [name for name in updated if name.startswith("Additional Complaint ")]
    for name in existing:
        if str(updated[name].get("Primary Complaint", "")).strip().lower() == complaint.lower():
            return updated, name
    name = f"Additional Complaint {len(existing) + 1}"
    updated[name] = {
        "Primary Complaint": complaint,
        "Duration of the Issue": "",
        "Onset (Gradual or Sudden)": "",
        "Mechanism of Injury or Cause": "",
        "Severity (1-10)": "",
        "Aggravating Factors": "",
        "Relieving Factors": "",
    }
    return updated, name


def normalize_additional_complaints(form):
    """Repair instruction-like labels from sessions created by older code."""
    updated = copy.deepcopy(form)
    for section, fields in updated.items():
        if not section.startswith("Additional Complaint ") or not isinstance(fields, dict):
            continue
        current = str(fields.get("Primary Complaint", "")).strip()
        if current:
            concise = concise_additional_complaint(current)
            # Preserve the patient's capitalization when the stored value is
            # already only the symptom label. Rewrite only instruction-like
            # legacy values.
            if concise.lower() != current.lower():
                fields["Primary Complaint"] = concise
    return updated
