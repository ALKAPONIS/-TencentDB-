"""Offline demo: the whole stack on one port, no API keys, no Docker.

* the real Haystack Agent, hooks, tools and knowledge base
* a scripted MockChatGenerator instead of gpt-5.1 (it searches, then quotes the top hit)
* ``dev.mock_memory_core`` instead of TencentDB Agent Memory
* the built eUI app (``web/dist/browser``) served at ``/``

    cd agent && python -m dev.run_offline      # then open http://localhost:8000
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import uvicorn

HERE = Path(__file__).resolve().parent
os.environ.setdefault("AIEC_API_KEY", "offline")
os.environ.setdefault("MEMORY_CORE_URL", "http://127.0.0.1:8420")
os.environ.setdefault("MEMORY_CORE_TOKEN", "offline")
os.environ.setdefault("MEMORY_ADMIN_USER_KEY", "sk-mem-offline")

from haystack.components.generators.chat import MockChatGenerator  # noqa: E402
from haystack.dataclasses import ChatMessage, ChatRole, ToolCall  # noqa: E402

import va.assistant as assistant  # noqa: E402


def scripted(messages: list[ChatMessage]) -> ChatMessage:
    last = messages[-1]
    memory_note = next((m.text for m in messages if m.role == ChatRole.SYSTEM and "<user_memory>" in (m.text or "")), "")
    if last.role == ChatRole.USER:
        return ChatMessage.from_assistant(
            tool_calls=[ToolCall(tool_name="search_press_releases", arguments={"query": last.text or ""})]
        )
    hits = []
    if last.tool_call_result:
        try:
            hits = json.loads(last.tool_call_result.result)
        except (ValueError, TypeError):
            hits = []
    prefix = "(offline demo: I would apply your stored preferences here.)\n\n" if memory_note else ""
    if not hits:
        return ChatMessage.from_assistant(prefix + "I could not find this in the press releases available to me.")
    top = hits[0]
    excerpt = top["excerpt"].split("\n", 1)[-1][:420]
    return ChatMessage.from_assistant(
        f"{prefix}**{top['title']}** ({top['reference']}, {top['date']})\n\n{excerpt}…\n\n"
        f"- Source: [{top['reference']}]({top['url']})"
    )


assistant.build_generator = lambda settings: MockChatGenerator(response_fn=scripted)

import app as agent_app  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

dist = HERE.parent.parent / "web" / "dist" / "browser"
if dist.is_dir():
    agent_app.app.mount("/", StaticFiles(directory=dist, html=True), name="web")


def _memory_core() -> None:
    uvicorn.run("dev.mock_memory_core:app", host="127.0.0.1", port=8420, log_level="warning")


if __name__ == "__main__":
    threading.Thread(target=_memory_core, daemon=True).start()
    uvicorn.run(agent_app.app, host="127.0.0.1", port=int(os.environ.get("PORT", "8000")))
