"""Bind numeric pain ratings to a unique complaint, never a combined score."""
import copy
import re

SCORE = re.compile(r"\b(10|[0-9])\s*(?:out\s+of|/)\s*10\b", re.I)
SITE = re.compile(r"\b(?:(left|right)\s+)?(head(?:ache)?|neck|back|shoulders?|arms?|hands?|wrists?|elbows?|hips?|legs?|knees?|ankles?|feet|foot|eyes?|chest|abdomen|stomach|jaw)\b", re.I)


def _sites(text):
    values = []
    for match in SITE.finditer(str(text)):
        site = match[2].lower()
        site = {"headache": "head", "feet": "foot"}.get(site, site.rstrip("s"))
        values.append((match.start(), match.end(), (match[1] or "").lower(), site))
    return values


def complaint_targets(form):
    targets = [("Pain Assessment", "Severity (1-10)",
                str(form.get("Present Complaint", {}).get("Primary Complaint", "")) + " " +
                str(form.get("Pain Assessment", {}).get("Primary Location of Pain", "")))]
    targets += [(name, "Severity (1-10)", fields.get("Primary Complaint", ""))
                for name, fields in form.items()
                if name.startswith("Additional Complaint ") and isinstance(fields, dict)]
    return [(section, field, _sites(text)) for section, field, text in targets
            if isinstance(form.get(section), dict) and field in form[section]]


def explicit_severity_updates(form, text, active_section=None):
    targets = complaint_targets(form)
    updates = {}
    clauses = re.split(r"[,;.!?]|\b(?:and|but|whereas|while)\b", text, flags=re.I)
    for clause in clauses:
        sites = _sites(clause)
        for score in SCORE.finditer(clause):
            # "7/10 for my leg" binds forward; "leg pain is 7/10" backward.
            following = sites if re.match(r"\s+(?:for|in|on)\b", clause[score.end():], re.I) else []
            candidates = [site for site in following if site[0] >= score.end()]
            if candidates:
                chosen = min(candidates, key=lambda site: site[0])
            else:
                candidates = [site for site in sites if site[1] <= score.start()]
                chosen = max(candidates, key=lambda site: site[1]) if candidates else None
            matched = []
            if chosen:
                matched = [(section, field) for section, field, known in targets
                           if any(site[3] == chosen[3] and
                                  (not chosen[2] or site[2] == chosen[2]) for site in known)]
            elif not sites and len(list(SCORE.finditer(text))) == 1:
                matched = [(section, field) for section, field, _ in targets
                           if section == active_section]
                if len(targets) == 1:
                    matched = [(targets[0][0], targets[0][1])]
            if len(matched) == 1:
                updates[matched[0]] = score[1] + "/10"
    return updates


def reconcile_severities(original, extracted, text, active_section=None):
    result = copy.deepcopy(extracted)
    targets = complaint_targets(original)
    if len(targets) > 1:
        # Do not trust a full-conversation model to overwrite other complaints'
        # ratings. Only this turn's explicitly scoped score can update them.
        for section, field, _ in targets:
            result.setdefault(section, {})[field] = original[section][field]
    for (section, field), value in explicit_severity_updates(original, text, active_section).items():
        result.setdefault(section, {})[field] = value
    return result
