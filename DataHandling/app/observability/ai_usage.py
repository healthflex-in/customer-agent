"""Privacy-safe usage and cost telemetry for external AI provider calls.

This module deliberately records only bounded operational metadata. Prompts,
responses, transcripts, document contents, and patient identifiers must never
be passed to this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import logging
import math
import os
import time
from typing import Callable, Mapping, TypeVar

from app.observability.privacy import error_type


_LOG = logging.getLogger("ai_usage")
_T = TypeVar("_T")


@dataclass(frozen=True)
class AIUsage:
    """Provider-reported usage units for one request."""

    measured: bool = False
    input_text_tokens: int = 0
    input_audio_tokens: int = 0
    output_tokens: int = 0
    cached_text_tokens: int = 0
    cached_audio_tokens: int = 0
    audio_seconds: float = 0.0


@dataclass(frozen=True)
class PriceRate:
    """USD unit rates. Missing fields stay unpriced rather than being guessed."""

    input_text_per_million: float | None = None
    input_audio_per_million: float | None = None
    output_per_million: float | None = None
    cached_text_per_million: float | None = None
    cached_audio_per_million: float | None = None
    audio_per_minute: float | None = None
    audio_billing_increment_seconds: float | None = None
    request: float | None = None


# Google standard paid-tier prices verified against the official pricing page
# on this date. Deployment can replace/extend these entries via AI_PRICING_JSON.
DEFAULT_PRICING_VERSION = "google-standard-2026-09-02"
DEFAULT_PRICING: dict[str, PriceRate] = {
    "google_genai/gemini-2.5-flash": PriceRate(
        input_text_per_million=0.30,
        input_audio_per_million=1.00,
        output_per_million=2.50,
        cached_text_per_million=0.03,
        cached_audio_per_million=0.10,
    ),
    "google_genai/gemini-2.5-flash-lite": PriceRate(
        input_text_per_million=0.10,
        input_audio_per_million=0.30,
        output_per_million=0.40,
        cached_text_per_million=0.01,
        cached_audio_per_million=0.03,
    ),
}


def _nonnegative_number(value: object, *, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be a non-negative number or null")
    result = float(value)
    if result < 0:
        raise ValueError(f"{field} must be a non-negative number or null")
    return result


def load_pricing_catalog(
    environment: Mapping[str, str] | None = None,
) -> tuple[str, dict[str, PriceRate]]:
    """Load the dated defaults plus validated deployment-specific overrides.

    ``AI_PRICING_JSON`` is an object keyed by ``provider/model``. Supported
    fields mirror :class:`PriceRate`. An override replaces the complete rate
    for that key, preventing an old default from being silently mixed with a
    partial new price.
    """

    env = os.environ if environment is None else environment
    raw = env.get("AI_PRICING_JSON", "").strip()
    explicit_version = env.get("AI_PRICING_VERSION")
    if raw and not (explicit_version or "").strip():
        raise ValueError("AI_PRICING_VERSION is required with AI_PRICING_JSON")
    version = (explicit_version or DEFAULT_PRICING_VERSION).strip()
    if not version:
        raise ValueError("AI_PRICING_VERSION cannot be empty")

    catalog = dict(DEFAULT_PRICING)
    if not raw:
        return version, catalog

    try:
        overrides = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError("AI_PRICING_JSON must be valid JSON") from exc
    if not isinstance(overrides, dict):
        raise ValueError("AI_PRICING_JSON must be a JSON object")

    allowed_fields = set(PriceRate.__dataclass_fields__)
    for key, values in overrides.items():
        if not isinstance(key, str) or "/" not in key:
            raise ValueError("AI_PRICING_JSON keys must use provider/model")
        if not isinstance(values, dict):
            raise ValueError(f"AI_PRICING_JSON[{key!r}] must be an object")
        unknown = set(values) - allowed_fields
        if unknown:
            raise ValueError(
                f"AI_PRICING_JSON[{key!r}] has unsupported fields: {sorted(unknown)}"
            )
        configured_rate = PriceRate(
            **{
                field: _nonnegative_number(values.get(field), field=f"{key}.{field}")
                for field in allowed_fields
            }
        )
        if (
            configured_rate.audio_billing_increment_seconds is not None
            and configured_rate.audio_billing_increment_seconds <= 0
        ):
            raise ValueError(f"{key}.audio_billing_increment_seconds must be positive")
        catalog[key] = configured_rate
    return version, catalog


PRICING_VERSION, PRICING_CATALOG = load_pricing_catalog()


def estimate_cost_usd(provider: str, model: str, usage: AIUsage) -> float | None:
    """Return an estimate only when every used unit has a configured rate."""

    rate = PRICING_CATALOG.get(f"{provider}/{model}")
    if rate is None:
        return None

    components = (
        (usage.input_text_tokens, rate.input_text_per_million, 1_000_000),
        (usage.input_audio_tokens, rate.input_audio_per_million, 1_000_000),
        (usage.output_tokens, rate.output_per_million, 1_000_000),
        (usage.cached_text_tokens, rate.cached_text_per_million, 1_000_000),
        (usage.cached_audio_tokens, rate.cached_audio_per_million, 1_000_000),
    )
    total = rate.request or 0.0
    for amount, unit_rate, divisor in components:
        if amount:
            if unit_rate is None:
                return None
            total += float(amount) * unit_rate / divisor
    if usage.audio_seconds:
        if rate.audio_per_minute is None or rate.audio_billing_increment_seconds is None:
            return None
        increment = rate.audio_billing_increment_seconds
        if increment <= 0:
            return None
        billed_seconds = math.ceil(usage.audio_seconds / increment) * increment
        total += billed_seconds * rate.audio_per_minute / 60
    return total


def _value(source: object, *names: str, default: object = None) -> object:
    for name in names:
        if isinstance(source, Mapping) and name in source:
            return source[name]
        value = getattr(source, name, None)
        if value is not None:
            return value
    return default


def _count_modality(details: object, modality: str) -> int:
    if not details:
        return 0
    total = 0
    for detail in details:
        raw_modality = _value(detail, "modality", default="")
        normalized = str(getattr(raw_modality, "value", raw_modality)).upper()
        if normalized.endswith(modality.upper()):
            total += int(_value(detail, "token_count", "tokenCount", default=0) or 0)
    return total


def gemini_usage(response: object) -> AIUsage:
    """Extract Gemini usage from direct-SDK or LlamaIndex response objects."""

    raw = getattr(response, "raw", None) or response
    metadata = _value(raw, "usage_metadata", "usageMetadata")
    if not metadata:
        return AIUsage()

    prompt_total = int(
        _value(metadata, "prompt_token_count", "promptTokenCount", default=0) or 0
    )
    prompt_details = _value(
        metadata, "prompt_tokens_details", "promptTokensDetails", default=[]
    )
    cached_details = _value(
        metadata, "cache_tokens_details", "cacheTokensDetails", default=[]
    )
    audio_total = _count_modality(prompt_details, "AUDIO")
    cached_audio = _count_modality(cached_details, "AUDIO")
    cached_total = int(
        _value(
            metadata,
            "cached_content_token_count",
            "cachedContentTokenCount",
            default=0,
        )
        or 0
    )
    cached_text = max(0, cached_total - cached_audio)
    text_total = max(0, prompt_total - audio_total)
    return AIUsage(
        measured=True,
        input_text_tokens=max(0, text_total - cached_text),
        input_audio_tokens=max(0, audio_total - cached_audio),
        output_tokens=int(
            _value(
                metadata,
                "candidates_token_count",
                "candidatesTokenCount",
                default=0,
            )
            or 0
        )
        + int(
            _value(
                metadata,
                "thoughts_token_count",
                "thoughtsTokenCount",
                default=0,
            )
            or 0
        ),
        cached_text_tokens=cached_text,
        cached_audio_tokens=cached_audio,
    )


def bedrock_usage(response: object) -> AIUsage:
    """Extract the token fields returned by Bedrock Converse."""

    usage = _value(response, "usage", default={}) or {}
    if not usage:
        return AIUsage()
    return AIUsage(
        measured=True,
        input_text_tokens=int(_value(usage, "inputTokens", "input_tokens", default=0) or 0),
        output_tokens=int(_value(usage, "outputTokens", "output_tokens", default=0) or 0),
    )


try:
    from prometheus_client import Counter, Histogram

    AI_CALLS = Counter(
        "ai_provider_calls_total",
        "External AI provider calls",
        ["provider", "model", "operation", "status"],
    )
    AI_LATENCY = Histogram(
        "ai_provider_latency_seconds",
        "External AI provider call latency",
        ["provider", "model", "operation", "status"],
    )
    AI_INPUT_TEXT_TOKENS = Counter(
        "ai_provider_input_text_tokens_total",
        "Provider-reported text input tokens",
        ["provider", "model", "operation"],
    )
    AI_INPUT_AUDIO_TOKENS = Counter(
        "ai_provider_input_audio_tokens_total",
        "Provider-reported audio input tokens",
        ["provider", "model", "operation"],
    )
    AI_OUTPUT_TOKENS = Counter(
        "ai_provider_output_tokens_total",
        "Provider-reported output tokens",
        ["provider", "model", "operation"],
    )
    AI_CACHED_TEXT_TOKENS = Counter(
        "ai_provider_cached_text_tokens_total",
        "Provider-reported cached text input tokens",
        ["provider", "model", "operation"],
    )
    AI_CACHED_AUDIO_TOKENS = Counter(
        "ai_provider_cached_audio_tokens_total",
        "Provider-reported cached audio input tokens",
        ["provider", "model", "operation"],
    )
    AI_AUDIO_SECONDS = Counter(
        "ai_provider_audio_seconds_total",
        "Audio seconds submitted to a provider",
        ["provider", "model", "operation"],
    )
    AI_ESTIMATED_COST = Counter(
        "ai_provider_estimated_cost_usd_total",
        "Estimated provider cost in USD using the labelled pricing version",
        ["provider", "model", "operation", "pricing_version"],
    )
    AI_UNPRICED_CALLS = Counter(
        "ai_provider_unpriced_calls_total",
        "Successful provider calls lacking a complete configured price",
        ["provider", "model", "operation", "pricing_version"],
    )
except ImportError:
    class _Noop:
        def labels(self, *args: object, **kwargs: object) -> "_Noop":
            return self

        def inc(self, *args: object, **kwargs: object) -> None:
            return None

        def observe(self, *args: object, **kwargs: object) -> None:
            return None

    AI_CALLS = AI_LATENCY = AI_INPUT_TEXT_TOKENS = _Noop()
    AI_INPUT_AUDIO_TOKENS = AI_OUTPUT_TOKENS = AI_AUDIO_SECONDS = _Noop()
    AI_CACHED_TEXT_TOKENS = AI_CACHED_AUDIO_TOKENS = _Noop()
    AI_ESTIMATED_COST = AI_UNPRICED_CALLS = _Noop()


def record_ai_call(
    *,
    provider: str,
    model: str,
    operation: str,
    status: str,
    elapsed_seconds: float,
    usage: AIUsage = AIUsage(),
    failure_type: str | None = None,
) -> float | None:
    """Publish one metadata-only event and return its estimated cost."""

    AI_CALLS.labels(provider, model, operation, status).inc()
    AI_LATENCY.labels(provider, model, operation, status).observe(elapsed_seconds)
    if usage.input_text_tokens:
        AI_INPUT_TEXT_TOKENS.labels(provider, model, operation).inc(usage.input_text_tokens)
    if usage.input_audio_tokens:
        AI_INPUT_AUDIO_TOKENS.labels(provider, model, operation).inc(usage.input_audio_tokens)
    if usage.output_tokens:
        AI_OUTPUT_TOKENS.labels(provider, model, operation).inc(usage.output_tokens)
    if usage.cached_text_tokens:
        AI_CACHED_TEXT_TOKENS.labels(provider, model, operation).inc(
            usage.cached_text_tokens
        )
    if usage.cached_audio_tokens:
        AI_CACHED_AUDIO_TOKENS.labels(provider, model, operation).inc(
            usage.cached_audio_tokens
        )
    if usage.audio_seconds:
        AI_AUDIO_SECONDS.labels(provider, model, operation).inc(usage.audio_seconds)

    cost = (
        estimate_cost_usd(provider, model, usage)
        if status == "success" and usage.measured
        else None
    )
    if status == "success":
        if cost is None:
            AI_UNPRICED_CALLS.labels(provider, model, operation, PRICING_VERSION).inc()
        else:
            AI_ESTIMATED_COST.labels(
                provider, model, operation, PRICING_VERSION
            ).inc(cost)

    _LOG.info(
        "ai_provider_call provider=%s model=%s operation=%s status=%s "
        "latency_ms=%d input_text_tokens=%d input_audio_tokens=%d "
        "output_tokens=%d cached_text_tokens=%d cached_audio_tokens=%d "
        "audio_seconds=%.3f estimated_cost_usd=%s "
        "pricing_version=%s failure_type=%s",
        provider,
        model,
        operation,
        status,
        round(elapsed_seconds * 1000),
        usage.input_text_tokens,
        usage.input_audio_tokens,
        usage.output_tokens,
        usage.cached_text_tokens,
        usage.cached_audio_tokens,
        usage.audio_seconds,
        "unpriced" if cost is None else f"{cost:.8f}",
        PRICING_VERSION,
        failure_type or "none",
    )
    return cost


def tracked_ai_call(
    *,
    provider: str,
    model: str,
    operation: str,
    call: Callable[[], _T],
    usage_extractor: Callable[[_T], AIUsage] | None = None,
    submitted_usage: AIUsage = AIUsage(),
) -> _T:
    """Execute a provider call and record success/failure exactly once."""

    started = time.perf_counter()
    try:
        response = call()
    except Exception as exc:
        record_ai_call(
            provider=provider,
            model=model,
            operation=operation,
            status="error",
            elapsed_seconds=time.perf_counter() - started,
            usage=submitted_usage,
            failure_type=error_type(exc),
        )
        raise

    usage_failure = None
    try:
        usage = usage_extractor(response) if usage_extractor else submitted_usage
    except Exception as exc:
        # Telemetry parsing must not turn a successful provider response into a
        # failed clinical request. The unpriced counter makes the gap visible.
        usage = submitted_usage
        usage_failure = f"Usage{error_type(exc)}"
    record_ai_call(
        provider=provider,
        model=model,
        operation=operation,
        status="success",
        elapsed_seconds=time.perf_counter() - started,
        usage=usage,
        failure_type=usage_failure,
    )
    return response
