"""MongoDB client lifecycle + collection accessors + id helpers.

Single shared cluster (stance-dashboard). Follows the customer-agent's
try-ObjectId-then-string id pattern documented in docs/system-overview.md.
"""

from __future__ import annotations

from typing import Any, Optional

from bson import ObjectId
from bson.errors import InvalidId
from pymongo import ASCENDING, MongoClient
from pymongo.collection import Collection

from app import config

_client: Optional[MongoClient] = None


def init_mongo() -> bool:
    global _client
    if not config.MONGO_URI:
        print("[mongo] MONGO_URI not set — MongoDB disabled")
        return False
    try:
        _client = MongoClient(config.MONGO_URI, serverSelectionTimeoutMS=5000)
        _client.admin.command("ping")
        _ensure_indexes()
        print(f"[mongo] Connected to {config.MONGO_DB_NAME}")
        return True
    except Exception as e:  # noqa: BLE001
        print(f"[mongo] Connection failed: {e}")
        _client = None
        return False


def _db():
    if _client is None:
        init_mongo()
    if _client is None:
        raise RuntimeError("MongoDB is not connected")
    return _client[config.MONGO_DB_NAME]


def coll(name: str) -> Collection:
    return _db()[name]


def _ensure_indexes() -> None:
    db = _client[config.MONGO_DB_NAME]  # type: ignore[index]
    db[config.COLL_RECO].create_index(
        [("appointmentId", ASCENDING), ("userId", ASCENDING)],
        unique=True,
        name="uniq_reco_key",
    )
    db[config.COLL_CONTEXT].create_index(
        [("userId", ASCENDING), ("appointmentId", ASCENDING)],
        unique=True,
        name="uniq_ctx_key",
    )
    # Prevent pileup: per-patient context + draft docs auto-expire after inactivity.
    # Active patients keep refreshing updatedAt; stale ones are reaped by Mongo.
    ttl = config.CONTEXT_TTL_DAYS * 24 * 3600
    for coll_name, idx_name in ((config.COLL_CONTEXT, "ttl_context"), (config.COLL_RECO, "ttl_reco")):
        try:
            db[coll_name].create_index(
                [("updatedAt", ASCENDING)], expireAfterSeconds=ttl, name=idx_name
            )
        except Exception as e:  # noqa: BLE001 — e.g. TTL value changed; non-fatal
            print(f"[mongo] TTL index {idx_name} skipped: {e}")


def is_connected() -> bool:
    if _client is None:
        return False
    try:
        _client.admin.command("ping")
        return True
    except Exception:  # noqa: BLE001
        return False


def as_object_id(value: Any) -> Any:
    """Best-effort ObjectId coercion; returns the original value on failure."""
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError):
        return value


def id_query(field: str, value: Any) -> dict:
    """Match a doc by an id field stored as either ObjectId or string."""
    oid = as_object_id(value)
    if isinstance(oid, ObjectId) and str(oid) == str(value):
        return {"$or": [{field: oid}, {field: str(value)}]}
    return {field: value}
