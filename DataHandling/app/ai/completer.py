"""Text completion entry point for the interview.

Graph nodes and handlers receive a function from prompt text to response
text. The configured provider is selected here. Gemini is the provider in
use. Another provider implements the same function and is chosen in this
module, from environment configuration, without changing the interview.
"""

from __future__ import annotations

from collections.abc import Callable

from app.ai.gemini import build_gemini_reasoning_completer


def build_reasoning_completer() -> Callable[[str], str] | None:
    """Return the form-extraction completer, or None when it cannot be built."""

    return build_gemini_reasoning_completer()
