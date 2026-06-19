from src.orchestrator.deterministic_orchestrator import DeterministicOrchestrator
from unittest.mock import Mock
try:
    DeterministicOrchestrator(Mock(), Mock(), Mock(), Mock(), Mock())
    print("Success")
except Exception as e:
    print(f"Error: {e}")
