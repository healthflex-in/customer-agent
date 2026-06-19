#!/usr/bin/env python3
"""
Test script for the Question Pool Management System.
"""
import sys
import os
import json
from datetime import datetime

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.db.mongodb_client import initialize_mongodb, get_mongodb_client
from src.orchestrator.condition_evaluator import ConditionEvaluator
from src.orchestrator.dependency_checker import DependencyChecker
from src.orchestrator.deterministic_orchestrator import DeterministicOrchestrator
from src.agents.agent_communication import AgentCommunication
from src.agents.question_agent import QuestionAgent


def test_mongodb_client():
    """Test the MongoDB client."""
    print("Testing MongoDB client...")
    
    # Initialize MongoDB client
    mongo_uri = "mongodb://localhost:27017"
    db_name = "test_healthflex"
    
    try:
        initialize_mongodb(mongo_uri, db_name)
        mongodb_client = get_mongodb_client()
        
        # Test question pool initialization
        mongodb_client.initialize_question_pool()
        
        # Test getting questions
        questions = mongodb_client.questions_collection.find().to_list(length=10)
        print(f"Found {len(questions)} questions in the pool")
        
        if questions:
            print("Sample question:")
            print(json.dumps(questions[0], default=str, indent=2))
        
        print("✓ MongoDB client test passed\n")
        return True
    except Exception as e:
        print(f"✗ MongoDB client test failed: {e}\n")
        return False


def test_condition_evaluator():
    """Test the condition evaluator."""
    print("Testing condition evaluator...")
    
    try:
        evaluator = ConditionEvaluator()
        
        # Test with no conditions
        conditions = {}
        patient_data = {"age": 35}
        form_data = {}
        
        result = evaluator.evaluate_conditions(conditions, patient_data, form_data)
        assert result == True, "Expected True for no conditions"
        
        # Test with age conditions
        conditions = {"min_age": 18}
        result = evaluator.evaluate_conditions(conditions, patient_data, form_data)
        assert result == True, "Expected True for age >= 18"
        
        conditions = {"min_age": 40}
        result = evaluator.evaluate_conditions(conditions, patient_data, form_data)
        assert result == False, "Expected False for age < 40"
        
        # Test condition summary
        summary = evaluator.get_condition_summary(conditions)
        assert "Age >= 40" in summary, "Expected age condition in summary"
        
        print("✓ Condition evaluator test passed\n")
        return True
    except Exception as e:
        print(f"✗ Condition evaluator test failed: {e}\n")
        return False


def test_dependency_checker():
    """Test the dependency checker."""
    print("Testing dependency checker...")
    
    try:
        # Mock repository
        mock_repository = Mock()
        mock_repository.get_question_by_id.side_effect = lambda qid: {
            "q1": {"category": "clinical"},
            "q2": {"category": "business"},
            "q3": {"category": "clinical"}
        }.get(qid)
        
        checker = DependencyChecker(mock_repository)
        
        # Test with no dependencies
        question = {}
        completed_questions = ["q1", "q2"]
        form_data = {}
        
        result = checker.check_dependencies(question, completed_questions, form_data)
        assert result == True, "Expected True for no dependencies"
        
        # Test with blocked_by dependencies
        question = {"dependencies": {"blocked_by": ["q1"]}}
        result = checker.check_dependencies(question, ["q2"], form_data)
        assert result == False, "Expected False for blocked_by not satisfied"
        
        result = checker.check_dependencies(question, ["q1", "q2"], form_data)
        assert result == True, "Expected True for blocked_by satisfied"
        
        # Test with prerequisite_categories
        question = {"dependencies": {"prerequisite_categories": ["clinical"]}}
        result = checker.check_dependencies(question, ["q1", "q2"], form_data)
        assert result == True, "Expected True for prerequisite_categories satisfied"
        
        result = checker.check_dependencies(question, ["q2"], form_data)
        assert result == False, "Expected False for prerequisite_categories not satisfied"
        
        # Test with min_questions_before
        question = {"dependencies": {"min_questions_before": 2}}
        result = checker.check_dependencies(question, ["q1"], form_data)
        assert result == False, "Expected False for min_questions_before not satisfied"
        
        result = checker.check_dependencies(question, ["q1", "q2"], form_data)
        assert result == True, "Expected True for min_questions_before satisfied"
        
        # Test dependency summary
        dependencies = {"blocked_by": ["q1"], "prerequisite_categories": ["clinical"]}
        summary = checker.get_dependency_summary(dependencies)
        assert "Blocked by: q1" in summary, "Expected blocked_by in summary"
        assert "Requires categories: clinical" in summary, "Expected prerequisite_categories in summary"
        
        print("✓ Dependency checker test passed\n")
        return True
    except Exception as e:
        print(f"✗ Dependency checker test failed: {e}\n")
        return False


