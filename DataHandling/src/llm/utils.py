# init_llm/init_reasoning_llm build whatever LLM_PROVIDER resolves to via
# app.ai.factory.create_llm — Anthropic, Bedrock, or (by default) Gemini
# through the unified llama_index.llms.google_genai.GoogleGenAI wrapper.
from llama_index.core import Settings
from src.enums import ENUMS
import json
import os
import time as _time
from pathlib import Path
from dotenv import load_dotenv
from app.observability.privacy import error_type
from app.observability.ai_usage import tracked_ai_call
from app.ai.factory import create_llm, telemetry_provider_label, usage_extractor_for
from app.ai.models import LLM_CONFIG

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


def init_llm(api_key=None):
    """Initialize the main (general-role) LLM for conversation flow.

    Provider + model name come from LLM_CONFIG (LLM_PROVIDER env var).
    `api_key` is only consulted on the google_genai path, for backward
    compatibility — Anthropic/Bedrock resolve their own credentials inside
    create_llm().
    """
    model_name = LLM_CONFIG.general
    llm = create_llm(model_name, api_key=api_key)
    if LLM_CONFIG.provider == "google_genai":
        # Settings.llm is a llama-index global consumed only by the optional,
        # best-effort Chroma/RAG path below — not by the live LangGraph
        # interview — so it's only meaningful for a real llama-index LLM.
        Settings.llm = llm
    return llm


def init_reasoning_llm(api_key=None):
    """
    Initialize the reasoning-grade LLM used exclusively for form extraction.
    The model is selected through LLM_CONFIG (LLM_PROVIDER env var).
    """
    model_name = LLM_CONFIG.reasoning
    return create_llm(model_name, api_key=api_key)


def final_form_filling(llm, prompt, history, form):
    """
    Fill the final form using the LLM model.
    """
    try:
        prompt_text = prompt.format(
            json.dumps(history, indent=2), json.dumps(form, indent=2)
        )

        _t0 = _time.perf_counter()
        model_name = str(getattr(llm, "model", LLM_CONFIG.general))
        if model_name.startswith("models/"):
            model_name = model_name.removeprefix("models/")
        response = tracked_ai_call(
            provider=telemetry_provider_label(LLM_CONFIG.provider),
            model=model_name,
            operation="final_form_legacy",
            call=lambda: llm.complete(prompt_text),
            usage_extractor=usage_extractor_for(LLM_CONFIG.provider),
        )
        print(f"[timing] final_form_filling_llm={(_time.perf_counter() - _t0) * 1000:.0f}ms")

        # Extract JSON part
        start = response.find("{")
        end = response.rfind("}")

        if start != -1 and end != -1:
            json_str = response[start : end + 1]
            try:
                formatted_final_filled_form = json.loads(json_str)
                return formatted_final_filled_form
            except json.JSONDecodeError as e:
                print(f"JSON parsing failed: {error_type(e)}")
                return form  # Return original form as fallback
        else:
            print("JSON response not found in LLM output")
            return form  # Return original form as fallback

    except Exception as e:
        print(f"Final form filling failed: {error_type(e)}")
        return form  # Return original form as fallback
