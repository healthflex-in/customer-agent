"""
Communication protocol between orchestrator and agent.
"""
from typing import Dict, Any, Optional
from datetime import datetime
import json
import uuid


class AgentCommunication:
    """Communication protocol between orchestrator and agent."""
    
    def __init__(self):
        """Initialize the communication protocol."""
        self.message_history = []
    
    def create_message(self, message_type: str, agent_id: str, 
                      orchestrator_id: str, payload: Dict[str, Any],
                      correlation_id: Optional[str] = None,
                      response_required: bool = False) -> Dict[str, Any]:
        """
        Create a message for communication.
        
        Args:
            message_type: The type of message
            agent_id: The agent ID
            orchestrator_id: The orchestrator ID
            payload: The message payload
            correlation_id: The correlation ID
            response_required: Whether a response is required
            
        Returns:
            The message
        """
        if correlation_id is None:
            correlation_id = str(uuid.uuid4())
        
        message = {
            "type": message_type,
            "timestamp": datetime.now().isoformat(),
            "agent_id": agent_id,
            "orchestrator_id": orchestrator_id,
            "payload": payload,
            "correlation_id": correlation_id,
            "response_required": response_required
        }
        
        self.message_history.append(message)
        return message
    
    def send_message(self, message: Dict[str, Any]) -> bool:
        """
        Send a message.
        
        Args:
            message: The message to send
            
        Returns:
            True if the message was sent, False otherwise
        """
        # In a real implementation, this would send the message over a network
        # For now, it's a placeholder
        print(f"Sending message: {message['type']} (correlation_id: {message['correlation_id']})")
        return True
    
    def receive_message(self, message: Dict[str, Any]) -> Dict[str, Any]:
        """
        Receive a message.
        
        Args:
            message: The message to receive
            
        Returns:
            The response message
        """
        # In a real implementation, this would receive a message from a network
        # For now, it's a placeholder
        print(f"Received message: {message['type']} (correlation_id: {message['correlation_id']})")
        
        # Create a response message
        response = {
            "type": "response",
            "timestamp": datetime.now().isoformat(),
            "agent_id": message["orchestrator_id"],
            "orchestrator_id": message["agent_id"],
            "payload": {"status": "received"},
            "correlation_id": message["correlation_id"],
            "response_required": False
        }
        
        self.message_history.append(response)
        return response
    
    def get_message_by_correlation_id(self, correlation_id: str) -> Optional[Dict[str, Any]]:
        """
        Get a message by correlation ID.
        
        Args:
            correlation_id: The correlation ID
            
        Returns:
            The message or None if not found
        """
        for message in self.message_history:
            if message["correlation_id"] == correlation_id:
                return message
        
        return None
