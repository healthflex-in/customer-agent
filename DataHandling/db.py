import pymongo
from pymongo import MongoClient
import uuid
import datetime
import json
import os
from typing import Dict, List, Any, Optional, Union


class MedicalInterviewDB:
    """
    Database connector for the Medical Interview application.
    Handles connections to MongoDB and provides methods for storing and retrieving interview data.
    """

    def __init__(
        self,
        connection_string: Optional[str] = None,
        db_name: str = "medical_interviews",
    ):
        """
        Initialize the database connection.

        Args:
            connection_string: MongoDB connection string. If None, attempts to use MONGODB_URI env variable
                              or defaults to localhost.
            db_name: Name of the database to use
        """
        # Use provided connection string, environment variable, or default to localhost
        if connection_string is None:
            connection_string = os.environ.get(
                "MONGODB_URI", "mongodb://localhost:27017/"
            )

        # Connect to MongoDB
        try:
            self.client = MongoClient(connection_string)
            self.db = self.client[db_name]
            self.interviews = self.db["interviews"]
            self.users = self.db["users"]
            self.interactions = self.db["interactions"]

            # Create indexes for faster queries
            self.users.create_index("user_id", unique=True)
            self.interviews.create_index("interview_id", unique=True)
            self.interactions.create_index(
                [("user_id", pymongo.ASCENDING), ("timestamp", pymongo.DESCENDING)]
            )

            # Test connection
            self.client.admin.command("ping")
            print(f"Connected to MongoDB: {db_name}")
        except Exception as e:
            print(f"Error connecting to MongoDB: {e}")
            raise

    def generate_user_id(self) -> str:
        """Generate a unique user ID"""
        return str(uuid.uuid4())

    def generate_interview_id(self) -> str:
        """Generate a unique interview ID"""
        return str(uuid.uuid4())

    def generate_interaction_id(self) -> str:
        """Generate a unique interaction ID"""
        return str(uuid.uuid4())

    def create_user(self, metadata: Optional[Dict[str, Any]] = None) -> str:
        """
        Create a new user in the database.

        Args:
            metadata: Optional metadata about the user

        Returns:
            The generated user_id
        """
        user_id = self.generate_user_id()
        user_data = {
            "user_id": user_id,
            "created_at": datetime.datetime.now(),
            "metadata": metadata or {},
        }

        try:
            self.users.insert_one(user_data)
            return user_id
        except Exception as e:
            print(f"Error creating user: {e}")
            raise

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve user data by user_id.

        Args:
            user_id: The user ID to find

        Returns:
            User data dictionary or None if not found
        """
        return self.users.find_one({"user_id": user_id}, {"_id": 0})

    def create_interview(self, user_id: str, session_id: str = None) -> str:
        """
        Create a new interview record.

        Args:
            user_id: The user ID associated with this interview
            session_id: Optional session identifier

        Returns:
            The generated interview_id
        """
        interview_id = self.generate_interview_id()
        interview_data = {
            "interview_id": interview_id,
            "user_id": user_id,
            "session_id": session_id or str(uuid.uuid4()),
            "created_at": datetime.datetime.now(),
            "updated_at": datetime.datetime.now(),
            "completed": False,
            "form_data": {},
            "current_section": "",
            "progress": 0,
        }

        try:
            self.interviews.insert_one(interview_data)
            return interview_id
        except Exception as e:
            print(f"Error creating interview: {e}")
            raise

    def update_interview_form(
        self,
        interview_id: str,
        form_data: Dict[str, Any],
        current_section: str,
        progress: float,
    ) -> bool:
        """
        Update the form data for an existing interview.

        Args:
            interview_id: The interview ID to update
            form_data: The updated form data
            current_section: The current section being worked on
            progress: Progress percentage (0-100)

        Returns:
            True if successful, False otherwise
        """
        try:
            result = self.interviews.update_one(
                {"interview_id": interview_id},
                {
                    "$set": {
                        "form_data": form_data,
                        "current_section": current_section,
                        "progress": progress,
                        "updated_at": datetime.datetime.now(),
                    }
                },
            )
            return result.modified_count > 0
        except Exception as e:
            print(f"Error updating interview: {e}")
            return False

    def complete_interview(self, interview_id: str, form_data: Dict[str, Any]) -> bool:
        """
        Mark an interview as completed and save the final form data.

        Args:
            interview_id: The interview ID to complete
            form_data: The final form data

        Returns:
            True if successful, False otherwise
        """
        try:
            result = self.interviews.update_one(
                {"interview_id": interview_id},
                {
                    "$set": {
                        "form_data": form_data,
                        "completed": True,
                        "progress": 100,
                        "completed_at": datetime.datetime.now(),
                        "updated_at": datetime.datetime.now(),
                    }
                },
            )
            return result.modified_count > 0
        except Exception as e:
            print(f"Error completing interview: {e}")
            return False

    def get_interview(self, interview_id: str) -> Optional[Dict[str, Any]]:
        """
        Retrieve interview data by interview_id.

        Args:
            interview_id: The interview ID to find

        Returns:
            Interview data dictionary or None if not found
        """
        return self.interviews.find_one({"interview_id": interview_id}, {"_id": 0})

    def get_user_interviews(self, user_id: str) -> List[Dict[str, Any]]:
        """
        Retrieve all interviews for a specific user.

        Args:
            user_id: The user ID to find interviews for

        Returns:
            List of interview data dictionaries
        """
        return list(
            self.interviews.find({"user_id": user_id}, {"_id": 0}).sort(
                "created_at", -1
            )
        )

    def log_interaction(
        self,
        user_id: str,
        interview_id: str,
        interaction_type: str,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        """
        Log an interaction in the conversation (question, response, etc.).

        Args:
            user_id: The user ID
            interview_id: The interview ID
            interaction_type: Type of interaction (e.g., "user_speech", "agent_response")
            content: The content of the interaction
            metadata: Optional metadata about the interaction

        Returns:
            The generated interaction_id
        """
        interaction_id = self.generate_interaction_id()
        interaction_data = {
            "interaction_id": interaction_id,
            "user_id": user_id,
            "interview_id": interview_id,
            "type": interaction_type,
            "content": content,
            "timestamp": datetime.datetime.now(),
            "metadata": metadata or {},
        }

        try:
            self.interactions.insert_one(interaction_data)
            return interaction_id
        except Exception as e:
            print(f"Error logging interaction: {e}")
            raise

    def get_interview_interactions(self, interview_id: str) -> List[Dict[str, Any]]:
        """
        Retrieve all interactions for a specific interview.

        Args:
            interview_id: The interview ID to find interactions for

        Returns:
            List of interaction data dictionaries sorted by timestamp
        """
        return list(
            self.interactions.find({"interview_id": interview_id}, {"_id": 0}).sort(
                "timestamp", 1
            )
        )

    def close(self):
        """Close the database connection"""
        if hasattr(self, "client"):
            self.client.close()


# Usage example
if __name__ == "__main__":
    # Test the database connection
    db = MedicalInterviewDB()

    # Create a test user
    user_id = db.create_user({"test_user": True})
    print(f"Created test user: {user_id}")

    # Create a test interview
    interview_id = db.create_interview(user_id)
    print(f"Created test interview: {interview_id}")

    # Log a test interaction
    interaction_id = db.log_interaction(
        user_id, interview_id, "agent_response", "Hello, how can I help you today?"
    )
    print(f"Created test interaction: {interaction_id}")

    # Close the connection
    db.close()
