"""
Tests for the dependency checker.
"""
import unittest
from unittest.mock import Mock
from src.orchestrator.dependency_checker import DependencyChecker


class TestDependencyChecker(unittest.TestCase):
    """Tests for the dependency checker."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.mock_repository = Mock()
        self.checker = DependencyChecker(self.mock_repository)
    
    def test_check_dependencies_no_dependencies(self):
        """Test checking dependencies with no dependencies."""
        question = {}
        completed_questions = ["q1", "q2"]
        form_data = {}
        
        result = self.checker.check_dependencies(question, completed_questions, form_data)
        
        self.assertTrue(result)
    
    def test_check_dependencies_blocked_by(self):
        """Test checking blocked_by dependencies."""
        # Test with dependency satisfied
        question = {
            "dependencies": {
                "blocked_by": ["q1", "q2"]
            }
        }
        completed_questions = ["q1", "q2", "q3"]
        form_data = {}
        
        result = self.checker.check_dependencies(question, completed_questions, form_data)
        self.assertTrue(result)
        
        # Test with dependency not satisfied
        completed_questions = ["q1"]
        result = self.checker.check_dependencies(question, completed_questions, form_data)
        self.assertFalse(result)
    
    def test_check_dependencies_prerequisite_categories(self):
        """Test checking prerequisite categories."""
        # Setup mock questions
        mock_q1 = {"category": "clinical"}
        mock_q2 = {"category": "business"}
        
        self.mock_repository.get_question_by_id.side_effect = lambda qid: {
            "q1": mock_q1,
            "q2": mock_q2
        }.get(qid)
        
        # Test with prerequisite categories satisfied
        question = {
            "dependencies": {
                "prerequisite_categories": ["clinical", "business"]
            }
        }
        completed_questions = ["q1", "q2"]
        form_data = {}
        
        result = self.checker.check_dependencies(question, completed_questions, form_data)
        self.assertTrue(result)
        
        # Test with prerequisite categories not satisfied
        completed_questions = ["q1"]
        result = self.checker.check_dependencies(question, completed_questions, form_data)
        self.assertFalse(result)
    
    def test_check_dependencies_min_questions_before(self):
        """Test checking minimum questions before."""
        # Test with minimum questions satisfied
        question = {
            "dependencies": {
                "min_questions_before": 2
            }
        }
        completed_questions = ["q1", "q2", "q3"]
        form_data = {}
        
        result = self.checker.check_dependencies(question, completed_questions, form_data)
        self.assertTrue(result)
        
        # Test with minimum questions not satisfied
        completed_questions = ["q1"]
        result = self.checker.check_dependencies(question, completed_questions, form_data)
        self.assertFalse(result)
    
    def test_get_completed_categories(self):
        """Test getting completed categories."""
        # Setup mock questions
        mock_q1 = {"category": "clinical"}
        mock_q2 = {"category": "business"}
        mock_q3 = {"category": "clinical"}
        
        self.mock_repository.get_question_by_id.side_effect = lambda qid: {
            "q1": mock_q1,
            "q2": mock_q2,
            "q3": mock_q3
        }.get(qid)
        
        completed_questions = ["q1", "q2", "q3"]
        form_data = {}
        
        result = self.checker._get_completed_categories(completed_questions, form_data)
        
        self.assertEqual(set(result), {"clinical", "business"})
    
    def test_get_dependency_summary(self):
        """Test getting dependency summary."""
        # Test with no dependencies
        dependencies = {}
        summary = self.checker.get_dependency_summary(dependencies)
        self.assertEqual(summary, "No dependencies")
        
        # Test with blocked_by
        dependencies = {"blocked_by": ["q1", "q2"]}
        summary = self.checker.get_dependency_summary(dependencies)
        self.assertIn("Blocked by: q1, q2", summary)
        
        # Test with prerequisite_categories
        dependencies = {"prerequisite_categories": ["clinical", "business"]}
        summary = self.checker.get_dependency_summary(dependencies)
        self.assertIn("Requires categories: clinical, business", summary)
        
        # Test with min_questions_before
        dependencies = {"min_questions_before": 3}
        summary = self.checker.get_dependency_summary(dependencies)
        self.assertIn("Minimum 3 questions before", summary)
        
        # Test with all dependencies
        dependencies = {
            "blocked_by": ["q1"],
            "prerequisite_categories": ["clinical"],
            "min_questions_before": 2
        }
        summary = self.checker.get_dependency_summary(dependencies)
        self.assertIn("Blocked by: q1", summary)
        self.assertIn("Requires categories: clinical", summary)
        self.assertIn("Minimum 2 questions before", summary)


if __name__ == "__main__":
    unittest.main()
