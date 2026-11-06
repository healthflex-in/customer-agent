"""Refuse to silently pay a provider's long-prompt pricing cliff.

Claude Haiku 5.5's standard rate applies only up to 100,000 PROMPT tokens;
every rate (input, output, cache) is 5x higher for the entire request once a
prompt crosses that line.
Source: https://platform.claude.com/docs/en/about-claude/pricing (checked
2026-11-06). No other model in this registry has an equivalent cliff today.

There's no local tokenizer available for Claude, and calling Anthropic's
`count_tokens` endpoint before every completion would double the network
round trips for every single interview turn just to guard against a case
that today's interview prompts are nowhere close to reaching. Instead this
uses a fast, deliberately pessimistic character-based estimate (assumes
MORE tokens than reality) with a wide safety margin, so the imprecision of
the estimate never matters near the real boundary.

On a trip, this raises rather than truncates: silently cutting a patient's
form state or conversation history to save money is a worse failure mode
than a clean, loud error for what should be an exceedingly rare case.
"""

from __future__ import annotations

# model -> token count above which the provider's rate multiplies.
# Add an entry here the day another model gets an equivalent cliff.
_PRICE_CLIFF_TOKENS: dict[str, int] = {
    "claude-haiku-5-5": 100_000,
}

# Deliberately low — real Claude prompts run closer to ~4 chars/token, so
# dividing by 3 overestimates the token count, tripping the guard earlier
# than the real cliff rather than later.
_CONSERVATIVE_CHARS_PER_TOKEN = 3.0

# Trip at 80% of the real cliff, not 100% — a buffer against the estimate's
# own imprecision, not just the cliff itself.
_SAFETY_MARGIN = 0.8


class PromptTooLargeForModelError(RuntimeError):
    """Raised instead of sending a request that would hit a pricing cliff."""


def guard_prompt_length(model: str, prompt: str) -> None:
    """Raise if `prompt` is estimated to cross `model`'s pricing cliff.

    No-op for any model without a configured cliff (which is most models).
    """

    cliff = _PRICE_CLIFF_TOKENS.get(model)
    if cliff is None:
        return

    estimated_tokens = int(len(prompt) / _CONSERVATIVE_CHARS_PER_TOKEN)
    safe_limit = int(cliff * _SAFETY_MARGIN)
    if estimated_tokens > safe_limit:
        raise PromptTooLargeForModelError(
            f"Prompt is ~{estimated_tokens} estimated tokens, over the "
            f"{safe_limit}-token safety margin for {model}'s {cliff}-token "
            "pricing cliff (5x rate above it). Refusing to send rather than "
            "silently pay the higher rate. If this is a legitimate prompt, "
            "route it to a model without a cliff instead of raising the margin."
        )
