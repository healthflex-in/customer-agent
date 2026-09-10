"""The growing, file-backed tests pool — the system's learning memory.

Structure (data/tests_pool.json):
{
  "version": 1,
  "conditions": {
    "knee_pain": {
      "objectiveTests": ["McMurray's Test", ...],   # resolved vs objectiveAssessments catalog
      "priorityByTest": { "McMurray's Test": 9 },
      "contraindications": ["acute_fracture_suspected"],
      "learnedTests": [                              # agent-appended over time
        { "testName": "...", "muscle": "vastus medialis", "joint": "knee",
          "unitName": "", "dataType": "POSITIVE_NEGATIVE", "priority": 5,
          "rationale": "...", "addedBy": "agent", "status": "proposed|approved",
          "addedAt": "2026-08-10T..." }
      ]
    }
  }
}

When the agent proposes a net-new test for a condition/muscle/joint, we append it
here (status "proposed"). Next run it is a deterministic candidate — the pool grows
and the LLM is needed less over time. A clinician promotes "proposed" → "approved".
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path

from app import config

_lock = threading.Lock()


def _path() -> Path:
    return Path(config.POOL_FILE)


def load() -> dict:
    p = _path()
    if not p.exists():
        return {"version": 1, "conditions": {}}
    try:
        return json.loads(p.read_text())
    except Exception as e:  # noqa: BLE001
        print(f"[pool] failed to read {p}: {e}")
        return {"version": 1, "conditions": {}}


def get_condition(condition: str) -> dict | None:
    return load().get("conditions", {}).get(condition)


def _atomic_write(data: dict) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, p)  # atomic on POSIX
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def add_learned_test(
    condition: str,
    *,
    test_name: str,
    muscle: str = "",
    joint: str = "",
    unit_name: str = "",
    data_type: str = "",
    priority: int = 5,
    rationale: str = "",
    added_by: str = "agent",
) -> bool:
    """Append a net-new test to the pool for a condition. Deduped by name.

    Returns True if it was newly added (i.e., the pool grew), False if it already
    existed. Thread-safe + atomic write so concurrent requests can't corrupt the file.
    """
    name_key = test_name.strip().lower()
    if not name_key:
        return False
    with _lock:
        data = load()
        conds = data.setdefault("conditions", {})
        entry = conds.setdefault(
            condition,
            {"objectiveTests": [], "priorityByTest": {}, "contraindications": [], "learnedTests": []},
        )
        existing = {t.get("testName", "").strip().lower() for t in entry.get("learnedTests", [])}
        existing |= {n.strip().lower() for n in entry.get("objectiveTests", [])}
        if name_key in existing:
            return False
        entry.setdefault("learnedTests", []).append(
            {
                "testName": test_name.strip(),
                "muscle": muscle,
                "joint": joint,
                "unitName": unit_name,
                "dataType": data_type,
                "priority": priority,
                "rationale": rationale,
                "addedBy": added_by,
                "status": "proposed",  # clinician promotes to "approved"
                "addedAt": datetime.now(timezone.utc).isoformat(),
            }
        )
        _atomic_write(data)
        print(f"[pool] learned new test for '{condition}': {test_name} (muscle={muscle}, joint={joint})")
        return True
