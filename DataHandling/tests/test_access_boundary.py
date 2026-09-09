import ast
import unittest
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = BACKEND_ROOT / "server.py"


class AccessBoundaryContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SERVER_PATH.read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def test_every_patient_data_http_route_requires_authorization_header(self):
        protected = []
        for node in self.tree.body:
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            methods = {
                decorator.func.attr
                for decorator in node.decorator_list
                if isinstance(decorator, ast.Call)
                and isinstance(decorator.func, ast.Attribute)
            }
            if methods.intersection({"get", "post", "put", "patch", "delete"}) and node.name != "health_check":
                protected.append(node)

        self.assertTrue(protected)
        for route in protected:
            argument_names = {
                argument.arg for argument in (*route.args.args, *route.args.kwonlyargs)
            }
            called_names = {
                call.func.id
                for call in ast.walk(route)
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            }
            self.assertIn("authorization", argument_names, route.name)
            self.assertIn("_http_auth_context", called_names, route.name)

    def test_websocket_authenticates_before_patient_lookup(self):
        websocket = next(
            node
            for node in self.tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "websocket_endpoint"
        )
        websocket_source = ast.get_source_segment(self.source, websocket) or ""
        token_check = websocket_source.index("_decode_configured_access_token")
        user_lookup = websocket_source.index("validate_user_exists")
        self.assertLess(token_check, user_lookup)
        self.assertIn('client_state.get("auth_context") is None', websocket_source)
        self.assertIn('code=1008', websocket_source)

    def test_no_public_token_issuance_route_exists(self):
        route_paths = []
        for function in self.tree.body:
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in function.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr
                    in {"get", "post", "put", "patch", "delete", "websocket"}
                    and decorator.args
                    and isinstance(decorator.args[0], ast.Constant)
                ):
                    route_paths.append(str(decorator.args[0].value).lower())
        self.assertFalse(any("token" in path or "auth" in path for path in route_paths))


if __name__ == "__main__":
    unittest.main()
