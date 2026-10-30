"""Per-model control for bounded Gemini thinking.

Gemini 2.5 accepts a numeric thinking budget. Gemini 3.5 Flash-Lite rejects
thinking_budget=0 and accepts a thinking level instead. Call sites ask for
bounded thinking and this map chooses the field that model accepts.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BoundedThinking:
    """One thinking field to send with a generateContent request."""

    field: str
    value: int | str

    def as_kwargs(self) -> dict[str, int | str]:
        return {self.field: self.value}

    def describe(self) -> str:
        return f"{self.field}={self.value}"


# Adding a model here is part of approving that model. 2.5 stays on the
# numeric budget production already uses. 3.5 Flash-Lite uses the level that
# matches "do not spend a reasoning budget" on that model.
_BOUNDED_THINKING = {
    "gemini-2.5-flash-lite": BoundedThinking("thinking_budget", 0),
    "gemini-2.5-flash": BoundedThinking("thinking_budget", 0),
    "gemini-3.5-flash-lite": BoundedThinking("thinking_level", "minimal"),
}


def bounded_thinking(model_name: str) -> BoundedThinking:
    """Return the thinking control for an approved model."""

    try:
        return _BOUNDED_THINKING[model_name]
    except KeyError as error:
        approved = ", ".join(sorted(_BOUNDED_THINKING))
        raise ValueError(
            f"No bounded thinking control is registered for {model_name}; "
            f"registered models: {approved}"
        ) from error
