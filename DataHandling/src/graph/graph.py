"""
Build and return the compiled LangGraph StateGraph for the medical interview.

Optimised graph (vs original):
- extract_form_data now runs extraction + intent classification concurrently
  and folds in the gap-fill pass inline — replacing 3 sequential LLM nodes.
- save_to_db removed from graph; server.py fires it as a background task
  after sending the response so the user never waits for the DB write.
"""
from langgraph.graph import StateGraph, END

from src.graph.state import InterviewState
from src.prompts import WELCOME_PROMPT

# Node factories
from src.graph.nodes.first_turn import make_handle_first_turn_node
from src.graph.nodes.extract import (
    make_extract_node as make_extract_form_data_node,
    make_classify_intent_node,
)
from src.graph.nodes.validate import (
    make_validate_section_node,
    make_advance_section_node,
)
from src.graph.nodes.correction import (
    make_detect_correction_node,
    make_apply_correction_node,
)
from src.graph.nodes.generate import (
    make_generate_question_node,
    make_generate_summary_node,
)
from src.graph.nodes.summary import (
    make_classify_summary_intent_node,
    make_handle_summary_response_node,
)
from src.graph.nodes.upload import make_handle_upload_response_node

# Edge routing functions
from src.graph.edges import (
    route_by_phase,
    after_classify_intent,
    after_validate_section,
    after_advance_section,
    after_apply_correction,
    after_handle_summary_response,
    after_handle_upload_response,
)


def create_checkpointer():
    """
    No checkpointer — MongoDB Atlas is the persistent source of truth.
    MemorySaver requires thread_id on every graph call and provides no benefit
    since full state is passed explicitly on each turn via graph_state.
    """
    return None


def build_interview_graph(llm_complete, system_prompt, save_customer_info_fn=None, checkpointer=None, reasoning_llm=None):
    """
    Build and compile the LangGraph StateGraph for the medical interview.

    checkpointer: optional LangGraph checkpointer (Postgres or in-memory).
    When provided, full graph state is persisted per thread_id — interviews
    survive server restarts and can resume from the last completed node.
    """
    graph = StateGraph(InterviewState)

    # ── Register nodes ────────────────────────────────────────────────────────
    graph.add_node("handle_first_turn", make_handle_first_turn_node(llm_complete, WELCOME_PROMPT, reasoning_llm=reasoning_llm))
    # Combined node: extraction + gap-fill + intent classification (concurrent)
    graph.add_node("extract_form_data", make_extract_form_data_node(llm_complete, reasoning_llm=reasoning_llm))
    # classify_intent is now a no-op stub (logic merged into extract_form_data)
    graph.add_node("classify_intent", make_classify_intent_node(llm_complete))
    graph.add_node("validate_section", make_validate_section_node())
    graph.add_node("advance_section", make_advance_section_node())
    graph.add_node("detect_correction", make_detect_correction_node(llm_complete))
    graph.add_node("apply_correction", make_apply_correction_node(llm_complete))
    graph.add_node("generate_question", make_generate_question_node(llm_complete, system_prompt))
    graph.add_node("generate_summary", make_generate_summary_node(llm_complete))
    graph.add_node("classify_summary_intent", make_classify_summary_intent_node(llm_complete))
    graph.add_node("handle_summary_response", make_handle_summary_response_node(llm_complete))
    graph.add_node("handle_upload_response", make_handle_upload_response_node())

    # ── Entry point ───────────────────────────────────────────────────────────
    graph.set_conditional_entry_point(
        route_by_phase,
        {
            "welcome": "handle_first_turn",
            "interviewing": "extract_form_data",
            "awaiting_upload": "handle_upload_response",
            "summary": "classify_summary_intent",
            "complete": END,
        },
    )

    # ── Linear edges ──────────────────────────────────────────────────────────
    graph.add_edge("handle_first_turn", END)

    # extract_form_data now returns is_correction_turn + reports_intent directly
    # so classify_intent (now a no-op) just passes through to the conditional edge
    graph.add_edge("extract_form_data", "classify_intent")

    graph.add_edge("detect_correction", "apply_correction")

    graph.add_edge("generate_question", END)
    graph.add_edge("generate_summary", END)

    graph.add_edge("classify_summary_intent", "handle_summary_response")

    # ── Conditional edges ─────────────────────────────────────────────────────
    graph.add_conditional_edges(
        "classify_intent",
        after_classify_intent,
        {
            "detect_correction": "detect_correction",
            "handle_upload_response": "handle_upload_response",
            "validate_section": "validate_section",
        },
    )

    graph.add_conditional_edges(
        "validate_section",
        after_validate_section,
        {
            "generate_question": "generate_question",
            "advance_section": "advance_section",
        },
    )

    graph.add_conditional_edges(
        "advance_section",
        after_advance_section,
        {
            "generate_summary": "generate_summary",
            "generate_question": "generate_question",
        },
    )

    graph.add_conditional_edges(
        "apply_correction",
        after_apply_correction,
        {
            "generate_summary": "generate_summary",
            "generate_question": "generate_question",
        },
    )

    graph.add_conditional_edges(
        "handle_summary_response",
        after_handle_summary_response,
        {
            END: END,
            "apply_correction": "apply_correction",
            "generate_summary": "generate_summary",
            "generate_question": "generate_question",
        },
    )

    graph.add_conditional_edges(
        "handle_upload_response",
        after_handle_upload_response,
        {
            END: END,                          # Phase 1: just showed upload prompt
            "generate_summary": "generate_summary",
            "generate_question": "generate_question",
        },
    )

    # Compile with optional checkpointer for durable session state.
    # With a checkpointer each turn is checkpointed after every node —
    # interviews survive restarts and can resume mid-question.
    return graph.compile(checkpointer=checkpointer)
