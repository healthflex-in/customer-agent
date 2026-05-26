import json
import os
import time
import random
import re
from threading import Thread
from datetime import datetime

try:
    import GPUtil
    HAS_GPUTIL = True
except ImportError:
    HAS_GPUTIL = False

from typing import Optional, Dict, Any, List
from llama_index.core import Settings
from llama_index.vector_stores.chroma import ChromaVectorStore
from llama_index.core.schema import QueryBundle
from llama_index.core import VectorStoreIndex
from llama_index.embeddings.huggingface import HuggingFaceEmbedding
from llama_index.core import Document
from llama_index.core.llms import ChatMessage, MessageRole
from llama_index.core.storage.chat_store import SimpleChatStore
from llama_index.core.memory import ChatMemoryBuffer
import chromadb

# Fix import paths based on project structure
try:
    from src.llm.utils import load_gemini_key, init_llm, final_form_filling
except ImportError:
    try:
        from src.llm.utils import load_gemini_key, init_llm, final_form_filling
    except ImportError:
        from llm.utils import load_gemini_key, init_llm, final_form_filling

from src.enums import ENUMS
from src.prompts import (
    WELCOME_PROMPT,
    SYSTEM_PROMPT,
    TEMPLATE_PROMPT,
    QUERY_TASK_PROMPT,
    FORMAT_PROMPT,
    PREDEFINED_QUESTIONS,
    MEDICAL_FORM_TEMPLATE,
    FINAL_FORM_FILL_PROMPT,
    DEFAULT_ANSWER,
    READY_TO_START_PROMPT,
    INTERVIEW_DECLINED_PROMPT,
    CORRECTION_DETECTION_PROMPT,
    CORRECTION_APPLY_PROMPT,
    CORRECTION_CONFIRMATION_PROMPT,
)

enums_obj = ENUMS()


