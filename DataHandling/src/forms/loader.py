"""
Form definition loader.

Reads FRM-01.md (and future form files) from data/forms/ once, parses them
into typed dataclasses, and caches the result for the lifetime of the process.

To edit FRM-01 questions or structure: update data/forms/FRM-01.md only —
no Python changes needed.
"""
from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path


_FORMS_DIR = Path(__file__).parent.parent.parent / "data" / "forms"


@dataclass
class VisitType:
    description: str
    skip_sections: frozenset  # frozenset[str]


@dataclass
class PredefinedQuestion:
    key: str
    title: str
    text: str


@dataclass
class FormDefinition:
    form_id: str
    sections: dict            # {section_name: [field_name, ...]}
    field_labels: dict        # {field_name: human_readable_label}
    visit_types: dict         # {visit_type_key: VisitType}
    predefined_questions: tuple  # (PredefinedQuestion, ...)
    general_visit_q1: str
    referral_question: str

    def empty_form(self) -> dict:
        """Return a fresh blank form dict. Call this instead of deepcopy(template)."""
        return {section: {field: "" for field in fields}
                for section, fields in self.sections.items()}

    def predefined_as_tuples(self) -> list:
        """Return [(title, text), ...] for legacy callers expecting the old format."""
        return [(q.title, q.text) for q in self.predefined_questions]


# ── Parsers ───────────────────────────────────────────────────────────────────

def _parse_sections(block: str) -> dict:
    sections = {}
    for chunk in block.split("\n### ")[1:]:
        lines = chunk.strip().splitlines()
        name = lines[0].strip()
        fields = [ln[2:].strip() for ln in lines[1:] if ln.startswith("- ")]
        sections[name] = fields
    return sections


def _parse_visit_types(block: str) -> dict:
    result = {}
    for chunk in block.split("\n### ")[1:]:
        lines = chunk.strip().splitlines()
        key = lines[0].strip()
        skip: set = set()
        description = ""
        for ln in lines[1:]:
            if ln.startswith("skip:"):
                val = ln[5:].strip()
                if val:
                    skip = {s.strip() for s in val.split(",")}
            elif ln.startswith("description:"):
                description = ln[12:].strip()
        result[key] = VisitType(description=description, skip_sections=frozenset(skip))
    return result


def _parse_field_labels(block: str) -> dict:
    labels = {}
    for ln in block.strip().splitlines():
        ln = ln.strip()
        if ": " in ln:
            key, _, val = ln.partition(": ")
            labels[key.strip()] = val.strip()
    return labels


def _parse_predefined_questions(block: str) -> tuple:
    questions = []
    for chunk in block.split("\n### ")[1:]:
        lines = chunk.strip().splitlines()
        key = lines[0].strip()
        title = ""
        body: list[str] = []
        for ln in lines[1:]:
            if ln.startswith("title:") and not title:
                title = ln[6:].strip()
            else:
                body.append(ln)
        questions.append(PredefinedQuestion(
            key=key,
            title=title,
            text="\n".join(body).strip(),
        ))
    return tuple(questions)


def _parse_text_block(block: str) -> str:
    """Extract plain text from a section, stripping horizontal rules."""
    lines = [ln for ln in block.strip().splitlines() if ln.strip() and ln.strip() != "---"]
    return "\n".join(lines)


def _parse(raw: str, form_id: str) -> FormDefinition:
    # Split on H2 headings — each becomes a named block
    blocks: dict[str, str] = {}
    current = None
    buf: list[str] = []
    for ln in raw.splitlines():
        if ln.startswith("## "):
            if current:
                blocks[current] = "\n".join(buf)
            current = ln[3:].strip()
            buf = []
        else:
            buf.append(ln)
    if current:
        blocks[current] = "\n".join(buf)

    return FormDefinition(
        form_id=form_id,
        sections=_parse_sections(blocks.get("Sections", "")),
        field_labels=_parse_field_labels(blocks.get("Field Labels", "")),
        visit_types=_parse_visit_types(blocks.get("Visit Types", "")),
        predefined_questions=_parse_predefined_questions(blocks.get("Predefined Questions", "")),
        general_visit_q1=_parse_text_block(blocks.get("General Visit First Question", "")),
        referral_question=_parse_text_block(blocks.get("Referral Question", "")),
    )


# ── Public API ────────────────────────────────────────────────────────────────

@functools.lru_cache(maxsize=8)
def load_form(form_id: str = "FRM-01") -> FormDefinition:
    """Load and cache a form definition from data/forms/<form_id>.md."""
    path = _FORMS_DIR / f"{form_id}.md"
    return _parse(path.read_text(encoding="utf-8"), form_id)
