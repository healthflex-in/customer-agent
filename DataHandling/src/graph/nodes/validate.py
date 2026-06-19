"""
Node factory functions for section validation and advancement.
"""
from src.graph.state import InterviewState
from src.graph.pure_functions.form_validation import validate_section


def make_validate_section_node():
    def validate_section_node(state: InterviewState) -> dict:
        form = state["form"]
        current_section = state["current_section"]
        result = validate_section(form, current_section)
        return {"missing_fields": result}

    return validate_section_node


def make_advance_section_node():
    def advance_section_node(state: InterviewState) -> dict:
        form_sections = state["form_sections"]
        current_section = state["current_section"]
        question_round = state["question_round"]

        # Determine next section
        if current_section == "Present Complaint" and "Previous Consultations" in form_sections:
            next_section = "Previous Consultations"
            new_round = question_round  # still in round 0

        else:
            current_idx = form_sections.index(current_section)
            next_idx = current_idx + 1

            if next_idx >= len(form_sections):
                # Past the last section — signal completion via round 3
                return {
                    "current_section": current_section,
                    "question_round": 3,
                    "attempts_on_current_section": 0,
                    "missing_fields": [],
                }

            next_section = form_sections[next_idx]

            # Increment question_round when both sections in a round are complete:
            # Round 0 ends after Previous Consultations (sections 0+1 done)
            # Round 1 ends after History & Diagnostics (sections 2+3 done)
            # Round 2 ends after Referral (sections 4+5 done)
            round_end_sections = {
                "Previous Consultations": 1,
                "History & Diagnostics": 2,
                "Referral": 3,
            }
            if current_section in round_end_sections:
                new_round = round_end_sections[current_section]
            else:
                new_round = question_round

        return {
            "current_section": next_section,
            "question_round": new_round,
            "attempts_on_current_section": 0,
            "missing_fields": [],
        }

    return advance_section_node
