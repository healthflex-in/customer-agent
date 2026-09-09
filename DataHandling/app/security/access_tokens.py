"""Strict verification for short-lived, clinician-issued access-link tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from typing import Any, Iterable


class AuthConfigurationError(RuntimeError):
    """Authentication is not safely configured."""


class TokenValidationError(ValueError):
    """A bearer token is absent, malformed, invalid, or expired."""


class AuthorizationError(PermissionError):
    """The authenticated actor cannot perform the requested operation."""


@dataclass(frozen=True)
class AuthContext:
    subject: str
    role: str
    scopes: frozenset[str]
    patient_ids: frozenset[str]


def _b64url_decode(value: str) -> bytes:
    if not value or any(char.isspace() for char in value):
        raise TokenValidationError("Malformed token encoding")
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise TokenValidationError("Malformed token encoding") from exc


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _strict_json(value: bytes) -> dict[str, Any]:
    def reject_duplicates(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise TokenValidationError("Duplicate token claim")
            result[key] = item
        return result

    try:
        parsed = json.loads(value, object_pairs_hook=reject_duplicates)
    except TokenValidationError:
        raise
    except Exception as exc:
        raise TokenValidationError("Malformed token JSON") from exc
    if not isinstance(parsed, dict):
        raise TokenValidationError("Token JSON must be an object")
    return parsed


def _validate_secret(secret: str) -> bytes:
    encoded = secret.encode("utf-8") if secret else b""
    if len(encoded) < 32:
        raise AuthConfigurationError(
            "AUTH_SIGNING_SECRET must contain at least 32 UTF-8 bytes"
        )
    return encoded


def _string_set(value: Any, claim: str) -> frozenset[str]:
    if isinstance(value, str):
        items: Iterable[Any] = value.split()
    elif isinstance(value, list):
        if any(not isinstance(item, str) for item in value):
            raise TokenValidationError(f"{claim} values must be strings")
        items = value
    else:
        raise TokenValidationError(f"{claim} must be a string or list")
    normalized = frozenset(
        str(item).strip() for item in items if isinstance(item, str) and item.strip()
    )
    if len(normalized) > 500:
        raise TokenValidationError(f"{claim} contains too many values")
    return normalized


def decode_access_token(
    token: str,
    *,
    secret: str,
    issuer: str,
    audience: str,
    now: int | None = None,
    max_lifetime_seconds: int = 14_400,
    clock_skew_seconds: int = 30,
) -> AuthContext:
    """Verify a strict HS256 JWT and return bounded authorization claims."""

    if not issuer or not audience:
        raise AuthConfigurationError("AUTH_ISSUER and AUTH_AUDIENCE are required")
    if max_lifetime_seconds <= 0 or clock_skew_seconds < 0:
        raise AuthConfigurationError("Invalid authentication time policy")
    if not token or len(token) > 8192:
        raise TokenValidationError("Missing or oversized access token")

    parts = token.split(".")
    if len(parts) != 3:
        raise TokenValidationError("Malformed access token")
    encoded_header, encoded_payload, encoded_signature = parts
    header = _strict_json(_b64url_decode(encoded_header))
    payload = _strict_json(_b64url_decode(encoded_payload))
    if header.get("alg") != "HS256" or header.get("typ") != "JWT":
        raise TokenValidationError("Unsupported token header")

    key = _validate_secret(secret)
    signed = f"{encoded_header}.{encoded_payload}".encode("ascii")
    expected = hmac.new(key, signed, hashlib.sha256).digest()
    supplied = _b64url_decode(encoded_signature)
    if not hmac.compare_digest(expected, supplied):
        raise TokenValidationError("Invalid access-token signature")

    required_strings = ("iss", "sub", "role")
    if any(not isinstance(payload.get(name), str) or not payload[name].strip() for name in required_strings):
        raise TokenValidationError("Missing required token claims")
    if payload["iss"] != issuer:
        raise TokenValidationError("Invalid token issuer")
    token_audience = payload.get("aud")
    if isinstance(token_audience, str):
        audiences = {token_audience}
    elif isinstance(token_audience, list) and all(
        isinstance(item, str) for item in token_audience
    ):
        audiences = set(token_audience)
    else:
        raise TokenValidationError("Invalid token audience")
    if audience not in audiences:
        raise TokenValidationError("Invalid token audience")

    issued_at = payload.get("iat")
    expires_at = payload.get("exp")
    if isinstance(issued_at, bool) or not isinstance(issued_at, (int, float)):
        raise TokenValidationError("Invalid token issued-at time")
    if isinstance(expires_at, bool) or not isinstance(expires_at, (int, float)):
        raise TokenValidationError("Invalid token expiry")
    current = int(time.time()) if now is None else int(now)
    if issued_at > current + clock_skew_seconds:
        raise TokenValidationError("Token is not active yet")
    if expires_at <= current - clock_skew_seconds:
        raise TokenValidationError("Access token has expired")
    if expires_at <= issued_at or expires_at - issued_at > max_lifetime_seconds:
        raise TokenValidationError("Token lifetime exceeds policy")

    role = payload["role"].strip().lower()
    if role not in {"patient", "clinician", "admin"}:
        raise TokenValidationError("Unsupported actor role")
    scopes = _string_set(payload.get("scope", ""), "scope")
    patient_ids = _string_set(payload.get("patients", []), "patients")
    return AuthContext(
        subject=payload["sub"].strip(),
        role=role,
        scopes=scopes,
        patient_ids=patient_ids,
    )


def create_access_token(
    *,
    subject: str,
    role: str,
    scopes: Iterable[str],
    secret: str,
    issuer: str,
    audience: str,
    patient_ids: Iterable[str] = (),
    lifetime_seconds: int = 3600,
    now: int | None = None,
) -> str:
    """Create a token for trusted tooling; this is never exposed as an API."""

    key = _validate_secret(secret)
    normalized_subject = str(subject).strip()
    normalized_role = str(role).strip().lower()
    normalized_scopes = sorted({str(scope).strip() for scope in scopes if str(scope).strip()})
    normalized_patients = sorted(
        {str(patient).strip() for patient in patient_ids if str(patient).strip()}
    )
    if not normalized_subject or normalized_role not in {"patient", "clinician", "admin"}:
        raise TokenValidationError("Invalid token subject or role")
    if not issuer or not audience or not normalized_scopes:
        raise TokenValidationError("Issuer, audience, and scopes are required")
    if lifetime_seconds <= 0:
        raise TokenValidationError("Token lifetime must be positive")
    issued_at = int(time.time()) if now is None else int(now)
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "iss": issuer,
        "aud": audience,
        "sub": normalized_subject,
        "role": normalized_role,
        "scope": " ".join(normalized_scopes),
        "iat": issued_at,
        "exp": issued_at + lifetime_seconds,
    }
    if normalized_patients:
        payload["patients"] = normalized_patients
    encoded_header = _b64url_encode(
        json.dumps(header, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    encoded_payload = _b64url_encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    signed = f"{encoded_header}.{encoded_payload}".encode("ascii")
    signature = hmac.new(key, signed, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_payload}.{_b64url_encode(signature)}"


def authorize_patient(context: AuthContext, patient_id: Any, scope: str) -> None:
    """Require a scope and an exact patient binding for record access."""

    target = str(patient_id).strip()
    if not target or scope not in context.scopes:
        raise AuthorizationError("Access denied")
    if context.role == "patient" and context.subject == target:
        return
    if context.role == "admin":
        return
    if context.role == "clinician" and target in context.patient_ids:
        return
    raise AuthorizationError("Access denied")


def authorize_directory(context: AuthContext) -> None:
    if context.role not in {"clinician", "admin"} or "users:read" not in context.scopes:
        raise AuthorizationError("Access denied")


def extract_bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise TokenValidationError("Bearer token is required")
    scheme, separator, value = authorization.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not value.strip():
        raise TokenValidationError("Bearer token is required")
    return value.strip()
