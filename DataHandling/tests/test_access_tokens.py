from __future__ import annotations

import base64
import hashlib
import hmac
import json
import unittest

from app.security.access_tokens import (
    AuthConfigurationError,
    AuthorizationError,
    TokenValidationError,
    authorize_directory,
    authorize_patient,
    create_access_token,
    decode_access_token,
    extract_bearer_token,
)


SECRET = "test-only-signing-secret-with-32-bytes-minimum"
ISSUER = "test-issuer"
AUDIENCE = "customer-agent"
NOW = 2_000_000_000


def _encode(payload, *, secret=SECRET, header=None):
    def part(value):
        raw = json.dumps(value, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    encoded_header = part(header or {"alg": "HS256", "typ": "JWT"})
    encoded_payload = part(payload)
    signing_input = f"{encoded_header}.{encoded_payload}".encode()
    signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).digest()
    return f"{encoded_header}.{encoded_payload}.{base64.urlsafe_b64encode(signature).rstrip(b'=').decode()}"


def _claims(**overrides):
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": "patient-1",
        "role": "patient",
        "scope": "interview:write forms:read forms:write consent:read consent:write",
        "iat": NOW - 10,
        "exp": NOW + 600,
    }
    claims.update(overrides)
    return claims


def _decode(token):
    return decode_access_token(
        token,
        secret=SECRET,
        issuer=ISSUER,
        audience=AUDIENCE,
        now=NOW,
    )


class AccessTokenTests(unittest.TestCase):
    def test_valid_patient_token_is_decoded(self):
        context = _decode(_encode(_claims()))
        self.assertEqual(context.subject, "patient-1")
        self.assertEqual(context.role, "patient")
        self.assertIn("forms:read", context.scopes)

    def test_trusted_generator_round_trips_through_verifier(self):
        token = create_access_token(
            subject="patient-1",
            role="patient",
            scopes=["forms:read", "interview:write"],
            secret=SECRET,
            issuer=ISSUER,
            audience=AUDIENCE,
            lifetime_seconds=600,
            now=NOW,
        )
        context = _decode(token)
        self.assertEqual(context.subject, "patient-1")
        self.assertEqual(context.scopes, {"forms:read", "interview:write"})

    def test_signature_tampering_is_rejected(self):
        token = _encode(_claims())
        with self.assertRaises(TokenValidationError):
            _decode(token[:-1] + ("A" if token[-1] != "A" else "B"))

    def test_algorithm_confusion_is_rejected(self):
        token = _encode(_claims(), header={"alg": "none", "typ": "JWT"})
        with self.assertRaises(TokenValidationError):
            _decode(token)

    def test_wrong_issuer_and_audience_are_rejected(self):
        with self.assertRaises(TokenValidationError):
            _decode(_encode(_claims(iss="other")))
        with self.assertRaises(TokenValidationError):
            _decode(_encode(_claims(aud="other")))
        with self.assertRaises(TokenValidationError):
            _decode(_encode(_claims(aud=[{"unexpected": "object"}])))

    def test_non_string_scope_and_patient_claims_are_rejected(self):
        with self.assertRaises(TokenValidationError):
            _decode(_encode(_claims(scope=["forms:read", 7])))
        with self.assertRaises(TokenValidationError):
            _decode(_encode(_claims(patients=["patient-1", {}])))

    def test_expired_future_and_excessive_lifetime_tokens_are_rejected(self):
        invalid_claims = (
            _claims(exp=NOW - 31),
            _claims(iat=NOW + 31),
            _claims(iat=NOW, exp=NOW + 14_401),
        )
        for claims in invalid_claims:
            with self.subTest(claims=claims):
                with self.assertRaises(TokenValidationError):
                    _decode(_encode(claims))

    def test_short_server_secret_fails_closed(self):
        with self.assertRaises(AuthConfigurationError):
            decode_access_token(
                _encode(_claims()),
                secret="short",
                issuer=ISSUER,
                audience=AUDIENCE,
                now=NOW,
            )

    def test_patient_can_access_only_self_with_required_scope(self):
        context = _decode(_encode(_claims()))
        authorize_patient(context, "patient-1", "forms:read")
        with self.assertRaises(AuthorizationError):
            authorize_patient(context, "patient-2", "forms:read")
        with self.assertRaises(AuthorizationError):
            authorize_patient(context, "patient-1", "users:read")

    def test_clinician_requires_explicit_patient_allow_list(self):
        context = _decode(
            _encode(
                _claims(
                    sub="clinician-1",
                    role="clinician",
                    patients=["patient-1"],
                    scope="interview:write forms:read",
                )
            )
        )
        authorize_patient(context, "patient-1", "forms:read")
        with self.assertRaises(AuthorizationError):
            authorize_patient(context, "patient-2", "forms:read")

    def test_admin_still_requires_operation_scope(self):
        context = _decode(_encode(_claims(role="admin", sub="admin-1")))
        authorize_patient(context, "any-patient", "forms:read")
        with self.assertRaises(AuthorizationError):
            authorize_patient(context, "any-patient", "users:read")

    def test_directory_requires_privileged_role_and_scope(self):
        clinician = _decode(
            _encode(_claims(role="clinician", scope="users:read", sub="c-1"))
        )
        authorize_directory(clinician)
        with self.assertRaises(AuthorizationError):
            authorize_directory(_decode(_encode(_claims(scope="users:read"))))

    def test_bearer_header_is_strict_but_case_insensitive(self):
        self.assertEqual(extract_bearer_token("bearer abc"), "abc")
        for value in (None, "abc", "Basic abc", "Bearer "):
            with self.subTest(value=value):
                with self.assertRaises(TokenValidationError):
                    extract_bearer_token(value)

    def test_unsupported_roles_and_oversized_tokens_are_rejected(self):
        with self.assertRaises(TokenValidationError):
            _decode(_encode(_claims(role="service")))
        with self.assertRaises(TokenValidationError):
            _decode("x" * 8193)


if __name__ == "__main__":
    unittest.main()
