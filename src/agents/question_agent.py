"""
Question agent that manages the interview process with patients.
"""
from typing import Dict, Any, Optional
import json

from src.orchestrator.deterministic_orchestrator import DeterministicOrchestrator
from src.agents.agent_communication import AgentCommunication


class QuestionAgent:
    """Question agent that manages the interview process with patients."""
    
    def __init__(self, orchestrator: DeterministicOrchestrator, 
                 communication_protocol: AgentCommunication):
        """
        Initialize the question agent.
        
        Args:
            orchestrator: The orchestrator
            communication_protocol: The communication protocol
        """
        self.orchestrator = orchestrator
        self.communication_protocol = communication_protocol
        self.current_session_id = None
    
    def start_interview(self, patient_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Start the interview process.
        
        Args:
            patient_data: The patient data
            
        Returns:
            The next question
        """
        self.current_session_id = self.orchestrator.start_session(patient_data)
        return self._get_next_question()
    
    def process_response(self, question_id: str, patient_response: str) -> Dict[str, Any]:
        """
        Process patient's response to a question.
        
        Args:
            question_id: The question ID
            patient_response: The patient's response
            
        Returns:
            The next question or completion message
        """
        if not self.current_session_id:
            return {"error": "No active session"}
        
        # Parse patient response
        try:
            response_data = json.loads(patient_response)
        except:
            response_data = {"raw_response": patient_response}
        
        # Mark question as completed
        success = self.orchestrator.complete_question(
            self.current_session_id, question_id, response_data
        )
        
        if success:
            return self._get_next_question()
        else:
            return {"error": "Failed to process response"}
    
    def _get_next_question(self) -> Dict[str, Any]:
        """
        Get the next question from the orchestrator.
        
        Returns:
            The next question or completion message
        """
        if not self.current_session_id:
            return {"error": "No active session"}
        
        next_question = self.orchestrator.get_next_question(self.current_session_id)
        
        if next_question:
            return {
                "question_id": str(next_question.get("_id")),
                "question_text": next_question.get("question_text"),
                "category": next_question.get("category"),
                "context": next_question.get("context", {})
            }
        else:
            return {"completed": True, "message": "All questions completed"}
    
    def get_session_info(self) -> Optional[Dict[str, Any]]:
        """
        Get session information.
        
        Returns:
            The session information or None if no active session
        """
        if not self.current_session_id:
            return None
        
        return self.orchestrator.get_session_info(self.current_session_id)
