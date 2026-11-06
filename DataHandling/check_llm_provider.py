"""Manual check: does LLM_PROVIDER actually reach the configured model?

Makes REAL billed API calls (a couple of short completions) — this is a
manual diagnostic, not a pytest file, so it's named so unittest discovery
(tests/, `test*.py`) never picks it up and runs it automatically.

Usage (from DataHandling/):
    python3 check_llm_provider.py
    python3 check_llm_provider.py --env .env.dev-isolated   # load a specific file

Requires the configured provider's package installed (e.g. `pip install
anthropic`) and a real API key set via LLM_API_KEY (or the provider's own
env var) in whichever env file you point --env at.
"""
import argparse
import sys

parser = argparse.ArgumentParser()
parser.add_argument("--env", default=".env", help="dotenv file to load before anything else")
args = parser.parse_args()

from dotenv import load_dotenv
if not load_dotenv(args.env):
    print(f"WARNING: {args.env!r} not found or empty — relying on already-exported env vars.")

# Imported only after load_dotenv() so LLM_CONFIG/MODEL_REGISTRY resolve
# against the file you pointed --env at, not whatever bare `.env` exists.
from app.ai.models import LLM_CONFIG, MODEL_REGISTRY

print("=" * 70)
print(f"LLM_PROVIDER   = {LLM_CONFIG.provider!r}")
print(f"general model  = {LLM_CONFIG.general!r}")
print(f"reasoning model= {LLM_CONFIG.reasoning!r}")
print(f"rotation chain = {LLM_CONFIG.rotation!r}")
print(f"(STT stays on Gemini regardless — audio model = {MODEL_REGISTRY.audio!r})")
print("=" * 70)

if LLM_CONFIG.provider == "anthropic" and not __import__("os").environ.get("LLM_API_KEY"):
    print("WARNING: LLM_PROVIDER=anthropic but LLM_API_KEY is empty — this will fail.")

# ── 1. The real live interview path: HealthAgent.llm_complete ───────────────
# This is what server.py actually wires into the LangGraph interview graph
# for every question/extraction/summary turn.
print("\n[1] HealthAgent.llm_complete (the live interview path)")
try:
    from src.llm.functionalities import HealthAgent

    agent = HealthAgent()
    if agent.llm is None:
        print("    FAILED: agent.llm is None — check the init log above for the real error.")
        sys.exit(1)
    reply = agent.llm_complete(
        "Reply with exactly one short friendly sentence introducing yourself as "
        "Sage, a physiotherapy intake assistant."
    )
    print(f"    OK -> {reply!r}")
except Exception as e:
    print(f"    FAILED: {type(e).__name__}: {e}")
    sys.exit(1)

# ── 2. The reasoning completer used for form extraction ─────────────────────
print("\n[2] build_reasoning_completer (form-extraction path)")
try:
    from app.ai.completer import build_reasoning_completer

    reasoning = build_reasoning_completer()
    if reasoning is None:
        print("    FAILED: completer is None — check LLM_API_KEY / provider credentials.")
        sys.exit(1)
    reply = reasoning(
        'Extract the patient age as JSON only: {"age": <number or null>}. '
        'Patient said: "I am 34 years old and I have had knee pain for 2 weeks."'
    )
    print(f"    OK -> {reply!r}")
except Exception as e:
    print(f"    FAILED: {type(e).__name__}: {e}")
    sys.exit(1)

print("\nAll checks passed.")
