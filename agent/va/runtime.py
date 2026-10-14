"""Process-wide singletons shared by the Hayhooks pipeline wrapper and the extra API routes."""

from __future__ import annotations

import time
from collections import OrderedDict
from functools import cached_property
from typing import Any

from .config import Settings, settings
from .knowledge import KnowledgeBase
from .memory import MemoryService


class TurnStore:
    """Short-lived store of per-turn traces so the UI can fetch 'what was used' after the stream ends."""

    def __init__(self, max_items: int = 500, ttl_s: float = 3600) -> None:
        self._items: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._max = max_items
        self._ttl = ttl_s

    def put(self, turn_id: str, trace: dict[str, Any]) -> None:
        if not turn_id:
            return
        self._items[turn_id] = (time.time(), trace)
        self._items.move_to_end(turn_id)
        while len(self._items) > self._max:
            self._items.popitem(last=False)

    def get(self, turn_id: str) -> dict[str, Any] | None:
        entry = self._items.get(turn_id)
        if entry is None or time.time() - entry[0] > self._ttl:
            return None
        return entry[1]


class Runtime:
    def __init__(self, cfg: Settings) -> None:
        self.settings = cfg
        self.memory = MemoryService(cfg)
        self.turns = TurnStore()

    @cached_property
    def knowledge(self) -> KnowledgeBase:
        return KnowledgeBase(self.settings.knowledge_dir)


runtime = Runtime(settings)
