"""
Extract nodes for the LangGraph interview graph.

Brand RAG: queries the Stance Health ChromaDB vector store to answer
questions about the clinic, services, pricing, etc. without interrupting
the medical interview flow.
"""

import os
from pathlib import Path


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
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=search_prompt,
            config=_types.GenerateContentConfig(
                tools=[_types.Tool(google_search=_types.GoogleSearch())],
                temperature=0.3,
            ),
        )
        text = response.text.strip() if response.text else ""
        if text and len(text) > 20:
            print(f"[brand_web_search] Answered via Google Search grounding")
            return text
    except Exception as e:
        print(f"[brand_web_search] Error: {e}")
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
        print(f"[brand_rag] Error: {e}")
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
        print(f"[medical_rag] Error: {e}")
        return ""


from typing import Callable

from langgraph.config import get_stream_writer

from src.graph.state import InterviewState


def _classify_visit_context(user_input: str, llm_complete) -> str:
    """
    LLM-based classification of visit purpose. Called once on the first
    substantive user message so question depth can be adapted intelligently.
    Returns 'specific_complaint', 'general_assessment', or 'unknown'.
    """
    prompt = (
        "A patient arrived at a physiotherapy / MSK health clinic. "
        "Classify their visit purpose based on their message.\n\n"
        f'Patient message: "{user_input}"\n\n'
        "CLASSIFICATION OPTIONS:\n"
        '- "specific_complaint": They describe a specific pain, injury, or health issue they want treated.\n'
        '- "general_assessment": They want a general check-up, wellness screening, posture review, '
        "or to explore the clinic — no specific complaint.\n"
        '- "unknown": Cannot determine from this message.\n\n'
        "Reply with ONLY one of: specific_complaint, general_assessment, unknown"
    )
    try:
        result = llm_complete(prompt).strip().lower().rstrip(".")
        if result in ("specific_complaint", "general_assessment", "unknown"):
            print(f"[visit_context] classified as: {result}")
            return result
    except Exception as e:
        print(f"[visit_context] classification error (non-fatal): {e}")
    return "unknown"


from src.graph.pure_functions.form_extraction import extract_form_data_from_text
from src.graph.pure_functions.form_validation import validate_section
from src.graph.pure_functions.intent_detection import should_check_for_correction
from src.graph.pure_functions.summary import classify_reports_intent
from src.prompts import FORMAT_PROMPT


