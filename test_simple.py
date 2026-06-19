#!/usr/bin/env python3
"""
Simple test to verify the Question Pool Management System.
"""
import sys
import os

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.orchestrator.condition_evaluator import ConditionEvaluator
from src.orchestrator.dependency_checker import DependencyChecker
from src.orchestrator.deterministic_orchestrator import DeterministicOrchestrator
from src.agents.agent_communication import AgentCommunication
from src.agents.question_agent import QuestionAgent


def test_condition_evaluator():
    """Test the condition evaluator."""
    print("Testing condition evaluator...")
    
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
    
    print("✓ Condition evaluator test passed")
    return True


def test_dependency_checker():
    """Test the dependency checker."""
    print("Testing dependency checker...")
    
    # Mock repository
    mock_repository = type('MockRepository', (), {})()
    mock_repository.get_question_by_id = lambda qid: {
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
    
    print("✓ Dependency checker test passed")
    return True


def test_deterministic_orchestrator():
    """Test the deterministic orchestrator."""
    print("Testing deterministic orchestrator...")
    
    # Mock repository
    mock_repository = type('MockRepository', (), {})()
    mock_repository.get_all_questions = lambda: []
    
    # Mock condition evaluator
    mock_condition_evaluator = type('MockConditionEvaluator', (), {})()
    mock_condition_evaluator.evaluate_conditions = lambda conditions, patient_data, form_data: True
    
    # Mock dependency checker
    mock_dependency_checker = type('MockDependencyChecker', (), {})()
    mock_dependency_checker.check_dependencies = lambda question, completed_questions, form_data: True
    
    # Mock communication
    mock_communication = type('MockCommunication', (), {})()
    
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
    next_question = orchestrator.get_next_question(session_id)
    assert next_question is None, "Expected None for no candidates"
    
    # Test getting session info
    session_info = orchestrator.get_session_info(session_id)
    assert session_info is not None, "Expected session info"
    assert "patient_data" not in session_info, "Expected patient data not in session info"
    
    print("✓ Deterministic orchestrator test passed")
    return True


def test_question_agent():
    """Test the question agent."""
    print("Testing question agent...")
    
    # Mock orchestrator and communication
    mock_orchestrator = type('MockOrchestrator', (), {})()
    mock_communication = type('MockCommunication', (), {})()
    
    # Add required methods
    mock_orchestrator.start_session = lambda patient_data: "session_123"
    mock_orchestrator.get_next_question = lambda session_id: {
        "_id": "1",
        "question_text": "Test question",
        "category": "clinical",
        "context": {}
    }
    mock_orchestrator.complete_question = lambda session_id, question_id, response: True
    
    # Create agent
    agent = QuestionAgent(mock_orchestrator, mock_communication)
    
    # Test starting interview
    patient_data = {"age": 35, "gender": "male"}
    
    result = agent.start_interview(patient_data)
    
    # Check the result
    if result["question_id"] == "1" and result["question_text"] == "Test question":
        print("✓ Question agent test passed")
        return True
    else:
        print(f"✗ Question agent test failed: Expected question ID 1, got {result.get('question_id')}")
        return False


def main():
    """Run all tests."""
    print("=" * 60)
    print("Question Pool Management System - Simple Test Suite")
    print("=" * 60)
    print()
    
    tests = [
        ("Condition Evaluator", test_condition_evaluator),
        ("Dependency Checker", test_dependency_checker),
        ("Deterministic Orchestrator", test_deterministic_orchestrator),
        ("Question Agent", test_question_agent),
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
            print(f"✗ {test_name} test failed with exception: {e}")
            failed += 1
        print()
    
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
    sys.exit(main())
