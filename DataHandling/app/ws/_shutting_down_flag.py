"""Graceful shutdown flag."""
_shutting_down = False

def is_shutting_down() -> bool:
    return _shutting_down

def set_shutting_down(value: bool = True) -> None:
    global _shutting_down
    _shutting_down = value
