"""
Orchestrator module — initializes and exposes the DeterministicOrchestrator.
"""
from typing import Optional

from src.db.mongodb_client import initialize_mongodb, get_mongodb_client
from src.db.question_pool_repository import QuestionPoolRepository
from src.orchestrator.condition_evaluator import ConditionEvaluator
from src.orchestrator.dependency_checker import DependencyChecker
from src.orchestrator.deterministic_orchestrator import DeterministicOrchestrator
from src.orchestrator.inference_engine import InferenceEngine

_orchestrator: Optional[DeterministicOrchestrator] = None
_question_repo: Optional[QuestionPoolRepository] = None


def initialize_orchestrator(mongo_uri: str, db_name: str):
    """Initialize the global orchestrator using the provided MongoDB connection."""
    global _orchestrator, _question_repo

    initialize_mongodb(mongo_uri, db_name)

    _question_repo = QuestionPoolRepository()
    condition_evaluator = ConditionEvaluator()
    dependency_checker = DependencyChecker(_question_repo)
    inference_engine = InferenceEngine()

    _orchestrator = DeterministicOrchestrator(
        question_repository=_question_repo,
        condition_evaluator=condition_evaluator,
        dependency_checker=dependency_checker,
        agent_communication=None,
        inference_engine=inference_engine,
    )
    print("[orchestrator] DeterministicOrchestrator initialized")


def get_orchestrator() -> Optional[DeterministicOrchestrator]:
    return _orchestrator


def get_question_repository() -> Optional[QuestionPoolRepository]:
    return _question_repo
