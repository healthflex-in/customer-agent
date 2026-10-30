import ast
from pathlib import Path
import unittest


class AsyncBoundaryTests(unittest.TestCase):
    def test_async_handlers_do_not_call_known_sync_io_directly(self):
        project_root = Path(__file__).resolve().parents[1]
        source_paths = [
            project_root / "server.py",
            project_root / "app" / "jobs" / "worker.py",
            *sorted((project_root / "app" / "api").glob("*.py")),
            *sorted((project_root / "app" / "ws" / "handlers").glob("*.py")),
        ]
        trees = [
            ast.parse(path.read_text(encoding="utf-8")) for path in source_paths
        ]

        blocking_names = {
            "build_graph_state",
            "build_interview_state",
            "build_interview_state_from_graph",
            "create_placeholder_form",
            "ensure_users_collection",
            "fetch_form_by_id",
            "fetch_latest_form_for_user",
            "fetch_tagged_questions",
            "fetch_user_forms",
            "init_mongo",
            "save_customer_info",
            "upload_bytes_to_s3",
            "validate_user_exists",
        }
        blocking_methods = {
            "complete",
            "delete_one",
            "find",
            "find_one",
            "generate_content",
            "generate_summary",
            "insert_one",
            "llm_complete",
            "main_processor",
            "talk_to_user",
            "update_one",
        }
        offenders = []

        for tree in trees:
            for async_function in (
                node for node in ast.walk(tree)
                if isinstance(node, ast.AsyncFunctionDef)
            ):
                for node in ast.walk(async_function):
                    if not isinstance(node, ast.Call):
                        continue
                    if isinstance(node.func, ast.Name) and node.func.id in blocking_names:
                        offenders.append(
                            f"{async_function.name}:{node.lineno}:{node.func.id}"
                        )
                    elif (
                        isinstance(node.func, ast.Attribute)
                        and node.func.attr in blocking_methods
                    ):
                        offenders.append(
                            f"{async_function.name}:{node.lineno}:{node.func.attr}"
                        )

        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
