"""User-directory lookups against the shared Mongo connection."""

from bson import ObjectId
from pymongo.collection import Collection

from app.db.mongo import init_mongo, mongo
from app.db.serializers import serialize_user
from app.observability.privacy import error_type


def validate_user_exists(user_id: str) -> bool:
    """
    Validate that a user exists in the MongoDB users collection.

    Args:
        user_id: The user ID to validate (as string or ObjectId)

    Returns:
        True if user exists, False otherwise
    """
    try:
        # Ensure MongoDB connection
        if mongo.users_collection is None:
            print(f"[validate_user_exists] MongoDB not connected")
            return False

        # Get database and collection names for logging
        db_name = mongo.users_collection.database.name
        collection_name = mongo.users_collection.name

        # Convert user_id to ObjectId if it's a valid ObjectId string
        try:
            user_object_id = ObjectId(user_id) if isinstance(user_id, str) else user_id
        except Exception:
            print("[validate_user_exists] Invalid subject identifier format")
            return False

        # Check if user exists
        user = mongo.users_collection.find_one({"_id": user_object_id})
        exists = user is not None

        if exists:
            print(f"[validate_user_exists] Subject exists in {db_name}.{collection_name}")
        else:
            print(f"[validate_user_exists] Subject not found in {db_name}.{collection_name}")

        return exists

    except Exception as e:
        print(f"[validate_user_exists] Validation failed: {error_type(e)}")
        return False


def ensure_users_collection() -> Collection:
    """Get an initialized users collection or raise."""

    if mongo.users_collection is None:
        # Attempt to reconnect once to avoid stale connections
        init_mongo()

    if mongo.users_collection is None:
        raise RuntimeError("User directory is not configured on the server.")

    return mongo.users_collection


def _fetch_users_sync(collection: Collection, mongo_query: dict, limit: int) -> list[dict]:
    """Execute and consume the synchronous PyMongo cursor off the event loop."""

    cursor = collection.find(mongo_query).sort("updatedAt", -1).limit(limit)
    users = []
    for doc in cursor:
        serialized = serialize_user(doc)
        if serialized and serialized["id"]:
            users.append(serialized)
    return users
