"""
Tests for the condition evaluator.
"""
import unittest
from src.orchestrator.condition_evaluator import ConditionEvaluator


class TestConditionEvaluator(unittest.TestCase):
    """Tests for the condition evaluator."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.evaluator = ConditionEvaluator()
    
    def test_evaluate_conditions_no_conditions(self):
        """Test evaluating conditions with no conditions."""
        conditions = {}
        patient_data = {"age": 35}
        form_data = {}
        
        result = self.evaluator.evaluate_conditions(conditions, patient_data, form_data)
        
        self.assertTrue(result)
    
    def test_evaluate_conditions_age_conditions(self):
        """Test evaluating age conditions."""
        # Test with min_age
        conditions = {"min_age": 18}
        patient_data = {"age": 35}
        form_data = {}
        
        result = self.evaluator.evaluate_conditions(conditions, patient_data, form_data)
        self.assertTrue(result)
        
        # Test with min_age not met
        conditions = {"min_age": 40}
        result = self.evaluator.evaluate_conditions(conditions, patient_data, form_data)
        self.assertFalse(result)
        
        # Test with max_age
        conditions = {"max_age": 40}
        result = self.evaluator.evaluate_conditions(conditions, patient_data, form_data)
        self.assertTrue(result)
        
        # Test with max_age not met
        conditions = {"max_age": 30}
        result = self.evaluator.evaluate_conditions(conditions, patient_data, form_data)
        self.assertFalse(result)
    
    def test_evaluate_conditions_medical_conditions(self):
        """Test evaluating medical conditions."""
        conditions = {"medical_conditions": ["diabetes", "hypertension"]}
        patient_data = {"medical_conditions": ["diabetes", "asthma"]}
        form_data = {}
        
        result = self.evaluator.evaluate_conditions(conditions, patient_data, form_data)
        self.assertTrue(result)  # diabetes is in both
        
        # Test with no matching conditions
        conditions = {"medical_conditions": ["cancer", "stroke"]}
        result = self.evaluator.evaluate_conditions(conditions, patient_data, form_data)
        self.assertFalse(result)
    
    def test_evaluate_conditions_has_reports(self):
        """Test evaluating has_reports conditions."""
        # Test with has_reports = True and reports present
        conditions = {"has_reports": True}
        patient_data = {}
        form_data = {
            "History & Diagnostics": {
                "Reports": "MRI scan showing abnormalities"
            }
        }
        
        result = self.evaluator.evaluate_conditions(conditions, patient_data, form_data)
        self.assertTrue(result)
        
        # Test with has_reports = True and no reports
        conditions = {"has_reports": True}
        form_data = {
            "History & Diagnostics": {
                "Reports": ""
            }
        }
        
        result = self.evaluator.evaluate_conditions(conditions, patient_data, form_data)
        self.assertFalse(result)
        
        # Test with has_reports = False and no reports
        conditions = {"has_reports": False}
        result = self.evaluator.evaluate_conditions(conditions, patient_data, form_data)
        self.assertTrue(result)
        
        # Test with has_reports = False and reports present
        form_data = {
            "History & Diagnostics": {
                "Reports": "X-ray showing fracture"
            }
        }
        
        result = self.evaluator.evaluate_conditions(conditions, patient_data, form_data)
        self.assertFalse(result)
    
    def test_evaluate_conditions_has_previous_treatments(self):
        """Test evaluating has_previous_treatments conditions."""
        # Test with has_previous_treatments = True and treatments present
        conditions = {"has_previous_treatments": True}
        patient_data = {}
        form_data = {
            "Previous Consultations": {
                "Previous Diagnosis or Advice and Prescribed Treatment Taken": "Physiotherapy for back pain"
            }
        }
        
        result = self.evaluator.evaluate_conditions(conditions, patient_data, form_data)
        self.assertTrue(result)
        
        # Test with has_previous_treatments = True and no treatments
        conditions = {"has_previous_treatments": True}
        form_data = {
            "Previous Consultations": {
                "Previous Diagnosis or Advice and Prescribed Treatment Taken": ""
            }
        }
        
        result = self.evaluator.evaluate_conditions(conditions, patient_data, form_data)
        self.assertFalse(result)
    
    def test_evaluate_conditions_custom_logic(self):
        """Test evaluating custom logic conditions."""
        # Test with custom logic that evaluates to True
        conditions = {"custom_logic": "True"}
        patient_data = {}
        form_data = {}

        result = self.evaluator.evaluate_conditions(conditions, patient_data, form_data, {})
        self.assertTrue(result)

        # Test with custom logic that evaluates to False
        conditions = {"custom_logic": "False"}
        result = self.evaluator.evaluate_conditions(conditions, patient_data, form_data, {})
        self.assertFalse(result)

        # Test with custom logic that uses form data
        conditions = {"custom_logic": "bool(form.get('Previous Consultations', {}).get('Previous Diagnosis or Advice and Prescribed Treatment Taken'))"}
        form_data = {
            "Previous Consultations": {
                "Previous Diagnosis or Advice and Prescribed Treatment Taken": "Treatment"
            }
        }

        result = self.evaluator.evaluate_conditions(conditions, patient_data, form_data, {})
        self.assertTrue(result)
    
    def test_get_condition_summary(self):
        """Test getting condition summary."""
        # Test with no conditions
        conditions = {}
        summary = self.evaluator.get_condition_summary(conditions)
        self.assertEqual(summary, "No conditions")
        
        # Test with age conditions
        conditions = {"min_age": 18, "max_age": 65}
        summary = self.evaluator.get_condition_summary(conditions)
        self.assertIn("Age >= 18", summary)
        self.assertIn("Age <= 65", summary)
        
        # Test with medical conditions
        conditions = {"medical_conditions": ["diabetes", "hypertension"]}
        summary = self.evaluator.get_condition_summary(conditions)
        self.assertIn("Medical conditions: diabetes, hypertension", summary)
        
        # Test with has_reports
        conditions = {"has_reports": True}
        summary = self.evaluator.get_condition_summary(conditions)
        self.assertIn("has reports", summary)
        
        # Test with custom logic
        conditions = {"custom_logic": "form['age'] > 18"}
        summary = self.evaluator.get_condition_summary(conditions)
        self.assertIn("Custom logic: form['age'] > 18", summary)


if __name__ == "__main__":
    unittest.main()
