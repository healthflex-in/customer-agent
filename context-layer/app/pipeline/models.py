"""Shared pipeline data models."""

from __future__ import annotations

from dataclasses import dataclass

# Where a candidate test came from.
SOURCE_OBJECTIVE = "objective"        # objectiveAssessments catalog
SOURCE_VALD = "vald"                  # vald-exercise-data (forceDeck/forceFrame)
SOURCE_LEARNED = "learned"            # in the growing tests_pool.json (agent-learned)
SOURCE_PROPOSED = "agent_proposed"    # freshly proposed this turn (not yet persisted)


@dataclass
class Candidate:
    object_id: str | None   # objectiveAssessments._id / vald key / None for learned/proposed
    test_name: str
    test_detail: str
    unit_name: str
    data_type: str
    calculation_type: str
    normative_text: str
    priority: int
    source: str = SOURCE_OBJECTIVE
    device_type: str = ""       # VALD only (forcedeck/forceframe/dynamo)
    muscle: str = ""            # for learned/proposed tests
    joint: str = ""
    needs_review: bool = False  # clinician must review before clinical use


@dataclass
class Ranked:
    candidate: Candidate
    rank: int
    confidence: float
    rationale: str
