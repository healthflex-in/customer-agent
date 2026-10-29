"""
Extract nodes for the LangGraph interview graph.

Brand RAG: queries the Stance Health ChromaDB vector store to answer
questions about the clinic, services, pricing, etc. without interrupting
the medical interview flow.
"""

import os
from pathlib import Path
from app.ai.models import MODEL_REGISTRY
from app.observability.ai_usage import gemini_usage, tracked_ai_call
from app.observability.privacy import error_type


def _get_chroma_client():
    """Return a ChromaDB PersistentClient from the known vector store path."""
    import chromadb
    for candidate in [
        Path(".") / "db" / "vector" / "single_book",
        Path("/app") / "db" / "vector" / "single_book",
    ]:
        if candidate.exists():
            return chromadb.PersistentClient(path=str(candidate))
    return None


def _answer_with_web_search(user_input: str) -> str:
    """
    Use Gemini with Google Search grounding to answer brand questions.
    This gives real, up-to-date answers (founder name, centers, news, etc.)
    rather than making things up or deflecting.
    """
    import os
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return ""

    try:
        from google import genai as _genai
        from google.genai import types as _types

        client = _genai.Client(api_key=api_key)
        search_prompt = (
            f"You are Sage, a warm assistant for Stance Health (https://www.stance.health/), "
            f"India's first technology-enabled MSK health platform. "
            f"A patient asked: \"{user_input}\"\n\n"
            f"Search for information about Stance Health and answer warmly in 2-4 sentences. "
            f"If you can't find specific information, say you'll connect them with the team."
        )
        response = tracked_ai_call(
            # Grounding has a separate quota/charge, so it remains explicitly
            # unpriced until the deployment supplies a billing-aware rate.
            provider="google_genai_grounded",
            model=MODEL_REGISTRY.search,
            operation="grounded_brand_search",
            call=lambda: client.models.generate_content(
                model=MODEL_REGISTRY.search,
                contents=search_prompt,
                config=_types.GenerateContentConfig(
                    tools=[_types.Tool(google_search=_types.GoogleSearch())],
                    temperature=0.3,
                ),
            ),
            usage_extractor=gemini_usage,
        )
        text = response.text.strip() if response.text else ""
        if text and len(text) > 20:
            print(f"[brand_web_search] Answered via Google Search grounding")
            return text
    except Exception as e:
        print(f"[brand_web_search] Failed: {error_type(e)}")
    return ""


def _answer_brand_question(user_input: str, llm_complete) -> str:
    """
    Query the Stance Health brandbook vector store (stance_brand_collection)
    and return a contextual answer. Falls back to LLM general knowledge.
    """
    try:
        client = _get_chroma_client()
        if not client:
            print("[brand_rag] ChromaDB not found")
            return ""

        try:
            collection = client.get_collection("stance_brand_collection")
        except Exception:
            # Fall back to medical collection if brand collection missing
            collection = client.get_collection("single_book_collection")

        # Retrieve top-3 relevant chunks
        results = collection.query(
            query_texts=[user_input],
            n_results=3,
        )
        docs = results.get("documents", [[]])[0]
        if not docs:
            return ""

        context = "\n\n".join(docs)

        # Core Stance Health facts — authoritative, hardcoded to prevent hallucination.
        # Web search is NOT used for brand questions: Stance Health is a newer company
        # with limited web presence, so web search hallucinated incorrect founder names.
        _core_facts = """
Stance Health is India's first technology-enabled MSK (Musculoskeletal) health platform.
Founded by: Rohit Arora and Ninad Karandikar.
Specialises in: physiotherapy, pain management, sports rehabilitation, posture correction, and general MSK assessments.
The platform combines expert clinicians with technology to deliver personalised, outcome-driven care.
Website: https://www.stance.health/
For specific details about locations, pricing, appointments, or team — visit https://www.stance.health/ or contact the Stance Health team directly.
"""
        prompt = f"""You are Sage, a warm and knowledgeable assistant for Stance Health.

Core Stance Health facts:
{_core_facts}

Additional context from Stance Health knowledge base:
{context}

A patient asked: "{user_input}"

STRICT RULES:
1. Answer warmly and concisely (2-4 sentences).
2. NEVER invent specific numbers (number of centers, prices, addresses) unless they appear in the context above.
3. If you don't have the specific detail, say: "I don't have that exact information — you can reach the team at https://www.stance.health/ for the latest details."
4. Do NOT end with "Now let's continue your assessment" for brand questions — just answer naturally.
5. "stance" always means Stance Health — treat them as the same.

Answer:"""
        return llm_complete(prompt).strip()
    except Exception as e:
        print(f"[brand_rag] Failed: {error_type(e)}")
        return ""


