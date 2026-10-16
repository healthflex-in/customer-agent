"""Clinic question lookup, PROM batching, and personalization of custom questions."""

import json

from app.db.mongo import mongo
from app.db.queries import build_user_form_queries, empty_tagged_questions_result
from app.forms.instruments import is_immutable_prom_question
from app.forms.prom import PROM_SCALE_MAP
from app.observability.privacy import error_type


def batch_tagged_questions(questions: list, llm_complete, form_template: dict = None) -> list:
    """
    Combine ALL PROM questions into a single multi_answer turn so the patient
    sees the entire assessment at once.

    Questions are ordered by PROM scale (using question_id prefix matching) and
    the resulting turn includes a `question_scales` list so the frontend can
    render section headers (e.g. "Oxford Hip Score", "PHQ-9").
    """
    del llm_complete  # Kept so existing positional callers stay compatible.
    if not questions:
        return []

    # --- Group by scale via question_id prefix ---
    if form_template:
        scale_order = list(form_template.keys())
    else:
        scale_order = []

    buckets: dict = {}
    for q in questions:
        q_dict = q if isinstance(q, dict) else {"text": q, "type": "text", "options": None, "question_id": None}
        qid = q_dict.get("question_id") or ""
        assigned = "Other Assessments"
        for pfx, name in PROM_SCALE_MAP.items():
            if qid.startswith(pfx + "_"):
                assigned = name
                break
        buckets.setdefault(assigned, []).append(q_dict)

    # Preserve scale ordering from form_template
    seen: set = set()
    scale_groups: list = []
    for sname in scale_order:
        if sname in buckets and sname not in seen:
            scale_groups.append((sname, buckets[sname]))
            seen.add(sname)
    for sname, qs in buckets.items():
        if sname not in seen:
            scale_groups.append((sname, qs))

    # Flatten all questions into a single ordered list with scale labels
    all_ids, all_texts, all_opts, all_types, all_scales = [], [], [], [], []
    for scale_name, qs in scale_groups:
        for c in qs:
            all_ids.append(c.get("question_id"))
            all_texts.append(c.get("text", ""))
            all_opts.append(c.get("options"))
            all_types.append(c.get("type", "text"))
            all_scales.append(scale_name)

    combined_text = "\n".join(f"{i + 1}. {t}" for i, t in enumerate(all_texts))
    print(f"[tagged-questions] {len(questions)} questions → 1 combined turn")
    return [{
        "text": combined_text,
        "type": "multi_answer",
        "options": None,
        "question_id": None,
        "question_ids": all_ids,
        "questions": all_texts,
        "question_options": all_opts,
        "question_types": all_types,
        "question_scales": all_scales,
    }]


