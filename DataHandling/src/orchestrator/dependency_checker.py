"""
Checks dependencies for question selection.
"""
from typing import List, Dict, Any
from bson import ObjectId


class DependencyChecker:
    """Checks dependencies for question selection."""
    
    def __init__(self, question_repository):
        """
        Initialize the dependency checker.
        
        Args:
            question_repository: The question repository
        """
        self.question_repository = question_repository
    
    def check_dependencies(self, question: Dict[str, Any], 
                          completed_questions: List[str],
                          form_data: Dict[str, Any]) -> bool:
        """
        Check if all dependencies are satisfied.
        
        Args:
            question: The question to check
            completed_questions: List of completed question IDs
            form_data: The form data
            
        Returns:
            True if all dependencies are satisfied, False otherwise
        """
        dependencies = question.get("dependencies", {})
        
        # Check blocked_by dependencies
        if "blocked_by" in dependencies:
            blocked_by = dependencies["blocked_by"]
            for blocked_id in blocked_by:
                if blocked_id not in completed_questions:
                    return False
        
        # Check prerequisite categories
        if "prerequisite_categories" in dependencies:
            required_categories = dependencies["prerequisite_categories"]
            completed_categories = self._get_completed_categories(
                completed_questions, form_data
            )
            if not all(cat in completed_categories for cat in required_categories):
                return False
        
        # Check minimum questions before
        if "min_questions_before" in dependencies:
            min_questions = dependencies["min_questions_before"]
            if len(completed_questions) < min_questions:
                return False
        
        return True
    
    def _get_completed_categories(self, completed_questions: List[str],
                                form_data: Dict[str, Any]) -> List[str]:
        """
        Get categories of completed questions.
        
        Args:
            completed_questions: List of completed question IDs
            form_data: The form data
            
        Returns:
            List of completed categories
        """
        completed_categories = []
        for question_id in completed_questions:
            question = self.question_repository.get_question_by_id(question_id)
            if question:
                completed_categories.append(question.get("category"))
        
        return completed_categories
    
    def get_dependency_summary(self, dependencies: Dict[str, Any]) -> str:
        """
        Generate a human-readable summary of dependencies.
        
        Args:
            dependencies: The dependencies to summarize
            
        Returns:
            A human-readable summary of dependencies
        """
        if not dependencies:
            return "No dependencies"
        
        summary_parts = []
        
        if "blocked_by" in dependencies:
            blocked_str = ", ".join(dependencies["blocked_by"])
            summary_parts.append(f"Blocked by: {blocked_str}")
        
        if "prerequisite_categories" in dependencies:
            categories_str = ", ".join(dependencies["prerequisite_categories"])
            summary_parts.append(f"Requires categories: {categories_str}")
        
        if "min_questions_before" in dependencies:
            summary_parts.append(f"Minimum {dependencies['min_questions_before']} questions before")
        
        return ", ".join(summary_parts) if summary_parts else "No dependencies"