class HealthAgent:
    def __init__(self):
        """
        Initialize the HealthAgent with all necessary components
        for conducting a medical interview.
        """
        # Initialize predefined questions and prompts
        self.init_prompts()

        # Initialize history for conversation tracking
        self.history = []
        self.history.append({"role": "agent", "message": self.welcome_prompt})

        try:
            # Initialize LLM
            self.gemini_api_key = load_gemini_key()
            self.llm = init_llm(self.gemini_api_key)
        except Exception as e:
            print(f"Error initializing LLM: {e}")
            self.llm = None

        # Initialize paths and configs
        self.model_name = enums_obj.embedding_model_name
        self.chat_store_path = enums_obj.chat_history_json_path
        self.medical_form_path = enums_obj.medical_interview_history_json_path
        self.chroma_db_path = enums_obj.git_db_path
        self.collection_name = enums_obj.git_collection_name

        # Check device availability
        try:
            if HAS_GPUTIL:
                self.devices = GPUtil.getAvailable()
                self.device = self.devices[0] if len(self.devices) else "cpu"
            else:
                self.device = "cpu"
        except:
            self.device = "cpu"

        try:
            # Initialize embedding model
            self.embed_model = HuggingFaceEmbedding(model_name=self.model_name)
        except Exception as e:
            print(f"Error initializing embedding model: {e}")
            self.embed_model = None

        # Initialize chromadb, health index, and chat components
        try:
            self.init_chromadb()
            self.init_health_info_index()
            self.init_chat_components()
        except Exception as e:
            print(f"Error initializing database components: {e}")
            self.health_relevancy_retriever = None
            self.index = None

        # Initialize form and tracking variables
        self.init_form()

        # Continuous tracking
        self.stop_flag = False
        self.info = []
        self.relevancy_action = "Listen"
        self.check_interval = 10

        # Start keyword checking thread
        self.start_keyword_thread()

    def ensure_string(self, text):
        """Ensure the text is a string and not some other object"""
        if hasattr(text, "text"):  # Some API responses might have a .text attribute
            return text.text
        elif hasattr(text, "__str__"):  # Convert to string if possible
            return str(text)
        else:
            return "Response could not be processed"

    def llm_complete(self, prompt):
        """Call LLM with appropriate error handling + token/latency logging."""
        import time as _time

        try:
            if self.llm is None:
                raise ValueError("LLM not initialized")

            t0 = _time.perf_counter()
            response = self.llm.complete(prompt)
            elapsed_ms = (_time.perf_counter() - t0) * 1000

            # Best-effort token-count logging. `response.raw` is a dict for the
            # google_genai wrapper; the legacy gemini wrapper returns an object
            # with attributes. Handle both.
            in_tok = out_tok = None
            try:
                raw = getattr(response, "raw", None)
                usage = None
                if isinstance(raw, dict):
                    usage = raw.get("usage_metadata")
                elif raw is not None:
                    usage = getattr(raw, "usage_metadata", None)
                if usage:
                    if isinstance(usage, dict):
                        in_tok = usage.get("prompt_token_count")
                        out_tok = usage.get("candidates_token_count")
                    else:
                        in_tok = getattr(usage, "prompt_token_count", None)
                        out_tok = getattr(usage, "candidates_token_count", None)
            except Exception:
                pass

            model_name = getattr(self.llm, "model", "?")
            if in_tok is not None and out_tok is not None:
                # Rough $ estimate using Gemini 2.0 Flash on-demand pricing
                # (input $0.10 / output $0.40 per 1M). Real bill may differ.
                cost_usd = (in_tok * 0.10 + out_tok * 0.40) / 1_000_000
                print(
                    f"[llm] model={model_name} in={in_tok} out={out_tok} "
                    f"latency={elapsed_ms:.0f}ms est_cost=${cost_usd:.6f}"
                )
            else:
                print(
                    f"[llm] model={model_name} latency={elapsed_ms:.0f}ms "
                    f"(token count unavailable)"
                )

            return self.ensure_string(response)
        except Exception as e:
            print(f"Error in LLM completion: {e}")
            raise

    def start_keyword_thread(self):
        """Start the background thread for keyword checking."""
        try:
            keyword_thread = Thread(target=self.check_for_keyword, daemon=True)
            keyword_thread.start()
        except Exception as e:
            print(f"Error starting keyword thread: {e}")

    def init_prompts(self):
        """Initialize all prompts and templates."""
        self.welcome_prompt = WELCOME_PROMPT
        self.system_prompt = SYSTEM_PROMPT
        self.query_task_prompt = QUERY_TASK_PROMPT
        self.format_prompt = FORMAT_PROMPT
        self.predefined_questions = PREDEFINED_QUESTIONS
        self.final_form_fill_prompt = FINAL_FORM_FILL_PROMPT
        self.default_answer = DEFAULT_ANSWER
        self.ready_to_start_message = READY_TO_START_PROMPT
        self.interview_declined_message = INTERVIEW_DECLINED_PROMPT

    def init_chromadb(self):
        """Initialize chromadb connection and vector store."""
        try:
            self.db_client = chromadb.PersistentClient(path=self.chroma_db_path)
            collection = self.db_client.get_collection(self.collection_name)
            vector_store = ChromaVectorStore(chroma_collection=collection)
            self.index = VectorStoreIndex.from_vector_store(
                vector_store, embed_model=self.embed_model
            )
        except Exception as e:
            print(f"Error initializing ChromaDB: {e}")
            self.index = VectorStoreIndex([Document(text="Fallback document")])

    def init_health_info_index(self):
        """Initialize health information vector index."""
        try:
            collection = self.db_client.get_collection(self.collection_name)
            vector_store = ChromaVectorStore(chroma_collection=collection)
            self.health_relevancy_index = VectorStoreIndex.from_vector_store(
                vector_store, embed_model=self.embed_model
            )
            self.health_relevancy_retriever = self.health_relevancy_index.as_retriever()
        except Exception as e:
            print(f"Error initializing health info index: {e}")
            if self.index:
                self.health_relevancy_retriever = self.index.as_retriever()
            else:
                self.health_relevancy_retriever = None

    def init_chat_components(self):
        """Initialize chat store and memory components."""
        try:
            # Initialize with an empty list for message history
            self.messages_history = []

            # Initialize chat engine with the LLM
            if self.index and self.llm:
                self.chat_engine = self.index.as_chat_engine(
                    llm=self.llm, chat_mode="condense_question"
                )
            else:
                raise ValueError("Index or LLM not properly initialized")

        except Exception as e:
            print(f"Error initializing chat components: {e}")

    def init_form(self):
        """Initialize form and tracking variables."""
        import copy
        from src.prompts import get_medical_form_template
        
        self.idx = 0
        # CRITICAL: Use get_medical_form_template() to get a fresh template
        # This ensures we always start with a clean template that hasn't been mutated
        # Even if MEDICAL_FORM_TEMPLATE was mutated, get_medical_form_template() returns a fresh copy
        fresh_template = get_medical_form_template()
        self.form = copy.deepcopy(fresh_template)
        self.final_filled_form = copy.deepcopy(fresh_template)
        self.form_sections = list(self.form.keys())
        self.current_section = self.form_sections[0]
        self.missing_fields = []
        self.conversation_state = {
            "last_question": None,
            "attempts": 0,
            "max_attempts": 3,
            "awaiting_confirmation": False,
            "awaiting_summary_confirmation": False,  # New state for final summary confirmation
            "last_message_type": "question",
            # Track if we've explicitly asked the Previous Consultations question
            "asked_previous_consultations": False,
            # Track if user has already uploaded reports
            "reports_uploaded": False,
        }
        self.talk_mode = "START"
        
        # Clear any existing JSON file to prevent old data from being restored
        # We're using MongoDB as the source of truth, not this JSON file
        try:
            if os.path.exists(self.medical_form_path):
                os.remove(self.medical_form_path)
                print(f"Cleared old JSON file: {self.medical_form_path}")
        except Exception as e:
            print(f"Error clearing JSON file (not critical): {e}")

    def check_for_keyword(self):
        """
        Check for health-related keywords in transcriptions every check_interval seconds.
        Runs in a separate thread.
        """
        while not self.stop_flag:
            try:
                if self.info and self.health_relevancy_retriever:
                    self.relevancy_action = self.describe_action(self.info)
                time.sleep(self.check_interval)
            except Exception as e:
                print(f"Error in keyword checking: {e}")
                time.sleep(self.check_interval)

    def describe_action(self, text_chunk, threshold=0.5):
        """
        Determine action based on relevance of text to health topics.

        Args:
            text_chunk: Text to analyze
            threshold: Confidence threshold

        Returns:
            Action to take: "Silent", "Nod", or "Nudge"
        """
        if not text_chunk or not self.health_relevancy_retriever:
            return "Listen"

        try:
            # Create query bundle for retrieval
            query = QueryBundle(query_str=str(text_chunk))
            selected_nodes = self.health_relevancy_retriever.retrieve(query)

            # Check relevance scores
            for node_info in selected_nodes:
                score = getattr(node_info, "score", 0)
                if score is not None and score > threshold:
                    return random.choice(["Silent", "Nod"])
            return "Nudge"
        except Exception as e:
            print(f"Error in describe_action: {e}")
            return "Listen"

    def formatter(self, user_input, prompt_template):
        """
        Format user input into structured data for the current section.
        Uses LLM to extract structured data from user responses.

        Args:
            user_input: The user's response text
            prompt_template: The prompt to use for formatting

        Returns:
            Formatted form data
        """
        example_output = """{"Patient Information": {"Age": "35", "Gender": "male"}}"""

        try:
            # Prepare enhanced prompt for LLM to get better structured extraction
            enhanced_prompt = TEMPLATE_PROMPT.format(
                prompt_template, json.dumps(self.form, indent=2), example_output
            )

            # Add more specific guidance to improve extraction quality
            enhanced_prompt += """
            IMPORTANT GUIDELINES:
            1. Extract ALL relevant medical information from the response
            2. Map information to the correct fields in the form
            3. Make reasonable inferences about fields based on the patient's language
            4. Preserve the exact structure of the form template
            5. Return only valid JSON that can be parsed directly
            """

            # Get structured response from LLM
            llm_response = self.llm_complete(enhanced_prompt)
            print("LLM FORMATTING RESPONSE:\n", llm_response)

            # Extract JSON from response
            json_str = self.extract_json_from_response(llm_response)

            if not json_str:
                # If no JSON found, try again with a simpler prompt
                fallback_prompt = f"""
                Convert this patient response to a JSON object matching the form structure:

                PATIENT RESPONSE: {user_input}
                FORM STRUCTURE: {json.dumps(self.form, indent=2)}

                Respond ONLY with valid JSON.
                """
                llm_response = self.llm_complete(fallback_prompt)
                json_str = self.extract_json_from_response(llm_response)

                if not json_str:
                    print("No valid JSON found in LLM response after retry")
                    return self.form

            # Parse the JSON response
            try:
                formatted_data = json.loads(json_str)

                # Update the form with extracted information
                for section, fields in self.form.items():
                    if section in formatted_data:
                        for key in fields:
                            # Only update if there's new information
                            new_value = formatted_data.get(section, {}).get(key, "")
                            if new_value:
                                self.form[section][key] = new_value

                # POST-PROCESSING: Fix incorrect extraction in "Previous Consultations"
                # 1) If status field is filled but no consultations were mentioned at all, clear it
                # 2) If user explicitly says they have had NO previous consultations, ensure status
                #    is not incorrectly set to things like "Same" and instead reflects N/A.
                if "Previous Consultations" in formatted_data:
                    prev_consultations = self.form.get("Previous Consultations", {})
                    prev_diagnosis_field = "Previous Diagnosis or Advice and Prescribed Treatment Taken"
                    status_field = "Current Status of Issue (Improved, Same, Worse)"
                    
                    prev_diagnosis_value = prev_consultations.get(prev_diagnosis_field, "").strip()
                    status_value = prev_consultations.get(status_field, "").strip()
                    
                    # Check if consultations were actually mentioned
                    consultation_keywords = [
                        "doctor", "physiotherapist", "hospital", "consulted", "visited",
                        "diagnosis", "diagnosed", "prescribed", "treatment", "medicine",
                        "injection", "exercise", "physio", "clinic"
                    ]
                    has_consultation_mention = any(
                        keyword in prev_diagnosis_value.lower() 
                        for keyword in consultation_keywords
                    ) or any(
                        keyword in user_input.lower() 
                        for keyword in ["consulted", "visited", "doctor", "hospital", "physio"]
                    )
                    
                    # Case 1: status is filled but no consultations mentioned anywhere → clear it
                    if status_value and not prev_diagnosis_value and not has_consultation_mention:
                        print(f"[formatter] POST-PROCESSING: Clearing incorrectly extracted 'Previous Consultations' data (status filled but no consultations mentioned)")
                        self.form["Previous Consultations"][status_field] = ""
                        # Also check if status was extracted from general complaint (like "getting worse")
                        # If so, it shouldn't be in Previous Consultations at all
                        if "worse" in status_value.lower() or "better" in status_value.lower() or "same" in status_value.lower():
                            # This is likely describing current complaint status, not previous consultation status
                            print(f"[formatter] POST-PROCESSING: Status '{status_value}' appears to be about current complaint, not previous consultations. Clearing.")
                            self.form["Previous Consultations"][status_field] = ""

                    # Case 2: user explicitly says they have NO previous consultations
                    if prev_diagnosis_value:
                        prev_lower = prev_diagnosis_value.lower()
                        no_consultation_indicators = [
                            "no consultation", "no consultations", "never consulted",
                            "have not consulted", "haven't consulted", "did not visit",
                            "didn't visit", "no doctor", "no hospital", "none", "nothing"
                        ]
                        has_no_consultations = any(ind in prev_lower for ind in no_consultation_indicators)
                        if has_no_consultations:
                            # In this scenario, any auto-filled status like "Same" is misleading.
                            # Either leave it empty or mark explicitly as not applicable.
                            if status_value:
                                print(f"[formatter] POST-PROCESSING: Clearing status '{status_value}' because user has no previous consultations.")
                            self.form["Previous Consultations"][status_field] = "Not applicable (no previous consultations for this issue)"

                # POST-PROCESSING: History & Diagnostics negative vs. missing values
                # We ONLY auto-fill explicit negatives when the user clearly says so.
                # Otherwise, placeholders like "None mentioned" should NOT be treated as filled.
                if "History & Diagnostics" in formatted_data:
                    hist_diag = self.form.get("History & Diagnostics", {})
                    user_lower = user_input.lower()

                    # 1) Reports field: only fill negative when user clearly says they have no reports
                    reports_field_name = "Reports"
                    reports_value = str(hist_diag.get(reports_field_name, "") or "").strip()

                    no_reports_indicators = [
                        "i dont have", "i don't have", "dont have", "don't have",
                        "no reports", "no report", "no mri", "no ct", "no x-ray", "no x ray",
                        "no scan", "no scans", "no imaging",
                    ]

                    if (not reports_value) and any(ind in user_lower for ind in no_reports_indicators):
                        self.form["History & Diagnostics"][reports_field_name] = (
                            "No relevant diagnostic reports available for this issue"
                        )
                        print(
                            "[formatter] POST-PROCESSING: User indicated no reports; setting History & Diagnostics.Reports accordingly."
                        )
                    else:
                        # If the LLM hallucinated a vague placeholder like "None mentioned" without
                        # the user explicitly denying reports, clear it so we still ask.
                        placeholder_values = {"none mentioned", "none reported", "not mentioned", "no details mentioned"}
                        if reports_value.lower() in placeholder_values and not any(
                            ind in user_lower for ind in no_reports_indicators
                        ):
                            print(
                                f"[formatter] POST-PROCESSING: Clearing placeholder Reports value '{reports_value}' (no explicit 'no reports' in user input)."
                            )
                            self.form["History & Diagnostics"][reports_field_name] = ""

                    # 2) Systemic Illness and Surgical History: clear placeholders unless user clearly says "no"
                    sys_hist_field = "Systemic Illness and Surgical History"
                    sys_hist_value = str(hist_diag.get(sys_hist_field, "") or "").strip()

                    explicit_no_history_indicators = [
                        "no major health issues",
                        "no health issues",
                        "no medical conditions",
                        "no medical history",
                        "no past surgeries",
                        "no previous surgeries",
                        "no surgeries",
                        "haven't had any surgeries",
                        "no chronic illness",
                        "no chronic illnesses",
                    ]

                    placeholder_history_values = {"none mentioned", "not mentioned", "none reported"}

                    # If LLM set a placeholder like "None mentioned" but the user did NOT explicitly say they have
                    # no illnesses/surgeries, clear it so the section is considered incomplete and can be asked about.
                    if (
                        sys_hist_value
                        and sys_hist_value.lower() in placeholder_history_values
                        and not any(ind in user_lower for ind in explicit_no_history_indicators)
                    ):
                        print(
                            f"[formatter] POST-PROCESSING: Clearing placeholder Systemic Illness and Surgical History value '{sys_hist_value}' (no explicit negative in user input)."
                        )
                        self.form["History & Diagnostics"][sys_hist_field] = ""

                # POST-PROCESSING: Clean up Referral.Source field if LLM filled it with non-informative or wrong values
                if "Referral" in self.form:
                    referral_source = str(self.form["Referral"].get("Source", "") or "").strip()
                    if referral_source:
                        lower_ref = referral_source.lower()
                        # Values like "Yes", "No", "None" are not valid referral sources and
                        # usually come from mis-extraction. Clear them so we still ask the question.
                        invalid_referral_values = {"yes", "no", "none", "n/a", "na", "nil", "nothing", "0"}
                        # Also, clinician/doctor descriptions (e.g. "orthopedic doctor") are NOT referral channels
                        # and typically belong to Previous Consultations, not Referral.
                        clinician_keywords = [
                            "doctor",
                            "dr ",
                            "dr.",
                            "orthopedic",
                            "orthopaedic",
                            "ortho",
                            "physio",
                            "physiotherapist",
                            "surgeon",
                            "consultant",
                            "specialist",
                            "hospital",
                            "clinic",
                            "center",
                            "centre",
                        ]
                        # Genuine referral channels we want to keep (friend, google, instagram, etc.)
                        referral_channel_keywords = [
                            "friend",
                            "family",
                            "relative",
                            "colleague",
                            "coworker",
                            "google",
                            "instagram",
                            "facebook",
                            "youtube",
                            "social media",
                            "online",
                            "website",
                            "ad ",
                            "advert",
                            "advertisement",
                            "referral",
                        ]

                        should_clear = False
                        if lower_ref in invalid_referral_values:
                            should_clear = True
                        else:
                            has_clinician = any(kw in lower_ref for kw in clinician_keywords)
                            has_channel = any(kw in lower_ref for kw in referral_channel_keywords)
                            # If it clearly looks like a clinician description and DOES NOT look like a referral channel,
                            # treat it as mis-extraction and clear so we still ask "How did you come to know about us?"
                            if has_clinician and not has_channel:
                                should_clear = True

                        if should_clear:
                            print(f"[formatter] POST-PROCESSING: Clearing incorrect Referral.Source value '{referral_source}'")
                            self.form["Referral"]["Source"] = ""

                print("Updated form sections after formatting:")
                for section, fields in self.form.items():
                    non_empty = {k: v for k, v in fields.items() if v}
                    if non_empty:
                        print(f"{section}: {non_empty}")

                return self.form
            except json.JSONDecodeError as e:
                print(f"JSON decode error: {e}")
                print(f"Problematic JSON string: {json_str}")
                return self.form

        except Exception as e:
            print(f"Error in formatter: {e}")
            return self.form

    def extract_json_from_response(self, response):
        """Extract valid JSON from an LLM response"""
        if not response:
            return None

        # Check if the entire response is JSON
        response = response.strip()
        if response.startswith("{") and response.endswith("}"):
            return response

        # Try to find JSON within the response
        start = response.find("{")
        end = response.rfind("}")

        if start != -1 and end != -1 and start < end:
            return response[start : end + 1]

        # Handle code block format
        if "```json" in response:
            parts = response.split("```json")
            if len(parts) > 1:
                code_part = parts[1].split("```")[0].strip()
                if code_part.startswith("{") and code_part.endswith("}"):
                    return code_part

        return None

    def classify_summary_response(self, user_input: str) -> dict:
        """
        Use the LLM to understand what the user wants to do AFTER seeing the final summary.
        
        This replaces most keyword-based intent detection in summary mode with a single
        LLM call that can interpret:
        - Whether the user is confirming the summary
        - Whether they want to make changes (and to what)
        - Whether they are saying they have / don't have reports
        - Whether they are asking a question about missing information
        
        Returns a dictionary like:
        {
          "intent": "confirm" | "request_change" | "has_reports" | "no_reports" | "question",
          "correction_text": "<optional free-text description of change>",
          "has_reports": true/false/null,
          "wants_upload": true/false/null
        }
        """
        try:
            summary_text = ""
            # Try to use the last agent message as the current summary if available
            if self.history:
                for msg in reversed(self.history):
                    if msg.get("role") == "agent" and "Here's a summary of the information you've provided" in msg.get(
                        "message", ""
                    ):
                        summary_text = msg["message"]
                        break
            if not summary_text:
                # Fallback: regenerate summary from current form
                summary_text = self.generate_summary()

            prompt = f"""
You are assisting with a medical intake interview. The patient has just been shown the following summary of their information:

SUMMARY:
{summary_text}

The patient now responded with:
"{user_input}"

Classify their intent and respond ONLY with a compact JSON object using this schema:
{{
  "intent": "confirm" | "request_change" | "has_reports" | "no_reports" | "question",
  "correction_text": string | null,
  "has_reports": true | false | null,
  "wants_upload": true | false | null
}}

Rules:
- Use "confirm" if they are basically saying the summary is correct and they don't want changes.
- Use "request_change" if they want to change / update / correct any information (even if they don't say which field yet).
- Use "has_reports" if they indicate they HAVE diagnostic reports available.
- Use "no_reports" if they indicate they do NOT have diagnostic reports.
- Use "question" if they are asking about the process or what's needed (e.g., 'do I need to upload reports?').
- Put any free-text description of what should change into "correction_text" (or null if not clear).
- If they mention reports AND want to upload them, set "has_reports": true and "wants_upload": true.
"""

            raw_response = self.llm_complete(prompt)
            json_str = self.extract_json_from_response(raw_response)
            if not json_str:
                return {}

            data = json.loads(json_str)
            if not isinstance(data, dict):
                return {}

            # Normalize keys and defaults
            intent = data.get("intent")
            if intent not in ["confirm", "request_change", "has_reports", "no_reports", "question"]:
                return {}

            result = {
                "intent": intent,
                "correction_text": data.get("correction_text"),
                "has_reports": data.get("has_reports"),
                "wants_upload": data.get("wants_upload"),
            }
            return result
        except Exception as e:
            print(f"[classify_summary_response] Error classifying summary response: {e}")
            return {}

    def classify_reports_intent(self, user_input: str) -> dict:
        """
        Use the LLM to understand whether the user has diagnostic reports
        (and whether they are willing to upload them) during the normal
        interview flow (not just summary mode).

        Returns a dictionary like:
        {
          "has_reports": true | false | null,
          "wants_upload": true | false | null
        }
        """
        try:
            # Heuristic: only force has_reports=True if user explicitly mentions HAVING reports
            # Not just mentioning test names, but actually indicating they have results/reports
            lowered = user_input.lower()
            
            # Keywords that indicate they HAVE reports (not just mentioning test types)
            has_reports_indicators = [
                "i have reports",
                "i have my",
                "i have an mri",
                "i have x-ray",
                "i have x ray",
                "i have ct",
                "i have scans",
                "i got reports",
                "i got my",
                "i have the reports",
                "i have the results",
                "my reports",
                "my mri",
                "my x-ray",
                "my x ray",
                "my ct scan",
                "the reports",
                "the mri",
                "the x-ray",
                "reports are",
                "reports show",
                "mri shows",
                "x-ray shows",
                "x ray shows",
                "ct shows",
                "upload",
                "uploaded",
            ]
            
            # Test type keywords (but these alone don't mean they have reports)
            test_keywords = [
                "x-ray",
                "x ray",
                "mri",
                "ct",
                "ct scan",
                "scan",
                "scans",
                "imaging",
                "blood test",
                "blood tests",
                "lab report",
                "lab reports",
                "reports",
            ]
            
            # Only force has_reports if they explicitly indicate they HAVE reports
            explicitly_has_reports = any(indicator in lowered for indicator in has_reports_indicators)
            mentions_any_test = any(k in lowered for k in test_keywords)

            prompt = f"""
You are assisting with a medical intake interview.

The patient just said:
"{user_input}"

From this single message, decide ONLY:
1. Whether they have ANY diagnostic reports related to this issue (MRI, X-ray, CT, scans, lab reports, etc.).
2. Whether they are willing to upload them now.

Respond ONLY with a JSON object like:
{{
  "has_reports": true | false | null,
  "wants_upload": true | false | null
}}

CRITICAL RULES:
- "has_reports" should be true ONLY if they EXPLICITLY mention:
  * Having reports (e.g., "I have X-ray reports", "I have my MRI", "I got reports")
  * Mentioning a specific test type in the context of having results (e.g., "My X-ray showed...", "The MRI revealed...")
  * Talking about uploading or sharing reports
  
- "has_reports" should be FALSE ONLY if they EXPLICITLY say they do NOT have reports:
  * "I don't have reports", "I don't have any reports", "No reports", "I have no reports", etc.
  
- "has_reports" should be NULL if:
  * They are just describing symptoms, pain, or their condition (e.g., "pain is 6/10", "located in the knee")
  * They mention test types but NOT in the context of having reports (e.g., "I need an MRI", "should I get an X-ray?")
  * They are describing their medical history without mentioning reports
  * They haven't mentioned reports at all - use null, NOT false

- "wants_upload" should be true only if they clearly want to upload now.
- "wants_upload" should be false if they clearly don't want to upload now.
- Otherwise set "wants_upload" to null.

IMPORTANT: Do NOT set "has_reports": true just because they mention medical terms, pain descriptions, or symptoms. Only set it to true if they explicitly indicate they HAVE reports or test results.
"""

            raw_response = self.llm_complete(prompt)
            json_str = self.extract_json_from_response(raw_response)
            if not json_str:
                return {}

            data = json.loads(json_str)
            if not isinstance(data, dict):
                return {}

            has_reports = data.get("has_reports")
            wants_upload = data.get("wants_upload")

            # Only force has_reports=True if user explicitly indicates they HAVE reports
            # Don't force it just because they mentioned test types (they might be asking about tests, not saying they have results)
            if explicitly_has_reports and has_reports is not False:
                has_reports = True
                print(f"[classify_reports_intent] Heuristic: User explicitly mentioned having reports, forcing has_reports=True")
            elif mentions_any_test and has_reports is None:
                # If they mentioned test types but LLM returned null, don't force it
                # Let the LLM's judgment stand (which should be null or false if they're just describing symptoms)
                print(f"[classify_reports_intent] User mentioned test types but didn't explicitly say they have reports. LLM returned null, not forcing has_reports=True")

            return {
                "has_reports": has_reports,
                "wants_upload": wants_upload,
            }
        except Exception as e:
            print(f"[classify_reports_intent] Error classifying reports intent: {e}")
            return {}

        # Handle regular code block
        if "```" in response:
            parts = response.split("```")
            if len(parts) > 1:
                code_part = parts[1].strip()
                if code_part.startswith("{") and code_part.endswith("}"):
                    return code_part

        return None

    def should_check_for_correction(self, user_input: str) -> bool:
        """
        Determine if we should check for corrections based on the user input
        and conversation context.
        
        Only check for corrections if the user explicitly uses correction language.
        This prevents false positives when users are just answering new questions.
        
        Args:
            user_input: The user's input text
            
        Returns:
            Boolean indicating whether to run correction detection
        """
        # Simple confirmation words - don't check for corrections
        simple_confirmations = {
            'yes', 'no', 'ok', 'okay', 'sure', 'correct', 'right',
            'yep', 'yeah', 'nope', 'nah', 'fine', 'good', 'yup',
            'alright', 'affirmative', 'negative'
        }
        
        # Strip whitespace and punctuation for comparison
        input_lower = user_input.lower().strip().strip('.,!?;:')
        
        # If it's a simple one-word confirmation, skip correction detection
        if input_lower in simple_confirmations:
            print(f"[should_check_for_correction] Skipping correction detection - simple confirmation: '{user_input}'")
            return False
        
        # If we're awaiting confirmation and the response is short (3 words or less),
        # skip correction detection
        if self.conversation_state.get('awaiting_confirmation', False):
            word_count = len(user_input.split())
            if word_count <= 3:
                # Check if it's a simple yes/no response
                words_lower = set(word.lower().strip('.,!?;:') for word in user_input.split())
                if words_lower.intersection(simple_confirmations):
                    print(f"[should_check_for_correction] Skipping correction detection - awaiting confirmation and got simple response: '{user_input}'")
                    return False
        
        # CRITICAL: Only check for corrections if user explicitly uses correction language
        # This prevents false positives when users are just answering new questions
        correction_keywords = [
            'actually', 'sorry', 'correction', 'correct', 'wrong', 'mistake',
            'meant', 'meant to say', 'i said', 'i meant', 'let me correct',
            'change', 'update', 'to clarify', 'clarify', 'misspoke', 'i misspoke',
            'instead', 'rather'
        ]
        
        # Explicit correction patterns that indicate user is correcting something
        correction_patterns = [
            'not x,', 'not x ', 'not x.',  # "not X, it's Y" pattern
            'was not', 'wasn\'t', 'it was not', 'it wasn\'t',
            'it\'s not', 'its not', 'is not', 'isn\'t',
            'not that', 'not the',
            'sorry, not', 'actually, not', 'i meant', 'i said',
            'change', 'update', 'correct that', 'fix that'
        ]
        
        input_lower_words = input_lower.split()
        has_correction_language = False
        
        # Check if any correction keyword appears in the input
        for keyword in correction_keywords:
            if keyword in input_lower:
                has_correction_language = True
                break
        
        # Check for explicit correction patterns
        if not has_correction_language:
            for pattern in correction_patterns:
                if pattern in input_lower:
                    has_correction_language = True
                    break
        
        # Special check for "not X, it's Y" or "not X, but Y" patterns
        # Only if it's clearly a correction pattern, not just describing status
        if not has_correction_language and 'not' in input_lower_words:
            # Look for patterns like "not X, it's Y" or "not X, but Y" where X and Y are different values
            # This indicates explicit correction, not just status description
            not_index = input_lower_words.index('not')
            if not_index < len(input_lower_words) - 2:
                # Check if "not" is part of a correction pattern
                # Must have "it's", "but", "actually", "i meant" nearby to be a correction
                context_words = input_lower_words[max(0, not_index-2):min(len(input_lower_words), not_index+5)]
                context_str = ' '.join(context_words)
                if any(word in context_str for word in ['but', 'actually', 'it\'s', 'its', 'i meant', 'i said', 'sorry']):
                    has_correction_language = True
        
        # Exclude common status descriptions that contain "not" but aren't corrections
        status_descriptions = [
            'not improved', 'not getting worse', 'not better', 'not worse',
            'remained the same', 'stayed the same', 'is the same', 'has been the same',
            'condition is not', 'status is not', 'it\'s not improved', 'it\'s not worse'
        ]
        for status_desc in status_descriptions:
            if status_desc in input_lower:
                # This is describing status, not correcting
                has_correction_language = False
                break
        
        if not has_correction_language:
            print(f"[should_check_for_correction] Skipping correction detection - no explicit correction language in: '{user_input}'")
            return False
        
        print(f"[should_check_for_correction] Checking for correction - explicit correction language detected in: '{user_input}'")
        return True

    def detect_correction(self, user_message, is_summary_mode=False):
        """
        Detect if the user is trying to correct previously provided information.
        
        Args:
            user_message: The user's message text
            is_summary_mode: If True, be more lenient in detecting corrections
                            (user is explicitly in "make changes" mode)
            
        Returns:
            Dictionary with correction details or None if not a correction
        """
        try:
            # Use a more lenient prompt if in summary confirmation mode
            if is_summary_mode:
                # In summary mode, user is reviewing their form and wants to make changes
                # Be very lenient - any mention of correcting/updating/changing a field is a correction
                # OR any direct statement that provides information for a field that's already filled
                prompt = f"""
The user is reviewing their medical form summary and wants to make changes.

User message: "{user_message}"

Current form data:
{json.dumps(self.form, indent=2)}

CRITICAL: The user is in summary review mode. They are correcting or updating information they previously provided.

Analyze their message to identify:
1. Which field or section they want to change
2. What the new value should be
3. What the current (old) value is (from the form data above)

EXAMPLES OF CORRECTIONS TO DETECT:

Explicit corrections:
- "Sorry I had mentioned my referral source as Instagram. I actually had known about you guys via YouTube."
  → section_name: "Referral", field_name: "Source", old_value: "Instagram", new_value: "YouTube"
  
- "I said the duration was 2 weeks, but it's actually 2 months"
  → section_name: "Present Complaint", field_name: "Duration of the Issue", old_value: "2 weeks", new_value: "2 months"
  
- "Actually, the pain level is 6, not 8"
  → section_name: "Pain Assessment", field_name: "Severity (1-10)", old_value: "8", new_value: "6"

- "I mentioned Instagram but it should be YouTube"
  → section_name: "Referral", field_name: "Source", old_value: "Instagram", new_value: "YouTube"

Direct statements (also corrections in summary mode):
- "i got to know you via Instagram" or "I got to know you via Instagram"
  → section_name: "Referral", field_name: "Source", old_value: (current value from form), new_value: "Instagram"
  
- "I was referred by my friend"
  → section_name: "Referral", field_name: "Source", old_value: (current value from form), new_value: "Friend"
  
- "The pain is 7 out of 10"
  → section_name: "Pain Assessment", field_name: "Severity (1-10)", old_value: (current value from form), new_value: "7"

IMPORTANT:
- In summary mode, ANY direct statement that provides information for a field that already has a value should be treated as a correction
- Look for phrases like "I had mentioned", "I said", "actually", "sorry", "not X, it's Y" (explicit corrections)
- ALSO look for direct statements like "i got to know you via X", "I was referred by Y", "the pain is Z" (implicit corrections)
- Match field names mentioned (like "referral source", "duration", "pain level") to actual field names in the form
- If the user provides information that matches a field in the form, it IS a correction - set is_correction to true

Respond with a JSON object:
{{
    "is_correction": true/false,
    "confidence": "high"/"medium"/"low",
    "old_value": "current value from form (if identifiable)",
    "new_value": "what they want to change it to",
    "field_name": "exact field name from form (if identifiable)",
    "section_name": "exact section name from form (if identifiable)",
    "needs_clarification": true/false,
    "clarification_question": "question to ask if ambiguous (or empty string)"
}}

If you cannot identify which field to update with confidence, set needs_clarification to true.
"""
            else:
                # Format the prompt with user message and current form (normal mode)
                prompt = CORRECTION_DETECTION_PROMPT.format(
                    user_message,
                    json.dumps(self.form, indent=2)
                )
            
            # Get LLM response
            llm_response = self.llm_complete(prompt)
            print(f"[detect_correction] LLM Response: {llm_response}")
            
            # Extract JSON from response
            json_str = self.extract_json_from_response(llm_response)
            
            if not json_str:
                print("[detect_correction] No valid JSON found in LLM response")
                return None
            
            # Parse the correction details
            correction_data = json.loads(json_str)
            print(f"[detect_correction] Parsed correction data: {correction_data}")
            
            # Return correction data if it's actually a correction
            if correction_data.get("is_correction", False):
                print(f"[detect_correction] ✓ Correction detected successfully: {correction_data}")
                return correction_data
            else:
                print(f"[detect_correction] ✗ Not detected as correction (is_correction=False)")
            
            return None
            
        except Exception as e:
            print(f"[detect_correction] Error detecting correction: {e}")
            return None

    def generate_summary(self) -> str:
        """
        Generate a patient-friendly, narrative summary of the filled form
        for user confirmation. Uses the LLM to turn the structured form
        into an easy-to-read story with a few key bullet points.
        
        Returns:
            A formatted summary string
        """
        try:
            # Use LLM to generate a patient-friendly narrative summary.
            # Include the full form JSON as context so the model can see all fields.
            form_json = json.dumps(self.form, indent=2, ensure_ascii=False)

            prompt = f"""
You are an empathetic medical assistant. Convert the structured medical intake
form below into a short, patient-friendly summary.

FORM DATA (JSON):
{form_json}

Write the summary as if you are explaining it to the patient in simple,
everyday language. Follow this style:

1. Start with 1–2 short paragraphs that narrate the overall story:
   - What the main problem is
   - How long it has been going on
   - How it started
   - Any important pain details (location, severity, better/worse factors)
   - Any relevant past consultations or lack of them
   - Any important history (health conditions, lifestyle, reports)
   - The patient's goals and how they want to improve

2. After the narrative, add a small **Key points** section with 3–7 bullet points
   that highlight the most important facts (problem, duration, severity,
   key history, goals, and referral source).

3. Avoid very technical language. Prefer phrases like:
   - "no known health conditions" instead of long lists of negatives
   - "doesn't drink or smoke" instead of "non-smoker, non-drinker"

4. NEVER leave raw field names or placeholders in square brackets like "[Duration of the Issue]".
   If a value is present in the form, use the actual value in natural language.
   If a value is missing, either:
     - briefly say it has not been mentioned yet (e.g., "you haven't mentioned any other health conditions yet"), OR
     - simply omit it from the narrative and key points.

5. Do NOT use markdown formatting characters like **, __, or backticks.
   Use plain text only. For bullet points, start lines with "- ".

6. Do NOT repeat section headings like "Present Complaint" or field names verbatim.
   Integrate them naturally into the story.

7. End with this exact question on a new paragraph:
   "Is this information correct, or would you like to make any changes?"

8. CRITICAL: You MUST ALWAYS generate a summary. NEVER say:
   - "The form is complete"
   - "No further questions are needed"
   - "All fields are filled"
   - "The interview is finished"
   - "We have all the information"
   - Any variation of these phrases
   
   You MUST ALWAYS present a narrative summary of the information, followed by key points,
   and then end with the confirmation question. There is NO exception to this rule.

Return ONLY the final summary text (paragraphs + plain-text bullets),
no extra explanation and no markdown symbols.
"""

            llm_summary = self.llm_complete(prompt)
            summary_text = self.ensure_string(llm_summary).strip()

            # As a safeguard, strip any stray markdown bold markers if the model added them.
            summary_text = summary_text.replace("**", "")

            # CRITICAL: Post-process to remove any "form is complete" or similar messages
            # If the LLM generated such a message, replace it with a proper summary
            forbidden_phrases = [
                "the form is complete",
                "no further questions are needed",
                "all fields are filled",
                "the interview is finished",
                "we have all the information",
                "no further questions",
                "form is complete"
            ]
            
            summary_lower = summary_text.lower()
            has_forbidden_phrase = any(phrase in summary_lower for phrase in forbidden_phrases)
            
            if has_forbidden_phrase:
                print(f"[generate_summary] WARNING: LLM generated forbidden 'form complete' message. Regenerating summary with stricter prompt.")
                # Regenerate with even stricter instructions
                strict_prompt = f"""
You are an empathetic medical assistant. Convert the structured medical intake form below into a short, patient-friendly summary.

FORM DATA (JSON):
{form_json}

CRITICAL REQUIREMENTS:
1. You MUST generate a narrative summary - this is NOT optional.
2. Start with 1-2 paragraphs narrating the patient's story (problem, duration, onset, pain details, consultations, history, goals).
3. Add a "Key points" section with 3-7 bullet points.
4. End with: "Is this information correct, or would you like to make any changes?"

ABSOLUTELY FORBIDDEN PHRASES (DO NOT USE THESE):
- "The form is complete"
- "No further questions are needed"
- "All fields are filled"
- "The interview is finished"
- Any variation of these

If you see these phrases in your response, you have made an error. Generate a proper summary instead.

Return ONLY the summary text (narrative + key points + confirmation question).
"""
                summary_text = self.ensure_string(self.llm_complete(strict_prompt)).strip()
                summary_text = summary_text.replace("**", "")
                
                # Double-check after regeneration
                summary_lower = summary_text.lower()
                if any(phrase in summary_lower for phrase in forbidden_phrases):
                    print(f"[generate_summary] ERROR: LLM still generated forbidden phrase after regeneration. Using fallback summary.")
                    # Use fallback summary
                    summary_parts = []
                    summary_parts.append("Here's a summary of the information you've provided:\n")
                    for section_name, section_fields in self.form.items():
                        if isinstance(section_fields, dict):
                            has_data = any(value and str(value).strip() for value in section_fields.values())
                            if has_data:
                                summary_parts.append(f"\n{section_name}:")
                                for field_name, field_value in section_fields.items():
                                    if field_value and str(field_value).strip():
                                        summary_parts.append(f"- {field_name}: {field_value}")
                    summary_parts.append("\nIs this information correct, or would you like to make any changes?")
                    summary_text = "\n".join(summary_parts)

            if not summary_text:
                raise ValueError("Empty summary from LLM")
            
            return summary_text
            
        except Exception as e:
            print(f"[generate_summary] Error generating summary: {e}")
            # Fallback to simple structured format if LLM fails
            summary_parts = []
            summary_parts.append("Here's a summary of the information you've provided:\n")
            for section_name, section_fields in self.form.items():
                if isinstance(section_fields, dict):
                    has_data = any(value and str(value).strip() for value in section_fields.values())
                    if has_data:
                        summary_parts.append(f"\n**{section_name}:**")
                        for field_name, field_value in section_fields.items():
                            if field_value and str(field_value).strip():
                                summary_parts.append(f"- {field_name}: {field_value}")
            summary_parts.append("\nIs this information correct, or would you like to make any changes?")
            return "\n".join(summary_parts)

    def _is_semantically_compatible(self, old_value: str, new_value: str, target_field_name: str) -> bool:
        """
        Check if old_value and new_value are semantically compatible for the target field.
        Prevents propagating incompatible value types (e.g., duration -> status).
        
        Args:
            old_value: The old value being corrected
            new_value: The new value
            target_field_name: The field name where we're considering propagation
            
        Returns:
            True if values are semantically compatible, False otherwise
        """
        old_lower = old_value.lower()
        new_lower = new_value.lower()
        field_lower = target_field_name.lower()
        
        # Status fields should only accept status values
        status_fields = ['status', 'improved', 'same', 'worse', 'better']
        status_values = ['improved', 'same', 'worse', 'better', 'not improved', 'not worse', 'not better']
        is_status_field = any(keyword in field_lower for keyword in status_fields)
        is_status_old = any(val in old_lower for val in status_values)
        is_status_new = any(val in new_lower for val in status_values)
        
        if is_status_field:
            # Status field should only get status values
            if not (is_status_old and is_status_new):
                return False
        elif is_status_old and is_status_new:
            # Status values should only go to status fields
            if not is_status_field:
                return False
        
        # Duration fields should only accept duration/time values
        duration_fields = ['duration', 'time', 'period', 'how long', 'since']
        duration_patterns = [r'\d+\s*(day|days|week|weeks|month|months|year|years|hour|hours)', 
                            r'\d+\s*(d|w|m|y)', 'one day', 'two days', 'few days']
        is_duration_field = any(keyword in field_lower for keyword in duration_fields)
        is_duration_old = any(re.search(pattern, old_lower) for pattern in duration_patterns)
        is_duration_new = any(re.search(pattern, new_lower) for pattern in duration_patterns)
        
        if is_duration_field:
            # Duration field should only get duration values
            if not (is_duration_old and is_duration_new):
                return False
        elif is_duration_old and is_duration_new:
            # Duration values should only go to duration fields
            if not is_duration_field:
                return False
        
        # Location fields should accept location values (right/left + body part)
        location_fields = ['location', 'where', 'knee', 'leg', 'arm', 'shoulder', 'back', 'neck']
        location_patterns = [r'\b(right|left)\s+\w+', r'\b\w+\s+(right|left)']
        is_location_field = any(keyword in field_lower for keyword in location_fields)
        is_location_old = any(re.search(pattern, old_lower) for pattern in location_patterns)
        is_location_new = any(re.search(pattern, new_lower) for pattern in location_patterns)
        
        # If both are locations, they're compatible (e.g., "right knee" -> "left knee")
        if is_location_old and is_location_new:
            return True
        
        # If old is location but new is not, don't propagate to non-location fields
        if is_location_old and not is_location_new and not is_location_field:
            return False
        
        # If new is location but old is not, don't propagate to location fields
        if is_location_new and not is_location_old and is_location_field:
            return False
        
        # If values are completely different semantic types, don't propagate
        # (e.g., "one day" is duration, "same" is status - incompatible)
        if (is_duration_old and is_status_new) or (is_status_old and is_duration_new):
            return False
        
        return True

    def _propagate_correction(self, old_value: str, new_value: str, primary_section: str, primary_field: str) -> list:
        """
        Propagate a correction to all fields that contain the old value.
        This ensures consistency across the form when a key piece of information changes.
        
        CRITICAL: Only propagates when values are semantically compatible to prevent
        incorrect updates (e.g., don't change "one day" to "same" in duration fields).
        
        Args:
            old_value: The old value to replace
            new_value: The new value to use
            primary_section: The section where the correction was made (to skip)
            primary_field: The field where the correction was made (to skip)
            
        Returns:
            List of field paths that were updated
        """
        updated_fields = []
        
        try:
            import re
            
            # Extract key terms for smart matching
            # For "Right leg, under the knee" -> extract "right leg" and "left leg"
            old_lower = old_value.lower()
            new_lower = new_value.lower()
            
            # Try to extract the core term being corrected
            # Common patterns: "right X" -> "left X", "X leg" -> "Y leg", etc.
            core_old_terms = []
            core_new_terms = []
            
            # Pattern 1: "right/left" + body part
            if "right" in old_lower and "left" in new_lower:
                # Extract "right [body_part]" from old_value
                match = re.search(r'\b(right\s+\w+)', old_lower)
                if match:
                    core_old_terms.append(match.group(1))
                    # Corresponding "left [body_part]" in new_value
                    body_part = match.group(1).replace("right", "").strip()
                    core_new_terms.append(f"left {body_part}")
            elif "left" in old_lower and "right" in new_lower:
                # Extract "left [body_part]" from old_value
                match = re.search(r'\b(left\s+\w+)', old_lower)
                if match:
                    core_old_terms.append(match.group(1))
                    # Corresponding "right [body_part]" in new_value
                    body_part = match.group(1).replace("left", "").strip()
                    core_new_terms.append(f"right {body_part}")
            
            # If we couldn't extract core terms, use the full values
            if not core_old_terms:
                core_old_terms = [old_value]
                core_new_terms = [new_value]
            
            print(f"[_propagate_correction] Core terms: {core_old_terms} -> {core_new_terms}")
            
            # Check all sections and fields
            for section_name, section_fields in self.form.items():
                if isinstance(section_fields, dict):
                    for field_name, field_value in section_fields.items():
                        # Skip the field we just updated
                        if section_name == primary_section and field_name == primary_field:
                            continue
                        
                        # Skip empty fields
                        if not field_value or not str(field_value).strip():
                            continue
                        
                        field_value_str = str(field_value)
                        field_value_lower = field_value_str.lower()
                        
                        # CRITICAL: Check semantic compatibility before propagating
                        # Don't propagate if values are incompatible (e.g., duration -> status)
                        if not self._is_semantically_compatible(old_value, new_value, field_name):
                            print(f"[_propagate_correction] Skipping {section_name}.{field_name} - semantic incompatibility: '{old_value}' -> '{new_value}'")
                            continue
                        
                        # Try to replace using core terms first
                        new_field_value = field_value_str
                        for old_term, new_term in zip(core_old_terms, core_new_terms):
                            if old_term.lower() in field_value_lower:
                                # Create a case-insensitive pattern
                                pattern = re.compile(re.escape(old_term), re.IGNORECASE)
                                new_field_value = pattern.sub(new_term, new_field_value)
                        
                        # If core terms didn't match, try the full old_value
                        if new_field_value == field_value_str and old_lower in field_value_lower:
                            pattern = re.compile(re.escape(old_value), re.IGNORECASE)
                            new_field_value = pattern.sub(new_value, field_value_str)
                        
                        if new_field_value != field_value_str:
                            self.form[section_name][field_name] = new_field_value
                            updated_fields.append(f"{section_name}.{field_name}")
                            print(f"[_propagate_correction] Updated {section_name}.{field_name}: '{field_value_str}' -> '{new_field_value}'")
            
            return updated_fields
            
        except Exception as e:
            print(f"[_propagate_correction] Error propagating correction: {e}")
            import traceback
            traceback.print_exc()
            return updated_fields

    def apply_correction(self, correction_data, is_summary_mode=False):
        """
        Apply a correction to the form.
        
        Args:
            correction_data: Dictionary containing correction details
            is_summary_mode: If True, don't set awaiting_confirmation (stay in summary mode)
            
        Returns:
            Tuple of (success: bool, message: str)
        """
        try:
            field_name = correction_data.get("field_name", "")
            section_name = correction_data.get("section_name", "")
            old_value = correction_data.get("old_value", "")
            new_value = correction_data.get("new_value", "")
            
            # Check if we need clarification
            if correction_data.get("needs_clarification", False):
                clarification_q = correction_data.get("clarification_question", "")
                if clarification_q:
                    return (False, clarification_q)
                else:
                    return (False, "I understand you want to make a correction, but I'm not sure which field to update. Could you please be more specific?")
            
            # Validate that we have the necessary information
            if not field_name or not section_name:
                return (False, "I understand you want to make a correction, but I'm not sure which field to update. Could you please specify what you'd like to change?")
            
            # Check if the section and field exist
            if section_name not in self.form:
                return (False, f"I couldn't find the section '{section_name}' in the form. Could you clarify what you'd like to change?")
            
            if field_name not in self.form[section_name]:
                return (False, f"I couldn't find the field '{field_name}' in the '{section_name}' section. Could you clarify what you'd like to change?")
            
            # Apply the correction using LLM to ensure proper formatting
            prompt = CORRECTION_APPLY_PROMPT.format(
                field_name,
                section_name,
                old_value,
                new_value,
                json.dumps(self.form, indent=2),
                section_name,  # Repeated for STEPS section
                field_name,    # Repeated for STEPS section
                new_value      # Repeated for STEPS section
            )
            
            llm_response = self.llm_complete(prompt)
            print(f"[apply_correction] LLM Response: {llm_response[:500]}...")
            json_str = self.extract_json_from_response(llm_response)
            
            if json_str:
                updated_form = json.loads(json_str)
                print(f"[apply_correction] Successfully parsed updated form")
                
                # VALIDATION: Verify the correct field was updated
                if section_name in updated_form and field_name in updated_form[section_name]:
                    actual_new_value = updated_form[section_name][field_name]
                    print(f"[apply_correction] Field {section_name}.{field_name} updated from '{old_value}' to '{actual_new_value}' (requested: '{new_value}')")
                    
                    # Check if the field was actually updated to the new value
                    if actual_new_value != new_value:
                        print(f"[apply_correction] WARNING: LLM updated field to '{actual_new_value}' instead of '{new_value}'")
                        print(f"[apply_correction] Forcing correct value...")
                        updated_form[section_name][field_name] = new_value
                        print(f"[apply_correction] ✓ Value corrected to '{new_value}'")
                    
                    # Additional check: Make sure old_value wasn't moved to another field
                    if old_value and str(old_value).strip():
                        for sec_name, sec_fields in updated_form.items():
                            if isinstance(sec_fields, dict):
                                for fld_name, fld_value in sec_fields.items():
                                    # If we find the old value in a different field, that's wrong
                                    if (sec_name != section_name or fld_name != field_name) and str(fld_value) == str(old_value):
                                        print(f"[apply_correction] ERROR: Found old value '{old_value}' in wrong field {sec_name}.{fld_name}")
                                        print(f"[apply_correction] This should have been cleared. Fixing...")
                                        # Don't clear it - it might be legitimately there
                                        # Just log the warning
                                        print(f"[apply_correction] WARNING: '{old_value}' exists in {sec_name}.{fld_name} - may need manual review")
                    
                    self.form = updated_form
                    
                    # CRITICAL: Propagate the correction to all related fields
                    # If the correction involves a key piece of information (like body part),
                    # update all fields that contain the old value
                    if old_value and str(old_value).strip() and new_value:
                        fields_updated = self._propagate_correction(old_value, new_value, section_name, field_name)
                        if fields_updated:
                            print(f"[apply_correction] Propagated correction to {len(fields_updated)} additional fields: {fields_updated}")
                    
                    # Generate confirmation message
                    if is_summary_mode:
                        # In summary mode, just confirm the change without asking for confirmation
                        confirmation_msg = f"I've updated '{field_name}' from '{old_value if old_value else '(empty)'}' to '{new_value}'."
                    else:
                        confirmation_msg = CORRECTION_CONFIRMATION_PROMPT.format(
                            field_name,
                            old_value if old_value else "(empty)",
                            new_value
                        )
                        # Set conversation state to await confirmation (only if not in summary mode)
                        self.conversation_state['awaiting_confirmation'] = True
                        self.conversation_state['last_message_type'] = 'confirmation_request'
                    
                    print(f"[apply_correction] Successfully updated {section_name}.{field_name} to '{new_value}'")
                    return (True, confirmation_msg)
                else:
                    print(f"[apply_correction] ERROR: Field {section_name}.{field_name} not found in LLM response")
                    # Fallback to direct update
                    self.form[section_name][field_name] = new_value
                    
                    # Propagate the correction to all related fields
                    if old_value and str(old_value).strip() and new_value:
                        fields_updated = self._propagate_correction(old_value, new_value, section_name, field_name)
                        if fields_updated:
                            print(f"[apply_correction] Propagated correction to {len(fields_updated)} additional fields: {fields_updated}")
                    
                    if is_summary_mode:
                        confirmation_msg = f"I've updated '{field_name}' to '{new_value}'."
                    else:
                        confirmation_msg = f"I've updated '{field_name}' to '{new_value}'. Is this correct?"
                        # Set conversation state to await confirmation (only if not in summary mode)
                        self.conversation_state['awaiting_confirmation'] = True
                        self.conversation_state['last_message_type'] = 'confirmation_request'
                    
                    print(f"[apply_correction] Used fallback - directly updated {section_name}.{field_name}")
                    return (True, confirmation_msg)
            else:
                # Fallback: directly update the field
                self.form[section_name][field_name] = new_value
                
                # Propagate the correction to all related fields
                if old_value and str(old_value).strip() and new_value:
                    fields_updated = self._propagate_correction(old_value, new_value, section_name, field_name)
                    if fields_updated:
                        print(f"[apply_correction] Propagated correction to {len(fields_updated)} additional fields: {fields_updated}")
                
                if is_summary_mode:
                    confirmation_msg = f"I've updated '{field_name}' to '{new_value}'."
                else:
                    confirmation_msg = f"I've updated '{field_name}' to '{new_value}'. Is this correct?"
                    # Set conversation state to await confirmation (only if not in summary mode)
                    self.conversation_state['awaiting_confirmation'] = True
                    self.conversation_state['last_message_type'] = 'confirmation_request'
                
                print(f"[apply_correction] Directly updated {section_name}.{field_name}")
                return (True, confirmation_msg)
                
        except Exception as e:
            print(f"[apply_correction] Error applying correction: {e}")
            return (False, "I encountered an error while trying to apply your correction. Could you please try again?")

    def validator(self, current_section=None):
        """
        Validate the form data for the current section.

        Args:
            current_section: Section to validate (defaults to self.current_section)

        Returns:
            Boolean indicating if all required fields are filled
        """
        section = current_section or self.current_section

        # Reset missing fields
        self.missing_fields = []

        # Special handling for "Previous Consultations" section
        if section == "Previous Consultations":
            previous_consultations_field = "Previous Diagnosis or Advice and Prescribed Treatment Taken"
            status_field = "Current Status of Issue (Improved, Same, Worse)"
            
            previous_value = self.form[section].get(previous_consultations_field, "").strip()
            status_value = self.form[section].get(status_field, "").strip()
            
            # CRITICAL: Check if data was incorrectly extracted
            # If status is filled but no consultations mentioned, it's likely incorrect extraction
            consultation_keywords = [
                "doctor", "physiotherapist", "hospital", "consulted", "visited",
                "diagnosis", "diagnosed", "prescribed", "treatment", "medicine",
                "injection", "exercise", "physio", "clinic"
            ]
            has_consultation_mention = False
            if previous_value:
                has_consultation_mention = any(
                    keyword in previous_value.lower() 
                    for keyword in consultation_keywords
                )
            
            # If status is filled but no consultations mentioned, clear it and mark as incomplete
            if status_value and not previous_value and not has_consultation_mention:
                print(f"[validator] WARNING: Status field filled but no consultations mentioned. This is incorrect extraction. Clearing.")
                self.form[section][status_field] = ""
                self.missing_fields.append(previous_consultations_field)
                self.missing_fields.append(status_field)
                return False
            
            # Check if user indicated no previous consultations
            previous_value_lower = previous_value.lower() if previous_value else ""
            no_consultation_indicators = [
                "no previous",
                "didn't visit",
                "did not visit",
                "haven't consulted",
                "have not consulted",
                "no consultations",
                "no doctor",
                "no hospital",
                "never consulted",
                "not consulted",
                "none",
                "nothing"
            ]
            
            has_no_consultations = any(indicator in previous_value_lower for indicator in no_consultation_indicators)
            
            # CRITICAL: If no previous consultations indicated, both fields should be set to "None" or the first field should have the "no" indicator
            if has_no_consultations:
                print(f"[validator] Detected no previous consultations")
                # If first field has "no" indicator, status field should be empty or "None"
                # Section is complete if first field has the indicator
                if not previous_value:
                    self.missing_fields.append(previous_consultations_field)
            else:
                # Normal validation - BOTH fields must be filled
                # If only status is filled but no consultations mentioned, it's incomplete
                if not previous_value and status_value:
                    # Status field filled but no consultations mentioned - this is incorrect extraction
                    print(f"[validator] WARNING: Status field filled but no consultations mentioned. Clearing incorrect data.")
                    # Clear the status field as it was incorrectly extracted
                    self.form[section][status_field] = ""
                    self.missing_fields.append(previous_consultations_field)
                    self.missing_fields.append(status_field)
                elif not previous_value:
                    # First field is empty
                    self.missing_fields.append(previous_consultations_field)
                elif not status_value:
                    # Status field is empty (but consultations were mentioned)
                    self.missing_fields.append(status_field)
        else:
            # For all other sections, check all fields normally
            for field, value in self.form[section].items():
                if not value:
                    self.missing_fields.append(field)

        print(f"Missing fields in {section}: {self.missing_fields}")

        # If there are missing fields, return False
        return len(self.missing_fields) == 0

    def all_ops(self):
        """
        Perform all operations after successful validation of a section.
        """
        # Save current progress to file
        self.save_progress()

        # Move to next section if available
        current_index = self.form_sections.index(self.current_section)
        if current_index < len(self.form_sections) - 1:
            self.current_section = self.form_sections[current_index + 1]
            self.conversation_state["attempts"] = 0
            self.conversation_state["last_question"] = None
            print(f"Moving to next section: {self.current_section}")

    def save_progress(self):
        """
        Save the current form state to a file.
        NOTE: Disabled when using MongoDB as the source of truth.
        Data is saved to MongoDB via save_customer_info() in server.py instead.
        """
        # Disabled - we're using MongoDB as the source of truth
        # This JSON file should not be used to restore data when using database storage
        return

    def talk_to_user(self, prompt_template):
        """
        Generate appropriate response based on the conversation state.

        Args:
            prompt_template: The prompt to use for generating the response
                           (can be either a prompt for LLM or the final question text)

        Returns:
            Response text to present to the user
        """
        # Update conversation state
        self.conversation_state["attempts"] += 1

        try:
            # Handle SKIP_SECTION - should not reach here, but handle as fallback
            if prompt_template == "SKIP_SECTION":
                print(f"[talk_to_user] WARNING: Received SKIP_SECTION, this should be handled earlier")
                # Check if form is complete, if so show summary
                if self.invalid_index() and not self.conversation_state.get('awaiting_summary_confirmation', False):
                    summary = self.generate_summary()
                    self.conversation_state['awaiting_summary_confirmation'] = True
                    return summary
                return "Let me move on to the next section."
            
            # Check if this is a direct question marked by make_template
            if prompt_template.startswith("DIRECT_QUESTION:"):
                # Extract the actual question and use it as-is to preserve formatting
                response = prompt_template.replace("DIRECT_QUESTION:", "", 1)
                print(f"[talk_to_user] Using direct question without LLM processing to preserve formatting")
                print(f"[talk_to_user] Question preview: {response[:100]}...")
            elif "You're a medical professional" not in prompt_template and \
               "Extract medical information" not in prompt_template and \
               len(prompt_template) < 1000:  # Direct questions are usually shorter
                # This is a direct question, use it as-is to preserve formatting
                print(f"[talk_to_user] Using direct question without LLM processing (fallback detection)")
                response = prompt_template
            else:
                # Generate response using direct LLM call
                print(f"[talk_to_user] Sending prompt to LLM for processing")
                prompt = f"{self.system_prompt}\n\n{prompt_template}"
                response = self.llm_complete(prompt)
                
                # CRITICAL: Filter out any "form is complete" messages that shouldn't appear
                # This should not happen in normal question generation, but safeguard against it
                forbidden_phrases = [
                    "the form is complete",
                    "no further questions are needed",
                    "all fields are filled",
                    "the interview is finished"
                ]
                response_lower = response.lower() if response else ""
                if any(phrase in response_lower for phrase in forbidden_phrases):
                    print(f"[talk_to_user] WARNING: LLM generated forbidden 'form complete' message in question generation. This should not happen.")
                    # This shouldn't happen during question generation, but if it does, return a generic question
                    response = "I need a bit more information to complete your medical history. Could you please provide the missing details?"
                
                # Post-process: Remove questions from OTHER sections to prevent repetition
                # Goal: Allow multiple questions within current section, but remove questions from other sections
                pain_assessment_indicators = ["scale of 1 to 10", "rate your pain", "aggravating factors", "relieving factors", "pain worse", "pain better"]
                treatment_goals_indicators = ["goals with treatment", "next 3 months", "long-term goal", "ultimate goal"]
                lifestyle_indicators = ["smoke or drink", "alcohol regularly", "exercise", "physically active", "demanding job"]
                referral_indicators = ["come to know about us", "referral", "Google", "Instagram", "Facebook", "YouTube"]
                diagnostic_indicators = ["mri", "x-ray", "ct scan", "blood reports", "diagnostic reports", "reports related", "upload", "document"]
                
                # Check if History & Diagnostics is complete - if so, always remove report/upload questions from subsequent sections
                history_diag_complete = self.validator("History & Diagnostics")
                if history_diag_complete and self.current_section != "History & Diagnostics":
                    # History & Diagnostics is complete, so remove any report/upload questions from other sections
                    has_history_diag_reports = any(indicator in response.lower() for indicator in diagnostic_indicators)
                    if has_history_diag_reports:
                        print(f"[talk_to_user] History & Diagnostics is complete, removing report/upload questions from {self.current_section} section")
                        # Remove diagnostic/report questions
                        diagnostic_phrases = ["do you have any mri", "x-ray", "ct scan", "blood reports", "diagnostic reports", "reports related", "upload", "document"]
                        for phrase in diagnostic_phrases:
                            if phrase in response.lower():
                                idx = response.lower().find(phrase)
                                if idx > 0:
                                    before = response[:idx].rstrip()
                                    if "\n\n" in before:
                                        response = before.rsplit("\n\n", 1)[0].strip()
                                    elif "\n" in before:
                                        response = before.rsplit("\n", 1)[0].strip()
                                    else:
                                        response = before.strip()
                                    break
                
                # Previously, we tried to strip questions from OTHER sections (Pain, History, Goals, Referral)
                # to avoid repetition. However, you want comprehensive multi-section questions,
                # and that pruning was sometimes leaving only an incomplete intro like
                # "To ensure I have a complete picture..." with no actual bullets.
                # So this cross-section question removal is now DISABLED on purpose.
                
                # Special check: If we're on Pain Assessment section, remove questions about fields that are already filled
                if self.current_section == "Pain Assessment":
                    pain_section = self.form.get("Pain Assessment", {})
                    original_response = response
                    
                    # Helper function to remove a question by finding its bullet point or sentence
                    def remove_question_by_phrase(text, phrase):
                        text_lower = text.lower()
                        if phrase not in text_lower:
                            return text
                        
                        idx = text_lower.find(phrase)
                        if idx == -1:
                            return text
                        
                        # Look backwards to find the start of this question (bullet point or new line)
                        before = text[:idx]
                        
                        # Check if it's a bullet point
                        last_bullet = before.rfind("•")
                        if last_bullet != -1:
                            # Find the end of this bullet point question
                            after_phrase = text[idx:]
                            # Find the next bullet, newline, or end
                            next_bullet = after_phrase.find("\n•")
                            next_double_newline = after_phrase.find("\n\n")
                            if next_bullet != -1 and (next_double_newline == -1 or next_bullet < next_double_newline):
                                # Remove from this bullet to the next
                                return text[:last_bullet].rstrip() + text[idx + next_bullet + 2:]
                            elif next_double_newline != -1:
                                return text[:last_bullet].rstrip() + text[idx + next_double_newline:]
                            else:
                                # Remove from this bullet to the end
                                return text[:last_bullet].rstrip()
                        
                        # Not a bullet point, try to find question boundary
                        last_newline = before.rfind("\n")
                        if last_newline != -1:
                            # Remove from this line onwards if it's a question
                            after = text[idx:]
                            if "?" in after:
                                q_idx = after.find("?")
                                # Check if there's more content after
                                remaining = after[q_idx+1:].strip()
                                if remaining and (remaining.startswith("\n") or remaining.startswith("•")):
                                    return text[:last_newline+1].strip() + "\n" + remaining
                                else:
                                    return text[:last_newline+1].strip()
                        
                        return text
                    
                    # Check each field and remove questions if field is already filled
                    if pain_section.get("Severity (1-10)") and str(pain_section.get("Severity (1-10)", "")).strip():
                        # Remove pain scale questions
                        phrases = ["scale of 1 to 10", "scale of 0 to 10", "rate your pain", "how would you rate", "pain level", "rate your"]
                        for phrase in phrases:
                            response = remove_question_by_phrase(response, phrase)
                    
                    if pain_section.get("Aggravating Factors") and str(pain_section.get("Aggravating Factors", "")).strip():
                        # Remove aggravating factors questions
                        phrases = ["aggravating factors", "makes your pain worse", "pain worse", "tend to make your", "activities or factors", "make your pain worse"]
                        for phrase in phrases:
                            response = remove_question_by_phrase(response, phrase)
                    
                    if pain_section.get("Relieving Factors") and str(pain_section.get("Relieving Factors", "")).strip():
                        # Remove relieving factors questions
                        phrases = ["relieving factors", "makes your pain better", "pain better", "tend to make your", "activities or factors", "make your pain better"]
                        for phrase in phrases:
                            response = remove_question_by_phrase(response, phrase)
                    
                    if pain_section.get("Primary Location of Pain") and str(pain_section.get("Primary Location of Pain", "")).strip():
                        # Remove location questions if already filled
                        phrases = ["where exactly", "location of pain", "where do you feel"]
                        for phrase in phrases:
                            response = remove_question_by_phrase(response, phrase)
                    
                    # Clean up any empty lines or formatting issues
                    response = response.strip()
                    if response != original_response:
                        print(f"[talk_to_user] Removed questions about already-filled Pain Assessment fields")
                    if not response or not any(char.isalnum() for char in response):
                        # Response is empty or only punctuation - check if form is complete
                        print(f"[talk_to_user] WARNING: After filtering, response is empty. All Pain Assessment fields may already be filled.")
                        if self.invalid_index() and not self.conversation_state.get('awaiting_summary_confirmation', False):
                            # Form is complete, show summary instead of generic message
                            summary = self.generate_summary()
                            self.conversation_state['awaiting_summary_confirmation'] = True
                            return summary
                        response = "Thank you for that information. Let me move on to the next section."
                
                # Specific handling for multi-section predefined question idx=1
                if self.idx == 1:  # "Past Treatment & History/Diagnostics" covers multiple sections
                    if self.current_section == "History & Diagnostics":
                        # Remove Previous Consultations questions if present (they're already answered)
                        prev_consultations_complete = self.validator("Previous Consultations")
                        if prev_consultations_complete:
                            # Remove Previous Consultations questions
                            if "Have you consulted any doctor" in response:
                                # Find where Previous Consultations ends
                                if "Now about your overall health, lifestyle, and any reports:" in response:
                                    response = response.split("Now about your overall health, lifestyle, and any reports:", 1)[1].strip()
                                    response = "Now about your overall health, lifestyle, and any reports:\n" + response
                                else:
                                    # Manual removal of Previous Consultations content
                                    lines = response.split("\n")
                                    filtered_lines = []
                                    skip_until_health = True
                                    for line in lines:
                                        if "Now about your overall health" in line or "overall health" in line.lower():
                                            skip_until_health = False
                                        if not skip_until_health:
                                            filtered_lines.append(line)
                                    response = "\n".join(filtered_lines).strip()
                            print(f"[talk_to_user] Removed Previous Consultations questions from History & Diagnostics response (already complete)")
                    elif self.current_section == "Previous Consultations":
                        # Remove History & Diagnostics questions if present
                        if "Now about your overall health, lifestyle, and any reports:" in response:
                            response = response.split("Now about your overall health, lifestyle, and any reports:")[0].strip()
                        # Also remove any health/lifestyle/reports questions
                        health_keywords = ["health conditions", "diabetes", "smoke or drink", "exercise", "MRI", "X-ray", "CT scan", "blood reports"]
                        for keyword in health_keywords:
                            if keyword.lower() in response.lower():
                                idx = response.lower().find(keyword.lower())
                                if idx > 0:
                                    response = response[:idx].strip()
                                    break
                        print(f"[talk_to_user] Removed History & Diagnostics questions from Previous Consultations response")
                
                # Specific handling for multi-section predefined question idx=2
                if self.idx == 2:  # "Goals & Referral" covers multiple sections
                    if self.current_section == "Treatment Goals":
                        # Remove referral questions if present
                        if "How did you come to know about us?" in response:
                            response = response.split("How did you come to know about us?")[0].strip()
                        # Also remove report/upload questions if History & Diagnostics is complete
                        history_diag_complete = self.validator("History & Diagnostics")
                        if history_diag_complete:
                            diagnostic_phrases = ["mri", "x-ray", "ct scan", "blood reports", "diagnostic reports", "reports related", "upload", "document"]
                            for phrase in diagnostic_phrases:
                                if phrase in response.lower():
                                    idx = response.lower().find(phrase)
                                    if idx > 0:
                                        before = response[:idx].rstrip()
                                        if "\n\n" in before:
                                            response = before.rsplit("\n\n", 1)[0].strip()
                                        elif "\n" in before:
                                            response = before.rsplit("\n", 1)[0].strip()
                                        else:
                                            response = before.strip()
                                        break
                            print(f"[talk_to_user] Filtered out report/upload questions from Treatment Goals response (History & Diagnostics already complete)")
                        print(f"[talk_to_user] Filtered out Referral questions from Treatment Goals response")
                    elif self.current_section == "Referral":
                        # Remove treatment goals questions if present
                        if "What are your goals with treatment?" in response:
                            if "How did you come to know about us?" in response:
                                response = "How did you come to know about us? (Friend/family referral, Google, Instagram, Facebook, YouTube, etc.)"
                        # Also remove report/upload questions if History & Diagnostics is complete
                        history_diag_complete = self.validator("History & Diagnostics")
                        if history_diag_complete:
                            diagnostic_phrases = ["mri", "x-ray", "ct scan", "blood reports", "diagnostic reports", "reports related", "upload", "document"]
                            for phrase in diagnostic_phrases:
                                if phrase in response.lower():
                                    idx = response.lower().find(phrase)
                                    if idx > 0:
                                        before = response[:idx].rstrip()
                                        if "\n\n" in before:
                                            response = before.rsplit("\n\n", 1)[0].strip()
                                        elif "\n" in before:
                                            response = before.rsplit("\n", 1)[0].strip()
                                        else:
                                            response = before.strip()
                                        break
                            print(f"[talk_to_user] Filtered out report/upload questions from Referral response (History & Diagnostics already complete)")
                        print(f"[talk_to_user] Filtered out Treatment Goals questions from Referral response")

            # Store last question
            self.conversation_state["last_question"] = response

            # Update talk mode
            self.talk_mode = "USER"

            return response

        except Exception as e:
            print(f"Error generating response: {e}")
            # Use predefined question as fallback
            if self.idx < len(self.predefined_questions):
                return self.predefined_questions[self.idx][1]
            return "Could you please tell me more about your medical condition?"

    def invalid_index(self):
        """
        Check if we've completed all sections.

        Returns:
            Boolean indicating if all sections are complete
        """
        return self.idx >= len(self.predefined_questions)

    def get_all_missing_fields(self):
        """
        Get all missing fields across all sections.
        
        Returns:
            Dictionary mapping section names to lists of missing field names
        """
        all_missing = {}
        for section in self.form_sections:
            # Temporarily set current_section to check this section
            original_section = self.current_section
            self.current_section = section
            self.validator(section)
            if self.missing_fields:
                all_missing[section] = self.missing_fields.copy()
            self.current_section = original_section
        return all_missing

    def make_template(self, mode, source=None, context=None):
        """
        Create appropriate prompts based on the mode and context.

        Args:
            mode: The type of prompt to create ("query", "requery", "format")
            source: The user input text (for format mode)
            context: Additional context for the prompt

        Returns:
            Prompt text for the LLM
        """
        if mode == "query":
            # Generate a question based on the current section and predefined questions
            if self.idx < len(self.predefined_questions):
                question = self.predefined_questions[self.idx][1]
            else:
                return "Thank you for completing the medical interview."
            
            # CRITICAL: When idx=1, ensure we start with Pain Assessment if it's not complete
            if self.idx == 1:
                pain_assessment_complete = self.validator("Pain Assessment")
                history_diag_complete = self.validator("History & Diagnostics")
                
                # If Pain Assessment is not complete, ensure current_section is set to it
                if not pain_assessment_complete:
                    if "Pain Assessment" in self.form_sections:
                        if self.current_section != "Pain Assessment":
                            self.current_section = "Pain Assessment"
                            print(f"[make_template] idx=1: Ensuring current_section is Pain Assessment (not complete)")
                # If Pain Assessment is complete but History & Diagnostics is not, ensure current_section is History & Diagnostics
                elif pain_assessment_complete and not history_diag_complete:
                    if "History & Diagnostics" in self.form_sections:
                        if self.current_section != "History & Diagnostics":
                            self.current_section = "History & Diagnostics"
                            print(f"[make_template] idx=1: Pain Assessment complete, ensuring current_section is History & Diagnostics")

            # Special handling: idx=0 - First comprehensive question
            # CRITICAL: For the very first question (when form is empty), ask ALL questions at once
            if self.idx == 0:
                # Check if form is completely empty (first question ever)
                total_filled = sum(
                    1 for section_data in self.form.values()
                    for value in section_data.values()
                    if value and str(value).strip()
                )
                
                # If form is empty, use the full comprehensive question without filtering
                if total_filled == 0:
                    print(
                        "[make_template] First question - form is empty, using FULL comprehensive question covering all sections"
                    )
                    # Use the question as-is - it already covers all sections
                    # Don't filter anything, just use the complete comprehensive question
                else:
                    # Form has some data - we need to check what's already filled and only ask about missing fields
                    # Don't use the predefined question directly - let the LLM generate a question based on what's missing
                    print(
                        f"[make_template] idx=0 but form has {total_filled} filled fields. Will use LLM to generate question for missing fields only."
                    )
                    # Mark that we should skip the direct question return and use LLM processing instead
                    # This will be handled in the logic below where we check is_first_question
            
            # Special handling: idx=1 now covers "Pain Assessment" and "History & Diagnostics"
            elif self.idx == 1:
                if self.current_section == "Pain Assessment":
                    question = self.predefined_questions[0][1]
                    lines = question.split("\n")
                    pain_questions = []
                    for line in lines:
                        line_lower = line.lower().strip()
                        if line.strip().startswith("•"):
                            if ("where" in line_lower and "feel" in line_lower) or ("severe" in line_lower and "scale" in line_lower):
                                pain_questions.append(line.strip())
                            elif "worse" in line_lower or "better" in line_lower or "aggravating" in line_lower or "relieving" in line_lower:
                                pain_questions.append(line.strip())
                    if pain_questions:
                        question = "To help me understand your pain better:\n" + "\n".join(pain_questions)
                    else:
                        question = "To help me understand your pain better:\n• Where exactly do you feel the pain, and how severe is it on a scale of 0 to 10?\n• What activities or factors tend to make the pain worse?\n• What activities or factors tend to make the pain better?"
                    print(f"[make_template] Extracted Pain Assessment-specific question from comprehensive complaint question (idx=1 flow)")
                elif self.current_section == "History & Diagnostics":
                    question = self.predefined_questions[1][1]
                    hist_diag = self.form.get("History & Diagnostics", {})
                    fields_filled = [
                        value for value in hist_diag.values()
                        if value and str(value).strip().lower() not in ["none", "no", "nothing", "n/a", "na", ""]
                    ]
                    if len(fields_filled) == len(hist_diag):
                        print(f"[make_template] History & Diagnostics already filled, skipping question")
                        return "SKIP_SECTION"
                    if "Now about your overall health, lifestyle, and any reports:" in question:
                        question = question.split("Now about your overall health, lifestyle, and any reports:", 1)[1].strip()
                        question = "Now about your overall health, lifestyle, and any reports:\n" + question
                    print(f"[make_template] Extracted History & Diagnostics-specific question from multi-section predefined question (idx=1 flow)")
            
            # Special handling: idx=2 covers "Treatment Goals" and "Referral"
            elif self.idx == 2:
                if self.current_section == "Treatment Goals":
                    if "How did you come to know about us?" in question:
                        question = question.split("How did you come to know about us?")[0].strip()
                    print(f"[make_template] Extracted Treatment Goals-specific question from multi-section predefined question")
                elif self.current_section == "Referral":
                    if "How did you come to know about us?" in question:
                        referral_start = question.find("How did you come to know about us?")
                        question = question[referral_start:].strip()
                    else:
                        question = "How did you come to know about us? (Friend/family referral, Google, Instagram, Facebook, YouTube, etc.)"
                    print(f"[make_template] Extracted Referral-specific question from multi-section predefined question")

            # Check if the section is completely empty
            section_data = self.form.get(self.current_section, {})
            is_section_empty = all(
                not value or (isinstance(value, str) and not value.strip())
                for value in section_data.values()
            )
            
            # CRITICAL: For the very first question (idx=0 and form is empty), always use the full comprehensive question
            # But if form has ANY data, use LLM to check what's filled and only ask about missing fields
            total_filled = sum(
                1 for section_data in self.form.values()
                for value in section_data.values()
                if value and str(value).strip()
            )
            is_first_question = (self.idx == 0 and total_filled == 0)
            
            if is_first_question:
                print(f"[make_template] FIRST QUESTION - form is completely empty, using full comprehensive question covering ALL sections")
                # Return the full comprehensive question directly without any processing
                return f"DIRECT_QUESTION:{question}"
            
            # CRITICAL: If idx == 0 but form has data, we should NOT return the direct question
            # Instead, let the LLM process it to check what's already filled and only ask about missing fields
            if self.idx == 0 and total_filled > 0:
                print(f"[make_template] idx=0 but form has {total_filled} filled fields. Using LLM to generate question for missing fields only, NOT returning direct comprehensive question.")
                # Clear the question variable so LLM doesn't see the full comprehensive question
                # The LLM will generate a new question based only on missing fields
                question = ""  # Empty question - LLM will generate based on missing fields only
                # Don't return DIRECT_QUESTION - let it fall through to LLM processing below
            
            # Special check for History & Diagnostics: If fields already filled, skip asking
            if self.current_section == "History & Diagnostics":
                filled_all = all(
                    value and str(value).strip().lower() not in ["none", "no", "nothing", "n/a", "na", ""]
                    for value in section_data.values()
                )
                if filled_all:
                    print(f"[make_template] History & Diagnostics already filled, skipping question")
                    return "SKIP_SECTION"
            
            # CRITICAL: If we're on History & Diagnostics but Previous Consultations is already complete,
            # make sure we don't include Previous Consultations questions in the extracted question
            if self.current_section == "History & Diagnostics" and self.idx == 1:
                # Check if Previous Consultations is complete
                prev_consultations_data = self.form.get("Previous Consultations", {})
                prev_consultations_complete = self.validator("Previous Consultations")
                
                if prev_consultations_complete:
                    # Previous Consultations is complete, ensure question doesn't include it
                    # Remove any Previous Consultations content from the question
                    if "Have you consulted any doctor" in question:
                        # Find where Previous Consultations ends and History & Diagnostics begins
                        if "Now about your overall health, lifestyle, and any reports:" in question:
                            # Extract only the History & Diagnostics part
                            question = question.split("Now about your overall health, lifestyle, and any reports:", 1)[1].strip()
                            question = "Now about your overall health, lifestyle, and any reports:\n" + question
                        else:
                            # Fallback: remove Previous Consultations questions manually
                            lines = question.split("\n")
                            filtered_lines = []
                            skip_until_health = True
                            for line in lines:
                                if "Now about your overall health" in line:
                                    skip_until_health = False
                                if not skip_until_health:
                                    filtered_lines.append(line)
                            question = "\n".join(filtered_lines).strip()
                    print(f"[make_template] Removed Previous Consultations from question (already complete)")

            # Final safety check: Ensure Previous Consultations never includes medical history
            if self.current_section == "Previous Consultations" and "Also," in question:
                print(f"[make_template] WARNING: Medical history part detected in Previous Consultations question, removing it")
                question = question.split("Also,")[0].strip()
            
            # CRITICAL: Final safety check for History & Diagnostics - ensure Previous Consultations is removed
            if self.current_section == "History & Diagnostics" and self.idx == 1:
                prev_consultations_complete = self.validator("Previous Consultations")
                if prev_consultations_complete:
                    # Previous Consultations is complete, ensure it's not in the question
                    if "Have you consulted any doctor" in question or "What did they diagnose" in question:
                        # Remove Previous Consultations part
                        if "Now about your overall health, lifestyle, and any reports:" in question:
                            question = question.split("Now about your overall health, lifestyle, and any reports:", 1)[1].strip()
                            question = "Now about your overall health, lifestyle, and any reports:\n" + question
                        else:
                            # Fallback: remove lines containing Previous Consultations keywords
                            lines = question.split("\n")
                            filtered_lines = []
                            skip_until_health = True
                            for line in lines:
                                line_lower = line.lower()
                                if any(phrase in line_lower for phrase in ["overall health", "lifestyle", "reports", "health conditions", "smoke or drink", "exercise", "mri", "x-ray"]):
                                    skip_until_health = False
                                if not skip_until_health:
                                    filtered_lines.append(line)
                            question = "\n".join(filtered_lines).strip()
                        print(f"[make_template] Final safety check: Removed Previous Consultations from History & Diagnostics question")
            
            # If section is empty, use the predefined question directly to preserve formatting
            # This prevents LLM from reformulating and asking the same question differently
            # CRITICAL: Only return direct question if section is empty AND it's the very first question (form completely empty)
            # If form has ANY data, use LLM to check what's filled and generate appropriate question
            if is_section_empty and is_first_question:
                print(f"[make_template] Section '{self.current_section}' is empty AND form is completely empty, returning predefined question directly to preserve bullet points")
                print(f"[make_template] Question preview: {question[:150]}...")
                # Return with a marker so talk_to_user knows not to process it
                return f"DIRECT_QUESTION:{question}"
            elif is_section_empty and not is_first_question:
                # Section is empty but form has some data - use LLM to check what's already filled
                print(f"[make_template] Section '{self.current_section}' is empty but form has {total_filled} filled fields. Using LLM to check filled fields and generate appropriate question.")
                # Don't return DIRECT_QUESTION - let it fall through to LLM processing below

            # Get all missing fields across all sections
            all_missing_fields = self.get_all_missing_fields()
            total_missing_count = sum(len(fields) for fields in all_missing_fields.values())
            
            # CRITICAL: If there are NO missing fields at all, return SKIP_SECTION to move to next section or generate summary
            if total_missing_count == 0:
                print(f"[make_template] No missing fields found across all sections. Returning SKIP_SECTION to move to next section or generate summary.")
                return "SKIP_SECTION"
            
            # Calculate how much of the form is filled
            total_fields = sum(len(section_data) for section_data in self.form.values())
            filled_fields = sum(
                1 for section_data in self.form.values()
                for value in section_data.values()
                if value and str(value).strip()
            )
            completion_percentage = (filled_fields / total_fields * 100) if total_fields > 0 else 0

            # If section has some data, ask LLM to formulate a follow-up question
            # Add section-specific restrictions
            section_restrictions = ""
            if self.current_section == "Previous Consultations":
                section_restrictions = "\n\nIMPORTANT: You can ask multiple questions about Previous Consultations. However, do NOT repeat questions about history/diagnostics - those are covered in the 'History & Diagnostics' section."
            elif self.current_section == "History & Diagnostics":
                section_restrictions = "\n\nIMPORTANT: You can ask multiple questions about History & Diagnostics (systemic illnesses/surgeries, lifestyle, reports). However, do NOT repeat questions about previous consultations for the current problem - that was already covered in a previous section."
            elif self.current_section == "Treatment Goals":
                # Check if History & Diagnostics is complete - if so, don't ask about reports
                history_diag_complete = self.validator("History & Diagnostics")
                if history_diag_complete:
                    section_restrictions = "\n\nIMPORTANT: You can ask multiple questions about Treatment Goals. However, do NOT repeat questions about referral source - that will be asked in a separate section. Also, do NOT ask about reports, scans, MRI, X-ray, or document uploads - those were already covered in the 'History & Diagnostics' section."
                else:
                    section_restrictions = "\n\nIMPORTANT: You can ask multiple questions about Treatment Goals. However, do NOT repeat questions about referral source - that will be asked in a separate section."
            elif self.current_section == "Referral":
                # Check if History & Diagnostics is complete - if so, don't ask about reports
                history_diag_complete = self.validator("History & Diagnostics")
                if history_diag_complete:
                    section_restrictions = "\n\nIMPORTANT: You can ask questions about Referral Source. However, do NOT repeat questions about treatment goals - those were already covered in previous sections. Also, do NOT ask about reports, scans, MRI, X-ray, or document uploads - those were already covered in the 'History & Diagnostics' section."
                else:
                    section_restrictions = "\n\nIMPORTANT: You can ask questions about Referral Source. However, do NOT repeat questions about treatment goals - those were already covered in previous sections."
            elif self.current_section == "Pain Assessment":
                # Check if pain assessment fields are already filled (from initial comprehensive complaint)
                pain_section = self.form.get("Pain Assessment", {})
                filled_fields = [field for field, value in pain_section.items() if value and str(value).strip()]
                if filled_fields:
                    section_restrictions = f"\n\nIMPORTANT: The patient has already provided some pain assessment information: {', '.join(filled_fields)}. Do NOT ask about these fields again. Only ask about fields that are still empty or missing."
                else:
                    section_restrictions = "\n\nIMPORTANT: You can ask multiple questions about pain assessment. However, do NOT repeat questions from other sections."
            
            # Build comprehensive missing fields summary
            missing_fields_summary = ""
            if all_missing_fields:
                missing_fields_summary = "\n\nALL MISSING FIELDS ACROSS ALL SECTIONS:\n"
                for section, fields in all_missing_fields.items():
                    if fields:
                        missing_fields_summary += f"- {section}: {', '.join(fields)}\n"
            
            # Determine if we should ask comprehensive questions
            # Ask comprehensive questions if:
            # 1. Less than 30% of form is filled (early in interview)
            # 2. OR there are missing fields in multiple sections
            should_ask_comprehensive = (
                completion_percentage < 30 or 
                len(all_missing_fields) > 1
            )
            
            comprehensive_instruction = ""
            if should_ask_comprehensive and total_missing_count > 3:
                comprehensive_instruction = f"""
            
            CRITICAL EFFICIENCY GOAL: Minimize the total number of questions asked!
            
            You currently have {total_missing_count} missing fields across {len(all_missing_fields)} section(s).
            The form is only {completion_percentage:.0f}% complete.
            
            STRATEGY: Ask COMPREHENSIVE questions that gather information for MULTIPLE sections and MULTIPLE fields at once.
            - You can ask about fields from DIFFERENT sections in the SAME question
            - For example, you can ask about "Present Complaint" AND "Pain Assessment" AND "Previous Consultations" in one comprehensive question
            - The goal is to fill as many fields as possible with the fewest number of questions
            
            PRIORITY ORDER (ask about these first):
            1. Present Complaint fields (Primary Complaint, Duration, Onset, Mechanism)
            2. Pain Assessment fields (Location, Severity, Aggravating/Relieving Factors)
            3. Previous Consultations (if applicable)
            4. History & Diagnostics (Systemic Illness, Lifestyle, Reports)
            5. Treatment Goals (Short-term, Long-term, Expectations)
            6. Referral Source
            
            EXAMPLE of a comprehensive question that covers multiple sections:
            "To help me understand your situation better, could you tell me:
            • What is the main problem you're experiencing and where exactly do you feel it?
            • How long has this been going on, and did it start suddenly or gradually?
            • On a scale of 1 to 10, how severe is the pain?
            • What makes it worse or better?
            • Have you consulted any doctor or physiotherapist for this before, and if so, what did they say?
            • Do you have any other health conditions, past surgeries, or do you smoke or drink?
            • Do you have any MRI, X-ray, CT scan, or blood reports related to this issue?"
            
            This single question can fill fields from Present Complaint, Pain Assessment, Previous Consultations, AND History & Diagnostics!
            
            IMPORTANT: Only ask about fields that are actually missing (see ALL MISSING FIELDS above).
            Do NOT repeat questions about fields that are already filled.
            """
            else:
                comprehensive_instruction = f"""
            
            The form is {completion_percentage:.0f}% complete with {total_missing_count} missing field(s) remaining.
            Focus on asking about the missing fields in the current section ({self.current_section}).
            You can still ask multiple questions at once within the current section to be efficient.
            """
            
            # If idx == 0 and form has data, don't show the standard question - it's the full comprehensive question
            # and we want the LLM to generate a new question based only on missing fields
            standard_question_note = ""
            if self.idx == 0 and total_filled > 0:
                standard_question_note = "\n\nCRITICAL: DO NOT use the standard comprehensive question below - most fields are already filled! Generate a NEW question that asks ONLY about the missing fields listed above."
            elif question:
                standard_question_note = f'\n\nThe standard question for this section is: "{question}"'
            
            prompt = f"""
            You're a medical professional conducting an interview.

            The current section you're asking about is: {self.current_section}
            {standard_question_note}
            {section_restrictions}
            {comprehensive_instruction}
            {missing_fields_summary}

            The current status of ALL sections in the form is:
            {json.dumps(self.form, indent=2)}

            CRITICAL: Before asking any questions, you MUST check which fields are already filled in the form above.
            - If a field already has a value (not empty, not "", not None), DO NOT ask about it again - it was already answered by the patient.
            - Only ask about fields that are empty or missing (see ALL MISSING FIELDS above).
            - If the patient's previous answer already covered information for a field, that field is considered filled and should NOT be asked about again.
            
            EXAMPLE: If the form shows:
            - "Present Complaint": {{"Primary Complaint": "pain in right knee", "Duration of the Issue": "three weeks ago", ...}}
            - "Pain Assessment": {{"Primary Location of Pain": "right knee, front and medial side", "Severity (1-10)": "6/10", ...}}
            
            Then DO NOT ask "What exactly is bothering you?" or "Where do you feel the pain?" or "How severe is it?" - these were already answered!
            
            {f'Since the form is only {completion_percentage:.0f}% complete, ask COMPREHENSIVE questions that cover multiple missing fields across multiple sections in a single question. This will minimize the total number of questions needed.' if should_ask_comprehensive else 'Focus on the missing fields in the current section, but you can ask multiple questions at once within that section.'}
            
            IMPORTANT: If the patient already answered questions about a section in a previous part of the conversation, do NOT ask those same questions again. Only ask about information that is still missing.

            CRITICAL: If ALL fields are already filled (no missing fields), DO NOT generate a message saying "all fields are filled" or "we can move on" or "we can confirm the information". 
            Instead, the system will automatically handle moving to the next section. Your job is ONLY to ask questions about missing fields.
            If there are no missing fields, this prompt should not have been sent to you - but if it was, do NOT respond with a completion message.

            CRITICAL FORMATTING RULES:
            1. If you need to ask multiple questions, you MUST format them as bullet points with line breaks.
            2. Each bullet point must start with • followed by a space.
            3. Each bullet point must be on its own line.
            4. Do NOT write questions separated by commas in a single paragraph.
            5. Do NOT add transition messages like "Thank you for that information", "Okay, I understand", "Based on the information you've already provided" - just ask the questions directly.
            
            CORRECT FORMAT EXAMPLE (comprehensive question covering multiple sections):
            To help me understand your situation better, could you tell me:
            • What is the main problem you're experiencing and where exactly do you feel it?
            • How long has this been going on, and did it start suddenly or gradually?
            • On a scale of 1 to 10, how severe is the pain?
            • What makes it worse or better?
            • Have you consulted any doctor or physiotherapist for this before?
            • Do you have any other health conditions, past surgeries, or do you smoke or drink?
            
            WRONG FORMAT (DO NOT USE):
            Thank you for that information. Based on what you've told me, could you tell me more about the main problem you're experiencing, how long it's been bothering you, and whether it came on suddenly or gradually?

            Reply ONLY with the final question(s) to ask the patient. Do not include transition messages, explanations, or additional text. Start directly with the question.
            """
            return prompt

        elif mode == "requery":
            # Create a prompt that specifically asks for missing information
            missing_fields_str = ", ".join(
                self.missing_fields[:3]
            )  # Limit to 3 fields at once

            # Get all missing fields across all sections to provide full context
            all_missing_fields = self.get_all_missing_fields()
            all_missing_summary = ""
            if all_missing_fields:
                all_missing_summary = "\n\nALL MISSING FIELDS ACROSS ALL SECTIONS:\n"
                for section, fields in all_missing_fields.items():
                    if fields:
                        all_missing_summary += f"- {section}: {', '.join(fields)}\n"

            # Add section-specific restrictions
            section_restrictions = ""
            if self.current_section == "Previous Consultations":
                section_restrictions = "\n\nIMPORTANT: You can ask multiple questions about Previous Consultations. However, do NOT repeat questions about medical history, systemic illnesses, or past surgeries - those will be asked in a separate section later."
            elif self.current_section == "Medical History":
                section_restrictions = "\n\nIMPORTANT: You can ask multiple questions about Medical History. However, do NOT repeat questions about previous consultations for the current problem - that was already covered in a previous section."

            prompt = f"""
            You're a medical professional conducting an interview.

            The patient hasn't provided complete information about: {missing_fields_str}

            These fields are part of the '{self.current_section}' section.
            {section_restrictions}

            CRITICAL: Before asking any questions, check the CURRENT FORM STATE below to see which fields are already filled.
            The current status of ALL sections in the form is:
            {json.dumps(self.form, indent=2)}

            {all_missing_summary}

            IMPORTANT RULES:
            1. ONLY ask about fields that are listed in "ALL MISSING FIELDS" above.
            2. DO NOT ask about any field that already has a value in the form above - it was already answered.
            3. You can ask multiple questions at once if needed to get all the missing information.
            4. Do NOT repeat questions from other sections that were already asked or already filled.
            
            Ask a friendly, conversational follow-up question to get ONLY the missing information.
            
            CRITICAL FORMATTING RULES:
            1. If you need to ask multiple questions (2 or more), you MUST format them as bullet points with line breaks.
            2. Each bullet point must start with • followed by a space.
            3. Each bullet point must be on its own line.
            4. Do NOT write questions separated by commas in a single paragraph.
            5. Only use a single paragraph if asking ONE simple question.
            6. Do NOT add transition messages like "Thank you for that information", "Okay, I understand", "Based on the information you've already provided" - just ask the questions directly.
            
            CORRECT FORMAT EXAMPLE (multiple questions):
            I need a bit more information:
            • First question about X?
            • Second question about Y?
            • Third question about Z?
            
            WRONG FORMAT (DO NOT USE):
            Thank you for that information. Based on what you've told me, I need a bit more information about X, Y, and Z?

            Reply ONLY with the final question(s) to ask the patient. Do not include transition messages, explanations, or additional text. Start directly with the question.
            """
            return prompt

        elif mode == "format":
            if not source:
                print("No input provided for formatting.")
                return "No input provided for formatting."

            # Create a prompt for extracting structured data from user input
            prompt = f"""
            Extract medical information from the patient's response and update the appropriate fields in the form.

            PATIENT RESPONSE: {source}

            CURRENT FORM:
            {json.dumps(self.form, indent=2)}

            CRITICAL EXTRACTION RULES:
            1. Extract ALL relevant medical information from the patient's response, even if it spans multiple sections.
            2. The response might contain information relevant to multiple sections, not just the current section ({self.current_section}).
            3. Map information to the correct fields in ALL relevant sections:
               - "Present Complaint": Primary Complaint, Duration of the Issue, Onset, Mechanism of Injury
               - "Pain Assessment": Primary Location of Pain, Severity (1-10), Aggravating Factors, Relieving Factors
               - "Previous Consultations": Previous Diagnosis, Current Status of Issue
               - "History & Diagnostics": Systemic Illness and Surgical History, Current Lifestyle, Reports
               - "Treatment Goals": Short-Term Goals, Long-Term Goals, Specific Expectations
               - "Referral": Source
            4. If the patient mentions pain location, severity, duration, onset, etc., extract these into the appropriate fields.
            5. If the patient provides comprehensive information covering multiple sections, extract data for ALL those sections.
            6. Preserve the exact structure of the form template.
            7. Only update fields with actual information from the patient's response - do not fill with placeholders.

            Return the complete updated form as a valid JSON object matching the structure of the current form.
            Only output the JSON object without any additional explanations.
            """
            return prompt

        return source if source else "No template available."

    def main_processor(self, user_response):
        """
        Process the user's response and determine the next action.

        Args:
            user_response: The user's response text

        Returns:
            The next question or response to present to the user
        """
        try:
            # Check if interview is complete (but not yet confirmed)
            if self.invalid_index() and not self.conversation_state.get('awaiting_summary_confirmation', False):
                # Show summary for first time
                summary = self.generate_summary()
                self.conversation_state['awaiting_summary_confirmation'] = True
                self.history.append({"role": "agent", "message": summary})
                print(f"[main_processor] Interview complete, showing summary for confirmation")
                return summary

            # CRITICAL: Only ask initial question if talk_mode is START AND user_response is empty
            # If user_response is provided, we should process it even if talk_mode is START
            # (this handles the case where the first user response comes in)
            if self.talk_mode == "START" and not user_response:
                # Create first question prompt
                prompt_template = self.make_template(mode="query")
                first_question = self.talk_to_user(prompt_template)
                self.talk_mode = "USER"
                print(f"[main_processor] Asked initial question, talk_mode changed to USER")
                return first_question

            # Process user's response (even if talk_mode was START - this is the first real input)
            if user_response:
                # If talk_mode is still START, this is the first user response
                if self.talk_mode == "START":
                    print(f"[main_processor] Processing first user response, changing talk_mode from START to USER")
                    self.talk_mode = "USER"
                    
                    # CRITICAL: Check if this is just a confirmation to the welcome message
                    # If user says "yes" or similar, immediately ask the comprehensive first question
                    user_input_lower = user_response.lower().strip()
                    confirmation_words = {'yes', 'yeah', 'yep', 'sure', 'ok', 'okay', 'yup', 'fine', 'good', 'alright', 'ready', 'i am ready', "i'm ready", 'lets go', "let's go", 'start', 'begin'}
                    
                    # Check if the form is completely empty (first question scenario)
                    total_filled = sum(
                        1 for section_data in self.form.values()
                        for value in section_data.values()
                        if value and str(value).strip()
                    )
                    
                    if total_filled == 0 and user_input_lower in confirmation_words:
                        print(f"[main_processor] User confirmed readiness with '{user_response}', immediately asking comprehensive first question")
                        # Add confirmation to history
                        self.history.append({"role": "user", "message": user_response})
                        # Ask the comprehensive first question
                        prompt_template = self.make_template(mode="query")
                        first_question = self.talk_to_user(prompt_template)
                        self.history.append({"role": "agent", "message": first_question})
                        return first_question
                
                # Add user response to history for context
                self.history.append({"role": "user", "message": user_response})
                print(f"[main_processor] Added user response to history. History length: {len(self.history)}, talk_mode: {self.talk_mode}")

                # STEP 0: Handle simple confirmations when awaiting confirmation
                if self.conversation_state.get('awaiting_confirmation', False):
                    user_input_lower = user_response.lower().strip()
                    simple_yes = {'yes', 'yeah', 'yep', 'correct', 'right', 'sure', 'ok', 'okay', 'yup', 'fine', 'good', 'alright'}
                    simple_no = {'no', 'nope', 'nah', 'incorrect', 'wrong'}
                    
                    if user_input_lower in simple_yes:
                        # User confirmed - clear the flag and continue with interview
                        print(f"[main_processor] User confirmed correction, continuing interview")
                        self.conversation_state['awaiting_confirmation'] = False
                        self.conversation_state['last_message_type'] = 'question'
                        
                        # Move to next question or continue current section
                        prompt_template = self.make_template(mode="query")
                        next_question = self.talk_to_user(prompt_template)
                        self.history.append({"role": "agent", "message": next_question})
                        return next_question
                    
                    elif user_input_lower in simple_no:
                        # User rejected - ask what needs to be changed
                        print(f"[main_processor] User rejected correction, asking for clarification")
                        self.conversation_state['awaiting_confirmation'] = False
                        self.conversation_state['last_message_type'] = 'question'
                        clarification_msg = "What would you like to change?"
                        self.history.append({"role": "agent", "message": clarification_msg})
                        return clarification_msg
                    
                    # If it's not a simple yes/no, fall through to normal processing
                    print(f"[main_processor] Awaiting confirmation but got complex response, processing normally")
                    self.conversation_state['awaiting_confirmation'] = False

                # STEP 1: Handle summary confirmation (corrections and late-report uploads after summary)
                if self.conversation_state.get('awaiting_summary_confirmation', False):
                    user_input_lower = user_response.lower().strip()

                    # ─────────────────────────────────────────────────────
                    # 1A. LLM-based intent classification (primary path)
                    # ─────────────────────────────────────────────────────
                    llm_intent = self.classify_summary_response(user_response)
                    if llm_intent:
                        intent = llm_intent.get("intent")
                        has_reports_flag = llm_intent.get("has_reports")
                        wants_upload_flag = llm_intent.get("wants_upload")
                        correction_text = llm_intent.get("correction_text")

                        # Reports-related intents (in summary mode)
                        if intent in ["has_reports", "no_reports"]:
                            if intent == "has_reports":
                                # User says they have reports at summary stage → offer upload once
                                if wants_upload_flag is not False:
                                    upload_prompt = (
                                        "Great! Since you mentioned you have reports, would you like to upload them? "
                                        "You can upload MRI, X-ray, CT scan, or blood test reports."
                                    )
                                    print("[main_processor] Summary mode (LLM): user has reports, asking about upload.")
                                    self.conversation_state["awaiting_report_upload"] = True
                                    self.history.append({"role": "agent", "message": upload_prompt})
                                    return upload_prompt
                                # If they explicitly don't want to upload, mark it and continue to confirmation
                                current_reports = self.form.get("History & Diagnostics", {}).get("Reports", "")
                                if not current_reports or not str(current_reports).strip():
                                    self.form["History & Diagnostics"]["Reports"] = (
                                        "User mentioned having reports but did not upload them."
                                    )
                            else:
                                # no_reports: mark field so we don't keep asking
                                current_reports = self.form.get("History & Diagnostics", {}).get("Reports", "")
                                if not current_reports or not str(current_reports).strip():
                                    self.form["History & Diagnostics"]["Reports"] = (
                                        "No relevant diagnostic reports available for this issue"
                                    )
                                print("[main_processor] Summary mode (LLM): user has no reports, updating Reports field.")

                        # Simple confirmation intent
                        if intent == "confirm":
                            print(f"[main_processor] Summary mode (LLM): user confirmed summary, interview complete")
                        self.conversation_state['awaiting_summary_confirmation'] = False
                        completion_msg = "Thank you for confirming. Your medical information has been recorded.\n\nYou can now close this page. Our team will review your information and get back to you soon."
                        self.history.append({"role": "agent", "message": completion_msg})
                        self.save_progress()
                        return completion_msg
                    
                    # Request change intent - either generic or specific
                    if intent == "request_change":
                        # Try to detect correction from the user's message directly
                        # The user might have provided the correction directly (e.g., "i was referred by my physiotherapist")
                        print(f"[main_processor] Summary mode (LLM): detected request_change intent, attempting to detect and apply correction")
                        correction_data = self.detect_correction(user_response, is_summary_mode=True)
                        
                        if correction_data and correction_data.get("is_correction", False):
                            # Correction detected - apply it
                            success, message = self.apply_correction(correction_data, is_summary_mode=True)
                            self.history.append({"role": "agent", "message": message})
                            if success:
                                # Regenerate summary with updated information
                                summary = self.generate_summary()
                                self.conversation_state['awaiting_summary_confirmation'] = True
                                self.history.append({"role": "agent", "message": summary})
                                print(f"[main_processor] Summary mode (LLM): correction applied, showing updated summary")
                                return summary  # Return just the summary, not the message + summary
                            else:
                                print(f"[main_processor] Summary mode (LLM): correction application failed: {message}")
                                return message
                        else:
                            # No specific correction detected via detect_correction
                            # Try to format the user's response as a normal form update (fallback)
                            print(f"[main_processor] Summary mode (LLM): correction detection failed, trying to format user response as form update")
                            try:
                                # Save form state before formatting to check if anything changed
                                import copy
                                form_before = copy.deepcopy(self.form)
                                
                                # Use formatter to extract information from user's response
                                format_prompt = self.make_template(mode="format")
                                formatted_data = self.formatter(user_response, format_prompt)
                                
                                # Check if any fields were actually updated by comparing before/after
                                fields_updated = False
                                for section_name in self.form:
                                    if section_name in form_before:
                                        for field_name in self.form[section_name]:
                                            old_value = str(form_before[section_name].get(field_name, "") or "").strip()
                                            new_value = str(self.form[section_name].get(field_name, "") or "").strip()
                                            if old_value != new_value and new_value:
                                                fields_updated = True
                                                print(f"[main_processor] Summary mode (LLM): Field updated - {section_name}.{field_name}: '{old_value}' -> '{new_value}'")
                                                break
                                        if fields_updated:
                                            break
                                
                                if fields_updated:
                                    # Regenerate summary with updated information
                                    summary = self.generate_summary()
                                    self.conversation_state['awaiting_summary_confirmation'] = True
                                    self.history.append({"role": "agent", "message": summary})
                                    print(f"[main_processor] Summary mode (LLM): form updated via formatter, showing updated summary")
                                    return summary
                                else:
                                    print(f"[main_processor] Summary mode (LLM): formatter did not update any fields")
                            except Exception as e:
                                print(f"[main_processor] Summary mode (LLM): error formatting user response: {e}")
                                import traceback
                                traceback.print_exc()
                            
                            # If formatting also failed, ask user what they want to change
                            clarification_msg = (
                                "Sure, what change would you like to make? "
                                "You can say something like 'Change the duration to 3 days' or "
                                "'Update the referral source to Google'."
                            )
                            print("[main_processor] Summary mode (LLM): generic change intent, asking for specific change details")
                            self.history.append({"role": "agent", "message": clarification_msg})
                            return clarification_msg
                
                        # Question intent - user is asking about requirements or process
                        if intent == "question":
                            # Fall through to existing question-handling logic (STEP 3 below),
                            # which checks for missing fields and explains what's needed.
                            print("[main_processor] Summary mode (LLM): user asking a question about summary/requirements")
                            # Do not return here; let the existing question logic run.

                    # ─────────────────────────────────────────────────────
                    # 1A. Handle late "I have reports" in summary view
                    # ─────────────────────────────────────────────────────
                    # If the user now says they DO have reports (even though summary shows none),
                    # offer the upload prompt once and show the upload card for that message only.
                    if not self.conversation_state.get("awaiting_report_upload", False):
                        has_report_words = any(
                            phrase in user_input_lower
                            for phrase in ["report", "reports", "mri", "x-ray", "x ray", "ct scan", "scan"]
                        )
                        positive_report_indicators = [
                            "i have reports",
                            "have reports",
                            "reports available",
                            "reports are available",
                            "i have an mri",
                            "i have x-ray",
                            "i have x ray",
                            "i have ct scan",
                            "i have scans",
                        ]
                        negative_report_indicators = [
                            "no reports",
                            "no report",
                            "dont have reports",
                            "don't have reports",
                            "dont have any reports",
                            "don't have any reports",
                            "i dont have reports",
                            "i don't have reports",
                        ]
                        is_positive_reports = any(p in user_input_lower for p in positive_report_indicators)
                        is_negative_reports = any(n in user_input_lower for n in negative_report_indicators)

                        if has_report_words and is_positive_reports and not is_negative_reports:
                            upload_prompt = (
                                "Great! Since you mentioned you have reports, would you like to upload them? "
                                "You can upload MRI, X-ray, CT scan, or blood test reports."
                            )
                            print("[main_processor] Summary mode: user indicated they have reports, asking about upload.")
                            self.conversation_state["awaiting_report_upload"] = True
                            self.history.append({"role": "agent", "message": upload_prompt})
                            # Stay in summary confirmation mode; upload handling/confirmation will follow.
                            return upload_prompt

                    # If we're already waiting for report upload confirmation in summary mode,
                    # handle that first before treating the message as a confirmation/correction.
                    if self.conversation_state.get("awaiting_report_upload", False):
                        print("[main_processor] Summary mode: waiting for report upload confirmation, checking user response")
                        upload_confirmation_indicators = [
                            "i have uploaded",
                            "i've uploaded",
                            "i uploaded",
                            "have uploaded",
                            "upload is done",
                            "uploaded the reports",
                            "uploaded my reports",
                            "uploaded my mri",
                            "uploaded my x-ray",
                            "uploaded my ct",
                            "uploaded my scan",
                        ]
                        if any(ind in user_input_lower for ind in upload_confirmation_indicators):
                            print("[main_processor] Summary mode: user confirmed reports upload, marking reports_uploaded flag")
                            self.conversation_state["awaiting_report_upload"] = False
                            self.conversation_state["reports_uploaded"] = True
                            # Do not exit summary mode; user still needs to confirm the summary.
                        else:
                            # Check if user is declining upload or wants to skip
                            skip_indicators = [
                                "no",
                                "nope",
                                "skip",
                                "not now",
                                "later",
                                "don't have",
                                "dont have",
                                "can't upload",
                                "dont want to",
                                "don't want to",
                            ]
                            if any(ind in user_input_lower for ind in skip_indicators):
                                print("[main_processor] Summary mode: user declined upload, clearing awaiting_report_upload flag")
                                self.conversation_state["awaiting_report_upload"] = False
                                # Optionally mark that they have reports but chose not to upload
                                current_reports = self.form.get("History & Diagnostics", {}).get("Reports", "")
                                if not current_reports or not str(current_reports).strip():
                                    self.form["History & Diagnostics"]["Reports"] = (
                                        "User mentioned having reports but did not upload them."
                                    )
                            # If not an explicit skip, fall through and continue summary handling

                    # ─────────────────────────────────────────────────────
                    # 1B. Fallback if LLM intent classification failed
                    # ─────────────────────────────────────────────────────
                    # If classify_summary_response() could not return a valid intent,
                    # avoid any further keyword-based heuristics and ask the user directly.
                    print(
                        "[main_processor] Summary mode: LLM intent classification failed, asking user for clarification"
                    )
                    clarification_msg = (
                        "I want to make sure I understood you correctly. "
                        "Is the summary accurate, or would you like to change anything? "
                        "You can say 'Everything looks correct' or describe what you'd like to change."
                    )
                    self.history.append({"role": "agent", "message": clarification_msg})
                    return clarification_msg
                
                # STEP 2: DISABLED - Correction detection during normal flow
                # Corrections are now only allowed after the final summary
                # if form_has_data and self.should_check_for_correction(user_response):
                #     ... (correction detection code disabled)
                
                # STEP 3: Proceed with normal form filling (no corrections during interview)
                
                # Use LLM to decide if user is asking about missing/required information
                user_response_lower = user_response.lower().strip()
                missing_info_intent = {}
                try:
                    missing_prompt = f"""
You are assisting with a medical intake interview.

The patient just said:
"{user_response}"

Decide if they are ASKING whether some information is needed or required
for the form (for example, 'do you need that information?', 'is that needed?').

Respond ONLY with a JSON object:
{{
  "is_question_about_missing_info": true | false
}}
"""
                    raw_missing = self.llm_complete(missing_prompt)
                    json_str_missing = self.extract_json_from_response(raw_missing)
                    if json_str_missing:
                        data_missing = json.loads(json_str_missing)
                        if isinstance(data_missing, dict):
                            missing_info_intent = data_missing
                except Exception as e:
                    print(f"[main_processor] Error in missing-info intent classification: {e}")
                
                if missing_info_intent.get("is_question_about_missing_info"):
                    # User is asking if information is needed - check what fields are missing
                    self.validator()  # This will populate missing_fields
                    if self.missing_fields:
                        # There are missing fields, respond that yes, the information is needed
                        # Format field names to be more user-friendly
                        friendly_field_names = []
                        for field in self.missing_fields[:2]:  # Limit to 2 fields
                            # Convert field names to more readable format
                            friendly_name = field.replace("_", " ").title()
                            # Special handling for common fields
                            if "smoke" in field.lower() or "drink" in field.lower() or "alcohol" in field.lower():
                                friendly_name = "smoking and alcohol habits"
                            elif "exercise" in field.lower() or "lifestyle" in field.lower():
                                friendly_name = "exercise and lifestyle"
                            elif "systemic" in field.lower() or "illness" in field.lower():
                                friendly_name = "health conditions"
                            elif "reports" in field.lower():
                                friendly_name = "reports"
                            friendly_field_names.append(friendly_name)
                        
                        if len(friendly_field_names) == 1:
                            response = f"Yes, that information would be helpful. Could you please provide more details about {friendly_field_names[0]}?"
                        else:
                            response = f"Yes, that information would be helpful. Could you please provide more details about {', '.join(friendly_field_names)}?"
                        self.history.append({"role": "agent", "message": response})
                        print(f"[main_processor] User asked if info is needed (LLM intent), responding that {len(self.missing_fields)} field(s) still need to be filled")
                        return response
                    else:
                        # All fields are filled - check if form is complete
                        if self.invalid_index() and not self.conversation_state.get('awaiting_summary_confirmation', False):
                            # Form is complete, show summary
                            summary = self.generate_summary()
                            self.conversation_state['awaiting_summary_confirmation'] = True
                            self.history.append({"role": "agent", "message": summary})
                            print(f"[main_processor] Form complete after user question, showing summary")
                            return summary
                        else:
                            # Form not complete yet, continue with next question
                            prompt_template = self.make_template(mode="query")
                            if prompt_template == "SKIP_SECTION":
                                # Move to next section
                                current_index = self.form_sections.index(self.current_section)
                                if current_index < len(self.form_sections) - 1:
                                    self.current_section = self.form_sections[current_index + 1]
                                    self.conversation_state["attempts"] = 0
                                    self.conversation_state["last_question"] = None
                                    prompt_template = self.make_template(mode="query")
                                else:
                                    # All sections complete, show summary
                                    summary = self.generate_summary()
                                    self.conversation_state['awaiting_summary_confirmation'] = True
                                    self.history.append({"role": "agent", "message": summary})
                                    return summary
                            
                            if prompt_template and prompt_template != "SKIP_SECTION":
                                next_question = self.talk_to_user(prompt_template)
                                self.history.append({"role": "agent", "message": next_question})
                                return next_question
                            else:
                                # No more questions, show summary
                                summary = self.generate_summary()
                                self.conversation_state['awaiting_summary_confirmation'] = True
                                self.history.append({"role": "agent", "message": summary})
                                return summary
                
                # Format user's response into structured data
                prompt_template = self.make_template(
                    mode="format", source=user_response
                )
                # Call formatter to update the form
                self.formatter(user_response, prompt_template)

                # CRITICAL: Check if user mentioned having reports - PRIORITY: ask about upload FIRST before continuing
                # Check regardless of current section (user might mention reports in comprehensive first answer)
                # Use LLM-based classification for reports intent
                upload_intent = self.classify_reports_intent(user_response)
                has_reports_flag = upload_intent.get("has_reports")
                wants_upload_flag = upload_intent.get("wants_upload")

                # Only check if we haven't already handled reports upload
                if not self.conversation_state.get("reports_uploaded", False) and not self.conversation_state.get(
                    "awaiting_report_upload", False
                ):
                    if has_reports_flag is True:
                        # User indicates they HAVE reports - ask about upload IMMEDIATELY
                        if wants_upload_flag is False:
                            # They have reports but don't want to upload now
                            reports_field = self.form.get("History & Diagnostics", {}).get("Reports", "")
                            if not reports_field or not str(reports_field).strip():
                                self.form["History & Diagnostics"]["Reports"] = (
                                    "User mentioned having reports but did not upload them."
                                )
                            print("[main_processor] User has reports but doesn't want to upload now.")
                        else:
                            # They have reports and are willing/neutral about upload → ask IMMEDIATELY
                            print(
                                "[main_processor] User mentioned having reports in response, asking about upload IMMEDIATELY."
                            )
                            upload_prompt = (
                                "Great! Since you mentioned you have reports, would you like to upload them? "
                                "You can upload MRI, X-ray, CT scan, or blood test reports."
                            )
                            self.history.append({"role": "agent", "message": upload_prompt})
                            self.conversation_state["awaiting_report_upload"] = True
                            # CRITICAL: Return immediately - don't continue with other questions until upload is handled
                            return upload_prompt
                    elif has_reports_flag is False:
                        # User explicitly said they do NOT have reports (not just that they didn't mention it)
                        # Only fill the field if they explicitly stated they don't have reports
                        reports_field = self.form.get("History & Diagnostics", {}).get("Reports", "")
                        if not reports_field or not str(reports_field).strip():
                            # Check if user explicitly said they don't have reports (not just null/not mentioned)
                            user_lower = user_response.lower()
                            explicit_no_reports = any(phrase in user_lower for phrase in [
                                "i dont have", "i don't have", "dont have", "don't have",
                                "no reports", "no report", "no mri", "no ct", "no x-ray", "no x ray",
                                "no scan", "no scans", "no imaging", "i have no reports",
                                "i don't have any reports", "i dont have any reports"
                            ])
                            
                            if explicit_no_reports:
                                self.form["History & Diagnostics"]["Reports"] = (
                                    "No relevant diagnostic reports available for this issue"
                                )
                                print("[main_processor] User explicitly said they have no reports; updating Reports field.")
                            else:
                                # LLM returned false but user didn't explicitly say no - don't fill the field yet
                                print("[main_processor] LLM returned has_reports=false but user didn't explicitly say no reports. Not auto-filling field - will ask later.")
                    # If has_reports_flag is None, do nothing - user hasn't mentioned reports yet, we'll ask later

                # CRITICAL: If we're waiting for report upload confirmation, check user's response
                if self.conversation_state.get('awaiting_report_upload', False):
                    print(f"[main_processor] Waiting for report upload confirmation, checking user response")
                    user_response_lower = user_response.lower().strip()
                    
                    # If user says they've uploaded, mark reports as uploaded and don't ask again
                    upload_confirmation_indicators = [
                        "i have uploaded", "i've uploaded", "i uploaded", "have uploaded",
                        "upload is done", "uploaded the reports", "uploaded my reports",
                        "uploaded my mri", "uploaded my x-ray", "uploaded my ct", "uploaded my scan"
                    ]
                    if any(indicator in user_response_lower for indicator in upload_confirmation_indicators):
                        print(f"[main_processor] User confirmed reports upload, marking reports_uploaded flag and continuing")
                        self.conversation_state['awaiting_report_upload'] = False
                        self.conversation_state['reports_uploaded'] = True
                        # After upload confirmation, continue with remaining questions (don't return here)
                        # Fall through to validation and next question logic below
                    else:
                        # Check if user is declining upload or wants to skip
                        skip_indicators = [
                            "no",
                            "nope",
                            "skip",
                            "not now",
                            "later",
                            "don't have",
                            "can't upload",
                            "don't want to",
                        ]
                        if any(indicator in user_response_lower for indicator in skip_indicators):
                            print(f"[main_processor] User declined upload, clearing flag and continuing")
                            self.conversation_state["awaiting_report_upload"] = False
                            # Update Reports field to indicate they have reports but didn't upload
                            current_reports = self.form.get("History & Diagnostics", {}).get("Reports", "")
                            if not current_reports or not str(current_reports).strip():
                                # Set a placeholder to indicate they mentioned having reports
                                self.form["History & Diagnostics"]["Reports"] = (
                                    "User mentioned having reports but did not upload"
                                )
                                print(
                                    "[main_processor] Updated Reports field to indicate user has reports but didn't upload"
                                )
                            # Respond with an empathetic reassurance, then continue with remaining questions
                            reassurance_msg = (
                                "That's completely okay. If you find your reports later, you can always upload them then. "
                                "For now, let's continue so we can understand your condition and help you better."
                            )
                            self.history.append({"role": "agent", "message": reassurance_msg})
                            # After reassurance, continue to check for remaining questions (don't return here)
                            # Fall through to validation and next question logic below
                        else:
                            # User might be providing more info or confirming - clear the flag and continue
                            # The upload will be handled separately via the upload endpoint
                            print(f"[main_processor] User response after upload prompt, clearing flag and continuing")
                            self.conversation_state["awaiting_report_upload"] = False
                            # Continue to process remaining questions
                
                # Validate if all fields in current section are filled
                is_section_complete = self.validator()

                if is_section_complete:
                    # Check if all sections covered by the current predefined question are complete
                    # Only increment idx when ALL sections for that question are done
                    all_sections_for_question_complete = False
                    
                    if self.idx == 0:
                        # idx=0 now covers "Present Complaint" and "Previous Consultations"
                        present_complaint_complete = self.validator("Present Complaint")
                        prev_consultations_complete = self.validator("Previous Consultations")
                        # Force asking Previous Consultations at least once
                        if not self.conversation_state.get("asked_previous_consultations", False):
                            prev_consultations_complete = False
                        all_sections_for_question_complete = present_complaint_complete and prev_consultations_complete
                    elif self.idx == 1:
                        # idx=1 now covers "Pain Assessment" and "History & Diagnostics"
                        pain_assessment_complete = self.validator("Pain Assessment")
                        history_diag_complete = self.validator("History & Diagnostics")
                        all_sections_for_question_complete = pain_assessment_complete and history_diag_complete
                    elif self.idx == 2:
                        # idx=2 covers "Treatment Goals" and "Referral"
                        treatment_goals_complete = self.validator("Treatment Goals")
                        referral_complete = self.validator("Referral")
                        all_sections_for_question_complete = treatment_goals_complete and referral_complete
                    else:
                        # For other predefined questions, one section = one question
                        all_sections_for_question_complete = True
                    
                    # CRITICAL: Handle multi-section predefined questions BEFORE calling all_ops()
                    # This ensures we don't move to the wrong section
                    if self.idx == 0:
                        # idx=0 covers "Present Complaint" and "Previous Consultations"
                        present_complaint_complete = self.validator("Present Complaint")
                        prev_consultations_complete = self.validator("Previous Consultations")
                        if not self.conversation_state.get("asked_previous_consultations", False):
                            prev_consultations_complete = False
                        if present_complaint_complete and not prev_consultations_complete:
                            if "Previous Consultations" in self.form_sections:
                                self.current_section = "Previous Consultations"
                                print(f"[main_processor] Present Complaint complete, explicitly setting current_section to Previous Consultations")
                        elif all_sections_for_question_complete:
                            self.all_ops()
                    elif self.idx == 1:
                        # idx=1 covers "Pain Assessment" and "History & Diagnostics"
                        pain_assessment_complete = self.validator("Pain Assessment")
                        history_diag_complete = self.validator("History & Diagnostics")
                        if pain_assessment_complete and not history_diag_complete:
                            if "History & Diagnostics" in self.form_sections:
                                self.current_section = "History & Diagnostics"
                                print(f"[main_processor] Pain Assessment complete, explicitly setting current_section to History & Diagnostics")
                        elif history_diag_complete and not pain_assessment_complete:
                            if "Pain Assessment" in self.form_sections:
                                self.current_section = "Pain Assessment"
                                print(f"[main_processor] History & Diagnostics complete, explicitly setting current_section to Pain Assessment")
                        elif all_sections_for_question_complete:
                            self.all_ops()
                            print(f"[main_processor] Pain Assessment and History & Diagnostics complete, moving to next predefined question")
                    elif self.idx == 2:
                        # idx=2 covers "Treatment Goals" and "Referral"
                        treatment_goals_complete = self.validator("Treatment Goals")
                        referral_complete = self.validator("Referral")
                        if treatment_goals_complete and not referral_complete:
                            # Treatment Goals is done, but Referral is not
                            # Set current_section to Referral explicitly (don't call all_ops)
                            if "Referral" in self.form_sections:
                                self.current_section = "Referral"
                                print(f"[main_processor] Treatment Goals complete, explicitly setting current_section to Referral")
                        elif referral_complete and not treatment_goals_complete:
                            # Referral is done, but Treatment Goals is not
                            # Set current_section to Treatment Goals explicitly (don't call all_ops)
                            if "Treatment Goals" in self.form_sections:
                                self.current_section = "Treatment Goals"
                                print(f"[main_processor] Referral complete, explicitly setting current_section to Treatment Goals")
                        elif all_sections_for_question_complete:
                            # Both are complete, move to next section
                            self.all_ops()
                    else:
                        # Single-section question, just move to next section
                        self.all_ops()
                    
                    # Only increment idx if all sections for this predefined question are complete
                    if all_sections_for_question_complete:
                        self.idx += 1
                        print(f"[main_processor] All sections for predefined question idx={self.idx-1} complete, moving to next predefined question")
                        
                        # CRITICAL: When idx becomes 1, ensure we start with Pain Assessment if it's not complete
                        if self.idx == 1:
                            pain_assessment_complete = self.validator("Pain Assessment")
                            if not pain_assessment_complete:
                                if "Pain Assessment" in self.form_sections:
                                    self.current_section = "Pain Assessment"
                                    print(f"[main_processor] idx=1: Setting current_section to Pain Assessment (not complete)")
                            else:
                                history_diag_complete = self.validator("History & Diagnostics")
                                if not history_diag_complete:
                                    if "History & Diagnostics" in self.form_sections:
                                        self.current_section = "History & Diagnostics"
                                        print(f"[main_processor] idx=1: Pain Assessment complete, setting current_section to History & Diagnostics")
                    else:
                        print(f"[main_processor] Section {self.current_section} complete, but other sections for this predefined question still incomplete. Staying on idx={self.idx}")

                    if not self.invalid_index():
                        # Prepare next question
                        prompt_template = self.make_template(mode="query")
                        # Handle SKIP_SECTION - move to next section
                        if prompt_template == "SKIP_SECTION":
                            print(f"[main_processor] Current section should be skipped, moving to next section")
                            current_index = self.form_sections.index(self.current_section)
                            if current_index < len(self.form_sections) - 1:
                                self.current_section = self.form_sections[current_index + 1]
                                # Try generating question for new section
                                prompt_template = self.make_template(mode="query")
                                if prompt_template == "SKIP_SECTION":
                                    # Keep moving forward until we find a section that needs questions
                                    while prompt_template == "SKIP_SECTION" and current_index < len(self.form_sections) - 1:
                                        current_index += 1
                                        self.current_section = self.form_sections[current_index]
                                        prompt_template = self.make_template(mode="query")
                        
                        if prompt_template and prompt_template != "SKIP_SECTION":
                            next_question = self.talk_to_user(prompt_template)
                            # Add agent response to history
                            self.history.append({"role": "agent", "message": next_question})
                            print(
                                f"[main_processor] Section complete, moving to next. History length: {len(self.history)}"
                            )
                            # Return question directly without transition message to avoid wasting prompts
                            return next_question
                        else:
                            # All sections are complete or skipped
                            print(f"[main_processor] All sections complete or skipped, showing summary")
                            self.save_progress()
                            summary = self.generate_summary()
                            self.conversation_state["awaiting_summary_confirmation"] = True
                            self.history.append({"role": "agent", "message": summary})
                            return summary
                    else:
                        # Interview complete - show summary and ask for confirmation
                        self.save_progress()
                        summary = self.generate_summary()
                        self.conversation_state['awaiting_summary_confirmation'] = True
                        self.history.append({"role": "agent", "message": summary})
                        print(f"[main_processor] Interview complete, showing summary for confirmation")
                        return summary
                else:
                    # Ask for missing information
                    prompt_template = self.make_template(mode="requery")
                    # Handle SKIP_SECTION
                    if prompt_template == "SKIP_SECTION":
                        # Move to next section and generate question
                        current_index = self.form_sections.index(self.current_section)
                        if current_index < len(self.form_sections) - 1:
                            self.current_section = self.form_sections[current_index + 1]
                            prompt_template = self.make_template(mode="query")
                            if prompt_template == "SKIP_SECTION":
                                # Keep moving forward
                                while prompt_template == "SKIP_SECTION" and current_index < len(self.form_sections) - 1:
                                    current_index += 1
                                    self.current_section = self.form_sections[current_index]
                                    prompt_template = self.make_template(mode="query")
                    
                    if prompt_template and prompt_template != "SKIP_SECTION":
                        follow_up_question = self.talk_to_user(prompt_template)
                        # Add agent response to history
                        self.history.append({"role": "agent", "message": follow_up_question})
                        print(f"[main_processor] Asking follow-up. History length: {len(self.history)}")
                        return follow_up_question
                    else:
                        # All sections complete, show summary
                        print(f"[main_processor] All sections complete, showing summary")
                        self.save_progress()
                        summary = self.generate_summary()
                        self.conversation_state["awaiting_summary_confirmation"] = True
                        self.history.append({"role": "agent", "message": summary})
                        return summary

            # Default response if something goes wrong (empty input when not in START mode)
            if self.talk_mode != "START":
                return (
                    "I'm sorry, I didn't catch that. Could you please repeat your answer?"
                )
            # If we're in START mode and got empty input, this shouldn't happen
            # but return a safe message
            return "Please provide your response to continue the interview."

        except Exception as e:
            print(f"Error in main_processor: {e}")
            # Fall back to predefined questions if there's an error
            if self.idx < len(self.predefined_questions):
                return self.predefined_questions[self.idx][1]
            return (
                "I apologize, there was an error. Could you please repeat your answer?"
            )

