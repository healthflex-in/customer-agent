"""
Node factory for handling the very first user response (phase == "welcome").
"""
from src.graph.state import InterviewState

_CONFIRMATION_WORDS = {"yes", "ok", "okay", "sure", "ready", "yep", "yeah", "yup", "alright", "go ahead", "start"}


def _is_simple_confirmation(user_input: str) -> bool:
    """Return True when the user's reply is a short affirmative."""
    normalised = user_input.strip().lower().rstrip("!.,")
    return normalised in _CONFIRMATION_WORDS


def make_handle_first_turn_node(predefined_questions, welcome_prompt):
    def handle_first_turn_node(state: InterviewState) -> dict:
        user_input = state["user_input"]
        history = list(state["history"])

        if _is_simple_confirmation(user_input):
            # Strip the DIRECT_QUESTION prefix before sending to the user
            raw = predefined_questions[0][1]
            question = raw  # already plain text; no prefix stored in PREDEFINED_QUESTIONS
        else:
            # User said something more than a simple yes — acknowledge and still ask Q1
            raw = predefined_questions[0][1]
            question = raw

        history.append({"role": "agent", "message": question})
        return {
            "response_text": question,
            "phase": "interviewing",
            "history": history,
        }

    return handle_first_turn_node
