"""The completion percentage in the environment is the one the interview uses."""

import unittest
from unittest.mock import patch

from app.interview.session import new_client_state
from src.graph import server_adapter


class CompletionSettingReachesTheGraphTests(unittest.TestCase):
    def _graph_state(self):
        client_state = new_client_state()
        server_adapter.init_graph_state_in_client(client_state, user_id="u", form_id="FRM-01")
        return server_adapter.build_graph_state(client_state, "hello", lambda *args, **kwargs: None)

    def test_the_graph_state_carries_the_configured_percentage(self):
        for configured in (0.0, 35.0, 80.0):
            with self.subTest(configured=configured):
                with patch.object(server_adapter, "COMPLETION_PERCENTAGE", configured):
                    self.assertEqual(self._graph_state()["completion_percentage"], configured)


if __name__ == "__main__":
    unittest.main()
