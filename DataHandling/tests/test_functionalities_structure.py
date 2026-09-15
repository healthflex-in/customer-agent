import ast
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
FUNCTIONALITIES_PATH = BACKEND_ROOT / "src" / "llm" / "functionalities.py"


class FunctionalitiesStructureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = FUNCTIONALITIES_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_has_exactly_one_active_health_agent_class(self):
        health_agent_classes = [
            node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef) and node.name == "HealthAgent"
        ]
        self.assertEqual(len(health_agent_classes), 1)

    def test_no_historical_experimental_health_agent_snapshot(self):
        self.assertNotIn("# experimental code", self.source)
        self.assertNotIn("# class HealthAgent", self.source)

    def test_health_agent_is_the_final_executable_definition(self):
        health_agent = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.ClassDef) and node.name == "HealthAgent"
        )
        self.assertIs(self.tree.body[-1], health_agent)
        trailing_lines = self.source.splitlines()[health_agent.end_lineno :]
        self.assertFalse(any(line.strip() for line in trailing_lines))


if __name__ == "__main__":
    unittest.main()
