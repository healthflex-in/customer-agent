import ast
import unittest
from pathlib import Path


SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"


class ClinicalEscalationBoundaryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SERVER_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_safety_check_precedes_all_turn_ai_routes(self):
        websocket = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "websocket_endpoint"
        )
        source = ast.get_source_segment(self.source, websocket) or ""
        safety = source.index("assess_urgent_risk(")
        self.assertLess(safety, source.index("_is_assessment"))
        self.assertLess(safety, source.index("_answer_brand_question"))
        self.assertLess(safety, source.index("_interview_graph.astream"))

    def test_escalation_stops_the_socket_and_does_not_log_patient_text(self):
        self.assertIn('websocket.close(code=4003', self.source)
        warning_start = self.source.index('"clinical_escalation_detected"')
        warning_end = self.source.index(")", warning_start)
        warning_call = self.source[warning_start:warning_end]
        self.assertNotIn("text_input", warning_call)
if __name__ == "__main__":
    unittest.main()
