"""
Node for persisting interview state to the database.
Side-effect only — returns no state changes.
"""
from typing import Callable

from src.graph.state import InterviewState


def make_save_to_db_node(save_customer_info_fn: Callable):
    def save_to_db_node(state: InterviewState) -> dict:
        save_customer_info_fn(
            state["user_id"],
            state["form"],
            state["current_section"],
            state["form_id"],
        )
        return {}

    return save_to_db_node