def _answer_medical_question(user_input: str, llm_complete) -> str:
    """
    Query the Snell's anatomy / medical knowledge ChromaDB (single_book_collection)
    to provide context for medical education questions like 'what causes knee pain'.
    """
    try:
        client = _get_chroma_client()
        if not client:
            return ""
        collection = client.get_collection("single_book_collection")
        results = collection.query(query_texts=[user_input], n_results=3)
        docs = results.get("documents", [[]])[0]
        if not docs:
            return ""
        context = "\n\n".join(docs)
        prompt = f"""You are Sage, a knowledgeable and warm physiotherapy assistant at Stance Health.

A patient asked: "{user_input}"

Use the following clinical reference information to provide a helpful, clear answer in 3-5 sentences.
Use simple, patient-friendly language. At the end, gently guide them back to the assessment.

Clinical context:
{context}

Answer:"""
        return llm_complete(prompt).strip()
    except Exception as e:
        print(f"[medical_rag] Failed: {error_type(e)}")
        return ""


from typing import Callable

from langgraph.config import get_stream_writer

from src.graph.state import InterviewState


def _mcp_question_hints(form: dict, visit_context: str, current_section: str, user_input: str) -> list:
    """Return clinical-mcp question recommendations (dev only, MCP_URL unset in prod)."""
    try:
        from app.mcp_client import is_available, recommend_questions
        if not is_available():
            return []
        parts = [f"visit_type={visit_context}"]
        for label, section, field in (
            ("complaint", "Present Complaint", "Primary Complaint"),
            ("location", "Pain Assessment", "Primary Location of Pain"),
            ("severity", "Pain Assessment", "Severity (1-10)"),
            ("duration", "Present Complaint", "Duration of the Issue"),
        ):
            value = form.get(section, {}).get(field, "")
            if value:
                parts.append(f"{label}={value}")
        if current_section:
            parts.append(f"section={current_section}")
        parts.append(f"patient_says={user_input[:200]}")
        recommendations = recommend_questions(" | ".join(parts), limit=8) or []
        if recommendations:
            print(f"[mcp] Loaded {len(recommendations)} question recommendations from clinical-mcp")
        return recommendations
    except Exception as exc:
        print(f"[mcp] Recommendation lookup failed: {error_type(exc)}")
        return []


def _extract_prom_answers(
    *,
    user_input: str,
    history: list,
    form: dict,
    current_section: str,
    last_question: str,
    llm_complete: Callable[[str], str],
    reasoning_llm: Callable[[str], str] | None,
) -> dict:
    if reasoning_llm is not None:
        reasoned = reasoning_extract(
            user_input=user_input,
            history=history,
            form=form,
            reasoning_llm=reasoning_llm,
        )
        if reasoned is not None:
            return reasoned
    return extract_form_data_from_text(
        user_input=user_input,
        form=form,
        prompt_template=FORMAT_PROMPT,
        llm_complete=llm_complete,
        current_section=current_section,
        last_question=last_question,
    )


import copy

from src.graph.pure_functions.additional_complaint import (
    add_complaint_section,
    same_complaint,
    same_complaint_section,
)
from src.graph.pure_functions.answer_mapping import (
    COMPLAINT_SECTIONS,
    apply_declines,
    choose_focus,
    SkipDecision,
    choose_skipped,
    complaint_labels,
    decide_skip,
    field_still_open,
    map_reply,
    read_open_message,
)
from src.graph.pure_functions.intent_detection import bare_yes_no, is_skip_all
from src.graph.pure_functions.phrasing import pick, your
from src.graph.pure_functions.question_plan import (
    question_lines,
    LIFESTYLE_COMPONENT_QUESTIONS,
    NOTED_COMPLAINT,
    REQUIRED_FIELD_QUESTIONS,
    SKIP_WHICH,
    SKIP_WHICH_KEEP_REQUIRED,
)
from src.graph.pure_functions.form_extraction import extract_form_data_from_text
from src.graph.pure_functions.intent_detection import end_confirmation_reply, is_end_request
from src.graph.pure_functions.clarification import (
    boundary_response_with_pending_question,
    build_activity_clearance_response,
    build_intake_clarification,
    OUT_OF_FLOW_RESPONSE,
    has_actionable_intake_fact,
    out_of_flow_kind,
)
from src.graph.pure_functions.reasoning_extractor import reasoning_extract
from src.forms.loader import load_form
from src.prompts import FORMAT_PROMPT


