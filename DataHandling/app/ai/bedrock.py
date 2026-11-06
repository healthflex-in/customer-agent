"""Bedrock text completion for the interview.

Uses the Bedrock Converse API via boto3 — the same dependency already used
in docscanner/client.py.  No new packages are required.

Selected when LLM_PROVIDER=bedrock (see app/ai/models.py — LLM_CONFIG).
Converse is already provider-agnostic on AWS's side, so the same code path
covers Claude, Llama, Nova, or GPT-OSS models on Bedrock; only the model ID
in LLM_GENERAL_MODEL/LLM_REASONING_MODEL changes. Speech transcription is
unaffected and continues to use Gemini regardless — see app/audio/stt.py.
"""

from __future__ import annotations

import os
from collections.abc import Callable

import boto3
from botocore.config import Config

from app.ai.models import LLM_CONFIG
from app.observability.ai_usage import bedrock_usage, tracked_ai_call
from app.observability.privacy import error_type


def _build_client(timeout_ms: int):
    # Use a dedicated Bedrock interview region so the docscanner (Nova, ap-south-1)
    # and interview (Claude, us-east-1) can live in different regions without
    # forcing a single AWS_REGION for both.
    region = (
        os.getenv("BEDROCK_INTERVIEW_REGION")
        or os.getenv("AWS_REGION", "us-east-1")
    )
    client = boto3.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=timeout_ms / 1000,
            read_timeout=timeout_ms / 1000,
        ),
    )
    return client, region


def build_bedrock_text_completer(
    model_id: str,
    *,
    operation: str,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    timeout_ms: int = 15_000,
) -> Callable[[str], str]:
    """Return a prompt → text callable backed by the Bedrock Converse API.

    The inference profile is read from BEDROCK_INTERVIEW_INFERENCE_PROFILE;
    when absent, model_id is used directly (works for on-demand models that
    don't require a cross-region profile).
    """
    client, region = _build_client(timeout_ms)
    inference_profile = os.getenv("BEDROCK_INTERVIEW_INFERENCE_PROFILE") or model_id

    def complete(prompt: str) -> str:
        response = tracked_ai_call(
            provider="aws_bedrock",
            model=model_id,
            operation=operation,
            call=lambda: client.converse(
                modelId=inference_profile,
                messages=[{"role": "user", "content": [{"text": prompt}]}],
                inferenceConfig={"maxTokens": max_tokens, "temperature": temperature},
            ),
            usage_extractor=bedrock_usage,
        )
        content = response.get("output", {}).get("message", {}).get("content", [])
        for block in content:
            if "text" in block:
                return block["text"].strip()
        return ""

    print(
        f"[bedrock] Completer ready — model={model_id} "
        f"region={region} profile={inference_profile} op={operation}"
    )
    return complete


def build_bedrock_reasoning_completer() -> Callable[[str], str] | None:
    """Build the form-extraction (JSON) completer using the configured Bedrock model."""
    model_id = LLM_CONFIG.reasoning
    if not model_id:
        return None
    try:
        return build_bedrock_text_completer(
            model_id,
            operation="form_extraction",
            temperature=0.1,
            max_tokens=4096,
        )
    except Exception as e:
        print(f"[bedrock] Reasoning completer init failed: {error_type(e)}")
        return None


def build_bedrock_interview_completer() -> Callable[[str], str] | None:
    """Build the interview Q&A completer using the configured Bedrock model."""
    model_id = LLM_CONFIG.general
    if not model_id:
        return None
    try:
        return build_bedrock_text_completer(
            model_id,
            operation="interview_text",
            temperature=0.3,
            max_tokens=4096,
        )
    except Exception as e:
        print(f"[bedrock] Interview completer init failed: {error_type(e)}")
        return None
