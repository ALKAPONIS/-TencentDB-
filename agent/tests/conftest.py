from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("AIEC_API_KEY", "test-key")
os.environ.setdefault("HAYSTACK_TELEMETRY_ENABLED", "False")
os.environ.setdefault("TOOL_RESULTS_DIR", str(ROOT / ".pytest_tool_results"))

from va.memory import Recall  # noqa: E402


class FakeUserMemory:
    """Stands in for UserMemory: records writes, returns canned recall."""

    def __init__(self, atoms: list[dict[str, Any]] | None = None, persona: str = "") -> None:
        self.atoms = atoms or []
        self.persona = persona
        self.writes: list[tuple[str, str]] = []
        self.deleted: list[str] = []
        self.user_id = "u-test"
        self.session_id = "s-test"

    async def recall(self, query: str, *, limit: int = 5) -> Recall:
        return Recall(persona=self.persona, atoms=list(self.atoms))

    async def search(self, query: str, *, limit: int = 5) -> dict[str, Any]:
        return {"atoms": list(self.atoms), "conversation": [], "error": ""}

    async def add_turn(self, user_text: str, assistant_text: str) -> str:
        self.writes.append((user_text, assistant_text))
        return ""

    async def delete_atoms(self, ids: list[str]) -> dict[str, Any]:
        self.deleted.extend(ids)
        return {"deleted_count": len(ids)}


@pytest.fixture
def fake_memory() -> FakeUserMemory:
    return FakeUserMemory(
        atoms=[{"id": "atom-1", "type": "instruction", "content": "Always answer in Greek.", "score": 0.9}],
        persona="Journalist covering EU agriculture.",
    )
