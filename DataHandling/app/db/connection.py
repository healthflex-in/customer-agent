"""Secure MongoDB client construction shared by every database entry point."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlsplit

if TYPE_CHECKING:
    from pymongo import MongoClient


_TRUE_VALUES = {"1", "true", "yes"}
_INSECURE_BOOLEAN_OPTIONS = {
    "tlsallowinvalidcertificates",
    "tlsallowinvalidhostnames",
    "tlsinsecure",
}


def validate_mongo_tls_options(uri: str) -> None:
    """Reject URI options that disable certificate or hostname verification.

    PyMongo verifies TLS certificates by default. That protection can still be
    disabled inside the URI, so validating only constructor keyword arguments
    is insufficient.
    """

    query = urlsplit(uri).query
    for raw_key, raw_value in parse_qsl(query, keep_blank_values=True):
        key = raw_key.lower()
        value = raw_value.strip().lower()
        if key in _INSECURE_BOOLEAN_OPTIONS and value in _TRUE_VALUES:
            raise ValueError(
                f"Insecure MongoDB TLS option is not permitted: {raw_key}"
            )
        if key == "ssl_cert_reqs" and value in {"cert_none", "0"}:
            raise ValueError(
                "Insecure MongoDB TLS option is not permitted: ssl_cert_reqs"
            )


def build_verified_mongo_options(
    *,
    ca_file: str | None = None,
    server_selection_timeout_ms: int = 5000,
) -> dict[str, object]:
    """Build MongoClient options without any verification bypass."""

    options: dict[str, object] = {
        "serverSelectionTimeoutMS": server_selection_timeout_ms,
    }
    normalized_ca_file = (ca_file or "").strip()
    if normalized_ca_file:
        options["tlsCAFile"] = normalized_ca_file
    return options


def create_verified_mongo_client(
    uri: str,
    *,
    ca_file: str | None = None,
    server_selection_timeout_ms: int = 5000,
) -> MongoClient:
    """Create a MongoClient that preserves PyMongo's secure TLS defaults."""

    from pymongo import MongoClient

    validate_mongo_tls_options(uri)
    return MongoClient(
        uri,
        **build_verified_mongo_options(
            ca_file=ca_file,
            server_selection_timeout_ms=server_selection_timeout_ms,
        ),
    )