def make_extract_node(llm_complete: Callable[[str], str], reasoning_llm: Callable[[str], str] = None):
    """
    Combined node: runs form extraction AND intent classification concurrently,
    then applies the classify_unanswered_fields gap-fill inline.

    Replaces three sequential nodes (extract → classify_unanswered → classify_intent)
    with one node that does all three work items in ~max(extract_time, intent_time).

    Updates:
      - form                (extracted + gap-filled)
      - history             (user turn appended)
      - is_correction_turn
      - reports_intent
    """
    def extract_form_data_node(state: InterviewState) -> dict:
        user_input: str = state["user_input"]
        form: dict = state["form"]
        history: list = list(state["history"])
        current_section: str = state.get("current_section", "")
        awaiting_upload: bool = state.get("awaiting_report_upload", False)

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
                    {"role": "user", "message": user_input},
                    {"role": "agent", "message": brand_answer},
                ]
                return {
                    "history": new_history,
                    "response_text": brand_answer,
                    "is_correction_turn": False,
                    "reports_intent": {},
                    "pending_question": None,
                }

        # ── Fast heuristic — if it's a correction, skip extraction LLM entirely ──
        if should_check_for_correction(user_input, awaiting_confirmation=False):
            new_history = history + [{"role": "user", "message": user_input}]
            return {
                "history": new_history,
                "is_correction_turn": True,
                "reports_intent": None,
                "pending_question": None,
            }

        # ── Visit context classification (fallback only) ──────────────────────
        _visit_context_update = {}
        current_visit_context = state.get("visit_context", "unknown")
        if current_visit_context == "unknown" and len(user_input.strip()) > 10:
            _visit_context_update = {"visit_context": _classify_visit_context(user_input, llm_complete)}
            # ── MCP: fetch recommended questions for this case (dev only) ────────
            # Runs only on the first substantive turn when visit context is resolved.
            # MCP_URL env is set in docker-compose.dev.yml but NOT in production.
            try:
                from app.mcp_client import is_available, recommend_questions as mcp_recommend
                if is_available():
                    _visit_ctx = _visit_context_update.get("visit_context", "unknown")
                    _complaint = form.get("Present Complaint", {}).get("Primary Complaint", "")
                    _pain_loc = form.get("Pain Assessment", {}).get("Primary Location of Pain", "")
                    _severity = form.get("Pain Assessment", {}).get("Severity (1-10)", "")
                    _duration = form.get("Present Complaint", {}).get("Duration of the Issue", "")
                    _parts = [f"visit_type={_visit_ctx}"]
                    if _complaint: _parts.append(f"complaint={_complaint}")
                    if _pain_loc: _parts.append(f"location={_pain_loc}")
                    if _severity: _parts.append(f"severity={_severity}/10")
                    if _duration: _parts.append(f"duration={_duration}")
                    if current_section: _parts.append(f"section={current_section}")
                    _parts.append(f"patient_says={user_input[:200]}")
                    _case_desc = " | ".join(_parts)
                    _mcp_recs = mcp_recommend(_case_desc, limit=8)
                    if _mcp_recs:
                        _visit_context_update["mcp_questions"] = _mcp_recs
                        print(f"[mcp] Loaded {len(_mcp_recs)} question recommendations from clinical-mcp")
            except Exception as _mcp_err:
                print(f"[mcp] Non-fatal: {_mcp_err}")

        # ── Detect complaint emerging in a general_assessment visit ──────────────
        # If the patient reveals a specific health complaint mid-general-visit,
        # flip to specific_complaint mode and clear the "Not applicable" placeholders
        # so the conductor and extraction treat them as real fields to fill.
        _effective_context = _visit_context_update.get("visit_context") or current_visit_context
        _COMPLAINT_KW = [
            "pain", "hurt", "ache", "injury", "complaint", "discomfort", "sore",
            "stiff", "swollen", "tight", "weakness", "numb", "numbness",
            "neck", "back", "knee", "shoulder", "hip", "wrist", "ankle",
            "elbow", "spine", "head", "headache", "lower back", "upper back",
        ]
        if _effective_context == "general_assessment" and any(kw in user_input.lower() for kw in _COMPLAINT_KW):
            print(f"[extract] General visit → complaint detected mid-session: '{user_input[:60]}'")
            _visit_context_update["visit_context"] = "specific_complaint"
            _effective_context = "specific_complaint"
            # Clear "Not applicable" placeholders so these fields can be properly filled
            _na_values = {
                "not applicable", "general visit — no specific complaint",
                "not applicable — no pain reported", "not applicable — general visit",
                "not applicable — general visit.", "none",
            }
            for _sec in ["Present Complaint", "Pain Assessment"]:
                if _sec in form and isinstance(form.get(_sec), dict):
                    form = dict(form)
                    form[_sec] = dict(form[_sec])
                    for _field in list(form[_sec].keys()):
                        if str(form[_sec].get(_field, "")).strip().lower() in _na_values:
                            form[_sec][_field] = ""

        # ── Auto-fill irrelevant sections for general_assessment ──────────────
        # Fill Present Complaint / Pain Assessment / Previous Consultations with
        # "Not applicable" so the conductor never asks about them and progress is accurate.
        if _effective_context == "general_assessment":
            _na_fills = {
                "Present Complaint": {
                    "Primary Complaint": "General visit — no specific complaint",
                    "Duration of the Issue": "Not applicable",
                    "Onset (Gradual or Sudden)": "Not applicable",
                    "Mechanism of Injury or Cause": "Not applicable",
                },
                "Previous Consultations": {
                    "Previous Diagnosis or Advice and Prescribed Treatment Taken":
                        "Not applicable — general visit",
                    "Current Status of Issue (Improved, Same, Worse)": "Not applicable",
                },
                "Pain Assessment": {
                    "Primary Location of Pain": "Not applicable — no pain reported",
                    "Severity (1-10)": "Not applicable",
                    "Aggravating Factors": "Not applicable",
                    "Relieving Factors": "Not applicable",
                },
            }
            for _sec, _fields in _na_fills.items():
                if _sec in form:
                    form = dict(form)
                    form[_sec] = dict(form[_sec])
                    for _field, _val in _fields.items():
                        existing = str(form[_sec].get(_field, "")).strip().lower()
                        if not existing or existing in {"none", "not applicable", "n/a", ""}:
                            form[_sec][_field] = _val

        # Prepare last agent question for intent context
        last_agent_q = ""
        for entry in reversed(history):
            if entry.get("role") == "agent":
                last_agent_q = entry.get("message", "")
                break

        # ── Run extraction + intent concurrently ─────────────────────────────────
        # IMPORTANT: get_stream_writer() uses LangGraph context variables that are
        # NOT propagated into ThreadPoolExecutor threads. All writer calls must
        # happen in the main thread context, not inside submitted callables.
        from concurrent.futures import ThreadPoolExecutor

        writer = get_stream_writer()
        writer({"stage": "Extracting medical details", "detail": current_section or "symptoms", "status": "active"})

        _is_prom = bool(state.get("tagged_turns"))

        def _run_extraction():
            # No get_stream_writer() calls here — runs in a thread, context not available
            try:
                # ── Reasoning extractor (primary path) ───────────────────────────────
                if reasoning_llm is not None:
                    from src.graph.pure_functions.reasoning_extractor import reasoning_extract
                    reasoned = reasoning_extract(
                        user_input=user_input,
                        history=history,
                        form=form,
                        reasoning_llm=reasoning_llm,
                        is_prom=_is_prom,
                    )
                    if reasoned is not None:
                        return ("reasoning", reasoned)

                # ── Fallback: FORMAT_PROMPT extraction ────────────────────────────────
                result = extract_form_data_from_text(
                    user_input=user_input,
                    form=form,
                    prompt_template=FORMAT_PROMPT,
                    llm_complete=llm_complete,
                    current_section=current_section,
                    last_question=last_agent_q,
                )
                return ("format_prompt", result)
            except Exception as _ex:
                print(f"[extract_node] Extraction failed: {_ex}")
                return ("failed", form)

        def _run_intent():
            if awaiting_upload:
                return {}
            return classify_reports_intent(
                user_input=user_input,
                llm_complete=llm_complete,
                context_question=last_agent_q,
            )

        def _run_question():
            # Skip prefetch in PROM/tagged mode — generate_question uses pre-batched turns
            if _is_prom:
                return None
            # Pre-compute the next question concurrently with extraction.
            try:
                from src.graph.nodes.generate import (
                    _generate_intelligent_question,
                    _VISIT_CONTEXT_DESCRIPTIONS,
                )
                _hist = history + [{"role": "user", "message": user_input}]
                _visit_ctx = state.get("visit_context", "unknown")
                return _generate_intelligent_question([], _hist, _visit_ctx, form, llm_complete)
            except Exception as _e:
                print(f"[question_prefetch] Non-fatal: {_e}")
                return None

        # All three run concurrently: extraction (slow), intent (fast), question (slow).
        # Total wait = max of the three instead of sum of extraction + question.
        with ThreadPoolExecutor(max_workers=3) as pool:
            fut_extract = pool.submit(_run_extraction)
            fut_intent = pool.submit(_run_intent)
            fut_question = pool.submit(_run_question)
            _extract_result = fut_extract.result()
            reports_intent = fut_intent.result()
            pending_q = fut_question.result()

        # Unpack the tagged extraction result and log it in the main thread
        _extract_method, updated_form = _extract_result if isinstance(_extract_result, tuple) else ("failed", form)
        _filled_count = sum(1 for sec in updated_form.values() if isinstance(sec, dict)
                            for v in sec.values() if v and str(v).strip())
        print(f"[extract_node] {_extract_method}: {_filled_count} fields filled")
        writer({"stage": "Extracting medical details", "detail": f"{_extract_method}: {_filled_count} fields", "status": "done"})

        # ── Inline gap-fill (replaces classify_unanswered_fields LLM call) ───────
        # Only triggers when there are still missing fields after extraction,
        # using a single focused LLM call limited to the current section.
        # Skipped for reasoning extraction — the reasoning model already does
        # a comprehensive fill, so running gap_fill on top is redundant.
        missing = [
            f for f in validate_section(updated_form, current_section)
            if "(If Any)" not in f and "(Optional)" not in f
        ]

        if _extract_method != "reasoning" and missing:
            last_agent_q = ""
            for entry in reversed(history):
                if entry.get("role") == "agent":
                    last_agent_q = entry.get("message", "")
                    break

            filled_facts = [
                f"{field}: {value}"
                for sec_data in updated_form.values()
                if isinstance(sec_data, dict)
                for field, value in sec_data.items()
                if value and str(value).strip()
            ]
            context = "; ".join(filled_facts[:6]) if filled_facts else "Nothing collected yet"
            missing_str = "\n".join(f'  - "{f}"' for f in missing)

            gap_prompt = f"""Medical intake assistant analysing a patient reply.

Context already collected: {context}
Assistant asked: "{last_agent_q}"
Patient replied: "{user_input}"

Still empty in "{current_section}":
{missing_str}

For EACH field above: did the patient's reply address it — directly OR by negating it?
- If YES (positive answer) → write the value using their words
- If they negated it ("no", "nope", "don't", "never", "haven't", "i dont have any") → write a clear negative like "None", "No past surgeries", "Does not smoke or drink", "No exercise"
- If truly irrelevant to that field → return null

IMPORTANT: negations count as answers. "i dont have any past surgeries" fills Surgical History. "i dont smoke" fills lifestyle.

Respond ONLY with JSON: {{"Field Name": "value or null"}}"""

            try:
                import json as _json
                raw = llm_complete(gap_prompt).strip()
                start, end = raw.find("{"), raw.rfind("}")
                if start != -1 and end != -1:
                    classified = _json.loads(raw[start:end + 1])
                    for field, value in classified.items():
                        if value is not None and str(value).strip():
                            if current_section in updated_form and field in updated_form[current_section]:
                                existing = updated_form[current_section].get(field, "")
                                if not existing or not str(existing).strip():
                                    updated_form[current_section][field] = str(value).strip()
                                    print(f"[gap_fill] {field} → {value}")
            except Exception as _e:
                print(f"[gap_fill] Non-fatal error: {_e}")

        # ── Sweep fill for short denial responses ("nothing", "no", "none", etc.) ─────────
        # When a user gives a sweeping denial to a compound question, deterministically
        # fill every form field whose topic was covered by the last agent question.
        # This is a fast keyword pass — no LLM call needed.
        _SWEEP_INPUTS = {
            "nothing", "no", "none", "nope", "na", "n/a", "not really",
            "nothing like that", "nothing much", "no nothing", "nothing like these",
            "no thank you", "not applicable", "nah", "nil",
            "just came simply", "just came", "i just came", "came simply",
            "just visiting", "just here", "no issues", "no problem",
            "no complaints", "no discomfort", "i have no discomfort",
            "nothing to share", "nothing to add", "not that i know of",
        }
        if user_input.lower().strip().rstrip(".,! ") in _SWEEP_INPUTS and last_agent_q:
            _q = last_agent_q.lower()
            # Each entry: (question keywords, section, field, fill_value)
            _sweep_map = [
                (["health condition", "illness", "systemic", "diabetes", "hypertension",
                  "thyroid", "heart", "bp", "blood pressure"],
                 "History & Diagnostics", "Systemic Illness and Surgical History",
                 "No known systemic illness or health conditions"),
                (["surgery", "surgeries", "fracture", "operation", "past surgeries"],
                 "History & Diagnostics", "Systemic Illness and Surgical History",
                 "No past surgeries or fractures"),
                (["lifestyle", "exercise", "smoking", "smoke", "drink", "alcohol",
                  "job type", "physically active", "smoke or drink"],
                 "History & Diagnostics", "Current Lifestyle",
                 "No specific lifestyle details mentioned"),
                (["report", "mri", "x-ray", "ct scan", "scan", "imaging", "diagnostic",
                  "blood report", "x ray"],
                 "History & Diagnostics", "Reports",
                 "No diagnostic reports available"),
                (["goal", "achieve", "3 month", "three month", "short-term", "hoping to"],
                 "Treatment Goals", "Short-Term Goals (within 3 months)",
                 "No specific short-term goals mentioned"),
                (["goal", "long-term", "long term", "ultimate goal", "after 3 month"],
                 "Treatment Goals", "Long-Term Goals (after 3 months)",
                 "No specific long-term goals mentioned"),
                (["expect", "specific expectation", "what do you expect"],
                 "Treatment Goals", "Specific Expectations from Treatment",
                 "No specific expectations mentioned"),
                (["previous", "consulted", "physiotherapist", "hospital", "past treatment",
                  "seen a doctor", "seen any doctor"],
                 "Previous Consultations",
                 "Previous Diagnosis or Advice and Prescribed Treatment Taken",
                 "None — no previous consultations mentioned"),
                # Pain section (only for specific complaint visits)
                (["location of pain", "where do you feel", "where exactly"],
                 "Pain Assessment", "Primary Location of Pain", "Not specified"),
                (["aggravat", "makes it worse", "worse"],
                 "Pain Assessment", "Aggravating Factors", "None mentioned"),
                (["relief", "makes it better", "reliev"],
                 "Pain Assessment", "Relieving Factors", "None mentioned"),
            ]
            for keywords, section, field, fill_value in _sweep_map:
                if any(kw in _q for kw in keywords):
                    sec_data = updated_form.get(section)
                    if isinstance(sec_data, dict) and field in sec_data:
                        existing = sec_data.get(field, "")
                        if not existing or not str(existing).strip():
                            updated_form[section][field] = fill_value
                            print(f"[sweep_fill] {section}.{field} → '{fill_value}'")

        result_extra = {}

        # ── Referral field: update only when the answer is actually a referral ──────────
        # Guard against medical/symptom answers being stored as referral source.
        # If the user's response looks like a symptom or treatment answer, it means
        # a previous question went unanswered — don't store as referral, re-ask later.
        if state.get("referral_asked", False):
            _ref_input = user_input.strip()
            _is_null = _ref_input.lower() in {"no", "nope", "nah", "none", "n/a", "na", ""}

            # Farewell/closing signals — don't store goodbyes as referral source
            _farewell_signals = [
                "bye", "goodbye", "good bye", "bye-bye", "see you", "take care",
                "thank you bye", "thanks bye", "okay bye", "ok bye", "alright bye",
                "that's it", "that is it", "that's all", "nothing else", "all done",
                "have a good", "have a nice", "cheerio",
            ]
            _is_farewell = any(s in _ref_input.lower() for s in _farewell_signals)

            # Medical signals: if the answer sounds like symptoms/treatment, it's NOT a referral
            _medical_signals = [
                "pain", "relief", "hurt", "ache", "ice", "heat", "medicine", "tablet",
                "injection", "exercise", "stretch", "rest", "apply", "helps", "worse",
                "better", "neck", "back", "knee", "shoulder", "hip", "wrist", "ankle",
                "head", "spine", "swelling", "stiffness", "doctor told", "physio told",
                "improve", "improving", "same", "getting worse",
            ]
            _is_medical = any(s in _ref_input.lower() for s in _medical_signals)

            if _is_farewell:
                print(f"[referral_fill] Farewell detected, not storing as referral: '{_ref_input[:60]}'")
                result_extra = {}  # Leave referral as-is; interview is ending anyway
            elif _is_medical:
                # This is a medical answer, not a referral — let extraction handle it
                # and mark referral as not yet asked so it gets re-asked next turn
                print(f"[referral_fill] Medical answer detected, not storing as referral: '{_ref_input[:60]}'")
                result_extra = {"referral_asked": False}
            elif _ref_input and not _is_null:
                _channel_map = {
                    "youtube": "YouTube", "instagram": "Instagram", "facebook": "Facebook",
                    "google": "Google", "twitter": "Twitter", "whatsapp": "WhatsApp",
                    "social media": "Social media", "online": "Found online",
                    "website": "Stance Health website",
                    "friend": "Friend/word of mouth", "family": "Family referral",
                    "relative": "Family referral", "colleague": "Colleague referral",
                    "word of mouth": "Word of mouth", "newspaper": "Newspaper",
                    "doctor": "Doctor referral", "physician": "Doctor referral",
                    "doctor referred": "Doctor referral", "referred by doctor": "Doctor referral",
                    "physiotherapist": "Physiotherapist referral", "hospital": "Hospital referral",
                    "podcast": "Podcast", "blog": "Blog/article",
                }
                _lower = _ref_input.lower()
                matched = next((label for kw, label in _channel_map.items() if kw in _lower), None)
                updated_form = dict(updated_form)
                updated_form["Referral"] = dict(updated_form.get("Referral", {}))
                updated_form["Referral"]["Source"] = matched or _ref_input
                print(f"[referral_fill] Source → {updated_form['Referral']['Source']}")
                result_extra = {}
            else:
                result_extra = {}

        # ── Comprehensive fill for long/detailed responses ────────────────────────
        # Only runs on format_prompt extraction (not reasoning) because the reasoning
        # model already extracts comprehensively from full conversation context.
        if _extract_method != "reasoning" and len(user_input.strip()) > 100:
            _still_empty = sum(
                1 for sec in updated_form.values()
                if isinstance(sec, dict)
                for v in sec.values()
                if not v or not str(v).strip()
            )
            if _still_empty >= 4:
                try:
                    from src.prompts import FINAL_FORM_FILL_PROMPT
                    import json as _json2
                    _full_history = history + [{"role": "user", "message": user_input}]
                    _conv_text = "\n".join(
                        f"{'Sage' if e.get('role') == 'agent' else 'Patient'}: {e.get('message', '')}"
                        for e in _full_history
                    )
                    _fill_prompt = FINAL_FORM_FILL_PROMPT.format(
                        _conv_text,
                        _json2.dumps(updated_form, indent=2),
                        _json2.dumps(updated_form, indent=2),
                    )
                    _raw = llm_complete(_fill_prompt).strip()
                    _start, _end = _raw.find("{"), _raw.rfind("}")
                    if _start != -1 and _end != -1:
                        _filled = _json2.loads(_raw[_start:_end + 1])
                        if isinstance(_filled, dict):
                            for _sec, _fields in _filled.items():
                                if _sec in updated_form and isinstance(_fields, dict):
                                    for _field, _val in _fields.items():
                                        if _val and str(_val).strip() and _field in updated_form.get(_sec, {}):
                                            updated_form[_sec][_field] = str(_val).strip()
                            print(f"[comprehensive_fill] Done — {_still_empty} empty fields filled from conversation")
                except Exception as _e:
                    print(f"[comprehensive_fill] Non-fatal: {_e}")

        new_history = history + [{"role": "user", "message": user_input}]

        result = {
            "form": updated_form,
            "history": new_history,
            "is_correction_turn": False,
            "reports_intent": reports_intent,
            "pending_question": pending_q,
        }
        if _visit_context_update:
            result.update(_visit_context_update)
        if result_extra:
            result.update(result_extra)
        return result

    return extract_form_data_node


def make_classify_intent_node(llm_complete: Callable[[str], str]):
    """No-op stub — intent classification is now handled inside make_extract_node."""
    def classify_intent_node(state: InterviewState) -> dict:
        return {}
    return classify_intent_node
