# Question Pool Management System

This system implements a deterministic question pool management system for the HealthAgent. It manages a pool of questions stored in MongoDB with a deterministic orchestrator that selects questions based on patient data without using AI for decision-making.

## Overview

The system consists of several key components:

1. **MongoDB Schema**: A collection for storing questions with conditions, dependencies, and context
2. **Deterministic Orchestrator**: A rule-based system that selects questions based on patient data
3. **Question Agent**: Manages the interview process with patients
4. **Communication Protocol**: Structured communication between orchestrator and agent

## Key Design Decisions

### 1. MCP vs Custom Solution

**Decision: Custom Solution**

MCP (Model Context Protocol) is better suited for AI-driven workflows where the orchestrator needs to make decisions using LLMs. However, for this use case, a deterministic orchestrator is required:

**Why Custom Solution is Better:**

1. **Deterministic Behavior**: Custom solution ensures predictable question selection without AI influence
2. **Performance**: No LLM calls for simple rule-based logic
3. **Reliability**: No dependency on external MCP servers or AI model availability
4. **Simplicity**: Straightforward implementation for rule-based question selection
5. **Debugging**: Easier to trace and debug question selection logic

### 2. MongoDB as Data Store

**Rationale:**

- **Scalability**: Can handle large question pools
- **Flexibility**: Easy to modify question conditions and dependencies
- **Persistence**: Questions persist across system restarts
- **Query Support**: Complex queries for question selection

### 3. Deterministic Question Selection

**Rationale:**

- **Predictability**: Consistent question selection for same patient data
- **Transparency**: Clear rules for question selection
- **Reliability**: No AI model dependencies
- **Performance**: Fast rule-based evaluation

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                 MongoDB Database                        │
│  ┌─────────────────┐  ┌─────────────────────────────┐  │
│  │     questions   │  │         customer-info      │  │
│  │   collection    │  │      collection           │  │
│  └─────────────────┘  └─────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────┐
│                Question Pool                          │
│  ┌─────────────────┐  ┌─────────────────────────────┐  │
│  │ MongoDB Client  │  │ Question Repository       │  │
│  └─────────────────┘  └─────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────┐
│                Orchestrator System                     │
│  ┌─────────────────┐  ┌─────────────────────────────┐  │
│  │ Condition       │  │ Dependency               │  │
│  │ Evaluator       │  │ Checker                   │  │
│  └─────────────────┘  └─────────────────────────────┘  │
│                       │  │                           │
│                       └───────────┬───────────────────┘
│                               ▼
│                ┌─────────────────────────┐            │
│                │  Deterministic        │            │
│                │     Orchestrator      │            │
│                └─────────────────────────┘            │
└─────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────┐
│                 Question Agent                         │
│  ┌─────────────────┐  ┌─────────────────────────────┐  │
│  │ Agent           │  │ Communication            │  │
│  │ Communication   │  │ Protocol                 │  │
│  └─────────────────┘  └─────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────┐
│                 HealthAgent Integration                 │
│  (Existing System)                                     │
└─────────────────────────────────────────────────────────┘
```

## MongoDB Schema

### Question Pool Collection

```json
{
  "_id": ObjectId,
  "question_text": "string (required)",
  "category": "string (enum: business, clinical, follow_up, further)",
  "priority": "number (1-10, higher = more urgent)",
  "conditions": {
    "patient_data_fields": ["string"],
    "min_age": "number",
    "max_age": "number",
    "gender": "string (enum: male, female, other, unspecified)",
    "medical_conditions": ["string"],
    "symptoms": ["string"],
    "has_reports": "boolean",
    "has_previous_treatments": "boolean",
    "form_sections_completed": ["string"],
    "form_sections_empty": ["string"],
    "custom_logic": "string (JSON expression)"
  },
  "dependencies": {
    "required_fields": ["string"],
    "blocked_by": ["ObjectId"],
    "prerequisite_categories": ["string"],
    "min_questions_before": "number"
  },
  "context": {
    "section": "string",
    "related_fields": ["string"],
    "interview_phase": "string (enum: initial, follow_up, summary)",
    "trigger_phrases": ["string"],
    "avoid_if": ["string"]
  },
  "metadata": {
    "created_at": "date",
    "updated_at": "date",
    "created_by": "string",
    "version": "number",
    "tags": ["string"],
    "is_active": "boolean"
  }
}
```

## Question Categories

1. **Business Questions**: Questions related to business aspects (e.g., referral source, treatment goals)
2. **Clinical Questions**: Questions related to medical aspects (e.g., symptoms, medical history)
3. **Follow-up Questions**: Questions that depend on previous answers (e.g., previous consultations)
4. **Further Questions**: Questions that provide additional context (e.g., other health conditions)

## Question Selection Algorithm

The orchestrator uses the following algorithm to select the next question:

1. **Filter by category and priority**: Get all eligible questions
2. **Apply conditions**: Filter questions based on patient data and form data
3. **Check dependencies**: Ensure all dependencies are satisfied
4. **Sort by priority and recency**: Sort questions by priority (higher first) and creation time
5. **Select highest priority question**: Choose the first question from the sorted list

## Integration with Existing HealthAgent

The orchestrator integrates with the existing HealthAgent by:

1. **Replacing Predefined Questions**: The HealthAgent uses questions from the MongoDB pool instead of the hardcoded `PREDEFINED_QUESTIONS`
2. **Maintaining Compatibility**: The existing HealthAgent interface remains unchanged
3. **Seamless Handoff**: The orchestrator handles question selection, while HealthAgent handles response processing

## Usage

### Starting an Interview

```python
from src.orchestrator.deterministic_orchestrator import DeterministicOrchestrator

