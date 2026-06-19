"""
Main orchestrator module that integrates all components.
"""
from typing import Dict, Any, Optional
from datetime import datetime
import uuid

from src.db.mongodb_client import get_mongodb_client
from src.db.question_pool_repository import QuestionPoolRepository
from src.orchestrator.condition_evaluator import ConditionEvaluator
from src.orchestrator.dependency_checker import DependencyChecker
from src.orchestrator.deterministic_orchestrator import DeterministicOrchestrator
from src.agents.agent_communication import AgentCommunication
from src.agents.question_agent import QuestionAgent


class QuestionPoolOrchestrator:
    """Main orchestrator that integrates all components."""
    
    def __init__(self, mongo_uri: str, db_name: str):
        """
        Initialize the orchestrator.
        
        Args:
            mongo_uri: MongoDB connection URI
            db_name: Database name
        """
        # Initialize MongoDB client
        from src.db.mongodb_client import initialize_mongodb
        initialize_mongodb(mongo_uri, db_name)
        
        # Initialize components
        self.mongodb_client = get_mongodb_client()
        self.question_repository = QuestionPoolRepository()
        self.condition_evaluator = ConditionEvaluator()
        self.dependency_checker = DependencyChecker(self.question_repository)
        self.agent_communication = AgentCommunication()
        
        # Initialize orchestrator
        self.orchestrator = DeterministicOrchestrator(
            self.question_repository,
            self.condition_evaluator,
            self.dependency_checker,
            self.agent_communication
        )
        
        # Initialize question agent
        self.question_agent = QuestionAgent(self.orchestrator, self.agent_communication)
    
    def start_interview(self, patient_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Start an interview with a patient.
        
        Args:
            patient_data: The patient data
            
        Returns:
            The first question
        """
        return self.question_agent.start_interview(patient_data)
    
    def process_response(self, question_id: str, patient_response: str) -> Dict[str, Any]:
        """
        Process a patient response.
        
        Args:
            question_id: The question ID
            patient_response: The patient response
            
        Returns:
            The next question or completion message
        """
        return self.question_agent.process_response(question_id, patient_response)
    
    def get_session_info(self) -> Optional[Dict[str, Any]]:
        """
        Get session information.
        
        Returns:
            The session information or None if no active session
        """
        return self.question_agent.get_session_info()
    
    def add_question(self, question_data: Dict[str, Any]) -> str:
        """
        Add a new question to the pool.
        
        Args:
            question_data: The question data
            
        Returns:
            The ID of the added question
        """
        return self.question_repository.insert_question(question_data)
    
    def get_all_questions(self, is_active: bool = True) -> List[Dict[str, Any]]:
        """
        Get all questions.
        
        Args:
            is_active: Whether to include only active questions
            
        Returns:
            List of questions
        """
        return self.question_repository.get_all_questions(is_active)
    
    def get_questions_by_category(self, category: str, is_active: bool = True) -> List[Dict[str, Any]]:
        """
        Get questions by category.
        
        Args:
            category: The category
            is_active: Whether to include only active questions
            
        Returns:
            List of questions
        """
        return self.question_repository.get_questions_by_category(category, is_active)
    
    def get_questions_by_section(self, section: str, is_active: bool = True) -> List[Dict[str, Any]]:
        """
        Get questions by section.
        
        Args:
            section: The section
            is_active: Whether to include only active questions
            
        Returns:
            List of questions
        """
        return self.question_repository.get_questions_by_section(section, is_active)


# Global instance for easy access
_orchestrator_instance = None


def get_orchestrator() -> QuestionPoolOrchestrator:
    """
    Get or create the global orchestrator instance.
    
    Returns:
        The orchestrator instance
    """
    global _orchestrator_instance
    if _orchestrator_instance is None:
        raise RuntimeError("Orchestrator not initialized. Call initialize_orchestrator() first.")
    return _orchestrator_instance


def initialize_orchestrator(mongo_uri: str, db_name: str):
    """
    Initialize the global orchestrator instance.
    
    Args:
        mongo_uri: MongoDB connection URI
        db_name: Database name
    """
    global _orchestrator_instance
    _orchestrator_instance = QuestionPoolOrchestrator(mongo_uri, db_name)
