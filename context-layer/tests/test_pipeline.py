"""Deterministic pipeline tests — no MongoDB / no LLM required."""

from app.pipeline import condition_classifier as cc
from app.pipeline import candidate_retriever as cr
from app.pipeline.models import Candidate
from app.pipeline import ranker


def test_classify_uses_category_first():
    conds = cc.classify({"Present Complaint": {"Primary Complaint": "shoulder"}}, category="knee_pain")
    assert conds[0].label == "knee_pain"
    assert conds[0].source == "category"


def test_classify_from_keywords():
    form = {"Present Complaint": {"Primary Complaint": "Pain in the right knee for 3 days"}}
    conds = cc.classify(form, category=None)
    assert conds[0].label == "knee_pain"
    assert conds[0].source == "keyword"


def test_classify_unknown():
    conds = cc.classify({"Present Complaint": {"Primary Complaint": "feeling generally unwell"}})
    assert conds[0].label == "unknown"


def test_detect_patient_flags():
    assert "acute_fracture_suspected" in cr.detect_patient_flags("possible fracture of the tibia")
    assert cr.detect_patient_flags("mild soreness") == set()


def test_ranker_deterministic_order_by_priority(monkeypatch):
    from app import config
    monkeypatch.setattr(config, "USE_LLM_RANKER", False)
    cands = [
        Candidate("1", "Low", "d", "", "", "MANUAL", "", priority=3),
        Candidate("2", "High", "d", "", "", "MANUAL", "", priority=9),
        Candidate("3", "Mid", "d", "", "", "MANUAL", "", priority=6),
    ]
    ranked = ranker.rank(cands, "conditions: knee_pain")
    assert [r.candidate.test_name for r in ranked] == ["High", "Mid", "Low"]
    assert ranked[0].rank == 1
    assert ranked[0].confidence == 0.9


def test_pool_learns_new_test(tmp_path, monkeypatch):
    """The pool grows: a proposed test is appended and deduped."""
    from app import config, pool

    pool_file = tmp_path / "tests_pool.json"
    pool_file.write_text('{"version": 1, "conditions": {}}')
    monkeypatch.setattr(config, "POOL_FILE", str(pool_file))

    added = pool.add_learned_test("knee_pain", test_name="Ober's Test",
                                  muscle="ITB", joint="knee", rationale="lateral knee pain")
    assert added is True
    again = pool.add_learned_test("knee_pain", test_name="ober's test")  # dedupe (case-insensitive)
    assert again is False

    entry = pool.get_condition("knee_pain")
    assert entry["learnedTests"][0]["testName"] == "Ober's Test"
    assert entry["learnedTests"][0]["status"] == "proposed"
    assert entry["learnedTests"][0]["joint"] == "knee"


def test_config_llm_defaults_are_always_true():
    """Verify that USE_LLM and USE_LLM_RANKER are hardcoded to True as per requirements."""
    from app import config
    assert config.USE_LLM is True
    assert config.USE_LLM_RANKER is True


def test_load_intake_returns_empty_when_appointment_predates_intake(monkeypatch):
    from datetime import datetime
    from app import db, service

    # Mock the database collections
    class MockCollection:
        def __init__(self, name):
            self.name = name

        def find(self, query, *args, **kwargs):
            if self.name == "customer-info":
                # An intake filled on July 7th, 2026
                return [
                    {
                        "userId": "user123",
                        "createdAt": datetime(2026, 7, 7, 10, 0, 0),
                        "category": "knee_pain",
                        "form_data": {
                            "Present Complaint": {
                                "Primary Complaint": "Knee pain from running"
                            }
                        }
                    }
                ]
            return []

        def find_one(self, query, *args, **kwargs):
            if self.name == "appointments":
                # An appointment on July 6th, 2026 (which predates the July 7th intake)
                return {
                    "_id": "appt123",
                    "appointmentStartTime": datetime(2026, 7, 6, 9, 0, 0)
                }
            return None

    def mock_coll(name):
        return MockCollection(name)

    monkeypatch.setattr(db, "coll", mock_coll)

    # Call _load_intake with appointment_id
    form_data, category = service._load_intake("user123", "appt123")

    # The appointment (July 6th) predates the only intake (July 7th), so nothing
    # applies — no back-filling a past appointment with a newer intake.
    assert category is None
    assert form_data == {}


def test_load_intake_forward_fills_previous_intake_for_later_appointment(monkeypatch):
    from datetime import datetime
    from app import db, service

    class MockCollection:
        def __init__(self, name):
            self.name = name

        def find(self, query, *args, **kwargs):
            if self.name == "customer-info":
                # A single intake collected on Aug 12th, 2026 (no appointmentId).
                return [
                    {
                        "userId": "user123",
                        "createdAt": datetime(2026, 8, 12, 10, 0, 0),
                        "category": "knee_pain",
                        "form_data": {
                            "Present Complaint": {
                                "Primary Complaint": "Knee pain from running"
                            }
                        },
                    }
                ]
            return []

        def find_one(self, query, *args, **kwargs):
            if self.name == "appointments":
                # A later appointment on Aug 15th with no intake of its own.
                return {
                    "_id": "appt123",
                    "appointmentStartTime": datetime(2026, 8, 15, 9, 0, 0),
                }
            return None

    monkeypatch.setattr(db, "coll", lambda name: MockCollection(name))

    form_data, category = service._load_intake("user123", "appt123")

    # The Aug 15th appointment has no intake of its own, so it carries forward the
    # most recent intake dated on/before it (the Aug 12th one).
    assert category == "knee_pain"
    assert form_data["Present Complaint"]["Primary Complaint"] == "Knee pain from running"
