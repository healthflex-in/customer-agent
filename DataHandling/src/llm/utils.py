from llama_index.llms.gemini import Gemini
from llama_index.core import Settings
from src.enums import ENUMS
import json
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
# Try multiple locations: current directory, parent directory, and relative to this file
env_paths = [
    Path(".") / ".env",  # Current working directory
    Path("..") / ".env",  # Parent directory
    Path(__file__).parent.parent.parent / ".env",  # DataHandling directory (relative to this file)
]

for env_path in env_paths:
    if env_path.exists():
        load_dotenv(env_path)
        break

enums_obj = ENUMS()


def load_gemini_key(key_path=enums_obj.config_key_path):
    """
    Load the Gemini API key from environment variable (.env file) or config file.
    Priority: 1. GEMINI_API_KEY env var, 2. config_key.txt file
    """
    # First, try to load from environment variable (from .env file)
    api_key = os.getenv("GEMINI_API_KEY")
    if api_key:
        return api_key.strip()
    
    # Fall back to config file if env var is not found
    try:
        with open(key_path, "r") as file:
            return file.read().strip()
    except FileNotFoundError:
        raise FileNotFoundError(
            f"GEMINI_API_KEY not found in environment variables and {enums_obj.config_key_not_found_error}"
        )


def init_llm(api_key):
    """
    Initialize the LLM model using the Gemini API key.
    """
    llm = Gemini(api_key=api_key, model=enums_obj.gemini_model_name)
    Settings.llm = llm
    return llm


def final_form_filling(llm, prompt, history, form):
    """
    Fill the final form using the LLM model.
    """
    try:
        prompt_text = prompt.format(
            json.dumps(history, indent=2), json.dumps(form, indent=2)
        )

        # Use the proper method for completion
        response = llm.complete(prompt_text)

        # Extract JSON part
        start = response.find("{")
        end = response.rfind("}")

        if start != -1 and end != -1:
            json_str = response[start : end + 1]
            try:
                formatted_final_filled_form = json.loads(json_str)
                return formatted_final_filled_form
            except json.JSONDecodeError as e:
                print(f"JSON parsing error: {e}")
                print(f"Problematic JSON string: {json_str}")
                return form  # Return original form as fallback
        else:
            print("JSON response not found in LLM output")
            return form  # Return original form as fallback

    except Exception as e:
        print(f"Error in final form filling: {e}")
        return form  # Return original form as fallback