# Initialize orchestrator
orchestrator = DeterministicOrchestrator(
    question_repository,
    condition_evaluator,
    dependency_checker,
    agent_communication
)

# Start interview with patient data
patient_data = {"age": 35, "gender": "male"}
session_id = orchestrator.start_session(patient_data)

# Get next question
next_question = orchestrator.get_next_question(session_id)
```

### Processing Responses

```python
# Process patient response
patient_response = {"answer": "test response"}
success = orchestrator.complete_question(session_id, question_id, patient_response)

# Get next question
next_question = orchestrator.get_next_question(session_id)
```

### Adding Questions

```python
from src.orchestrator.deterministic_orchestrator import get_orchestrator

orchestrator = get_orchestrator()

new_question = {
    "question_text": "New question text",
    "category": "clinical",
    "priority": 8,
    "conditions": {"min_age": 18},
    "dependencies": {},
    "context": {
        "section": "Present Complaint",
        "related_fields": ["Primary Complaint"]
    },
    "metadata": {
        "created_at": datetime.now(),
        "updated_at": datetime.now(),
        "created_by": "admin",
        "version": 1,
        "tags": ["new"],
        "is_active": True
    }
}

question_id = orchestrator.add_question(new_question)
print(f"Added question with ID: {question_id}")
```

## Testing

The system includes comprehensive tests:

1. **Unit Tests**: Test individual components (condition evaluator, dependency checker, orchestrator)
2. **Integration Tests**: Test the full interview flow
3. **System Tests**: Test the entire system end-to-end

To run the tests:

```bash
python -m pytest tests/ -v
```

## Deployment

### MongoDB Setup

```bash
# Create MongoDB user and database
mongosh

// Create database and user
use healthflex

// Create user with read/write access
db.createUser({
    user: "healthflex_user",
    pwd: "secure_password",
    roles: [
        { role: "readWrite", db: "healthflex" }
    ]
})

// Create indexes for better performance
db.questions.createIndex({"metadata.is_active": 1})
db.questions.createIndex({"category": 1})
db.questions.createIndex({"priority": -1})
db.questions.createIndex({"conditions.patient_data_fields": 1})
```

### Configuration

```python
# config/orchestrator_config.py
import os
from typing import Dict, Any

class OrchestratorConfig:
    def __init__(self):
        self.mongo_uri = os.getenv("MONGO_URI", "mongodb://localhost:27017")
        self.mongo_db_name = os.getenv("MONGO_DB_NAME", "healthflex")
        self.max_questions_per_session = int(os.getenv("MAX_QUESTIONS_PER_SESSION", "50"))
        self.default_session_timeout = int(os.getenv("DEFAULT_SESSION_TIMEOUT", "3600"))
        self.enable_orchestrator = os.getenv("ENABLE_ORCHESTRATOR", "true").lower() == "true"
    
    def get_mongo_config(self) -> Dict[str, Any]:
        return {
            "uri": self.mongo_uri,
            "db_name": self.mongo_db_name
        }
```

### Docker Deployment

```dockerfile
FROM python:3.9-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY src/ ./src/

EXPOSE 8000

CMD ["python3", "-m", "src.orchestrator.deterministic_orchestrator"]
```

## Performance Considerations

### 1. Query Optimization

- **Index Strategy**: Create compound indexes for common query patterns
- **Caching**: Cache frequently accessed questions
- **Lazy Loading**: Load questions on-demand rather than all at once

### 2. Memory Management

- **Session Cleanup**: Implement session timeout and cleanup
- **Connection Pooling**: Use connection pooling for MongoDB
- **Resource Monitoring**: Monitor memory usage and performance

## Security Considerations

### 1. Data Protection

- **Encryption**: Encrypt sensitive patient data
- **Access Control**: Implement role-based access control
- **Audit Logging**: Log all question access and modifications

### 2. Data Privacy

- **Anonymization**: Remove patient identifiers from logs
- **Data Retention**: Implement data retention policies
- **Compliance**: Ensure compliance with healthcare regulations

## Future Enhancements

### 1. Advanced Question Selection

- **Machine Learning**: Use ML to predict optimal question sequences
- **Adaptive Logic**: Dynamically adjust questions based on patient responses
- **Context Awareness**: Consider conversation context in question selection

### 2. Multi-Agent Support

- **Parallel Processing**: Support multiple agents working in parallel
- **Load Balancing**: Distribute questions across multiple orchestrators
- **Scalability**: Scale to handle large numbers of concurrent interviews

### 3. Enhanced Analytics

- **Question Effectiveness**: Track which questions yield the best results
- **Patient Journey Analysis**: Analyze patient progression through questions
- **Performance Metrics**: Monitor system performance and user satisfaction

## License

This project is licensed under the MIT License.

## Contact

For questions or issues, please contact the development team.