_FOCUS_FIELD_ID = "Present Complaint.Primary Complaint"


def _primary_complaint(form: dict) -> str:
    return str((form.get("Present Complaint") or {}).get("Primary Complaint") or "").strip()


def _offered_focus(history: list) -> tuple[list[str], str]:
    """Return the complaints offered by the last focus question and the reply that named them."""

    for index in range(len(history) - 1, -1, -1):
        entry = history[index]
        if entry.get("role") != "agent":
            continue
        complaints = list(entry.get("focus_complaints") or [])
        if not complaints:
            return [], ""
        reply = next(
            (
                earlier.get("message", "")
                for earlier in reversed(history[:index])
                if earlier.get("role") == "user" and not earlier.get("focus_reply")
            ),
            "",
        )
        return complaints, reply
    return [], ""


def _ask_focus(state: InterviewState, form: dict, history: list, complaints: list[str]) -> dict:
    question = (
        "You mentioned more than one concern:\n"
        + "\n".join(f"- {complaint}" for complaint in complaints)
        + "\n\nWhich one would you like to focus on first? We'll go through the others after that."
    )
    counts = dict(state.get("question_ask_counts") or {})
    counts[_FOCUS_FIELD_ID] = counts.get(_FOCUS_FIELD_ID, 0) + 1
    return {
        "form": form,
        "history": history + [
            {"role": "agent", "message": question, "focus_complaints": complaints},
        ],
        "response_text": question,
        "phase": "interviewing",
        "current_section": "Present Complaint",
        "visit_context": "specific_complaint",
        "question_field_ids": [_FOCUS_FIELD_ID],
        "question_ask_counts": counts,
        "reports_intent": {},
        "pending_question": None,
        "direct_response_handled": True,
    }


_NOT_A_VALUE = {"", "Not provided", "Declined", "Not applicable", "Skipped by patient"}


def _revised_answers(before: dict, after: dict, updated_ids: list[str]) -> list[dict]:
    """The answers this turn changed from a value the patient had already given."""

    changed = []
    for field_id in dict.fromkeys(updated_ids):
        section, field = field_id.split(".", 1)
        old = str((before.get(section) or {}).get(field) or "").strip()
        new = str((after.get(section) or {}).get(field) or "").strip()
        if old not in _NOT_A_VALUE and new and old != new:
            changed.append({"field": field, "value": new})
    return changed


def _open_message_reply(
    form: dict, history: list, user_input: str, question: str, llm: Callable[[str], str]
) -> tuple[str, dict | None]:
    """Read a message the keyword check took for an unrelated question.

    Returns ("answer", None) when it is an ordinary answer that merely starts like
    a question ("may be a few days"), so the intake carries on; ("form_question",
    reply) when the patient asks what the pending question is about, with a reply
    that says which problem and repeats the question; otherwise ("other", None),
    which keeps the boundary reply. A form-question reply does not count as an ask,
    and the pending question ids are kept for the next reply.
    """

    pending = next(
        (entry for entry in reversed(history)
         if entry.get("role") == "agent" and entry.get("question_field_ids")),
        None,
    )
    labels = complaint_labels(form)
    reading = read_open_message(user_input, question, list(dict.fromkeys(labels.values())), llm)
    if reading != "form_question" or pending is None:
        return ("answer" if reading == "answer" else "other"), None
    ids = list(pending["question_field_ids"])
    pending_question = str(pending.get("question_text") or pending.get("message") or "").strip()
    main = str((form.get("Present Complaint") or {}).get("Primary Complaint") or "").strip()
    complaint = labels.get(ids[0].split(".", 1)[0]) or main
    subject = your(complaint) if complaint else "what brings you in today"
    reply = f"{pick('form_question', len(history)).format(complaint=subject)}\n\n{pending_question}"
    return "form_question", {
        "history": history + [
            {"role": "user", "message": user_input, "clinical_extraction": False},
            {
                "role": "agent", "message": reply, "clinical_extraction": False,
                "question_field_ids": ids, "question_text": pending_question,
            },
        ],
        "response_text": reply,
        "reports_intent": {},
        "pending_question": None,
        "direct_response_handled": True,
    }


