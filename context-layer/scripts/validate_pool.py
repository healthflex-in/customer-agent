"""Validate data/tests_pool.json against the live objectiveAssessments catalog.

Reports any `objectiveTests` name that won't resolve (curation error) and prints
a summary of learned tests awaiting clinician approval. Read-only.

Usage:
    python -m scripts.validate_pool
"""

from __future__ import annotations

from app import config, db, pool


def main() -> None:
    if not db.init_mongo():
        raise SystemExit("MongoDB not connected — set MONGO_URI")

    catalog = {
        str(d.get("testName", "")).strip().lower()
        for d in db.coll(config.COLL_OBJECTIVE_ASSESSMENTS).find({}, {"testName": 1})
    }
    print(f"Catalog: {len(catalog)} objectiveAssessments tests\n")

    data = pool.load()
    total_missing = 0
    total_learned = 0
    for cond, entry in data.get("conditions", {}).items():
        names = entry.get("objectiveTests", []) or []
        missing = [n for n in names if n.strip().lower() not in catalog]
        learned = entry.get("learnedTests", []) or []
        pending = [t for t in learned if t.get("status") != "approved"]
        total_missing += len(missing)
        total_learned += len(learned)
        flag = "⚠️ " if missing else "✅ "
        print(f"{flag}{cond}: {len(names)} objective ({len(missing)} unresolved), "
              f"{len(learned)} learned ({len(pending)} pending review)")
        if missing:
            print(f"     unresolved → {missing}")
        for t in pending:
            print(f"     pending → {t.get('testName')} (muscle={t.get('muscle')}, joint={t.get('joint')})")

    print(f"\nTotals: {total_missing} unresolved objective names, {total_learned} learned tests.")
    if total_missing:
        print("Reconcile unresolved names with getAllTests, or move them to learnedTests.")


if __name__ == "__main__":
    main()
