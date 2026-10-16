"""MongoDB client and collection handles shared by the customer-agent.

One process-wide object holds the client and collections. Callers read
``mongo.<name>`` so a reconnect inside ``init_mongo()`` is visible everywhere,
including modules imported before the connection exists.
"""

from app.audit.chat_history import ensure_chat_history_indexes
from app.clinical.escalation import ensure_escalation_indexes
from app.config import (
    MONGO_CHAT_HISTORY_COLLECTION,
    MONGO_CLINICAL_ESCALATIONS_COLLECTION,
    MONGO_CUSTOMER_INFO_COLLECTION,
    MONGO_DB_NAME,
    MONGO_REPORT_JOBS_COLLECTION,
    MONGO_TLS_CA_FILE,
    MONGO_URI,
    MONGO_USERS_COLLECTION,
)
from app.db.connection import create_verified_mongo_client
from app.forms.attempts import ensure_form_attempt_index
from app.forms.lifecycle import ensure_form_lifecycle_ttl_index
from app.jobs.reports import ensure_report_job_indexes
from app.observability.logging import log as _log
from app.observability.privacy import error_type


class MongoResources:
    """Connection handles. Attributes stay ``None`` until ``init_mongo()`` succeeds."""

    mongo_client = None
    users_collection = None
    customer_info_collection = None
    tagged_questions_collection = None
    report_jobs_collection = None
    clinical_escalations_collection = None
    chat_history_collection = None


mongo = MongoResources()

_HANDLES = (
    "mongo_client",
    "users_collection",
    "customer_info_collection",
    "tagged_questions_collection",
    "report_jobs_collection",
    "clinical_escalations_collection",
    "chat_history_collection",
)


def _clear_handles() -> None:
    for name in _HANDLES:
        setattr(mongo, name, None)


def init_mongo():
    """Initialize MongoDB client for user directory lookups and customer info."""
    if not MONGO_URI:
        print("MONGO_URI not set. User suggestions and customer info endpoints will be disabled.")
        return

    try:
        mongo.mongo_client = create_verified_mongo_client(
            MONGO_URI,
            ca_file=MONGO_TLS_CA_FILE,
        )
        mongo.mongo_client.admin.command("ping")
        db = mongo.mongo_client[MONGO_DB_NAME]
        mongo.users_collection = db[MONGO_USERS_COLLECTION]
        mongo.customer_info_collection = db[MONGO_CUSTOMER_INFO_COLLECTION]
        mongo.tagged_questions_collection = db["tagged-questions"]
        mongo.report_jobs_collection = db[MONGO_REPORT_JOBS_COLLECTION]
        mongo.clinical_escalations_collection = db[MONGO_CLINICAL_ESCALATIONS_COLLECTION]
        mongo.chat_history_collection = db[MONGO_CHAT_HISTORY_COLLECTION]

        # A formId identifies a questionnaire; attemptId identifies one intake.
        # This migration preserves legacy records while allowing repeat intakes.
        ensure_form_attempt_index(mongo.customer_info_collection)

        # Drafts alone receive expiresAt. Answered/completed records omit it,
        # and the partial filter provides defense if stale data is present.
        ensure_form_lifecycle_ttl_index(mongo.customer_info_collection)
        ensure_report_job_indexes(mongo.report_jobs_collection)
        ensure_escalation_indexes(mongo.clinical_escalations_collection)
        try:
            ensure_chat_history_indexes(mongo.chat_history_collection)
        except Exception as chat_index_error:
            # Transcript persistence is diagnostic only. A bad/missing audit
            # index must not make the patient intake database unavailable.
            _log.error(
                "chat_history_index_initialization_failed",
                error_type=error_type(chat_index_error),
            )

        print(
            "Connected to MongoDB collections: "
            f"{MONGO_DB_NAME}.{MONGO_USERS_COLLECTION}, "
            f"{MONGO_DB_NAME}.{MONGO_CUSTOMER_INFO_COLLECTION}, "
            f"{MONGO_DB_NAME}.{MONGO_CHAT_HISTORY_COLLECTION}"
        )
    except Exception as e:
        _clear_handles()
        print(f"MongoDB connection failed: {error_type(e)}")
