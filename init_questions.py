#!/usr/bin/env python3
"""
Script to initialize the MongoDB collection with sports physiotherapy questions.
"""
import sys
import os
import json
from datetime import datetime

# Add the src directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'src'))

from src.db.mongodb_client import initialize_mongodb, get_mongodb_client


def create_sports_physiotherapy_questions():
    """Create the sports physiotherapy question pool."""
    print("Creating sports physiotherapy question pool...")
    
    # Initialize MongoDB
    mongo_uri = "mongodb://localhost:27017"
    db_name = "healthflex"
    
    try:
        initialize_mongodb(mongo_uri, db_name)
        mongodb_client = get_mongodb_client()
        
        # Clear existing questions
        mongodb_client.questions_collection.delete_many({})
        
        # Create questions
        questions = [
            # Business Questions
            {
                "question_text": "How did you learn about our sports physiotherapy clinic? (Google search, social media, referral, word of mouth, healthcare provider)",
                "category": "business",
                "priority": 2,
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
            {
                "question_text": "What are your primary goals for sports injury rehabilitation? (Return to sport, pain relief, functional improvement, injury prevention, performance enhancement)",
                "category": "business",
                "priority": 3,
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
            },
            # Clinical Questions
            {
                "question_text": "What specific sports or physical activities were you involved in when the injury occurred? (running, weightlifting, soccer, basketball, tennis, etc.)",
                "category": "clinical",
                "priority": 10,
                "conditions": {
                    "interview_phase": "initial"
                },
                "dependencies": {},
                "context": {
                    "section": "Present Complaint",
                    "related_fields": ["Mechanism of Injury (If Any)"],
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
            {
                "question_text": "What exactly is bothering you right now (pain, stiffness, weakness, swelling, instability, etc.), where do you feel it, and how severe is it on a scale of 0 to 10?",
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
            {
                "question_text": "How would you describe the quality of your pain? (sharp, dull, burning, aching, stabbing, throbbing, constant, intermittent)",
                "category": "clinical",
                "priority": 8,
                "conditions": {
                    "interview_phase": "initial"
                },
                "dependencies": {},
                "context": {
                    "section": "Present Complaint",
                    "related_fields": ["Primary Complaint"],
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
            {
                "question_text": "What activities make the pain worse? (specific movements, activities, positions)",
                "category": "clinical",
                "priority": 7,
                "conditions": {
                    "interview_phase": "initial"
                },
                "dependencies": {},
                "context": {
                    "section": "Present Complaint",
                    "related_fields": ["Aggravating Factors"],
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
            {
                "question_text": "What activities or positions relieve the pain? (rest, ice, heat, stretching, specific movements)",
                "category": "clinical",
                "priority": 6,
                "conditions": {
                    "interview_phase": "initial"
                },
                "dependencies": {},
                "context": {
                    "section": "Present Complaint",
                    "related_fields": ["Relieving Factors"],
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
            {
                "question_text": "Have you ever had any sports-related injuries before? If yes, what were they and when did they occur?",
                "category": "follow_up",
                "priority": 8,
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
            {
                "question_text": "Do you have any history of ligament injuries (ACL, MCL, ankle sprains) or tendon injuries (Achilles, rotator cuff, patellar tendon)?",
                "category": "clinical",
                "priority": 7,
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
                    "tags": ["default", "clinical"],
                    "is_active": True
                }
            },
            {
                "question_text": "Do you have any current or past history of fractures, dislocations, or other significant musculoskeletal injuries?",
                "category": "clinical",
                "priority": 7,
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
                    "tags": ["default", "clinical"],
                    "is_active": True
                }
            },
            # Follow-up Questions
            {
                "question_text": "Have you consulted any doctor, physiotherapist, or sports medicine specialist for this issue before? If yes, what did they say and what treatment was prescribed?",
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
            {
                "question_text": "What was the current status of your issue when you stopped seeing the previous healthcare provider? (Improved, Same, Worse, Resolved)",
                "category": "follow_up",
                "priority": 6,
                "conditions": {
                    "interview_phase": "initial",
                    "custom_logic": "if form['Previous Consultations']['Previous Diagnosis or Advice and Prescribed Treatment Taken']"
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
            {
                "question_text": "Did you have any imaging (X-ray, MRI, CT scan) or other diagnostic tests for this issue? If yes, what were the findings?",
                "category": "follow_up",
                "priority": 6,
                "conditions": {
                    "interview_phase": "initial",
                    "custom_logic": "if form['History & Diagnostics']['Reports']"
                },
                "dependencies": {},
                "context": {
                    "section": "History & Diagnostics",
                    "related_fields": ["Reports"],
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
                "question_text": "Do you have any other health conditions like diabetes, high blood pressure, thyroid issues, heart conditions, or any past surgeries or fractures?",
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
            {
                "question_text": "What medications are you currently taking (prescription or over-the-counter)?",
                "category": "further",
                "priority": 5,
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
            {
                "question_text": "How would you describe your current level of physical activity? (sedentary, light walking, recreational sports, competitive athletics, strength training, etc.)",
                "category": "further",
                "priority": 5,
                "conditions": {
                    "interview_phase": "initial"
                },
                "dependencies": {},
                "context": {
                    "section": "Lifestyle Factors",
                    "related_fields": ["Current Lifestyle"],
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
            {
                "question_text": "What are your specific expectations from this treatment regarding return to sport or activity? (timeline, level of performance, pain-free activity, injury prevention)",
                "category": "further",
                "priority": 5,
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
                    "tags": ["default", "further"],
                    "is_active": True
                }
            },
            # Additional Sports-Specific Questions
            {
                "question_text": "What specific sport or activity are you hoping to return to? (running, soccer, basketball, tennis, weightlifting, etc.)",
                "category": "clinical",
                "priority": 9,
                "conditions": {
                    "interview_phase": "initial"
                },
                "dependencies": {},
                "context": {
                    "section": "Present Complaint",
                    "related_fields": ["Primary Complaint"],
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
            {
                "question_text": "What was the mechanism of your injury? (sudden twist, direct impact, overuse, improper landing, contact collision)",
                "category": "clinical",
                "priority": 8,
                "conditions": {
                    "interview_phase": "initial"
                },
                "dependencies": {},
                "context": {
                    "section": "Present Complaint",
                    "related_fields": ["Mechanism of Injury (If Any)"],
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
            {
                "question_text": "Did you experience any swelling, bruising, or deformity at the time of injury?",
                "category": "clinical",
                "priority": 7,
                "conditions": {
                    "interview_phase": "initial"
                },
                "dependencies": {},
                "context": {
                    "section": "Present Complaint",
                    "related_fields": ["Primary Complaint"],
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
            {
                "question_text": "How did you manage your injury before coming to our clinic? (rest, ice, compression, elevation, physiotherapy, medication, no treatment)",
                "category": "follow_up",
                "priority": 6,
                "conditions": {
                    "interview_phase": "initial",
                    "custom_logic": "if form['Previous Consultations']['Previous Diagnosis or Advice and Prescribed Treatment Taken']"
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
            {
                "question_text": "What are your concerns about returning to sport after this injury? (re-injury risk, performance decline, pain recurrence, complications)",
                "category": "business",
                "priority": 5,
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
        
        # Insert questions
        result = mongodb_client.questions_collection.insert_many(questions)
        
        print(f"✓ Successfully created {len(result.inserted_ids)} questions in MongoDB")
        
        # Display some sample questions
        print("\nSample questions created:")
        for i, question in enumerate(questions[:5]):
            print(f"\n{i+1}. {question['question_text']}")
            print(f"   Category: {question['category']}")
            print(f"   Priority: {question['priority']}")
        
        return True
    except Exception as e:
        print(f"✗ Error creating questions: {e}")
        return False


def main():
    """Main function."""
    print("=" * 60)
    print("Sports Physiotherapy Question Pool Initialization")
    print("=" * 60)
    
    if create_sports_physiotherapy_questions():
        print("\n✓ Question pool initialization completed successfully!")
        return 0
    else:
        print("\n✗ Question pool initialization failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())
