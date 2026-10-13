"""Bounded request-id tracking for WebSocket text submissions."""

from __future__ import annotations

import re
from collections import OrderedDict
from enum import Enum
from typing import Optional


_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class RequestDecision(str, Enum):
    ACCEPT = "accept"
    DUPLICATE = "duplicate"
    CONFLICT = "conflict"


class RecentRequestWindow:
    """Track a bounded number of request IDs across active WebSockets.

    Identity is scoped by application user identifier. Legacy clients without
    request IDs remain accepted during protocol rollout.
    """

    def __init__(self, capacity: int = 4096) -> None:
        if capacity <= 0:
            raise ValueError("Request window capacity must be positive")
        self._capacity = capacity
        self._requests: OrderedDict[tuple[str, str], Optional[str]] = OrderedDict()

    def register(
        self,
        actor_id: object,
        request_id: object = None,
        question_id: object = None,
    ) -> RequestDecision:
        if request_id is None or request_id == "":
            return RequestDecision.ACCEPT
        if not isinstance(request_id, str) or not _REQUEST_ID_PATTERN.fullmatch(request_id):
            raise ValueError("Invalid requestId")

        normalized_actor = str(actor_id or "anonymous")
        if question_id not in (None, ""):
            if not isinstance(question_id, str) or len(question_id) > 256:
                raise ValueError("Invalid questionId")
            normalized_question = question_id
        else:
            normalized_question = None
        key = (normalized_actor, request_id)
        if key in self._requests:
            original_question = self._requests[key]
            self._requests.move_to_end(key)
            if original_question != normalized_question:
                return RequestDecision.CONFLICT
            return RequestDecision.DUPLICATE

        self._requests[key] = normalized_question
        if len(self._requests) > self._capacity:
            self._requests.popitem(last=False)
        return RequestDecision.ACCEPT
