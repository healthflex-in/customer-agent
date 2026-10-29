"""In-process memory of interview counters across a page refresh.

Resume rebuilds a patient's socket from the form saved in MongoDB under
userId + formId + attemptId. The ask counts are not part of that document, so
a refresh used to reset them and let a question be asked again. They are kept
here under the same key instead.

This is best effort by design. A restart, or a reconnect that reaches another
replica, finds nothing and starts the counts from zero, which is exactly what
happened before this module existed. Entries expire and the store is bounded.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Callable

from app.config import ATTEMPT_MEMORY_MAX_ENTRIES, ATTEMPT_MEMORY_TTL_SECONDS

AttemptKey = tuple[str, str, str | None]

_COUNTS_KEY = "graph_question_ask_counts"


class AttemptMemory:
    """A bounded, expiring map from an attempt to its ask counts."""

    def __init__(
        self,
        *,
        ttl_seconds: int,
        max_entries: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        self._clock = clock
        self._entries: OrderedDict[AttemptKey, tuple[float, dict]] = OrderedDict()
        self._lock = threading.Lock()

    def remember(self, key: AttemptKey, ask_counts: dict) -> None:
        with self._lock:
            self._entries[key] = (self._clock() + self._ttl, dict(ask_counts))
            self._entries.move_to_end(key)
            while len(self._entries) > self._max:
                self._entries.popitem(last=False)

    def recall(self, key: AttemptKey) -> dict | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, ask_counts = entry
            if self._clock() >= expires_at:
                del self._entries[key]
                return None
            return dict(ask_counts)


_MEMORY = AttemptMemory(
    ttl_seconds=ATTEMPT_MEMORY_TTL_SECONDS,
    max_entries=ATTEMPT_MEMORY_MAX_ENTRIES,
)


def attempt_key(client_state: dict) -> AttemptKey | None:
    """The key resume uses to find an attempt: userId + formId + attemptId."""

    user_id = client_state.get("user_id")
    form_id = client_state.get("form_id")
    if not user_id or not form_id:
        return None
    return str(user_id), str(form_id), client_state.get("attempt_id") or None


def remember_attempt_counters(client_state: dict, memory: AttemptMemory = _MEMORY) -> None:
    """Keep this socket's ask counts for the attempt, so a refresh can restore them."""

    key = attempt_key(client_state)
    if key is not None:
        memory.remember(key, client_state.get(_COUNTS_KEY) or {})


def restore_attempt_counters(client_state: dict, memory: AttemptMemory = _MEMORY) -> bool:
    """Load the attempt's ask counts into a fresh socket. Returns True when found."""

    key = attempt_key(client_state)
    counts = memory.recall(key) if key is not None else None
    if counts is None:
        return False
    client_state[_COUNTS_KEY] = counts
    return True
