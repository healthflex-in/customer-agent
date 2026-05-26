"""
Side-by-side A/B test of Gemini 2.0 Flash vs Flash-Lite.

Doesn't touch Mongo, Whisper, or the WS layer — just instantiates the LLM
twice (once per model) and runs the same prompts through both. Reports:

  - token counts (input / output)
  - latency
  - $ estimate per call
  - the actual response strings, so a human can eyeball quality

Run inside the container:
  docker exec healthflex-customer-agent python3 /app/tests/llm_ab.py
"""

from __future__ import annotations

import os
import sys
import time
from textwrap import shorten

# Make `src.*` importable when run from /app inside the container.
sys.path.insert(0, "/app")

from dotenv import load_dotenv

load_dotenv()

from llama_index.llms.google_genai import GoogleGenAI  # noqa: E402


# Two test prompts that mirror what HealthAgent actually does:
#   1. A classification task (the kind classify_summary_response would do).
#   2. A short conversational turn (the kind talk_to_user would do).
PROMPTS = [
    {
        "name": "classify",
        "prompt": (
            "You are a healthcare intake classifier. Given the user's reply, "
            "respond with a JSON object containing keys 'intent' (one of: "
            "'confirm', 'reject', 'unclear', 'correction') and 'confidence' "
            "(0.0-1.0). No other text.\n\n"
            "User reply: 'Yes that's correct, I do have a headache since "
            "yesterday morning.'"
        ),
    },
    {
        "name": "conversation",
        "prompt": (
            "You are a friendly medical intake assistant. The user just said "
            "they have had a headache for two days. Ask one short follow-up "
            "question (under 20 words) to learn more about the symptom. "
            "Reply with just the question, no preamble."
        ),
    },
]


GEMINI_PRICING_USD_PER_M = {
    "gemini-2.0-flash":      {"in": 0.10,  "out": 0.40},
    "gemini-2.5-flash":      {"in": 0.30,  "out": 2.50},  # 2.5 line is pricier on output
    "gemini-2.5-flash-lite": {"in": 0.10,  "out": 0.40},
    "gemini-2.0-flash-lite": {"in": 0.075, "out": 0.30},  # deprecated, kept for fallback
}


def run(model_name: str, prompt: str) -> dict:
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    llm = GoogleGenAI(model=model_name, api_key=api_key)

    t0 = time.perf_counter()
    response = llm.complete(prompt)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    in_tok = out_tok = None
    try:
        raw = response.raw
        usage = raw.get("usage_metadata") if isinstance(raw, dict) else None
        if isinstance(usage, dict):
            in_tok = usage.get("prompt_token_count")
            out_tok = usage.get("candidates_token_count")
    except Exception:
        pass

    pricing = GEMINI_PRICING_USD_PER_M.get(model_name, {"in": 0, "out": 0})
    cost = (
        ((in_tok or 0) * pricing["in"] + (out_tok or 0) * pricing["out"])
        / 1_000_000
    )

    return {
        "model": model_name,
        "text": str(response).strip(),
        "in_tok": in_tok,
        "out_tok": out_tok,
        "latency_ms": elapsed_ms,
        "cost_usd": cost,
    }


def main() -> None:
    models = [
        "gemini-2.0-flash",       # what prod currently uses
        "gemini-2.5-flash-lite",  # current "lite" tier
        "gemini-2.5-flash",       # newer baseline (pricier output)
    ]

    totals = {m: {"in": 0, "out": 0, "ms": 0, "cost": 0.0} for m in models}

    for case in PROMPTS:
        print(f"\n{'='*72}\nPROMPT: {case['name']}")
        print(f"  {shorten(case['prompt'], width=120)}")
        for model in models:
            try:
                r = run(model, case["prompt"])
            except Exception as e:
                print(f"  ! {model}: error {e}")
                continue
            print(
                f"\n  {model}"
                f"\n    in={r['in_tok']} out={r['out_tok']}"
                f" latency={r['latency_ms']:.0f}ms cost=${r['cost_usd']:.6f}"
                f"\n    text: {shorten(r['text'], width=200)}"
            )
            totals[model]["in"] += r["in_tok"] or 0
            totals[model]["out"] += r["out_tok"] or 0
            totals[model]["ms"] += r["latency_ms"]
            totals[model]["cost"] += r["cost_usd"]

    print(f"\n{'='*72}\nTOTALS across {len(PROMPTS)} prompts")
    for m, t in totals.items():
        print(
            f"  {m}\n"
            f"    tokens: {t['in']} in + {t['out']} out\n"
            f"    latency: {t['ms']:.0f}ms total\n"
            f"    cost: ${t['cost']:.6f}"
        )

    # Savings summary vs the current prod model.
    baseline_key = "gemini-2.0-flash"
    baseline = totals.get(baseline_key)
    if baseline and baseline["cost"] > 0:
        print()
        for m, t in totals.items():
            if m == baseline_key or t["cost"] == 0:
                continue
            delta = (baseline["cost"] - t["cost"]) / baseline["cost"] * 100
            sign = "saves" if delta > 0 else "costs MORE by"
            print(
                f"  vs {baseline_key}: {m} {sign} {abs(delta):.1f}% "
                f"(${baseline['cost']:.6f} → ${t['cost']:.6f})"
            )


if __name__ == "__main__":
    main()
