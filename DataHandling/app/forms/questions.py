"""PROM question batching and personalization of non-clinical custom questions."""

import json

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
