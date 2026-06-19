"""
Evaluates conditions for question selection.
"""
from typing import Dict, Any, List
from datetime import datetime
import re


class ConditionEvaluator:
    """Evaluates conditions for question selection."""
    
    def __init__(self):
        """Initialize the condition evaluator."""
        self.age_ranges = {}
    
    def evaluate_conditions(self, conditions: Dict[str, Any],
                          patient_data: Dict[str, Any],
                          form_data: Dict[str, Any],
                          health_state: Dict[str, Any] = None) -> bool:
        """
        Evaluate all conditions for a question.

        Args:
            conditions: The conditions to evaluate
            patient_data: The patient data
            form_data: The form data
            health_state: The health state (optional)

        Returns:
            True if all conditions are met, False otherwise
        """
        # Check age conditions
        if not self._check_age_conditions(conditions, patient_data):
            return False

        # Check medical conditions
        if not self._check_medical_conditions(conditions, patient_data):
            return False

        # Check form field conditions
        if not self._check_form_conditions(conditions, form_data):
            return False

        # Check custom logic
        if not self._check_custom_logic(conditions, patient_data, form_data, health_state):
            return False

        return True
    
    def _check_age_conditions(self, conditions: Dict[str, Any], 
                            patient_data: Dict[str, Any]) -> bool:
        """
        Check age-related conditions.
        
        Args:
            conditions: The conditions to check
            patient_data: The patient data
            
        Returns:
            True if age conditions are met, False otherwise
        """
        if "min_age" in conditions and "age" in patient_data:
            if patient_data["age"] < conditions["min_age"]:
                return False
        
        if "max_age" in conditions and "age" in patient_data:
            if patient_data["age"] > conditions["max_age"]:
                return False
        
        return True
    
    def _check_medical_conditions(self, conditions: Dict[str, Any], 
                                 patient_data: Dict[str, Any]) -> bool:
        """
        Check medical condition requirements.
        
        Args:
            conditions: The conditions to check
            patient_data: The patient data
            
        Returns:
            True if medical conditions are met, False otherwise
        """
        if "medical_conditions" in conditions:
            patient_conditions = patient_data.get("medical_conditions", [])
            required_conditions = conditions["medical_conditions"]
            if not any(cond in patient_conditions for cond in required_conditions):
                return False
        
        return True
    
    def _check_form_conditions(self, conditions: Dict[str, Any], 
                             form_data: Dict[str, Any]) -> bool:
        """
        Check form field conditions.
        
        Args:
            conditions: The conditions to check
            form_data: The form data
            
        Returns:
            True if form conditions are met, False otherwise
        """
        if "has_reports" in conditions:
            reports_field = form_data.get("History & Diagnostics", {}).get("Reports", "")
            if conditions["has_reports"] != (bool(reports_field and reports_field.strip())):
                return False
        
        if "has_previous_treatments" in conditions:
            prev_treatments_field = form_data.get("Previous Consultations", {}).get(
                "Previous Diagnosis or Advice and Prescribed Treatment Taken", "")
            if conditions["has_previous_treatments"] != (
                bool(prev_treatments_field and prev_treatments_field.strip())
            ):
                return False
        
        return True
    
    def _check_custom_logic(self, conditions: Dict[str, Any],
                          patient_data: Dict[str, Any],
                          form_data: Dict[str, Any],
                          health_state: Dict[str, Any] = None) -> bool:
        """
        Evaluate custom logic expressions.

        Args:
            conditions: The conditions to check
            patient_data: The patient data
            form_data: The form data
            health_state: The health state (optional)

        Returns:
            True if custom logic is met, False otherwise
        """
        if "custom_logic" in conditions:
            # Simple logic evaluation - could be extended with a proper expression parser
            logic = conditions["custom_logic"]
            try:
                # For now, use eval with restricted globals
                # In production, use a proper expression evaluator
                eval_context = {"form": form_data, "patient": patient_data}
                if health_state:
                    eval_context["health"] = health_state
                return eval(logic, eval_context)
            except:
                return False

        return True
    
    def get_condition_summary(self, conditions: Dict[str, Any]) -> str:
        """
        Generate a human-readable summary of conditions.
        
        Args:
            conditions: The conditions to summarize
            
        Returns:
            A human-readable summary of conditions
        """
        if not conditions:
            return "No conditions"
        
        summary_parts = []
        
        if "min_age" in conditions:
            summary_parts.append(f"Age >= {conditions['min_age']}")
        
        if "max_age" in conditions:
            summary_parts.append(f"Age <= {conditions['max_age']}")
        
        if "medical_conditions" in conditions:
            conditions_str = ", ".join(conditions["medical_conditions"])
            summary_parts.append(f"Medical conditions: {conditions_str}")
        
        if "has_reports" in conditions:
            status = "has reports" if conditions["has_reports"] else "no reports"
            summary_parts.append(f"{status}")
        
        if "has_previous_treatments" in conditions:
            status = "has previous treatments" if conditions["has_previous_treatments"] else "no previous treatments"
            summary_parts.append(f"{status}")
        
        if "custom_logic" in conditions:
            summary_parts.append(f"Custom logic: {conditions['custom_logic']}")
        
        return ", ".join(summary_parts) if summary_parts else "No conditions"
