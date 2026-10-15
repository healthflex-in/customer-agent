"""User list and consent HTTP routes."""

from fastapi import HTTPException, Query

from app.db.users import _fetch_users_sync, ensure_users_collection
from app.runtime.blocking import run_blocking
from bson import ObjectId


def register_user_routes(app) -> None:
    """Register user and consent routes on the application itself."""

    @app.get("/api/users")
    async def get_users(
        search: str = Query(default="", description="Optional search term"),
        limit: int = Query(default=50, ge=1, le=500),
        ):
        """Return up-to-date list of users for the selector."""
        try:
            collection = await run_blocking(ensure_users_collection)

            mongo_query = {}
            if search:
                regex = {"$regex": search, "$options": "i"}
                mongo_query = {
                    "$or": [
                        {"firstName": regex},
                        {"lastName": regex},
                        {"name": regex},
                        {"email": regex},
                    ]
                }

            users = await run_blocking(_fetch_users_sync, collection, mongo_query, limit)

            return {"users": users}
        except HTTPException:
            raise
        except RuntimeError as err:
            raise HTTPException(
                status_code=500,
                detail=str(err),
            ) from err
        except Exception as e:
            raise HTTPException(
                status_code=500, detail=f"Failed to fetch users: {str(e)}"
            )


    @app.get("/api/users/{user_id}/consent")
    async def get_consent_status(
        user_id: str,
        ):
        """
        Check whether a user has accepted the consent policy.
        Checks two sources:
        1. users.profileData.consentAccepted (set by our API or legacy)
        2. consentrecords collection (written by consent.stance.health via recordConsent GraphQL mutation)
        """
        try:
            users_col = await run_blocking(ensure_users_collection)
            user_oid = ObjectId(user_id)

            # Source 1: users.profileData.consentAccepted
            user = await run_blocking(
                users_col.find_one,
                {"_id": user_oid},
                {"profileData.consentAccepted": 1, "profileData.consentAcceptedAt": 1}
            )
            if not user:
                raise HTTPException(status_code=404, detail="User not found")

            profile = user.get("profileData", {})
            if profile.get("consentAccepted"):
                return {
                    "consentAccepted": True,
                    "source": "profileData",
                    "consentAcceptedAt": str(profile.get("consentAcceptedAt", "")),
                }

            # Source 2: consentrecords collection (created by consent.stance.health)
            try:
                db = users_col.database
                consent_col = db["consentrecords"]
                record = await run_blocking(
                    consent_col.find_one,
                    {"userId": user_oid, "isActive": True},
                    {"acceptedAt": 1}
                )
                if record:
                    return {
                        "consentAccepted": True,
                        "source": "consentrecords",
                        "consentAcceptedAt": str(record.get("acceptedAt", "")),
                    }
            except Exception as ce:
                print(f"[consent] consentrecords check failed (non-fatal): {ce}")

            return {"consentAccepted": False}

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to check consent: {str(e)}")


    @app.post("/api/users/{user_id}/consent")
    async def accept_consent(
        user_id: str,
        ):
        """Record that a user has accepted the consent policy."""
        try:
            from datetime import datetime, timezone
            collection = await run_blocking(ensure_users_collection)
            user_oid = ObjectId(user_id)
            result = await run_blocking(
                collection.update_one,
                {"_id": user_oid},
                {"$set": {
                    "profileData.consentAccepted": True,
                    "profileData.consentAcceptedAt": datetime.now(timezone.utc),
                    "profileData.consentPolicyVersion": "1.1.0",
                }}
            )
            if result.matched_count == 0:
                raise HTTPException(status_code=404, detail="User not found")
            return {"success": True, "consentAccepted": True}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to record consent: {str(e)}")
