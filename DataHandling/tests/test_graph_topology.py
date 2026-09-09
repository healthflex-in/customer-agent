import ast
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from src.graph.state import get_fresh_interview_state


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _load_edges_without_langgraph():
    """Load pure routers while substituting only LangGraph's END sentinel."""
    package = types.ModuleType("langgraph")
    package.__path__ = []
    graph_module = types.ModuleType("langgraph.graph")
    graph_module.END = "__end__"
    module_name = "_graph_edges_contract"
    spec = importlib.util.spec_from_file_location(
        module_name, BACKEND_ROOT / "src" / "graph" / "edges.py"
    )
    module = importlib.util.module_from_spec(spec)
    with patch.dict(
        sys.modules,
        {"langgraph": package, "langgraph.graph": graph_module},
    ):
        assert spec.loader is not None
        spec.loader.exec_module(module)
    return module


class ExtractRoutingCharacterizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.edges = _load_edges_without_langgraph()

    def test_correction_has_highest_precedence(self):
        state = {
            "is_correction_turn": True,
            "tagged_turns": ["question"],
            "awaiting_report_upload": True,
        }
        self.assertEqual(self.edges.after_extract(state), "detect_correction")

    def test_tagged_prom_bypasses_report_upload_routing(self):
        state = {
            "tagged_turns": ["question"],
            "awaiting_report_upload": True,
            "reports_intent": {"has_reports": True},
        }
        self.assertEqual(self.edges.after_extract(state), "validate_section")

    def test_existing_and_new_report_intent_route_to_upload(self):
        self.assertEqual(
            self.edges.after_extract(
                {"awaiting_report_upload": True, "reports_uploaded": False}
            ),
            "handle_upload_response",
        )
        self.assertEqual(
            self.edges.after_extract(
                {"reports_intent": {"has_reports": True}, "reports_uploaded": False}
            ),
            "handle_upload_response",
        )
        self.assertEqual(
            self.edges.after_extract(
                {"reports_intent": {"has_reports": True}, "reports_uploaded": True}
            ),
            "validate_section",
        )

    def test_default_route_is_validation(self):
        self.assertEqual(self.edges.after_extract({}), "validate_section")


class GraphTopologyContractTests(unittest.TestCase):
    def test_extraction_routes_directly_without_noop_node(self):
        graph_path = BACKEND_ROOT / "src" / "graph" / "graph.py"
        tree = ast.parse(graph_path.read_text(encoding="utf-8"))
        node_names = set()
        conditional_sources = set()
        linear_edges = set()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if not node.args or not isinstance(node.args[0], ast.Constant):
                continue
            source = node.args[0].value
            if node.func.attr == "add_node":
                node_names.add(source)
            elif node.func.attr == "add_conditional_edges":
                conditional_sources.add(source)
            elif (
                node.func.attr == "add_edge"
                and len(node.args) > 1
                and isinstance(node.args[1], ast.Constant)
            ):
                linear_edges.add((source, node.args[1].value))

        self.assertNotIn("classify_intent", node_names)
        self.assertIn("extract_form_data", conditional_sources)
        self.assertNotIn(("extract_form_data", "classify_intent"), linear_edges)

    def test_removed_state_and_thought_label_do_not_return(self):
        files = (
            BACKEND_ROOT / "src" / "graph" / "state.py",
            BACKEND_ROOT / "src" / "graph" / "server_adapter.py",
        )
        for path in files:
            self.assertNotIn(
                "tagged_cleanup_done", path.read_text(encoding="utf-8"), path
            )
        fresh = get_fresh_interview_state()
        self.assertNotIn("tagged_cleanup_done", fresh)
        server = (BACKEND_ROOT / "server.py").read_text(encoding="utf-8")
        self.assertNotIn('"classify_intent": {', server)


if __name__ == "__main__":
    unittest.main()