def personalize_questions(raw_questions: list, patient_context: dict, llm_complete) -> list:
    """
    Rewrite only non-PROM custom questions for this patient's situation.

    Uses FRM-01 intake data (onset, body part, severity, duration) to replace
    generic template phrasing ("past 4 weeks", "your joint") with patient-accurate
    language ("past 3 days", "your right knee").  If a question touches something
    already well-documented in the patient's history, it is reframed as a follow-up
    ("You mentioned X last time — has that changed?").

    Falls back to raw templates on any error so the interview is never blocked.
    """
    if not raw_questions:
        return raw_questions

    # Build a compact clinical summary from FRM-01 (key facts only, skip empty / N/A values)
    _na_markers = {"not applicable", "n/a", "na", "none", "general visit"}
    facts = []
    for section, fields in patient_context.items():
        if isinstance(fields, dict):
            for field, value in fields.items():
                v = str(value).strip()
                if v and not any(v.lower().startswith(m) for m in _na_markers):
                    facts.append(f"{field}: {v}")
        elif isinstance(fields, str) and fields.strip():
            facts.append(f"{section}: {fields.strip()}")

    if not facts:
        print("[personalize-questions] No usable patient facts; using raw templates")
        return raw_questions

    # Published/clinical PROM wording and time windows are part of the instrument
    # definition. They are never sent to an LLM for rewriting. A stored snapshot
    # is also frozen so a resume cannot silently adopt later question-bank edits.
    editable_indexes = [
        index
        for index, question in enumerate(raw_questions)
        if not is_immutable_prom_question(question)
        and not (isinstance(question, dict) and question.get("definition_frozen"))
    ]
    if not editable_indexes:
        print("[personalize-questions] Clinical definitions are immutable; skipping personalization")
        return raw_questions

    raw_texts = [
        raw_questions[index]["text"]
        if isinstance(raw_questions[index], dict)
        else raw_questions[index]
        for index in editable_indexes
    ]

    patient_summary = "\n".join(facts[:30])  # Cap to keep prompt concise
    questions_json = json.dumps(raw_texts)

    prompt = (
        "You are a clinical assistant personalizing assessment questions for a specific patient.\n\n"
        "PATIENT INTAKE HISTORY (FRM-01):\n"
        f"{patient_summary}\n\n"
        f"CUSTOM QUESTION TEMPLATES ({len(raw_texts)} questions):\n"
        f"{questions_json}\n\n"
        "RULES:\n"
        "1. Replace 'past 4 weeks' (or any generic time window) with the patient's actual "
        "duration if documented (e.g. 'past 5 days', 'past 2 months'). If duration is unknown, "
        "remove the time reference entirely.\n"
        "2. Replace generic body-part language ('your joint', 'the affected area') with the "
        "patient's specific condition (e.g. 'your right hip', 'your lower back').\n"
        "3. If a question asks about something already well-captured in the intake history, "
        "reframe it as a follow-up: 'You mentioned [X] — has that changed recently?'\n"
        "4. Do NOT change the clinical meaning or intent of any question.\n"
        "5. Keep exactly the same number of questions in the same order.\n"
        "6. Keep each question concise — one sentence where possible.\n\n"
        f"Return ONLY a valid JSON array of exactly {len(raw_texts)} strings. No other text."
    )

    try:
        raw = llm_complete(prompt).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        personalized_texts = json.loads(raw.strip())
        if isinstance(personalized_texts, list) and len(personalized_texts) == len(editable_indexes):
            print(f"[personalize-questions] Personalized {len(personalized_texts)} questions using patient history")
            result = list(raw_questions)
            for output_index, source_index in enumerate(editable_indexes):
                question = raw_questions[source_index]
                result[source_index] = (
                    {**question, "text": personalized_texts[output_index]}
                    if isinstance(question, dict)
                    else personalized_texts[output_index]
                )
            return result
        print(f"[personalize-questions] Length mismatch ({len(personalized_texts)} vs {len(editable_indexes)}), using raw")
    except Exception as e:
        print(f"[personalize-questions] Failed with {error_type(e)}; using raw templates")

    return raw_questions