# experimental code
# import json
# import os
# import time
# import random
# from threading import Thread
# from datetime import datetime
# import GPUtil

# from typing import Optional, Dict, Any, List
# from llama_index.core import Settings
# from llama_index.vector_stores.chroma import ChromaVectorStore
# from llama_index.core.schema import QueryBundle
# from llama_index.core import VectorStoreIndex
# from llama_index.embeddings.huggingface import HuggingFaceEmbedding
# from llama_index.core import Document
# from llama_index.core.llms import ChatMessage, MessageRole
# from llama_index.core.storage.chat_store import SimpleChatStore
# from llama_index.core.memory import ChatMemoryBuffer
# import chromadb

# # Import database connector if available
# try:
#     from db import MedicalInterviewDB

#     has_db = True
# except ImportError:
#     has_db = False

# # Fix import paths based on project structure
# try:
#     from src.llm.utils import load_gemini_key, init_llm, final_form_filling
# except ImportError:
#     try:
#         from src.llm.utils import load_gemini_key, init_llm, final_form_filling
#     except ImportError:
#         from llm.utils import load_gemini_key, init_llm, final_form_filling

# from src.enums import ENUMS
# from src.prompts import (
#     WELCOME_PROMPT,
#     SYSTEM_PROMPT,
#     TEMPLATE_PROMPT,
#     QUERY_TASK_PROMPT,
#     FORMAT_PROMPT,
#     PREDEFINED_QUESTIONS,
#     MEDICAL_FORM_TEMPLATE,
#     FINAL_FORM_FILL_PROMPT,
#     DEFAULT_ANSWER,
#     READY_TO_START_PROMPT,
#     INTERVIEW_DECLINED_PROMPT,
# )

