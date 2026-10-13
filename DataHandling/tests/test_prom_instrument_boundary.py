import ast
import unittest
from pathlib import Path


SERVER_PATH = Path(__file__).resolve().parents[1] / "server.py"


class PromInstrumentBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SERVER_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def _function_source(self, name: str) -> str:
        function = next(
            node
            for node in self.tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        )
        return ast.get_source_segment(self.source, function) or ""

    def test_immutable_filter_precedes_personalization_llm(self):
        source = self._function_source("personalize_questions")
        self.assertLess(source.index("is_immutable_prom_question"), source.index("llm_complete(prompt)"))
        self.assertIn("if not editable_indexes", source)

    def test_resume_uses_stored_administered_definition(self):
        source = self._function_source("websocket_endpoint")
        restore = source.index("questions_from_prom_snapshot")
        personalize = source.index("personalize_questions")
        self.assertLess(restore, personalize)

    def test_persistence_has_separate_prom_snapshot_field(self):
        source = self._function_source("save_customer_info")
        self.assertIn('customer_doc["promSnapshot"]', source)
        websocket_source = self._function_source("websocket_endpoint")
        self.assertIn("prom_snapshot=client_state.get(\"prom_snapshot\")", websocket_source)

    def test_clinical_prefix_defaults_are_not_inferred(self):
        fetch_source = self._function_source("fetch_tagged_questions")
        default_meta_block = fetch_source[
            fetch_source.index("_PROM_DEFAULT_METAS"):
            fetch_source.index("_QUESTION_ID_META_OVERRIDES")
        ]
        self.assertNotIn('"prom_', default_meta_block)


if __name__ == "__main__":
    unittest.main()