def test_deterministic_orchestrator():
    """Test the deterministic orchestrator."""
    print("Testing deterministic orchestrator...")
    
    try:
        # Mock repository
        mock_repository = Mock()
        mock_condition_evaluator = Mock()
        mock_dependency_checker = Mock()
        mock_communication = Mock()
        
        # Create orchestrator
        orchestrator = DeterministicOrchestrator(
            mock_repository,
            mock_condition_evaluator,
            mock_dependency_checker,
            mock_communication
        )
        
        # Test starting a session
        patient_data = {"age": 35, "gender": "male"}
        session_id = orchestrator.start_session(patient_data)
        
        assert session_id is not None, "Expected session ID"
        assert session_id in orchestrator.active_sessions, "Expected session in active sessions"
        assert orchestrator.active_sessions[session_id]["patient_data"] == patient_data, "Expected patient data in session"
        
        # Test getting next question with no candidates
        mock_repository.get_all_questions.return_value = []
        next_question = orchestrator.get_next_question(session_id)
        assert next_question is None, "Expected None for no candidates"
        
        # Test getting session info
        session_info = orchestrator.get_session_info(session_id)
        assert session_info is not None, "Expected session info"
        assert "patient_data" not in session_info, "Expected patient data not in session info"
        
        print("✓ Deterministic orchestrator test passed\n")
        return True
    except Exception as e:
        print(f"✗ Deterministic orchestrator test failed: {e}\n")
        return False


def test_question_agent():
    """Test the question agent."""
    print("Testing question agent...")
    
    try:
        # Mock orchestrator and communication
        mock_orchestrator = Mock()
        mock_communication = Mock()
        
        # Create agent
        agent = QuestionAgent(mock_orchestrator, mock_communication)
        
        # Test starting interview
        patient_data = {"age": 35, "gender": "male"}
        mock_orchestrator.start_session.return_value = "session_123"
        mock_orchestrator.get_next_question.return_value = {
            "question_id": "q1",
            "question_text": "Test question",
            "category": "clinical",
            "context": {}
        }
        
        result = agent.start_interview(patient_data)
        
        assert result["question_id"] == "q1", "Expected question ID q1"
        assert result["question_text"] == "Test question", "Expected question text"
        assert result["category"] == "clinical", "Expected category clinical"
        
        # Test processing response
        mock_orchestrator.complete_question.return_value = True
        mock_orchestrator.get_next_question.return_value = {
            "completed": True,
            "message": "Interview completed"
        }
        
        response_result = agent.process_response("q1", '{"answer": "test response"}')
        
        assert response_result["completed"] == True, "Expected completed"
        assert response_result["message"] == "Interview completed", "Expected completion message"
        
        print("✓ Question agent test passed\n")
        return True
    except Exception as e:
        print(f"✗ Question agent test failed: {e}\n")
        return False


def test_integration():
    """Test the full integration."""
    print("Testing full integration...")
    
    try:
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
                
                assert result["question_id"] == "q1"
                assert result["question_text"] == "Test question"
                
                # Process response
                mock_orchestrator_instance.complete_question.return_value = True
                mock_orchestrator_instance.get_next_question.return_value = {
                    "completed": True,
                    "message": "Interview completed"
                }
                
                response_result = agent.process_response("q1", '{"answer": "test response"}')
                
                assert response_result["completed"] == True
                assert response_result["message"] == "Interview completed"
        
        print("✓ Integration test passed\n")
        return True
    except Exception as e:
        print(f"✗ Integration test failed: {e}\n")
        return False


def main():
    """Run all tests."""
    print("=" * 60)
    print("Question Pool Management System - Test Suite")
    print("=" * 60)
    print()
    
    tests = [
        ("MongoDB Client", test_mongodb_client),
        ("Condition Evaluator", test_condition_evaluator),
        ("Dependency Checker", test_dependency_checker),
        ("Deterministic Orchestrator", test_deterministic_orchestrator),
        ("Question Agent", test_question_agent),
        ("Integration", test_integration),
    ]
    
    passed = 0
    failed = 0
    
    for test_name, test_func in tests:
        print(f"Running {test_name} test...")
        try:
            if test_func():
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"✗ {test_name} test failed with exception: {e}\n")
            failed += 1
    
    print("=" * 60)
    print(f"Test Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    if failed == 0:
        print("All tests passed! ✓")
        return 0
    else:
        print("Some tests failed! ✗")
        return 1


if __name__ == "__main__":
    # Mock the Mock class for testing
    from unittest.mock import Mock
    
    sys.exit(main())
