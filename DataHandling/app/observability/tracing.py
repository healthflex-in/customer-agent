"""Langfuse LLM content tracing, enabled only with credentials and explicit approval."""

import os

from app.observability.privacy import env_flag, error_type

_credentials_present = bool(
    os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")
)
_content_approved = env_flag("LANGFUSE_CAPTURE_CONTENT", default=False)
langfuse_enabled = _credentials_present and _content_approved
langfuse_client = None
if langfuse_enabled:
    try:
        from langfuse import get_client
        from openinference.instrumentation.llama_index import LlamaIndexInstrumentor
        langfuse_client = get_client()
        LlamaIndexInstrumentor().instrument()
        print("[langfuse] Content tracing explicitly enabled")
    except Exception as _lf_err:
        print(f"[langfuse] Initialization failed: {error_type(_lf_err)}")
        langfuse_enabled = False
else:
    if _credentials_present:
        print("[langfuse] Content tracing disabled by privacy default")
    else:
        print("[langfuse] Skipping — credentials not configured")
