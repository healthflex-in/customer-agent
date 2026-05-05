
import json
import os
import time
import random
from threading import Thread
from datetime import datetime
import GPUtil

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
    from src.llm.utils import load_gemini_key, init_llm, final_form_filling


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
            self.devices = GPUtil.getAvailable()
            self.device = self.devices[0] if len(self.devices) else "cpu"
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
        """Call LLM with appropriate error handling"""
        try:
            if self.llm is None:
                raise ValueError("LLM not initialized")

            # Use the correct method for the LLM type
            response = self.llm.complete(prompt)
            # Convert response to string
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
        self.idx = 0
        self.form = MEDICAL_FORM_TEMPLATE.copy()
        self.final_filled_form = MEDICAL_FORM_TEMPLATE.copy()
        self.form_sections = list(self.form.keys())
        self.current_section = self.form_sections[0]
        self.missing_fields = []
        self.conversation_state = {
            "last_question": None,
            "attempts": 0,
            "max_attempts": 3,
        }
        self.talk_mode = "START"

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

    def get_nudge_message(self):
        """
        Generate a polite message to guide the user back to relevant topics.

        Returns:
            A string containing a gentle nudge to stay on topic
        """
        nudge_messages = [
            "I notice we may be moving away from medical topics. Could we focus back on your health information?",
            "That's interesting, but to complete your medical interview, let's return to the health-related questions.",
            "I appreciate sharing that, but for this medical form, could we discuss more about your health condition?",
            "To help me better understand your medical needs, could we please return to discussing your health?",
            "I need to collect specific medical information. Could we please focus on your health details?",
        ]

        return random.choice(nudge_messages)

    def check_relevancy_and_get_action(self, text):
        """
        Check if the input text is relevant to health topics and return appropriate action.

        Args:
            text: User input text to analyze

        Returns:
            Tuple of (action, message) where action is "Listen", "Silent", "Nod", or "Nudge"
            and message is a nudge message if action is "Nudge", otherwise None
        """
        action = self.describe_action(text)

        if action == "Nudge":
            return (action, self.get_nudge_message())
        else:
            return (action, None)

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

        # Handle regular code block
        if "```" in response:
            parts = response.split("```")
            if len(parts) > 1:
                code_part = parts[1].strip()
                if code_part.startswith("{") and code_part.endswith("}"):
                    return code_part

        return None

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

        # Check each required field in the current section
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
        """
        try:
            # Create a save data structure
            save_data = {
                "timestamp": datetime.now().isoformat(),
                "form_data": self.form,
                "current_section": self.current_section,
            }

            # Create the directory if it doesn't exist
            os.makedirs(os.path.dirname(self.medical_form_path), exist_ok=True)

            # Save to file
            with open(self.medical_form_path, "w") as f:
                json.dump(save_data, f, indent=4)

            print(f"Progress saved to {self.medical_form_path}")

        except Exception as e:
            print(f"Error saving progress: {e}")

    def talk_to_user(self, prompt_template):
        """
        Generate appropriate response based on the conversation state.

        Args:
            prompt_template: The prompt to use for generating the response

        Returns:
            Response text to present to the user
        """
        # Update conversation state
        self.conversation_state["attempts"] += 1

        try:
            # Generate response using direct LLM call
            prompt = f"{self.system_prompt}\n\n{prompt_template}"
            response = self.llm_complete(prompt)

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

            prompt = f"""
            You're a medical professional conducting an interview.

            The current section you're asking about is: {self.current_section}

            The standard question for this section is: "{question}"

            The current status of this section in the form is:
            {json.dumps(self.form[self.current_section], indent=2)}

            Based on what's already filled in, formulate an appropriate question that focuses on getting the missing information.
            If the section is completely empty, you can use the standard question.

            CRITICAL FORMATTING RULES:
            1. If you need to ask multiple questions, you MUST format them as bullet points with line breaks.
            2. Each bullet point must start with • followed by a space.
            3. Each bullet point must be on its own line.
            4. Do NOT write questions separated by commas in a single paragraph.
            
            CORRECT FORMAT EXAMPLE (if asking multiple questions):
            To help me understand what's been going on, could you tell me more about:
            • What is the main problem you're experiencing?
            • How long has it been bothering you?
            • Did it come on suddenly or gradually?
            
            WRONG FORMAT (DO NOT USE):
            To help me understand what's been going on, could you tell me more about the main problem you're experiencing, how long it's been bothering you, and whether it came on suddenly or gradually?

            Reply ONLY with the final question to ask the patient. Do not include any explanations or additional text.
            """
            return prompt

        elif mode == "requery":
            # Create a prompt that specifically asks for missing information
            missing_fields_str = ", ".join(
                self.missing_fields[:3]
            )  # Limit to 3 fields at once

            prompt = f"""
            You're a medical professional conducting an interview.

            The patient hasn't provided complete information about: {missing_fields_str}

            These fields are part of the '{self.current_section}' section.

            Ask a friendly, conversational follow-up question to get this specific information.
            
            CRITICAL FORMATTING RULES:
            1. If you need to ask multiple questions (2 or more), you MUST format them as bullet points with line breaks.
            2. Each bullet point must start with • followed by a space.
            3. Each bullet point must be on its own line.
            4. Do NOT write questions separated by commas in a single paragraph.
            5. Only use a single paragraph if asking ONE simple question.
            
            CORRECT FORMAT EXAMPLE (multiple questions):
            I need a bit more information:
            • First question about X?
            • Second question about Y?
            • Third question about Z?
            
            WRONG FORMAT (DO NOT USE):
            I need a bit more information about X, Y, and Z?

            Reply ONLY with the final question to ask the patient. Do not include any explanations or additional text.
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

            Please update the form with any relevant information from the patient's response.
            The response might contain information relevant to multiple sections, not just the current section ({self.current_section}).

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
            # Check if interview is complete
            if self.invalid_index():
                return "Thank you for completing the medical interview. All required information has been collected."

            # Initial question
            if self.talk_mode == "START":
                # Create first question prompt
                prompt_template = self.make_template(mode="query")
                first_question = self.talk_to_user(prompt_template)
                self.talk_mode = "USER"
                return first_question

            # Process user's response
            elif user_response:
                # Format user's response into structured data
                prompt_template = self.make_template(
                    mode="format", source=user_response
                )
                # Call formatter to update the form
                self.formatter(user_response, prompt_template)

                # Validate if all fields in current section are filled
                is_section_complete = self.validator()

                if is_section_complete:
                    # Move to next section
                    self.all_ops()
                    self.idx += 1

                    if not self.invalid_index():
                        # Prepare next question
                        prompt_template = self.make_template(mode="query")
                        next_question = self.talk_to_user(prompt_template)
                        return f"Thank you for that information. {next_question}"
                    else:
                        # Interview complete
                        self.save_progress()
                        return "Thank you for completing all questions. Your medical information has been recorded."
                else:
                    # Ask for missing information
                    prompt_template = self.make_template(mode="requery")
                    follow_up_question = self.talk_to_user(prompt_template)
                    return follow_up_question

            # Default response if something goes wrong
            return (
                "I'm sorry, I didn't catch that. Could you please repeat your answer?"
            )

        except Exception as e:
            print(f"Error in main_processor: {e}")
            # Fall back to predefined questions if there's an error
            if self.idx < len(self.predefined_questions):
                return self.predefined_questions[self.idx][1]
            return (
                "I apologize, there was an error. Could you please repeat your answer?"
            )
