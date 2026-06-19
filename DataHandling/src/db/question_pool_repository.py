"""
Repository for managing questions in the question pool.
"""
from typing import List, Optional, Dict, Any
from bson import ObjectId
from datetime import datetime

from src.db.mongodb_client import get_mongodb_client


class QuestionPoolRepository:
    """Repository for managing questions in the question pool."""
    
    def __init__(self):
        """Initialize the repository."""
        self.collection = get_mongodb_client().questions_collection
    
    def get_all_questions(self, is_active: bool = True) -> List[Dict[str, Any]]:
        """
        Get all questions, optionally filtered by active status.
        
        Args:
            is_active: Whether to include only active questions
            
        Returns:
            List of question documents
        """
        query = {"metadata.is_active": is_active} if is_active else {}
        return list(self.collection.find(query))
    
    def get_question_by_id(self, question_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a specific question by ID.
        
        Args:
            question_id: The ID of the question to retrieve
            
        Returns:
            Question document or None if not found
        """
        try:
            return self.collection.find_one({"_id": ObjectId(question_id)})
        except:
            return None
    
    def insert_question(self, question_data: Dict[str, Any]) -> str:
        """
        Insert a new question and return its ID.
        
        Args:
            question_data: The question data to insert
            
        Returns:
            The ID of the inserted question
        """
        # Add timestamp if not present
        if "metadata" in question_data:
            question_data["metadata"]["created_at"] = datetime.now()
            question_data["metadata"]["updated_at"] = datetime.now()
        
        result = self.collection.insert_one(question_data)
        return str(result.inserted_id)
    
    def update_question(self, question_id: str, update_data: Dict[str, Any]):
        """
        Update an existing question.
        
        Args:
            question_id: The ID of the question to update
            update_data: The update data
        """
        # Update timestamp
        if "metadata" in update_data:
            update_data["metadata"]["updated_at"] = datetime.now()
        
        self.collection.update_one(
            {"_id": ObjectId(question_id)},
            {"$set": update_data}
        )
    
    def delete_question(self, question_id: str) -> bool:
        """
        Delete a question.
        
        Args:
            question_id: The ID of the question to delete
            
        Returns:
            True if the question was deleted, False otherwise
        """
        result = self.collection.delete_one({"_id": ObjectId(question_id)})
        return result.deleted_count > 0
    
    def get_questions_by_category(self, category: str, is_active: bool = True) -> List[Dict[str, Any]]:
        """
        Get questions by category.
        
        Args:
            category: The category to filter by
            is_active: Whether to include only active questions
            
        Returns:
            List of question documents
        """
        query = {"category": category}
        if is_active:
            query["metadata.is_active"] = True
        
        return list(self.collection.find(query))
    
    def get_questions_by_section(self, section: str, is_active: bool = True) -> List[Dict[str, Any]]:
        """
        Get questions by section.
        
        Args:
            section: The section to filter by
            is_active: Whether to include only active questions
            
        Returns:
            List of question documents
        """
        query = {"context.section": section}
        if is_active:
            query["metadata.is_active"] = True
        
        return list(self.collection.find(query))
