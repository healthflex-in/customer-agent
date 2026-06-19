"""
Tests for the question agent.
"""
import unittest
from unittest.mock import Mock, patch
import json

from src.agents.question_agent import QuestionAgent


class TestQuestionAgent(unittest.TestCase):
    """Tests for the question agent."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.mock_orchestrator = Mock()
        self.mock_communication = Mock()
        
        self.agent = QuestionAgent(self.mock_orchestrator, self.mock_communication)
    
    def test_start_interview(self):
        """Test starting an interview."""
        patient_data = {"age": 35, "gender": "male"}
        
        mock_next_question = {
            "question_id": "q1",
            "question_text": "Test question",
            "category": "clinical",
            "context": {}
        }
        
        self.mock_orchestrator.start_session.return_value = "session_123"
        self.mock_orchestrator.get_next_question.return_value = mock_next_question
        
        result = self.agent.start_interview(patient_data)
        
        self.assertEqual(result["question_id"], "q1")
        self.assertEqual(result["question_text"], "Test question")
        self.assertEqual(result["category"], "clinical")
        self.mock_orchestrator.start_session.assert_called_once_with(patient_data)
    
    def test_process_response(self):
        """Test processing a response."""
        # Setup
        self.agent.current_session_id = "session_123"
        
        mock_next_question = {
            "question_id": "q2",
            "question_text": "Next question",
            "category": "business",
            "context": {}
        }
        
        self.mock_orchestrator.complete_question.return_value = True
        self.mock_orchestrator.get_next_question.return_value = mock_next_question
        
        # Test
        patient_response = '{"answer": "test response"}'
        result = self.agent.process_response("q1", patient_response)
        
        # Assert
        self.assertEqual(result["question_id"], "q2")
        self.assertEqual(result["question_text"], "Next question")
        self.mock_orchestrator.complete_question.assert_called_once_with(
            "session_123", "q1", {"answer": "test response"}
        )
    
    def test_process_response_no_session(self):
        """Test processing a response without an active session."""
        result = self.agent.process_response("q1", '{"answer": "test"}')
        
        self.assertIn("error", result)
        self.assertEqual(result["error"], "No active session")
    
    def test_process_response_invalid_json(self):
        """Test processing a response with invalid JSON."""
        # Setup
        self.agent.current_session_id = "session_123"
        
        mock_next_question = {
            "question_id": "q2",
            "question_text": "Next question",
            "category": "business",
            "context": {}
        }
        
        self.mock_orchestrator.complete_question.return_value = True
        self.mock_orchestrator.get_next_question.return_value = mock_next_question
        
        # Test with invalid JSON
        patient_response = 'invalid json'
        result = self.agent.process_response("q1", patient_response)
        
        # Assert
        self.assertEqual(result["question_id"], "q2")
        self.mock_orchestrator.complete_question.assert_called_once_with(
            "session_123", "q1", {"raw_response": "invalid json"}
        )
    
    def test_process_response_completion(self):
        """Test processing a response that completes the interview."""
        # Setup
        self.agent.current_session_id = "session_123"
        
        self.mock_orchestrator.complete_question.return_value = True
        self.mock_orchestrator.get_next_question.return_value = {
            "completed": True,
            "message": "Interview completed"
        }
        
        # Test
        patient_response = '{"answer": "test"}'
        result = self.agent.process_response("q1", patient_response)
        
        # Assert
        self.assertTrue(result["completed"])
        self.assertEqual(result["message"], "Interview completed")
    
    def test_get_next_question(self):
        """Test getting the next question."""
        # Setup
        self.agent.current_session_id = "session_123"
        
        mock_next_question = {
            "_id": "1",
            "question_text": "Test question",
            "category": "clinical",
            "context": {"section": "Present Complaint"}
        }
        
        self.mock_orchestrator.get_next_question.return_value = mock_next_question
        
        # Test
        result = self.agent._get_next_question()
        
        # Assert
        self.assertEqual(result["question_id"], "1")
        self.assertEqual(result["question_text"], "Test question")
        self.assertEqual(result["category"], "clinical")
        self.assertEqual(result["context"], {"section": "Present Complaint"})
    
    def test_get_next_question_no_session(self):
        """Test getting the next question without an active session."""
        result = self.agent._get_next_question()
        
        self.assertIn("error", result)
        self.assertEqual(result["error"], "No active session")
    
    def test_get_next_question_no_questions(self):
        """Test getting the next question when no questions are available."""
        # Setup
        self.agent.current_session_id = "session_123"
        
        self.mock_orchestrator.get_next_question.return_value = None
        
        # Test
        result = self.agent._get_next_question()
        
        # Assert
        self.assertTrue(result["completed"])
        self.assertEqual(result["message"], "All questions completed")
    
    def test_get_session_info(self):
        """Test getting session info."""
        # Setup
        self.agent.current_session_id = "session_123"
        
        mock_session_info = {
            "completed_questions": ["q1", "q2"],
            "current_question": None,
            "start_time": "2024-01-01T00:00:00Z",
            "form_data": {}
        }
        
        self.mock_orchestrator.get_session_info.return_value = mock_session_info
        
        # Test
        result = self.agent.get_session_info()
        
        # Assert
        self.assertEqual(result, mock_session_info)
        self.mock_orchestrator.get_session_info.assert_called_once_with("session_123")
    
    def test_get_session_info_no_session(self):
        """Test getting session info without an active session."""
        result = self.agent.get_session_info()
        
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
