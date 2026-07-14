"""MongoDB client lifecycle and collection accessors."""
import os
from typing import Optional
from pymongo import MongoClient
from pymongo.collection import Collection

# Re-export serializers for convenience
from app.db.serializers import serialize_datetime, normalize_user_id

_client: Optional[MongoClient] = None
_users_collection: Optional[Collection] = None
_customer_info_collection: Optional[Collection] = None

MONGO_URI = os.environ.get("MONGO_URI", "")
MONGO_DB_NAME = os.environ.get("MONGO_DB_NAME", "stance-dashboard")
MONGO_USERS_COLLECTION = os.environ.get("MONGO_USERS_COLLECTION", "users")
MONGO_CUSTOMER_INFO_COLLECTION = os.environ.get("MONGO_CUSTOMER_INFO_COLLECTION", "customer-info")

def init_mongo() -> bool:
    global _client, _users_collection, _customer_info_collection
    if not MONGO_URI:
        print("MONGO_URI not set — MongoDB disabled")
        return False
    try:
        _client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
        _client.admin.command("ping")
        db = _client[MONGO_DB_NAME]
        _users_collection = db[MONGO_USERS_COLLECTION]
        _customer_info_collection = db[MONGO_CUSTOMER_INFO_COLLECTION]
        # Unique index to prevent duplicate forms per user
        _customer_info_collection.create_index(
            [("userId", 1), ("formId", 1)], unique=True, name="unique_user_form"
        )
        # TTL: auto-delete abandoned empty forms after 7 days
        _customer_info_collection.create_index(
            [("createdAt", 1)], expireAfterSeconds=7*24*3600,
            partialFilterExpression={"title": "New Form"}, name="ttl_abandoned_forms"
        )
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