# # Additional prompt to prevent making assumptions
# NO_ASSUMPTIONS_PROMPT = """
# IMPORTANT GUIDELINES FOR THIS MEDICAL INTERVIEW:

# 1. Do NOT make assumptions about the patient's situation that are not directly stated by them.
# 2. Only ask questions based on facts they have provided or standard medical interview protocol.
# 3. Do NOT assume connections between symptoms and lifestyle unless explicitly stated by the patient.
# 4. Do NOT bring up topics or facts that the patient hasn't mentioned themselves.
# 5. Your role is to gather information neutrally, not to suggest or assume details about the patient's life or circumstances.
# 6. If you need more information, ask direct questions without implying assumptions.
# 7. Avoid phrases like "your coach" or "your trainer" unless the patient has specifically mentioned having one.
# 8. Focus only on the medical facts relevant to the current section of the interview.
# 9. Do not add speculative context to your questions.
# """

# enums_obj = ENUMS()


# class HealthAgent:
#     def __init__(self, db_instance=None, user_id=None, interview_id=None):
#         """
#         Initialize the HealthAgent with all necessary components
#         for conducting a medical interview.

#         Args:
#             db_instance: Optional MedicalInterviewDB instance for database interactions
#             user_id: Optional user ID for database tracking
#             interview_id: Optional interview ID for database tracking
#         """
#         # Initialize database connection if provided
#         self.db = db_instance
#         self.user_id = user_id
#         self.interview_id = interview_id

