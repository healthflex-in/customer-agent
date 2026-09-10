"""Per-user/appointment context: load / save / switch.

The heavy recommendation payload lives in customer_reco_form_data; this sidecar
holds provenance + the cache watermark (sourceFormHash, condition, poolVersion)
so we only re-run the LLM ranker when something material changed.

"Switching context when a new patient is viewed" = activate(other) — an OrderedDict
LRU keeps the N most-recent active contexts in memory; everything stays persisted.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from datetime import datetime, timezone

from app import config, db

_cache: "OrderedDict[str, dict]" = OrderedDict()


def _key(user_id, appointment_id) -> str:
    return f"{user_id}:{appointment_id}"


def form_hash(form_data: dict) -> str:
    return hashlib.sha1(
        json.dumps(form_data or {}, sort_keys=True, default=str).encode()
    ).hexdigest()


def activate(user_id, appointment_id) -> dict:
    """Load (or create) the context doc and mark it most-recently-used."""
    key = _key(user_id, appointment_id)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]

    doc = db.coll(config.COLL_CONTEXT).find_one(
        {"userId": db.as_object_id(user_id), "appointmentId": db.as_object_id(appointment_id)}
    ) or {
        "userId": db.as_object_id(user_id),
        "appointmentId": db.as_object_id(appointment_id),
        "sourceFormHash": None,
        "condition": None,
        "poolVersion": None,
        "candidateTestNames": [],
        "provenance": [],
        "version": 0,
    }
    _cache[key] = doc
    _cache.move_to_end(key)
    while len(_cache) > config.CONTEXT_CACHE_SIZE:
        _cache.popitem(last=False)  # evict least-recently-used
    return doc


def is_fresh(ctx: dict, source_hash: str, condition: str, pool_version: str) -> bool:
    return bool(
        ctx.get("sourceFormHash") == source_hash
        and (ctx.get("condition") or {}).get("label") == condition
        and ctx.get("poolVersion") == pool_version
        and ctx.get("provenance")
    )


def save(user_id, appointment_id, *, condition: dict, source_hash: str,
         pool_version: str, candidate_names: list[str], provenance: list[dict]) -> dict:
    key = _key(user_id, appointment_id)
    prev = _cache.get(key, {})
    now = datetime.now(timezone.utc)
    update = {
        "condition": condition,
        "sourceFormHash": source_hash,
        "poolVersion": pool_version,
        "candidateTestNames": candidate_names,
        "provenance": provenance,
        "updatedAt": now,
    }
    result = db.coll(config.COLL_CONTEXT).find_one_and_update(
        {"userId": db.as_object_id(user_id), "appointmentId": db.as_object_id(appointment_id)},
        {"$set": update, "$inc": {"version": 1}, "$setOnInsert": {"createdAt": now}},
        upsert=True,
        return_document=True,  # pymongo ReturnDocument.AFTER == True
    )
    merged = {**prev, **(result or update)}
    _cache[key] = merged
    _cache.move_to_end(key)
    return merged
