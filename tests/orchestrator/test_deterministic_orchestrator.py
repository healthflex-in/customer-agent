"""
Tests for the deterministic orchestrator.
"""
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta

from src.orchestrator.deterministic_orchestrator import DeterministicOrchestrator


class TestDeterministicOrchestrator(unittest.TestCase):
    """Tests for the deterministic orchestrator."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.mock_repository = Mock()
        self.mock_condition_evaluator = Mock()
        self.mock_dependency_checker = Mock()
        self.mock_communication = Mock()
        self.mock_inference_engine = Mock()

        self.orchestrator = DeterministicOrchestrator(
            self.mock_repository,
            self.mock_condition_evaluator,
            self.mock_dependency_checker,
            self.mock_communication,
            self.mock_inference_engine
        )
    
    def test_start_session(self):
        """Test starting a session."""
        patient_data = {"age": 35, "gender": "male"}
        session_id = self.orchestrator.start_session(patient_data)
        
        self.assertIsNotNone(session_id)
        self.assertIn(session_id, self.orchestrator.active_sessions)
        self.assertEqual(self.orchestrator.active_sessions[session_id]["patient_data"], patient_data)
    
    def test_get_next_question_with_candidates(self):
        """Test getting next question with candidates."""
        # Setup
        session_id = self.orchestrator.start_session({"age": 35})
        
        mock_questions = [
            {
                "_id": "1",
                "question_text": "Question 1",
                "category": "clinical",
                "priority": 10,
                "conditions": {},
                "dependencies": {},
                "metadata": {"is_active": True, "created_at": datetime.now().timestamp()}
            },
            {
                "_id": "2",
                "question_text": "Question 2",
                "category": "clinical",
                "priority": 5,
                "conditions": {},
                "dependencies": {},
                "metadata": {"is_active": True, "created_at": datetime.now().timestamp()}
            }
        ]
        
        self.mock_repository.get_all_questions.return_value = mock_questions
        self.mock_condition_evaluator.evaluate_conditions.return_value = True
        self.mock_dependency_checker.check_dependencies.return_value = True
        
        # Test
        next_question = self.orchestrator.get_next_question(session_id)
        
        # Assert
        self.assertIsNotNone(next_question)
        self.assertEqual(next_question["question_text"], "Question 1")  # Higher priority
    
    def test_get_next_question_no_candidates(self):
        """Test getting next question with no candidates."""
        session_id = self.orchestrator.start_session({"age": 35})
        
        self.mock_repository.get_all_questions.return_value = []
        
        next_question = self.orchestrator.get_next_question(session_id)
        
        self.assertIsNone(next_question)
    
    def test_complete_question(self):
        """Test completing a question."""
        # Setup
        session_id = self.orchestrator.start_session({"age": 35})
        mock_question = {
            "_id": "1",
            "question_text": "Test question",
            "category": "clinical",
            "priority": 10,
            "conditions": {},
            "dependencies": {},
            "metadata": {"is_active": True, "created_at": datetime.now().timestamp()}
        }
        
        self.mock_repository.get_all_questions.return_value = [mock_question]
        self.mock_condition_evaluator.evaluate_conditions.return_value = True
        self.mock_dependency_checker.check_dependencies.return_value = True
        
        # Get next question
        next_question = self.orchestrator.get_next_question(session_id)
        self.assertIsNotNone(next_question)
        
        # Complete question
        patient_response = {"answer": "test response"}
        success = self.orchestrator.complete_question(session_id, "1", patient_response)
        
        self.assertTrue(success)
        self.assertEqual(len(self.orchestrator.active_sessions[session_id]["completed_questions"]), 1)
        self.assertEqual(self.orchestrator.active_sessions[session_id]["completed_questions"][0], "1")
        self.assertIsNone(self.orchestrator.active_sessions[session_id]["current_question"])
    
    def test_get_session_info(self):
        """Test getting session info."""
        # Setup
        session_id = self.orchestrator.start_session({"age": 35, "name": "Test Patient"})
        
        # Mock repository for question retrieval
        mock_question = {
            "_id": "1",
            "question_text": "Test question",
            "category": "clinical",
            "priority": 10,
            "conditions": {},
            "dependencies": {},
            "metadata": {"is_active": True, "created_at": datetime.now().timestamp()}
        }
        self.mock_repository.get_all_questions.return_value = [mock_question]
        self.mock_condition_evaluator.evaluate_conditions.return_value = True
        self.mock_dependency_checker.check_dependencies.return_value = True
        
        # Get session info
        session_info = self.orchestrator.get_session_info(session_id)
        
        self.assertIsNotNone(session_info)
        self.assertNotIn("patient_data", session_info)
        self.assertIn("completed_questions", session_info)
        self.assertIn("current_question", session_info)
        self.assertIn("start_time", session_info)
        self.assertIn("form_data", session_info)
    
    def test_cleanup_inactive_sessions(self):
        """Test cleaning up inactive sessions."""
        # Setup
        session_id = self.orchestrator.start_session({"age": 35})
        
        # Mock session start time to be old
        self.orchestrator.active_sessions[session_id]["start_time"] = datetime.now() - timedelta(hours=1)
        
        # Test cleanup with very short timeout
        self.orchestrator.cleanup_inactive_sessions(max_age_hours=0.001)
        
        # Session should be removed
        self.assertNotIn(session_id, self.orchestrator.active_sessions)


if __name__ == "__main__":
    unittest.main()
