"""MongoDB client lifecycle and collection accessors."""
import os
from typing import Optional
from pymongo import MongoClient
from pymongo.collection import Collection

# Re-export serializers for convenience
from app.db.serializers import serialize_datetime, normalize_user_id
from app.db.connection import create_verified_mongo_client
from app.forms.lifecycle import ensure_form_lifecycle_ttl_index
from app.forms.attempts import ensure_form_attempt_index

_client: Optional[MongoClient] = None
_users_collection: Optional[Collection] = None
_customer_info_collection: Optional[Collection] = None

MONGO_URI = os.environ.get("MONGO_URI", "")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "stance-dashboard")
MONGO_USERS_COLLECTION = os.environ.get("MONGO_USERS_COLLECTION", "users")
MONGO_CUSTOMER_INFO_COLLECTION = os.environ.get("MONGO_CUSTOMER_INFO_COLLECTION", "customer-info")
MONGO_TLS_CA_FILE = os.environ.get("MONGO_TLS_CA_FILE")

def init_mongo() -> bool:
    global _client, _users_collection, _customer_info_collection
    if not MONGO_URI:
        print("MONGO_URI not set — MongoDB disabled")
        return False
    try:
        _client = create_verified_mongo_client(
            MONGO_URI,
            ca_file=MONGO_TLS_CA_FILE,
        )
        _client.admin.command("ping")
        db = _client[MONGO_DB_NAME]
        _users_collection = db[MONGO_USERS_COLLECTION]
        _customer_info_collection = db[MONGO_CUSTOMER_INFO_COLLECTION]
        ensure_form_attempt_index(_customer_info_collection)
        ensure_form_lifecycle_ttl_index(_customer_info_collection)
        print(f"[mongo] Connected to {MONGO_DB_NAME}.{MONGO_USERS_COLLECTION}, {MONGO_DB_NAME}.{MONGO_CUSTOMER_INFO_COLLECTION}")
        return True
    except Exception as e:
        print(f"[mongo] Connection failed: {e}")
        _client = None
        return False

def get_users_collection() -> Optional[Collection]:
    if _users_collection is None:
        init_mongo()
    return _users_collection

def get_customer_info_collection() -> Optional[Collection]:
    if _customer_info_collection is None:
        init_mongo()
    return _customer_info_collection

def get_client() -> Optional[MongoClient]:
    return _client