#         # Initialize predefined questions and prompts
#         self.init_prompts()

#         # Initialize history for conversation tracking
#         self.history = []
#         self.history.append({"role": "agent", "message": self.welcome_prompt})

#         try:
#             # Initialize LLM
#             self.gemini_api_key = load_gemini_key()
#             self.llm = init_llm(self.gemini_api_key)
#         except Exception as e:
#             print(f"Error initializing LLM: {e}")
#             self.llm = None

#         # Initialize paths and configs
#         self.model_name = enums_obj.embedding_model_name
#         self.chat_store_path = enums_obj.chat_history_json_path
#         self.medical_form_path = enums_obj.medical_interview_history_json_path
#         self.chroma_db_path = enums_obj.git_db_path
#         self.collection_name = enums_obj.git_collection_name

#         # Check device availability
#         try:
#             self.devices = GPUtil.getAvailable()
#             self.device = self.devices[0] if len(self.devices) else "cpu"
#         except:
#             self.device = "cpu"

#         try:
#             # Initialize embedding model
#             self.embed_model = HuggingFaceEmbedding(model_name=self.model_name)
#         except Exception as e:
#             print(f"Error initializing embedding model: {e}")
#             self.embed_model = None

#         # Initialize chromadb, health index, and chat components
#         try:
#             self.init_chromadb()
#             self.init_health_info_index()
#             self.init_chat_components()
#         except Exception as e:
#             print(f"Error initializing database components: {e}")
#             self.health_relevancy_retriever = None
#             self.index = None

