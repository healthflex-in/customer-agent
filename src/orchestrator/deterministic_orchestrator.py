"""
Deterministic orchestrator for question selection.
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
import uuid
import json

from src.db.mongodb_client import get_mongodb_client
from src.orchestrator.condition_evaluator import ConditionEvaluator
from src.orchestrator.dependency_checker import DependencyChecker


class DeterministicOrchestrator:
    """Deterministic orchestrator for question selection."""
    
    def __init__(self, question_repository, condition_evaluator,
                 dependency_checker, agent_communication, inference_engine):
        """
        Initialize the orchestrator.

        Args:
            question_repository: The question repository
            condition_evaluator: The condition evaluator
            dependency_checker: The dependency checker
            agent_communication: The agent communication module
            inference_engine: The inference engine module
        """
        self.question_repository = question_repository
        self.condition_evaluator = condition_evaluator
        self.dependency_checker = dependency_checker
        self.agent_communication = agent_communication
        self.inference_engine = inference_engine
        self.active_sessions = {}
    
    def start_session(self, patient_data: Dict[str, Any]) -> str:
        """
        Start a new interview session.
        
        Args:
            patient_data: The patient data
            
        Returns:
            The session ID
        """
        session_id = str(uuid.uuid4())
        self.active_sessions[session_id] = {
            "patient_data": patient_data,
            "completed_questions": [],
            "current_question": None,
            "start_time": datetime.now(),
            "form_data": {},
            "health_state": {}
        }
        return session_id
    
    def get_next_question(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Get the next question for a session.
        
        Args:
            session_id: The session ID
            
        Returns:
            The next question or None if no questions available
        """
        if session_id not in self.active_sessions:
            return None
        
        session = self.active_sessions[session_id]
        patient_data = session["patient_data"]
        completed_questions = session["completed_questions"]
        form_data = session["form_data"]
        
        # Get all available questions
        all_questions = self.question_repository.get_all_questions()
        
        # Filter and select next question
        candidate_questions = []
        for question in all_questions:
            # Check if question is active
            if not question.get("metadata", {}).get("is_active", True):
                continue
            
            # Check conditions
            if not self.condition_evaluator.evaluate_conditions(
                question.get("conditions", {}), patient_data, form_data, session["health_state"]
            ):
                continue
            
            # Check dependencies
            if not self.dependency_checker.check_dependencies(
                question, completed_questions, form_data
            ):
                continue
            
            candidate_questions.append(question)
        
        # Sort by priority (higher first) and recency
        candidate_questions.sort(key=lambda q: (
            q.get("priority", 0),
            -datetime.fromtimestamp(q.get("metadata", {}).get("created_at", 0)).timestamp()
        ), reverse=True)
        
        if candidate_questions:
            next_question = candidate_questions[0]
            session["current_question"] = next_question
            return next_question
        
        return None
    
    def complete_question(self, session_id: str, question_id: str, 
                         patient_response: Dict[str, Any]) -> bool:
        """
        Mark a question as completed and update form data.
        
        Args:
            session_id: The session ID
            question_id: The question ID
            patient_response: The patient response
            
        Returns:
            True if the question was completed, False otherwise
        """
        if session_id not in self.active_sessions:
            return False
        
        session = self.active_sessions[session_id]
        session["completed_questions"].append(question_id)
        
        # Update form data with patient response
        self._update_form_data(session["form_data"], patient_response)

        # Update health state
        session["health_state"] = self.inference_engine.infer_health_state(
            session["patient_data"], session["form_data"]
        )

        # Clear current question
        session["current_question"] = None
        
        return True
    
    def _update_form_data(self, form_data: Dict[str, Any], 
                         patient_response: Dict[str, Any]):
        """
        Update form data with patient response.
        
        Args:
            form_data: The form data to update
            patient_response: The patient response
        """
        # This would be customized based on the specific question and response
        # For now, it's a placeholder
        pass
    
    def get_session_info(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        Get session information.
        
        Args:
            session_id: The session ID
            
        Returns:
            The session information or None if session not found
        """
        if session_id not in self.active_sessions:
            return None
        
        session = self.active_sessions[session_id].copy()
        
        # Remove sensitive data
        session.pop("patient_data", None)
        
        return session
    
    def cleanup_inactive_sessions(self, max_age_hours: int = 24):
        """
        Clean up inactive sessions.
        
        Args:
            max_age_hours: Maximum age of sessions to keep
        """
        current_time = datetime.now()
        sessions_to_remove = []
        
        for session_id, session in self.active_sessions.items():
            session_age = (current_time - session["start_time"]).total_seconds() / 3600
            if session_age > max_age_hours:
                sessions_to_remove.append(session_id)
        
        for session_id in sessions_to_remove:
            del self.active_sessions[session_id]
        
        if sessions_to_remove:
            print(f"Cleaned up {len(sessions_to_remove)} inactive sessions")
