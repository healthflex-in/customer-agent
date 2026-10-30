"""
Node factory for handling the very first user response (phase == "welcome").

A bare "yes, let's start" gets the opening intake question. Any other reply is
a real answer and continues to the normal extraction step in the same turn.
"""
from src.graph.state import InterviewState

# Simple affirmative words that don't carry health information.
_CONFIRMATIONS = {
    "yes", "ok", "okay", "sure", "ready", "yep", "yeah", "yup", "alright",
    "go ahead", "start", "let's go", "lets go", "yeah can we start",
    "can we start", "yes we can", "i'm ready", "im ready", "let's start",
    "lets start", "begin", "yeah sure", "ok let's go", "ok lets go",
}


def make_handle_first_turn_node(welcome_prompt: str, question_text: str = ""):
    def handle_first_turn_node(state: InterviewState) -> dict:
        user_input = state["user_input"]

        # Do not use message length here: short clinical replies such as
        # "I have fever" or "knee pain" still contain real information.
        _normalized = user_input.strip().lower().rstrip("!?.,")
        if _normalized in _CONFIRMATIONS:
            return {
                "response_text": welcome_prompt,
                "phase": "interviewing",
                "visit_context": "unknown",
                "history": list(state["history"]) + [
                    {"role": "agent", "message": welcome_prompt,
                     **({"question_text": question_text} if question_text else {})}
                ],
            }
        return {"phase": "interviewing"}

    return handle_first_turn_node