#         # Initialize form and tracking variables
#         self.init_form()

#         # Continuous tracking
#         self.stop_flag = False
#         self.info = []
#         self.relevancy_action = "Listen"
#         self.check_interval = 10

#         # Start keyword checking thread
#         self.start_keyword_thread()

#     def ensure_string(self, text):
#         """Ensure the text is a string and not some other object"""
#         if hasattr(text, "text"):  # Some API responses might have a .text attribute
#             return text.text
#         elif hasattr(text, "__str__"):  # Convert to string if possible
#             return str(text)
#         else:
#             return "Response could not be processed"

#     def llm_complete(self, prompt):
#         """Call LLM with appropriate error handling"""
#         try:
#             if self.llm is None:
#                 raise ValueError("LLM not initialized")

#             # Use the correct method for the LLM type
#             response = self.llm.complete(prompt)
#             # Convert response to string
#             return self.ensure_string(response)
#         except Exception as e:
#             print(f"Error in LLM completion: {e}")
#             raise

#     def start_keyword_thread(self):
#         """Start the background thread for keyword checking."""
#         try:
#             keyword_thread = Thread(target=self.check_for_keyword, daemon=True)
#             keyword_thread.start()
#         except Exception as e:
#             print(f"Error starting keyword thread: {e}")

#     def init_prompts(self):
#         """Initialize all prompts and templates."""
#         self.welcome_prompt = WELCOME_PROMPT
#         # Combine system prompt with no assumptions prompt
#         self.system_prompt = SYSTEM_PROMPT + "\n" + NO_ASSUMPTIONS_PROMPT
#         self.query_task_prompt = QUERY_TASK_PROMPT
#         self.format_prompt = FORMAT_PROMPT
#         self.predefined_questions = PREDEFINED_QUESTIONS
#         self.final_form_fill_prompt = FINAL_FORM_FILL_PROMPT
#         self.default_answer = DEFAULT_ANSWER
#         self.ready_to_start_message = READY_TO_START_PROMPT
#         self.interview_declined_message = INTERVIEW_DECLINED_PROMPT