def fetch_tagged_questions(user_id: str, form_id: str) -> tuple:
    """
    Return (resolved_texts, prom_form_template, source_document_id) for the
    exact (user_id, form_id). Returns ([], {}, None) if nothing is found.
    """
    if mongo.tagged_questions_collection is None:
        return empty_tagged_questions_result()
    try:
        queries = build_user_form_queries(user_id, form_id)

        doc = None
        _from_customer_info = False
        for q in queries:
            doc = mongo.tagged_questions_collection.find_one(q)
            if doc:
                break

        # Fallback: check mongo.customer_info_collection for template docs that embed the
        # questions array directly (new template format from the clinic admin system)
        if not doc and mongo.customer_info_collection is not None:
            for q in queries:
                doc = mongo.customer_info_collection.find_one(q)
                if doc and doc.get("questions"):
                    _from_customer_info = True
                    print("[tagged-questions] Found embedded questions in customer-info")
                    break
                doc = None

        if not doc:
            print("[tagged-questions] No matching document found")
            return empty_tagged_questions_result()

        raw_questions = doc.get("questions", [])
        if not isinstance(raw_questions, list):
            return empty_tagged_questions_result()

        # Defaults are limited to non-clinical/custom questions. Published PROM
        # options cannot be safely inferred from an ID prefix: individual items
        # can have different anchors and directionality.
        _PROM_DEFAULT_METAS: dict = {
            "biz_feedback": {"type": "single_choice", "options": ["Yes, happy to help", "No, thank you"]},
        }

        # Hard overrides: DB value is wrong for these question IDs
        _QUESTION_ID_META_OVERRIDES: dict = {
            "biz_feedback_nps": {"type": "scale", "options": None},
        }

        def _infer_meta(qid: str, db_type: str, db_options) -> tuple:
            """Return (type, options), inferring from question ID prefix when not in DB."""
            if qid in _QUESTION_ID_META_OVERRIDES:
                m = _QUESTION_ID_META_OVERRIDES[qid]
                return m["type"], m["options"]
            if db_type and db_type != "text" and db_options:
                return db_type, db_options
            for prefix, meta in _PROM_DEFAULT_METAS.items():
                if qid.startswith(prefix):
                    return meta["type"], meta["options"]
            return db_type or "text", db_options

        # Split questions into three buckets:
        # 1. full_embedded: dict with text + type already present (new template format)
        # 2. id_only: dict with only an ID → needs question-bank lookup
        # 3. plain: raw strings or dicts with only text
        full_embedded = []   # (qid, q_dict) — text/type/options already available
        question_ids = []    # IDs that need question-bank lookup
        plain_texts = []     # raw text strings

        for q in raw_questions:
            if isinstance(q, str):
                plain_texts.append(q)
            elif isinstance(q, dict):
                qid = q.get("questionId") or q.get("id") or q.get("question_id")
                has_text = bool(q.get("text"))
                has_type = bool(q.get("type") and q.get("type") != "text")
                if qid and has_text and has_type:
                    # Embedded format: full question data already available
                    full_embedded.append((qid, q))
                elif qid:
                    question_ids.append(qid)
                elif has_text:
                    plain_texts.append(q["text"])

        # Resolve ID-only questions from question-bank
        id_to_doc: dict = {}
        if question_ids:
            try:
                qb = mongo.tagged_questions_collection.database["question-bank"]
                bank_docs = qb.find(
                    {"id": {"$in": question_ids}},
                    {
                        "id": 1,
                        "text": 1,
                        "type": 1,
                        "options": 1,
                        "instrumentVersion": 1,
                        "instrument_version": 1,
                        "_id": 0,
                    },
                )
                id_to_doc = {d["id"]: d for d in bank_docs if d.get("text")}
            except Exception as e:
                print(f"[tagged-questions] Question-ID resolution failed: {error_type(e)}")

        # Build resolved list (preserve order: plain texts first, then all question dicts)
        resolved = [{"text": t, "type": "text", "options": None, "question_id": None} for t in plain_texts]

        # Embedded questions — use their own text/type/options directly
        all_question_ids_for_template = []
        for qid, q in full_embedded:
            _type, _options = _infer_meta(qid, q.get("type", "text"), q.get("options"))
            resolved.append({
                "text": q["text"],
                "type": _type,
                "options": _options,
                "question_id": qid,
                "instrument_version": q.get("instrumentVersion") or q.get("instrument_version"),
            })
            all_question_ids_for_template.append(qid)

        # ID-only questions — use question-bank data
        for qid in question_ids:
            all_question_ids_for_template.append(qid)
            if qid in id_to_doc:
                d = id_to_doc[qid]
                _type, _options = _infer_meta(qid, d.get("type", "text"), d.get("options"))
                resolved.append({
                    "text": d["text"],
                    "type": _type,
                    "options": _options,
                    "question_id": qid,
                    "instrument_version": d.get("instrumentVersion") or d.get("instrument_version"),
                })
            else:
                print(f"[tagged-questions] Warning: '{qid}' not found in question-bank")

        # Build prom_template from resolved questions using full question text as field labels.
        # This ensures the MongoDB skeleton and saved answers both use the full question text
        # as the key, giving a human-readable form_data structure.
        prom_template: dict = {}
        for _r in resolved:
            if not isinstance(_r, dict):
                continue
            _r_qid  = _r.get("question_id") or ""
            _r_text = _r.get("text", "").strip()
            if not _r_text or not _r_qid:
                continue
            _r_scale = "Other Assessments"
            for _r_pfx, _r_nm in PROM_SCALE_MAP.items():
                if _r_qid.startswith(_r_pfx + "_"):
                    _r_scale = _r_nm
                    break
            if _r_scale not in prom_template:
                prom_template[_r_scale] = {}
            prom_template[_r_scale][_r_text] = ""

        # Re-sort resolved so all questions within a scale are contiguous.
        # batch_tagged_questions uses positional slicing keyed on prom_template field counts,
        # so the order of resolved MUST match the order of scales in prom_template.
        _scale_order = {s: i for i, s in enumerate(prom_template.keys())}
        def _scale_rank(item):
            if not isinstance(item, dict):
                return len(_scale_order)
            _qid = item.get("question_id") or ""
            for _pfx, _nm in PROM_SCALE_MAP.items():
                if _qid.startswith(_pfx + "_"):
                    return _scale_order.get(_nm, len(_scale_order))
            return _scale_order.get("Other Assessments", len(_scale_order))
        resolved.sort(key=_scale_rank)

        _source_doc_id = str(doc.get("_id", "")) if _from_customer_info and doc else None
        print(f"[tagged-questions] Resolved {len(resolved)} questions and {len(prom_template)} PROM scales")
        return resolved, prom_template, _source_doc_id
    except Exception as e:
        print(f"[tagged-questions] Fetch failed: {error_type(e)}")
    return empty_tagged_questions_result()


