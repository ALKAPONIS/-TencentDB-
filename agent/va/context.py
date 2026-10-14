"""Request-scoped context registry.

Haystack's ``State.get`` returns deep copies, so a tool that received ``hook_context``
through ``inputs_from_state`` would get a copy of the memory client and its writes to the
trace would be lost. Instead, the ``before_run`` hook registers the live ``hook_context``
under a short ``request_id`` string kept in State; tools receive only that id and look
the context up here. ``after_run`` releases it.
"""

from __future__ import annotations

import uuid
from typing import Any

_REGISTRY: dict[str, dict[str, Any]] = {}

STATE_SCHEMA: dict[str, Any] = {"request_id": {"type": str}}


def new_request_id() -> str:
    return uuid.uuid4().hex


def register(request_id: str, ctx: dict[str, Any]) -> None:
    _REGISTRY[request_id] = ctx


def lookup(request_id: str | None) -> dict[str, Any]:
    return _REGISTRY.get(request_id or "", {})


def release(request_id: str | None) -> None:
    _REGISTRY.pop(request_id or "", None)


def active_count() -> int:
    return len(_REGISTRY)