#     def init_chromadb(self):
#         """Initialize chromadb connection and vector store."""
#         try:
#             self.db_client = chromadb.PersistentClient(path=self.chroma_db_path)
#             collection = self.db_client.get_collection(self.collection_name)
#             vector_store = ChromaVectorStore(chroma_collection=collection)
#             self.index = VectorStoreIndex.from_vector_store(
#                 vector_store, embed_model=self.embed_model
#             )
#         except Exception as e:
#             print(f"Error initializing ChromaDB: {e}")
#             self.index = VectorStoreIndex([Document(text="Fallback document")])

#     def init_health_info_index(self):
#         """Initialize health information vector index."""
#         try:
#             collection = self.db_client.get_collection(self.collection_name)
#             vector_store = ChromaVectorStore(chroma_collection=collection)
#             self.health_relevancy_index = VectorStoreIndex.from_vector_store(
#                 vector_store, embed_model=self.embed_model
#             )
#             self.health_relevancy_retriever = self.health_relevancy_index.as_retriever()
#         except Exception as e:
#             print(f"Error initializing health info index: {e}")
#             if self.index:
#                 self.health_relevancy_retriever = self.index.as_retriever()
#             else:
#                 self.health_relevancy_retriever = None

#     def init_chat_components(self):
#         """Initialize chat store and memory components."""
#         try:
#             # Initialize with an empty list for message history
#             self.messages_history = []

