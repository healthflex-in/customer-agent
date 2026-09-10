"""Step 2 — deterministic candidate pool (file-backed + catalog-resolved).

Reads the growing `tests_pool.json` (source of truth). For each condition:
- `objectiveTests` names are resolved against the live `objectiveAssessments` catalog.
- `learnedTests` (agent-appended over time) are emitted directly, carrying their own
  metadata and flagged `needs_review` until a clinician marks them "approved".
Contraindications are HARD-filtered here, before any LLM sees the pool.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app import config, db, pool
from app.pipeline.models import SOURCE_LEARNED, SOURCE_OBJECTIVE, Candidate


@dataclass
class RetrievalResult:
    candidates: list[Candidate] = field(default_factory=list)
    pool_version: str = ""
    matched_conditions: list[str] = field(default_factory=list)
    unmatched_test_names: list[str] = field(default_factory=list)


_CONTRA_KEYWORDS: dict[str, list[str]] = {
    "acute_fracture_suspected": ["fracture", "broken bone", "deformity"],
    "pregnancy": ["pregnant", "pregnancy"],
}


def detect_patient_flags(form_blob: str) -> set[str]:
    flags: set[str] = set()
    blob = (form_blob or "").lower()
    for flag, kws in _CONTRA_KEYWORDS.items():
        if any(kw in blob for kw in kws):
            flags.add(flag)
    return flags


def retrieve(condition_labels: list[str], applies_to: str, patient_flags: set[str]) -> RetrievalResult:
    pool_data = pool.load()
    conds = pool_data.get("conditions", {})

    wanted: dict[str, int] = {}          # objective test name → max priority
    contra: set[str] = set()
    learned_entries: list[dict] = []
    matched: list[str] = []
    for label in condition_labels:
        entry = conds.get(label)
        if not entry:
            continue
        matched.append(label)
        prio = entry.get("priorityByTest", {}) or {}
        for name in entry.get("objectiveTests", []) or []:
            wanted[name] = max(wanted.get(name, 5), int(prio.get(name, 5)))
        contra.update(entry.get("contraindications", []) or [])
        learned_entries.extend(entry.get("learnedTests", []) or [])

    if not matched:
        return RetrievalResult(pool_version="none")

    contraindicated = patient_flags.intersection(contra)

    # Resolve objective names against the live catalog (case-insensitive).
    catalog = {
        str(d.get("testName", "")).strip().lower(): d
        for d in db.coll(config.COLL_OBJECTIVE_ASSESSMENTS).find(
            {"testName": {"$in": list(wanted.keys())}}
        )
    } if wanted else {}

    candidates: list[Candidate] = []
    unmatched: list[str] = []
    if not contraindicated:
        for name, priority in wanted.items():
            doc = catalog.get(name.strip().lower())
            if not doc:
                unmatched.append(name)  # in pool file but not in catalog → surface for curation
                continue
            candidates.append(
                Candidate(
                    object_id=str(doc.get("_id")),
                    test_name=doc.get("testName", name),
                    test_detail=doc.get("testDetail", ""),
                    unit_name=doc.get("unitName", ""),
                    data_type=doc.get("dataType", ""),
                    calculation_type=doc.get("calculationType", "MANUAL"),
                    normative_text=doc.get("textForNormativeData", ""),
                    priority=priority,
                    source=SOURCE_OBJECTIVE,
                )
            )
        # Agent-learned tests (persisted in the pool file).
        for lt in learned_entries:
            candidates.append(
                Candidate(
                    object_id=None,
                    test_name=lt.get("testName", ""),
                    test_detail=lt.get("rationale", ""),
                    unit_name=lt.get("unitName", ""),
                    data_type=lt.get("dataType", ""),
                    calculation_type="MANUAL",
                    normative_text="",
                    priority=int(lt.get("priority", 5)),
                    source=SOURCE_LEARNED,
                    muscle=lt.get("muscle", ""),
                    joint=lt.get("joint", ""),
                    needs_review=lt.get("status") != "approved",
                )
            )

    learned_count = sum(len(conds.get(m, {}).get("learnedTests", [])) for m in matched)
    pool_version = f"pool:v{pool_data.get('version', 1)}:{'|'.join(sorted(matched))}:L{learned_count}"
    return RetrievalResult(
        candidates=candidates,
        pool_version=pool_version,
        matched_conditions=sorted(set(matched)),
        unmatched_test_names=unmatched,
    )
