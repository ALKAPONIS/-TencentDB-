"""Hayhooks wrapper: serves the Haystack Agent as an OpenAI-compatible chat completion endpoint.

Request body extras understood by this wrapper (sent by the eUI front end):

* ``user``: pseudonymous user id kept in the browser (OpenAI's standard ``user`` field)
* ``metadata.session_id``: one id per chat window
* ``metadata.turn_id``: one id per message, used to fetch the turn trace from ``/api/turn/{turn_id}``
"""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from typing import Any

from haystack.dataclasses import ChatMessage
from hayhooks import BasePipelineWrapper, async_streaming_generator

from va.assistant import build_agent
from va.memory import sanitize_id
from va.runtime import runtime

MAX_HISTORY = 20


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # OpenAI content parts
        return "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
    return ""


def to_chat_messages(messages: list[dict[str, Any]]) -> list[ChatMessage]:
    """Keep only user/assistant text from the client; the agent owns the system prompt."""
    out: list[ChatMessage] = []
    for m in messages[-MAX_HISTORY:]:
        text = _text(m.get("content")).strip()
        if not text:
            continue
        if m.get("role") == "user":
            out.append(ChatMessage.from_user(text[:8000]))
        elif m.get("role") == "assistant":
            out.append(ChatMessage.from_assistant(text[:8000]))
    return out


class PipelineWrapper(BasePipelineWrapper):
    def setup(self) -> None:
        self.agent = build_agent(runtime.settings, runtime.knowledge)

    async def run_chat_completion_async(
        self, model: str, messages: list[dict], body: dict
    ) -> AsyncGenerator:
        metadata = body.get("metadata") or {}
        user_id = sanitize_id(body.get("user"), "user")
        session_id = sanitize_id(metadata.get("session_id") or body.get("session_id"), "session")
        turn_id = sanitize_id(metadata.get("turn_id"), "turn")

        chat = to_chat_messages(messages)
        user_text = next((m.text for m in reversed(chat) if m.is_from("user")), "") or ""
        trace: dict[str, Any] = {
            "turn_id": turn_id,
            "status": "running",
            "started_at": time.time(),
            "model": runtime.settings.assistant_model,
        }
        ctx: dict[str, Any] = {
            "memory": runtime.memory.for_user(user_id, session_id),
            "user_id": user_id,
            "session_id": session_id,
            "user_text": user_text,
            "trace": trace,
        }
        runtime.turns.put(turn_id, trace)

        def on_end(_result: dict[str, Any]) -> None:
            trace["status"] = "done"
            trace["duration_s"] = round(time.time() - trace["started_at"], 2)
            return None

        return async_streaming_generator(
            pipeline=self.agent,
            pipeline_run_args={"messages": chat, "hook_context": ctx},
            on_pipeline_end=on_end,
        )