def _yes_no_clarification(form, history, user_input, yes_no, asked_ids, form_sections) -> dict:
    """Ask which question a bare "no" or "yes" is for. Repeats the open questions to choose from."""

    missing = [tuple(f.split(".", 1)) for f in asked_ids]
    lines = "\n".join(
        f"- {line}" for line in question_lines(missing, load_form("FRM-01").field_labels)
    )
    question = f"Which question is that {yes_no.lower()} for? Here are the ones I just asked:\n{lines}"
    return {
        "form": form,
        "form_sections": form_sections,
        "history": history + [
            {"role": "user", "message": user_input},
            {"role": "agent", "message": question, "yes_no_clarify": True, "question_field_ids": asked_ids},
        ],
        "response_text": question,
        "phase": "interviewing",
        "reports_intent": {},
        "pending_question": None,
        "direct_response_handled": True,
        "noted_complaints": [],
    }


def _last_agent(history: list) -> dict:
    for entry in reversed(history):
        if entry.get("role") == "agent":
            return entry
    return {}


def _question_text(field_id: str) -> str:
    name = field_id.split(".", 1)[1]
    return (
        REQUIRED_FIELD_QUESTIONS.get(name)
        or LIFESTYLE_COMPONENT_QUESTIONS.get(name)
        or name
    )


def _fresh_complaints(form: dict, reported: list[str]) -> list[str]:
    """Complaint labels that are a new problem, not a restatement of one already known.

    A reported label is known when it restates the main complaint, an added
    complaint, or the body area the patient gave as the location, so "lower
    back pain" is not new when the main complaint is "back pain".
    """

    known = []
    primary = _primary_complaint(form)
    if primary:
        known.append(primary)
    for name, fields in form.items():
        if name.startswith("Additional Complaint ") and isinstance(fields, dict):
            label = str(fields.get("Primary Complaint") or "").strip()
            if label:
                known.append(label)
    location = str((form.get("Pain Assessment") or {}).get("Primary Location of Pain") or "").strip()
    if location:
        known.append(location)
    fresh: list[str] = []
    for label in reported:
        if any(same_complaint(label, other) for other in [*known, *fresh]):
            continue
        fresh.append(label)
    return fresh


def _ask_which_skip(
    form: dict,
    history: list,
    field_ids: list[str],
    form_sections: list[str],
    noted: list[str],
    keep_required_note: bool,
) -> dict:
    lines = [_question_text(field_id) for field_id in field_ids]
    lead = SKIP_WHICH_KEEP_REQUIRED if keep_required_note else SKIP_WHICH
    question = lead + "\n" + "\n".join(f"- {line}" for line in lines)
    if noted:
        question = f"{NOTED_COMPLAINT.format(labels=', '.join(noted))}\n\n{question}"
    return {
        "form": form,
        "form_sections": form_sections,
        "history": history + [{
            "role": "agent",
            "message": question,
            "skip_question_ids": field_ids,
        }],
        "response_text": question,
        "phase": "interviewing",
        "reports_intent": {},
        "pending_question": None,
        "direct_response_handled": True,
        "noted_complaints": [],
    }


def _apply_focus(form: dict, sections: list[str], complaints: list[str], chosen: int) -> tuple[dict, list[str]]:
    """Make the chosen complaint primary and give every other one its own section."""

    updated = copy.deepcopy(form)
    updated.setdefault("Present Complaint", {})["Primary Complaint"] = complaints[chosen]
    for index, complaint in enumerate(complaints):
        if index != chosen:
            updated, _ = add_complaint_section(updated, complaint)
    return updated, list(sections) + [name for name in updated if name not in sections]


