"""
MongoDB client for the Question Pool Management System.
"""
from pymongo import MongoClient
from typing import Optional
from datetime import datetime
import logging

from bson import ObjectId

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class MongoDBClient:
    """MongoDB client for managing question pool and patient data."""
    
    def __init__(self, uri: str, db_name: str):
        """
        Initialize MongoDB client.
        
        Args:
            uri: MongoDB connection URI
            db_name: Database name
        """
        self.client = MongoClient(uri)
        self.db = self.client[db_name]
        self.questions_collection = self.db["questions"]
        self.customer_info_collection = self.db["customer-info"]
        
        # Create indexes for better performance
        self._create_indexes()
        
    def _create_indexes(self):
        """Create indexes for better query performance."""
        try:
            # Index for active questions
            self.questions_collection.create_index([("metadata.is_active", 1)])
            
            # Index for category-based queries
            self.questions_collection.create_index([("category", 1)])
            
            # Index for priority-based queries
            self.questions_collection.create_index([("priority", -1)])
            
            # Index for condition-based queries
            self.questions_collection.create_index([("conditions.patient_data_fields", 1)])
            
            # Index for dependencies
            self.questions_collection.create_index([("dependencies.blocked_by", 1)])
            
            # Index for context-based queries
            self.questions_collection.create_index([("context.section", 1)])
            
            logger.info("Indexes created successfully")
        except Exception as e:
            logger.warning(f"Failed to create indexes: {e}")
    
    def initialize_question_pool(self):
        """Initialize the question pool with default questions if empty."""
        if self.questions_collection.count_documents({}) == 0:
            logger.info("Initializing question pool with default questions...")
            self._insert_default_questions()
        else:
            logger.info("Question pool already initialized")
    
    def _insert_default_questions(self):
        """Insert default questions for testing and initial setup."""
        default_questions = [
            # Business Questions
            {
                "question_text": "How did you come to know about Stance Health? (Friend/family referral, Google, Instagram, Facebook, YouTube, etc.)",
                "category": "business",
                "priority": 3,
                "conditions": {
                    "interview_phase": "initial"
                },
                "dependencies": {},
                "context": {
                    "section": "Referral",
                    "related_fields": ["Source"],
                    "interview_phase": "initial"
                },
                "metadata": {
                    "created_at": datetime.now(),
                    "updated_at": datetime.now(),
                    "created_by": "system",
                    "version": 1,
                    "tags": ["default", "business"],
                    "is_active": True
                }
            },
            # Clinical Questions
            {
                "question_text": "What exactly is bothering you right now (pain, stiffness, weakness, swelling, etc.), where do you feel it, and how severe is it on a scale of 0 to 10?",
                "category": "clinical",
                "priority": 10,
                "conditions": {
                    "interview_phase": "initial"
                },
                "dependencies": {},
                "context": {
                    "section": "Present Complaint",
                    "related_fields": ["Primary Complaint", "Severity (1-10)", "Primary Location of Pain"],
                    "interview_phase": "initial"
                },
                "metadata": {
                    "created_at": datetime.now(),
                    "updated_at": datetime.now(),
                    "created_by": "system",
                    "version": 1,
                    "tags": ["default", "clinical"],
                    "is_active": True
                }
            },
            # Follow-up Questions
            {
                "question_text": "Have you consulted any doctor, physiotherapist, or hospital for this before? If yes, what did they say and what treatment was prescribed?",
                "category": "follow_up",
                "priority": 7,
                "conditions": {
                    "interview_phase": "initial",
                    "custom_logic": "if not form['Previous Consultations']['Previous Diagnosis or Advice and Prescribed Treatment Taken']"
                },
                "dependencies": {},
                "context": {
                    "section": "Previous Consultations",
                    "related_fields": ["Previous Diagnosis or Advice and Prescribed Treatment Taken", "Current Status of Issue (Improved, Same, Worse)"],
                    "interview_phase": "initial"
                },
                "metadata": {
                    "created_at": datetime.now(),
                    "updated_at": datetime.now(),
                    "created_by": "system",
                    "version": 1,
                    "tags": ["default", "follow_up"],
                    "is_active": True
                }
            },
            # Further Questions
            {
                "question_text": "Do you have any other health conditions like diabetes, high blood pressure, thyroid, heart issues, or any past surgeries or fractures?",
                "category": "further",
                "priority": 6,
                "conditions": {
                    "interview_phase": "initial"
                },
                "dependencies": {},
                "context": {
                    "section": "History & Diagnostics",
                    "related_fields": ["Systemic Illness and Surgical History"],
                    "interview_phase": "initial"
                },
                "metadata": {
                    "created_at": datetime.now(),
                    "updated_at": datetime.now(),
                    "created_by": "system",
                    "version": 1,
                    "tags": ["default", "further"],
                    "is_active": True
                }
            },
            # Additional Clinical Question
            {
                "question_text": "When did this issue start (gradual or sudden), and how long have you had it?",
                "category": "clinical",
                "priority": 9,
                "conditions": {
                    "interview_phase": "initial"
                },
                "dependencies": {},
                "context": {
                    "section": "Present Complaint",
                    "related_fields": ["Duration of the Issue", "Onset (Gradual or Sudden)"],
                    "interview_phase": "initial"
                },
                "metadata": {
                    "created_at": datetime.now(),
                    "updated_at": datetime.now(),
                    "created_by": "system",
                    "version": 1,
                    "tags": ["default", "clinical"],
                    "is_active": True
                }
            },
            # Business Question
            {
                "question_text": "What are your main goals for this treatment (short-term and long-term)?",
                "category": "business",
                "priority": 4,
                "conditions": {
                    "interview_phase": "initial"
                },
                "dependencies": {},
                "context": {
                    "section": "Treatment Goals",
                    "related_fields": ["Short-Term Goals (within 3 months)", "Long-Term Goals (after 3 months)", "Specific Expectations from Treatment"],
                    "interview_phase": "initial"
                },
                "metadata": {
                    "created_at": datetime.now(),
                    "updated_at": datetime.now(),
                    "created_by": "system",
                    "version": 1,
                    "tags": ["default", "business"],
                    "is_active": True
                }
            }
        ]
        
        try:
            result = self.questions_collection.insert_many(default_questions)
            logger.info(f"Inserted {len(result.inserted_ids)} default questions")
        except Exception as e:
            logger.error(f"Failed to insert default questions: {e}")


# Global instance for easy access
_mongodb_client = None


def get_mongodb_client() -> MongoDBClient:
    """Get or create the global MongoDB client instance."""
    global _mongodb_client
    if _mongodb_client is None:
        raise RuntimeError("MongoDB client not initialized. Call initialize_mongodb() first.")
    return _mongodb_client


def initialize_mongodb(uri: str, db_name: str):
    """Initialize the global MongoDB client."""
    global _mongodb_client
    _mongodb_client = MongoDBClient(uri, db_name)
    _mongodb_client.initialize_question_pool()
