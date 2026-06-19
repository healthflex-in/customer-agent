"""
Integration tests for the orchestrator system.
"""
import unittest
from unittest.mock import Mock, patch

from src.agents.question_agent import QuestionAgent


class TestOrchestratorIntegration(unittest.TestCase):
    """Integration tests for the orchestrator system."""
    
    def test_full_interview_flow(self):
        """Test the full interview flow."""
        # Mock all dependencies
        with patch('src.agents.question_agent.DeterministicOrchestrator') as mock_orchestrator_class:
            with patch('src.agents.question_agent.AgentCommunication') as mock_communication_class:
                # Setup mocks
                mock_orchestrator_instance = Mock()
                mock_orchestrator_class.return_value = mock_orchestrator_instance
                
                mock_communication_instance = Mock()
                mock_communication_class.return_value = mock_communication_instance
                
                # Import and initialize agent
                from src.agents.question_agent import QuestionAgent
                agent = QuestionAgent(mock_orchestrator_instance, mock_communication_instance)
                
                # Start interview
                patient_data = {"age": 35, "gender": "male"}
                mock_orchestrator_instance.start_session.return_value = "session_123"
                mock_orchestrator_instance.get_next_question.return_value = {
                    "question_id": "q1",
                    "question_text": "Test question",
                    "category": "clinical"
                }
                
                result = agent.start_interview(patient_data)
                
                self.assertEqual(result["question_id"], "q1")
                self.assertEqual(result["question_text"], "Test question")
                
                # Process response
                mock_orchestrator_instance.complete_question.return_value = True
                mock_orchestrator_instance.get_next_question.return_value = {
                    "completed": True,
                    "message": "Interview completed"
                }
                
                response_result = agent.process_response("q1", '{"answer": "test response"}')
                
                self.assertTrue(response_result["completed"])
                self.assertEqual(response_result["message"], "Interview completed")
    
    def test_interview_with_conditions(self):
        """Test interview with question conditions."""
        # Mock all dependencies
        with patch('src.agents.question_agent.DeterministicOrchestrator') as mock_orchestrator_class:
            with patch('src.agents.question_agent.AgentCommunication') as mock_communication_class:
                # Setup mocks
                mock_orchestrator_instance = Mock()
                mock_orchestrator_class.return_value = mock_orchestrator_instance
                
                mock_communication_instance = Mock()
                mock_communication_class.return_value = mock_communication_instance
                
                # Import and initialize agent
                from src.agents.question_agent import QuestionAgent
                agent = QuestionAgent(mock_orchestrator_instance, mock_communication_instance)
                
                # Start interview
                patient_data = {"age": 35, "gender": "male", "medical_conditions": ["diabetes"]}
                mock_orchestrator_instance.start_session.return_value = "session_123"
                
                # First question - clinical
                mock_orchestrator_instance.get_next_question.side_effect = [
                    {
                        "question_id": "q1",
                        "question_text": "Clinical question",
                        "category": "clinical"
                    },
                    {
                        "question_id": "q2",
                        "question_text": "Business question",
                        "category": "business"
                    },
                    {
                        "completed": True,
                        "message": "Interview completed"
                    }
                ]
                
                # Process first response
                result1 = agent.start_interview(patient_data)
                self.assertEqual(result1["question_id"], "q1")
                
                # Process first response
                result2 = agent.process_response("q1", '{"answer": "test"}')
                self.assertEqual(result2["question_id"], "q2")
                
                # Process second response
                result3 = agent.process_response("q2", '{"answer": "test"}')
                self.assertTrue(result3["completed"])
    
    def test_interview_with_dependencies(self):
        """Test interview with question dependencies."""
        # Mock all dependencies
        with patch('src.agents.question_agent.DeterministicOrchestrator') as mock_orchestrator_class:
            with patch('src.agents.question_agent.AgentCommunication') as mock_communication_class:
                # Setup mocks
                mock_orchestrator_instance = Mock()
                mock_orchestrator_class.return_value = mock_orchestrator_instance
                
                mock_communication_instance = Mock()
                mock_communication_class.return_value = mock_communication_instance
                
                # Import and initialize agent
                from src.agents.question_agent import QuestionAgent
                agent = QuestionAgent(mock_orchestrator_instance, mock_communication_instance)
                
                # Start interview
                patient_data = {"age": 35, "gender": "male"}
                mock_orchestrator_instance.start_session.return_value = "session_123"
                
                # Mock questions with dependencies
                mock_orchestrator_instance.get_next_question.side_effect = [
                    {
                        "question_id": "q1",
                        "question_text": "First question",
                        "category": "clinical"
                    },
                    {
                        "question_id": "q2",
                        "question_text": "Second question (requires q1)",
                        "category": "business"
                    },
                    {
                        "completed": True,
                        "message": "Interview completed"
                    }
                ]
                
                # Start interview
                result1 = agent.start_interview(patient_data)
                self.assertEqual(result1["question_id"], "q1")
                
                # Process first response
                result2 = agent.process_response("q1", '{"answer": "test"}')
                self.assertEqual(result2["question_id"], "q2")
                
                # Process second response
                result3 = agent.process_response("q2", '{"answer": "test"}')
                self.assertTrue(result3["completed"])


if __name__ == "__main__":
    unittest.main()