#             # Initialize chat engine with the LLM
#             if self.index and self.llm:
#                 self.chat_engine = self.index.as_chat_engine(
#                     llm=self.llm, chat_mode="condense_question"
#                 )
#             else:
#                 raise ValueError("Index or LLM not properly initialized")

#         except Exception as e:
#             print(f"Error initializing chat components: {e}")

#     def init_form(self, load_from_db=False):
#         """
#         Initialize form and tracking variables.

#         Args:
#             load_from_db: Whether to attempt loading the form from database
#         """
#         self.idx = 0
#         self.form = MEDICAL_FORM_TEMPLATE.copy()
#         self.final_filled_form = MEDICAL_FORM_TEMPLATE.copy()
#         self.form_sections = list(self.form.keys())
#         self.current_section = self.form_sections[0]
#         self.missing_fields = []
#         self.conversation_state = {
#             "last_question": None,
#             "attempts": 0,
#             "max_attempts": 3,
#         }
#         self.talk_mode = "START"

#         # If we have database connection and interview_id, try to load existing form
#         if load_from_db and has_db and self.db and self.interview_id:
#             try:
#                 interview = self.db.get_interview(self.interview_id)
#                 if interview and interview.get("form_data"):
#                     self.form = interview["form_data"]
#                     self.current_section = interview.get(
#                         "current_section", self.current_section
#                     )
#                     self.idx = self.form_sections.index(self.current_section)
#                     print(
#                         f"Loaded form data from database for interview {self.interview_id}"
#                     )
#             except Exception as e:
#                 print(f"Error loading form from database: {e}")

#     def check_for_keyword(self):
#         """
#         Check for health-related keywords in transcriptions every check_interval seconds.
#         Runs in a separate thread.
#         """
#         while not self.stop_flag:
#             try:
#                 if self.info and self.health_relevancy_retriever:
#                     self.relevancy_action = self.describe_action(self.info)
#                 time.sleep(self.check_interval)
#             except Exception as e:
#                 print(f"Error in keyword checking: {e}")
#                 time.sleep(self.check_interval)

#     def describe_action(self, text_chunk, threshold=0.5):
#         """
#         Determine action based on relevance of text to health topics.

#         Args:
#             text_chunk: Text to analyze
#             threshold: Confidence threshold

#         Returns:
#             Action to take: "Silent", "Nod", or "Nudge"
#         """
#         if not text_chunk or not self.health_relevancy_retriever:
#             return "Listen"

#         try:
#             # Create query bundle for retrieval
#             query = QueryBundle(query_str=str(text_chunk))
#             selected_nodes = self.health_relevancy_retriever.retrieve(query)

#             # Check relevance scores
#             for node_info in selected_nodes:
#                 score = getattr(node_info, "score", 0)
#                 if score is not None and score > threshold:
#                     return random.choice(["Silent", "Nod"])
#             return "Nudge"
#         except Exception as e:
#             print(f"Error in describe_action: {e}")
#             return "Listen"

#     def formatter(self, user_input, prompt_template):
#         """
#         Format user input into structured data for the current section.
#         Uses LLM to extract structured data from user responses.

#         Args:
#             user_input: The user's response text
#             prompt_template: The prompt to use for formatting

#         Returns:
#             Formatted form data
#         """
#         example_output = """{"Patient Information": {"Age": "35", "Gender": "male"}}"""

#         try:
#             # Prepare enhanced prompt for LLM to get better structured extraction
#             enhanced_prompt = TEMPLATE_PROMPT.format(
#                 prompt_template, json.dumps(self.form, indent=2), example_output
#             )

#             # Add more specific guidance to improve extraction quality
#             enhanced_prompt += """
#             IMPORTANT GUIDELINES:
#             1. Extract ALL relevant medical information from the response
#             2. Map information to the correct fields in the form
#             3. Make reasonable inferences about fields based on the patient's language
#             4. Preserve the exact structure of the form template
#             5. Return only valid JSON that can be parsed directly
#             6. Do NOT invent or assume information not explicitly stated by the patient
#             7. If uncertain about a field, leave it blank rather than guessing
#             """

#             # Get structured response from LLM
#             llm_response = self.llm_complete(enhanced_prompt)
#             print("LLM FORMATTING RESPONSE:\n", llm_response)

#             # Extract JSON from response
#             json_str = self.extract_json_from_response(llm_response)

#             if not json_str:
#                 # If no JSON found, try again with a simpler prompt
#                 fallback_prompt = f"""
#                 Convert this patient response to a JSON object matching the form structure:

#                 PATIENT RESPONSE: {user_input}
#                 FORM STRUCTURE: {json.dumps(self.form, indent=2)}

#                 Respond ONLY with valid JSON.
#                 """
#                 llm_response = self.llm_complete(fallback_prompt)
#                 json_str = self.extract_json_from_response(llm_response)

#                 if not json_str:
#                     print("No valid JSON found in LLM response after retry")
#                     return self.form

#             # Parse the JSON response
#             try:
#                 formatted_data = json.loads(json_str)

#                 # Update the form with extracted information
#                 for section, fields in self.form.items():
#                     if section in formatted_data:
#                         for key in fields:
#                             # Only update if there's new information
#                             new_value = formatted_data.get(section, {}).get(key, "")
#                             if new_value:
#                                 self.form[section][key] = new_value

#                 print("Updated form sections after formatting:")
#                 for section, fields in self.form.items():
#                     non_empty = {k: v for k, v in fields.items() if v}
#                     if non_empty:
#                         print(f"{section}: {non_empty}")

#                 # If we have database connection, update form in database
#                 if has_db and self.db and self.interview_id:
#                     progress = 0
#                     try:
#                         section_index = self.form_sections.index(self.current_section)
#                         progress = (section_index / len(self.form_sections)) * 100
#                     except:
#                         pass

#                     try:
#                         self.db.update_interview_form(
#                             interview_id=self.interview_id,
#                             form_data=self.form,
#                             current_section=self.current_section,
#                             progress=progress,
#                         )
#                     except Exception as e:
#                         print(f"Error updating form in database: {e}")

#                 return self.form
#             except json.JSONDecodeError as e:
#                 print(f"JSON decode error: {e}")
#                 print(f"Problematic JSON string: {json_str}")
#                 return self.form

#         except Exception as e:
#             print(f"Error in formatter: {e}")
#             return self.form

#     def extract_json_from_response(self, response):
#         """Extract valid JSON from an LLM response"""
#         if not response:
#             return None

#         # Check if the entire response is JSON
#         response = response.strip()
#         if response.startswith("{") and response.endswith("}"):
#             return response

#         # Try to find JSON within the response
#         start = response.find("{")
#         end = response.rfind("}")

#         if start != -1 and end != -1 and start < end:
#             return response[start : end + 1]

#         # Handle code block format
#         if "```json" in response:
#             parts = response.split("```json")
#             if len(parts) > 1:
#                 code_part = parts[1].split("```")[0].strip()
#                 if code_part.startswith("{") and code_part.endswith("}"):
#                     return code_part

#         # Handle regular code block
#         if "```" in response:
#             parts = response.split("```")
#             if len(parts) > 1:
#                 code_part = parts[1].strip()
#                 if code_part.startswith("{") and code_part.endswith("}"):
#                     return code_part

#         return None

#     def validator(self, current_section=None):
#         """
#         Validate the form data for the current section.

#         Args:
#             current_section: Section to validate (defaults to self.current_section)

#         Returns:
#             Boolean indicating if all required fields are filled
#         """
#         section = current_section or self.current_section

#         # Reset missing fields
#         self.missing_fields = []

#         # Check each required field in the current section
#         for field, value in self.form[section].items():
#             if not value:
#                 self.missing_fields.append(field)

#         print(f"Missing fields in {section}: {self.missing_fields}")

#         # If there are missing fields, return False
#         return len(self.missing_fields) == 0

#     def all_ops(self):
#         """
#         Perform all operations after successful validation of a section.
#         """
#         # Save current progress to file
#         self.save_progress()

#         # Move to next section if available
#         current_index = self.form_sections.index(self.current_section)
#         if current_index < len(self.form_sections) - 1:
#             self.current_section = self.form_sections[current_index + 1]
#             self.conversation_state["attempts"] = 0
#             self.conversation_state["last_question"] = None
#             print(f"Moving to next section: {self.current_section}")

#     def save_progress(self):
#         """
#         Save the current form state to a file and database if available.
#         """
#         try:
#             # Create a save data structure
#             save_data = {
#                 "timestamp": datetime.now().isoformat(),
#                 "form_data": self.form,
#                 "current_section": self.current_section,
#             }

#             # Create the directory if it doesn't exist
#             os.makedirs(os.path.dirname(self.medical_form_path), exist_ok=True)

#             # Save to file
#             with open(self.medical_form_path, "w") as f:
#                 json.dump(save_data, f, indent=4)

#             print(f"Progress saved to {self.medical_form_path}")

#             # Save to database if available
#             if has_db and self.db and self.interview_id:
#                 try:
#                     progress = 0
#                     section_index = self.form_sections.index(self.current_section)
#                     total_sections = len(self.form_sections)
#                     progress = (section_index / total_sections) * 100

#                     self.db.update_interview_form(
#                         interview_id=self.interview_id,
#                         form_data=self.form,
#                         current_section=self.current_section,
#                         progress=progress,
#                     )
#                     print(
#                         f"Progress saved to database for interview {self.interview_id}"
#                     )
#                 except Exception as e:
#                     print(f"Error saving progress to database: {e}")

#         except Exception as e:
#             print(f"Error saving progress: {e}")

#     def talk_to_user(self, prompt_template):
#         """
#         Generate appropriate response based on the conversation state.

#         Args:
#             prompt_template: The prompt to use for generating the response

#         Returns:
#             Response text to present to the user
#         """
#         # Update conversation state
#         self.conversation_state["attempts"] += 1

#         try:
#             # Enhance prompt to avoid making assumptions
#             prompt = (
#                 f"{self.system_prompt}\n\n{NO_ASSUMPTIONS_PROMPT}\n\n{prompt_template}"
#             )

#             # Add reminder about existing information
#             prompt += f"\n\nINFORMATION I ALREADY KNOW:\n{json.dumps(self.form, indent=2)}\n\n"
#             prompt += "Remember to ONLY use facts the patient has explicitly told you. DO NOT make any assumptions."

#             # Generate response using direct LLM call
#             response = self.llm_complete(prompt)

#             # Store last question
#             self.conversation_state["last_question"] = response

#             # Update talk mode
#             self.talk_mode = "USER"

#             return response

#         except Exception as e:
#             print(f"Error generating response: {e}")
#             # Use predefined question as fallback
#             if self.idx < len(self.predefined_questions):
#                 return self.predefined_questions[self.idx][1]
#             return "Could you please tell me more about your medical condition?"

#     def invalid_index(self):
#         """
#         Check if we've completed all sections.

#         Returns:
#             Boolean indicating if all sections are complete
#         """
#         return self.idx >= len(self.predefined_questions)

#     def make_template(self, mode, source=None, context=None):
#         """
#         Create appropriate prompts based on the mode and context.

#         Args:
#             mode: The type of prompt to create ("query", "requery", "format")
#             source: The user input text (for format mode)
#             context: Additional context for the prompt

#         Returns:
#             Prompt text for the LLM
#         """
#         if mode == "query":
#             # Generate a question based on the current section and predefined questions
#             if self.idx < len(self.predefined_questions):
#                 question = self.predefined_questions[self.idx][1]
#             else:
#                 return "Thank you for completing the medical interview."

#             prompt = f"""
#             You're a medical professional conducting an interview.

#             The current section you're asking about is: {self.current_section}

#             The standard question for this section is: "{question}"

#             The current status of this section in the form is:
#             {json.dumps(self.form[self.current_section], indent=2)}

#             Based on what's already filled in, formulate an appropriate question that focuses on getting the missing information.
#             If the section is completely empty, you can use the standard question.
            
#             IMPORTANT: Do NOT make assumptions about the patient. Only reference information they have explicitly provided.
#             Do NOT suggest connections between symptoms and activities unless the patient has stated them.
            
#             Reply ONLY with the final question to ask the patient. Do not include any explanations or additional text.
#             """
#             return prompt

#         elif mode == "requery":
#             # Create a prompt that specifically asks for missing information
#             missing_fields_str = ", ".join(
#                 self.missing_fields[:3]
#             )  # Limit to 3 fields at once

#             prompt = f"""
#             You're a medical professional conducting an interview.

#             The patient hasn't provided complete information about: {missing_fields_str}

#             These fields are part of the '{self.current_section}' section.

#             Ask a friendly, conversational follow-up question to get this specific information.
            
#             IMPORTANT: Do NOT make assumptions about the patient. Only reference information they have explicitly provided.
#             Do NOT suggest connections between symptoms and activities unless the patient has stated them.
            
#             Reply ONLY with the final question to ask the patient. Do not include any explanations or additional text.
#             """
#             return prompt

#         elif mode == "format":
#             if not source:
#                 print("No input provided for formatting.")
#                 return "No input provided for formatting."

#             # Create a prompt for extracting structured data from user input
#             prompt = f"""
#             Extract medical information from the patient's response and update the appropriate fields in the form.

#             PATIENT RESPONSE: {source}

#             CURRENT FORM:
#             {json.dumps(self.form, indent=2)}

#             Please update the form with any relevant information from the patient's response.
#             The response might contain information relevant to multiple sections, not just the current section ({self.current_section}).
            
#             IMPORTANT: Do NOT invent or assume information not explicitly stated by the patient.
#             If uncertain about any field, leave it blank rather than guessing.

#             Return the complete updated form as a valid JSON object matching the structure of the current form.
#             Only output the JSON object without any additional explanations.
#             """
#             return prompt

#         return source if source else "No template available."

#     def main_processor(self, user_response):
#         """
#         Process the user's response and determine the next action.

#         Args:
#             user_response: The user's response text

#         Returns:
#             The next question or response to present to the user
#         """
#         try:
#             # Check if interview is complete
#             if self.invalid_index():
#                 return "Thank you for completing the medical interview. All required information has been collected."

#             # Initial question
#             if self.talk_mode == "START":
#                 # Create first question prompt
#                 prompt_template = self.make_template(mode="query")
#                 first_question = self.talk_to_user(prompt_template)
#                 self.talk_mode = "USER"
#                 return first_question

#             # Process user's response
#             elif user_response:
#                 # Format user's response into structured data
#                 prompt_template = self.make_template(
#                     mode="format", source=user_response
#                 )
#                 # Call formatter to update the form
#                 self.formatter(user_response, prompt_template)

#                 # Validate if all fields in current section are filled
#                 is_section_complete = self.validator()

#                 if is_section_complete:
#                     # Move to next section
#                     self.all_ops()
#                     self.idx += 1

#                     if not self.invalid_index():
#                         # Prepare next question
#                         prompt_template = self.make_template(mode="query")
#                         next_question = self.talk_to_user(prompt_template)
#                         return f"Thank you for that information. {next_question}"
#                     else:
#                         # Interview complete
#                         self.save_progress()

#                         # If we have database connection, mark interview as complete
#                         if has_db and self.db and self.interview_id:
#                             try:
#                                 self.db.complete_interview(
#                                     interview_id=self.interview_id, form_data=self.form
#                                 )
#                                 print(
#                                     f"Interview {self.interview_id} marked as complete in database"
#                                 )
#                             except Exception as e:
#                                 print(
#                                     f"Error marking interview as complete in database: {e}"
#                                 )

#                         return "Thank you for completing all questions. Your medical information has been recorded."
#                 else:
#                     # Ask for missing information
#                     prompt_template = self.make_template(mode="requery")
#                     follow_up_question = self.talk_to_user(prompt_template)
#                     return follow_up_question

#             # Default response if something goes wrong
#             return (
#                 "I'm sorry, I didn't catch that. Could you please repeat your answer?"
#             )

#         except Exception as e:
#             print(f"Error in main_processor: {e}")
#             # Fall back to predefined questions if there's an error
#             if self.idx < len(self.predefined_questions):
#                 return self.predefined_questions[self.idx][1]
#             return (
#                 "I apologize, there was an error. Could you please repeat your answer?"
#             )