def make_extract_node(llm_complete: Callable[[str], str], reasoning_llm: Callable[[str], str] = None):
    """
    Map the patient's reply onto the form fields it answers.

    FRM-01 replies go through one mapping call over every unanswered field, so
    answers given in any order, or ahead of the question, are kept. PROM
    sessions keep their own extractor.

    Updates:
      - form                (mapped answers applied)
      - history             (user turn appended)
      - reports_intent
      - visit_context
    """
    def extract_form_data_node(state: InterviewState) -> dict:
        user_input: str = state["user_input"]
        form: dict = state["form"]
        history: list = list(state["history"])
        current_section: str = state.get("current_section", "")
        awaiting_upload: bool = state.get("awaiting_report_upload", False)

        # The patient is answering "are you sure you want to end?". A clear yes or
        # no is settled here without mapping the reply onto any field; anything
        # else is read as an ordinary answer below.
        if _last_agent(history).get("end_confirm") and not state.get("tagged_turns"):
            confirmation = end_confirmation_reply(user_input)
            if confirmation:
                return {
                    "history": history + [{"role": "user", "message": user_input}],
                    "reports_intent": {},
                    "pending_question": None,
                    "end_requested": confirmation == "yes",
                    "end_reply": "confirmed" if confirmation == "yes" else "declined",
                }

        # An accidentally-created additional complaint must have a deterministic
        # exit. Without this branch, "skip this" and "there is no additional
        # complaint" are treated as missing clinical answers and the same batch
        # is repeated forever.
        # A patient who says an added complaint is really their main one gets it
        # removed whichever section is being asked, not only that section.
        same_issue_target = same_complaint_section(user_input, form)
        if current_section.startswith("Additional Complaint ") or same_issue_target:
            from src.graph.pure_functions.additional_complaint import (
                additional_complaint_removal_target,
                additional_complaint_replacement,
                is_additional_complaint_cancellation,
            )
            removal_target = same_issue_target or additional_complaint_removal_target(
                user_input, form, current_section
            )
            if removal_target or is_additional_complaint_cancellation(user_input):
                import copy as _copy
                updated_form = _copy.deepcopy(form)
                section_to_remove = removal_target or current_section
                updated_form.pop(section_to_remove, None)
                updated_sections = [
                    section for section in state.get("form_sections", list(updated_form))
                    if section != section_to_remove
                ]
                base_history = history + [{"role": "user", "message": user_input}]
                from src.graph.nodes.generate import next_intake_step
                step = next_intake_step(
                    {
                        **state,
                        "form": updated_form,
                        "form_sections": updated_sections,
                    },
                    base_history,
                )
                if same_issue_target:
                    lead_in = "Understood, that's the same issue, so I've taken out the extra complaint.\n\n"
                elif step["phase"] == "interviewing":
                    lead_in = "I've removed that additional complaint. Let's continue with the remaining intake.\n\n"
                else:
                    lead_in = "I've removed that additional complaint. Here is the updated information:\n\n"
                response = lead_in + step["response_text"]
                return {
                    **step,
                    "form_sections": updated_sections,
                    "history": base_history + [
                        {"role": "agent", "message": response},
                    ],
                    "response_text": response,
                    "reports_intent": {},
                    "pending_question": None,
                    "direct_response_handled": True,
                }

            replacement = additional_complaint_replacement(user_input)
            if replacement:
                import copy as _copy
                form = _copy.deepcopy(form)
                form[current_section]["Primary Complaint"] = replacement
                from src.graph.pure_functions.complaint_severity import (
                    explicit_severity_updates,
                )
                for (section, field), value in explicit_severity_updates(
                    form, user_input, current_section
                ).items():
                    form[section][field] = value

        last_agent_for_boundary = next(
            (
                entry.get("question_text") or entry.get("message", "")
                for entry in reversed(history)
                if entry.get("role") == "agent"
            ),
            "",
        )
        boundary_kind = out_of_flow_kind(user_input, last_agent_for_boundary)
        out_of_flow_response = OUT_OF_FLOW_RESPONSE if boundary_kind else None
        mixed_boundary_response = None
        if out_of_flow_response:
            # An open message may be an ordinary answer that merely starts like a
            # question ("may be a few days"). The model reads it first; only an
            # "answer" reading changes anything, and every other case behaves as before.
            reading, form_question_reply = "other", None
            if boundary_kind == "open":
                reading, form_question_reply = _open_message_reply(
                    form, history, user_input, last_agent_for_boundary, reasoning_llm or llm_complete
                )
            if reading == "answer":
                out_of_flow_response = None  # carry on and map it as an answer
            elif has_actionable_intake_fact(user_input):
                # Preserve explicit patient facts in a mixed message, but keep
                # the medical-advice boundary as the response for this turn.
                mixed_boundary_response = out_of_flow_response
            elif form_question_reply:
                return form_question_reply
            else:
                out_of_flow_response = boundary_response_with_pending_question(
                    out_of_flow_response, last_agent_for_boundary
                )
                return {
                    "history": history + [
                        {"role": "user", "message": user_input, "clinical_extraction": False},
                        {"role": "agent", "message": out_of_flow_response, "clinical_extraction": False},
                    ],
                    "response_text": out_of_flow_response,
                    "reports_intent": {},
                    "pending_question": None,
                    "direct_response_handled": True,
                }

        # ── Brand / clinic FAQ detection (RAG) ──────────────────────────────
        # If the user asks about Stance Health (the brand, clinic, services, pricing,
        # locations, team, etc.), answer using the vector store instead of advancing
        # the medical interview. The interview state is NOT changed.
        _brand_signals = [
            "about stance health", "what is stance health", "tell me about stance",
            "what does stance health", "how does stance health",
            "stance health team", "stance health location",
            "what do you offer", "tell me more about stance",
            # Clinic/center location queries
            "other centers", "other clinics", "other branches",
            "where are the", "how many centers", "how many clinics",
            "nearest center", "nearest clinic",
            "more about the clinic", "more about your clinic",
            "about the clinic", "about your center", "about the center",
            # Contact / booking
            "book a session", "cost of", "pricing", "fees",
            "contact stance", "how much does stance",
        ]
        _lower = user_input.lower()
        if any(sig in _lower for sig in _brand_signals):
            brand_answer = _answer_brand_question(user_input, llm_complete)
            if brand_answer:
                new_history = history + [
                    {"role": "user", "message": user_input, "clinical_extraction": False},
                    {"role": "agent", "message": brand_answer, "clinical_extraction": False},
                ]
                return {
                    "history": new_history,
                    "response_text": brand_answer,
                    "reports_intent": {},
                    "pending_question": None,
                    "direct_response_handled": True,
                }

        activity_response = build_activity_clearance_response(user_input)
        if activity_response and not mixed_boundary_response:
            # This is a question, not an answer to the compound intake prompt.
            # Answer before classification/extraction so it cannot overwrite
            # form data or immediately repeat the same unanswered question.
            return {
                "history": history + [
                    {"role": "user", "message": user_input, "clinical_extraction": False},
                    {"role": "agent", "message": activity_response, "clinical_extraction": False},
                ],
                "response_text": activity_response,
                "reports_intent": {},
                "pending_question": None,
                "direct_response_handled": True,
            }

        current_visit_context = state.get("visit_context", "unknown")
        last_agent_q = last_agent_for_boundary
        clarification_response = build_intake_clarification(
            user_input, last_agent_q
        )

        writer = get_stream_writer()
        writer({"stage": "Extracting medical details", "detail": current_section or "symptoms", "status": "active"})

        reports_intent: dict = {}
        visit_context = current_visit_context
        focused_sections = None
        noted: list[str] = []
        decline_notice = False
        mandatory_notice = False
        revised_answers: list[dict] = []
        saved_answers: list[str] = []
        saved_field_ids: list[str] = []
        patient_tone = "neutral"
        end_requested = False
        if state.get("tagged_turns"):
            updated_form = _extract_prom_answers(
                user_input=user_input,
                history=history,
                form=form,
                current_section=current_section,
                last_question=last_agent_q,
                llm_complete=llm_complete,
                reasoning_llm=reasoning_llm,
            )
            detail = "PROM answers"
        else:
            mapper = reasoning_llm or llm_complete
            form_sections = state.get("form_sections") or list(form)
            offered, offered_reply = (
                ([], "") if _primary_complaint(form) else _offered_focus(history)
            )
            if offered:
                from src.graph.nodes.generate import MAX_TIMES_ASKED

                chosen = choose_focus(user_input, offered, mapper)
                times_asked = (state.get("question_ask_counts") or {}).get(_FOCUS_FIELD_ID, 0)
                if chosen is None and times_asked < MAX_TIMES_ASKED:
                    return _ask_focus(
                        state, form,
                        history + [{"role": "user", "message": user_input, "focus_reply": True}],
                        offered,
                    )
                form, form_sections = _apply_focus(
                    form, form_sections, offered, 0 if chosen is None else chosen
                )
                focused_sections = form_sections
                if offered_reply:
                    # Distribute the facts from the reply that named every complaint.
                    form = map_reply(
                        reply=offered_reply, last_question="", form=form,
                        form_sections=form_sections, visit_context="specific_complaint", llm=mapper,
                    ).form
            last = _last_agent(history)
            asked_ids = list(last.get("question_field_ids") or [])
            complaint_snapshot = {
                section: copy.deepcopy(form.get(section)) for section in COMPLAINT_SECTIONS
            }
            patient_text = " ".join(
                [entry.get("message", "") for entry in history if entry.get("role") == "user"]
                + [user_input]
            )
            mapping = map_reply(
                reply=user_input,
                last_question=last_agent_q,
                form=form,
                form_sections=form_sections,
                visit_context="specific_complaint" if offered else current_visit_context,
                llm=mapper,
                asked_ids=asked_ids,
                patient_text=patient_text,
            )
            noted = (
                []
                if offered or not _primary_complaint(form)
                else _fresh_complaints(mapping.form, mapping.complaints)
            )
            if noted:
                mapped_form = mapping.form
                for section, fields in complaint_snapshot.items():
                    if isinstance(fields, dict):
                        mapped_form[section] = fields
                for label in noted:
                    mapped_form, _ = add_complaint_section(mapped_form, label)
                mapping.form = mapped_form
                form_sections = list(form_sections) + [
                    name for name in mapped_form if name not in form_sections
                ]
                focused_sections = form_sections
            if not offered and not _primary_complaint(form) and len(mapping.complaints) > 1:
                # Keep the general facts, but hold the complaint details until
                # the patient says which complaint they belong to first.
                held = copy.deepcopy(mapping.form)
                for section in COMPLAINT_SECTIONS:
                    if section in form:
                        held[section] = copy.deepcopy(form[section])
                return _ask_focus(
                    state, held,
                    history + [{"role": "user", "message": user_input}],
                    mapping.complaints,
                )
            updated_form = mapping.form
            # A bare "no" or "yes" that fits none of several open questions: ask which
            # one it is for, once per batch. Nothing is saved or counted meanwhile.
            yes_no = bare_yes_no(user_input)
            if yes_no and not mapping.updated_field_ids and not _last_agent(history).get("yes_no_clarify"):
                open_asked = [f for f in _last_agent(history).get("question_field_ids") or [] if field_still_open(updated_form, f)]
                if len(open_asked) > 1:
                    return _yes_no_clarification(updated_form, history, user_input, yes_no, open_asked, form_sections)
            # A saved answer the patient changed this turn is acknowledged, so
            # they know the earlier answer was updated and not ignored.
            revised_answers = _revised_answers(form, mapping.form, mapping.updated_field_ids)
            revised_fields = {item["field"] for item in revised_answers}
            saved_field_ids = [
                field_id for field_id in mapping.updated_field_ids
                if field_id.split(".", 1)[1] not in revised_fields
            ]
            saved_answers = [field_id.split(".", 1)[1] for field_id in saved_field_ids]
            patient_tone = mapping.tone
            reports_intent = mapping.reports_intent
            visit_context = mapping.visit_context
            detail = f"{len(mapping.updated_field_ids)} fields"
            pending_skip = list(last.get("skip_question_ids") or [])
            # Asking to end is not a request to skip: it never opens the "which
            # one?" prompt and never counts as refusing a question.
            nothing_saved = not mapping.updated_field_ids
            end_requested = is_end_request(user_input) or (mapping.intent == "end" and nothing_saved)
            skip_all = is_skip_all(user_input) and not end_requested
            if pending_skip and end_requested:
                decision = SkipDecision([], [], False, False)
            elif skip_all:
                # "Skip all" is the only words that exit every remaining question at once.
                from src.graph.nodes.generate import MAX_TIMES_ASKED
                decision = decide_skip(
                    form=updated_form,
                    asked_ids=pending_skip or asked_ids,
                    declined_ids=[],
                    unspecified=True,
                    ask_counts=state.get("question_ask_counts") or {},
                    max_asks=MAX_TIMES_ASKED,
                    skip_all=True,
                )
            elif pending_skip:
                chosen = choose_skipped(
                    user_input, [_question_text(field_id) for field_id in pending_skip], mapper
                )
                # An unclear reply is not a refusal. Only named questions are declined.
                decision = SkipDecision(
                    decline_ids=[] if chosen is None else [
                        pending_skip[index] for index in chosen if index < len(pending_skip)
                    ],
                    ask_which=[],
                    mandatory_refused=False,
                    keep_required_note=False,
                )
            else:
                from src.graph.nodes.generate import MAX_TIMES_ASKED
                decision = decide_skip(
                    form=updated_form,
                    asked_ids=asked_ids,
                    declined_ids=list(mapping.declined_field_ids),
                    unspecified=(
                        mapping.decline_unspecified
                        or (mapping.intent == "skip" and nothing_saved and not mapping.declined_field_ids)
                    ) and not end_requested,
                    ask_counts=state.get("question_ask_counts") or {},
                    max_asks=MAX_TIMES_ASKED,
                )
                if decision.ask_which:
                    return _ask_which_skip(
                        updated_form,
                        history + [{"role": "user", "message": user_input}],
                        decision.ask_which,
                        form_sections,
                        noted,
                        decision.keep_required_note,
                    )
            updated_form, closed = apply_declines(updated_form, decision.decline_ids)
            decline_notice = bool(closed)
            mandatory_notice = decision.mandatory_refused
        updated_form.pop("Referral", None)
        writer({"stage": "Extracting medical details", "detail": detail, "status": "done"})

        result_extra = {}
        if focused_sections:
            result_extra["form_sections"] = focused_sections
        if visit_context != current_visit_context:
            result_extra["visit_context"] = visit_context
            if current_visit_context == "unknown":
                hints = _mcp_question_hints(updated_form, visit_context, current_section, user_input)
                if hints:
                    result_extra["mcp_questions"] = hints

        new_history = history + [{"role": "user", "message": user_input}]

        if clarification_response:
            new_history.append({"role": "agent", "message": clarification_response})

        result = {
            "form": updated_form,
            "history": new_history,
            "reports_intent": reports_intent,
            "pending_question": None,
            "noted_complaints": noted,
            "decline_notice": decline_notice,
            "mandatory_notice": mandatory_notice,
            "revised_answers": revised_answers,
            "saved_answers": saved_answers,
            "saved_field_ids": saved_field_ids,
            "patient_tone": patient_tone,
            "end_requested": end_requested,
        }

        if mixed_boundary_response:
            # Do not let a safety refusal discard concrete intake data. Build
            # the next step from the updated form, then combine it with the
            # fixed boundary response. No diagnosis/advice is generated.
            from src.graph.nodes.generate import next_intake_step
            step = next_intake_step(
                {
                    **state,
                    **result_extra,
                    "form": updated_form,
                    "form_sections": list(updated_form),
                    "noted_complaints": noted,
                    "decline_notice": decline_notice,
                    "mandatory_notice": mandatory_notice,
                    "revised_answers": revised_answers,
                    "saved_answers": saved_answers,
                    "saved_field_ids": saved_field_ids,
                    "patient_tone": patient_tone,
                    "end_requested": end_requested,
                },
                new_history,
            )
            combined_response = (
                mixed_boundary_response
                + "\n\nI've recorded the intake information you shared.\n\n"
                + step["response_text"]
            )
            result.update(
                {
                    **step,
                    "history": new_history + [
                        {
                            "role": "agent",
                            "message": combined_response,
                            "clinical_extraction": False,
                        }
                    ],
                    "response_text": combined_response,
                    "pending_question": None,
                    "direct_response_handled": True,
                }
            )

        if clarification_response:
            result.update(
                {
                    "response_text": clarification_response,
                    "direct_response_handled": True,
                }
            )
        result.update(result_extra)
        return result

    return extract_form_data_node
