"""Agent tests: deterministic, no network (MockChatGenerator + fake memory)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from haystack.components.generators.chat import MockChatGenerator
from haystack.dataclasses import ChatMessage, ToolCall

from va.assistant import build_agent
from va.config import Settings
from va.hooks import _background
from va.knowledge import KnowledgeBase
from va.tools import build_tools


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase(Settings().knowledge_dir)


def test_knowledge_base_loads_all_seed_documents(kb: KnowledgeBase) -> None:
    refs = {s["reference"] for s in kb.sources}
    assert refs == {"IP/25/881", "IP/25/1277", "IP/25/1307", "IP/25/1343", "SPEECH/25/1297"}
    assert all(s["url"].startswith("https://ec.europa.eu/commission/presscorner/detail/en/") for s in kb.sources)


def test_knowledge_search_finds_the_right_release(kb: KnowledgeBase) -> None:
    hits = kb.search("emergency support farmers Slovenia frost")
    assert hits and hits[0]["reference"] == "IP/25/1307"


def test_tool_schemas_hide_request_context(kb: KnowledgeBase) -> None:
    for t in build_tools(kb):
        assert "ctx" not in t.parameters.get("properties", {}), t.name


def _run(agent, messages, ctx) -> dict[str, Any]:
    async def go() -> dict[str, Any]:
        result = await agent.run_async(messages=messages, hook_context=ctx)
        if _background:  # let the fire-and-forget memory write finish
            await asyncio.gather(*list(_background))
        return result

    return asyncio.run(go())


def test_agent_uses_memory_tools_and_writes_turn(kb: KnowledgeBase, fake_memory) -> None:
    seen_prompts: list[list[ChatMessage]] = []
    replies = iter(
        [
            ChatMessage.from_assistant(
                tool_calls=[ToolCall(tool_name="search_press_releases", arguments={"query": "farmers Slovenia"})]
            ),
            ChatMessage.from_assistant("Η Επιτροπή διέθεσε 2,9 εκατ. ευρώ στη Σλοβενία (IP/25/1307, 22 May 2025)."),
        ]
    )

    def respond(messages: list[ChatMessage]) -> ChatMessage:
        seen_prompts.append(list(messages))
        return next(replies)

    agent = build_agent(Settings(), kb, chat_generator=MockChatGenerator(response_fn=respond))
    ctx: dict[str, Any] = {"memory": fake_memory, "user_text": "How much did Slovenia get?", "trace": {}}
    result = _run(agent, [ChatMessage.from_user("How much did Slovenia get?")], ctx)

    # before_run hook: the memory note sits right after the agent's system prompt
    first_call = seen_prompts[0]
    assert first_call[0].role.value == "system" and "virtual assistant" in first_call[0].text
    assert first_call[1].role.value == "system" and "<user_memory>" in first_call[1].text
    assert "[atom-1]" in first_call[1].text and "Journalist" in first_call[1].text

    # tool ran and its source was recorded for the side panel
    trace = ctx["trace"]
    assert trace["sources"][0]["reference"] == "IP/25/1307"
    assert trace["tools"][0]["name"] == "search_press_releases"
    assert trace["memory"]["persona_used"] is True
    assert trace["step_count"] >= 2 and trace["exit_reason"]

    # after_run hook wrote the user/assistant pair to memory
    assert fake_memory.writes == [("How much did Slovenia get?", result["last_message"].text)]
    assert trace["memory_write"] == "written"


def test_agent_answers_without_memory(kb: KnowledgeBase) -> None:
    agent = build_agent(Settings(), kb, chat_generator=MockChatGenerator("Hello."))
    ctx: dict[str, Any] = {"memory": None, "trace": {}}
    result = _run(agent, [ChatMessage.from_user("Hi")], ctx)
    assert result["last_message"].text == "Hello."
    assert ctx["trace"]["memory"] == {"available": False}
    assert ctx["trace"]["memory_write"] == "skipped"


def test_forget_memory_only_deletes_ids_shown_in_this_run(kb: KnowledgeBase, fake_memory) -> None:
    replies = iter(
        [
            ChatMessage.from_assistant(
                tool_calls=[
                    ToolCall(tool_name="forget_memory", arguments={"memory_id": "atom-999"}),
                    ToolCall(tool_name="forget_memory", arguments={"memory_id": "atom-1"}),
                ]
            ),
            ChatMessage.from_assistant("Done."),
        ]
    )
    agent = build_agent(Settings(), kb, chat_generator=MockChatGenerator(response_fn=lambda m: next(replies)))
    ctx: dict[str, Any] = {"memory": fake_memory, "user_text": "forget that", "trace": {}}
    _run(agent, [ChatMessage.from_user("Forget that I want Greek answers")], ctx)
    assert fake_memory.deleted == ["atom-1"]
