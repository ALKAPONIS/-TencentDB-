"""MemoryService / UserMemory against a mocked memory-core (respx), including bootstrap."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
import respx
from haystack.components.generators.chat import MockChatGenerator
from haystack.dataclasses import ChatMessage

from va.assistant import build_agent
from va.config import Settings
from va.hooks import _background
from va.knowledge import KnowledgeBase
from va.memory import MemoryService

BASE = "http://memory-core.test:8420"


def ok(data: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"code": 0, "message": "ok", "request_id": "r", "data": data})


@pytest.fixture
def settings(monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("MEMORY_CORE_URL", BASE)
    monkeypatch.setenv("MEMORY_CORE_TOKEN", "gw-token")
    monkeypatch.setenv("MEMORY_ADMIN_USER_KEY", "sk-mem-admin")
    return Settings()


def mock_core(router: respx.MockRouter, calls: dict[str, list[dict[str, Any]]]) -> None:
    def record(name: str, data: dict[str, Any]):
        def handler(request: httpx.Request) -> httpx.Response:
            calls.setdefault(name, []).append(json.loads(request.content or b"{}"))
            assert request.headers["authorization"] == "Bearer gw-token"
            assert request.headers["x-tdai-service-id"] == "default"
            return ok(data)

        return handler

    router.get(f"{BASE}/health").respond(200, json={"status": "ok", "version": "2.0.2", "stores": {"ftsAvailable": True}})
    router.post(f"{BASE}/v3/internal/meta/user/init-admin").respond(409, json={"code": 409, "message": "already_initialized"})
    router.post(f"{BASE}/v3/meta/auth/verify").mock(
        side_effect=record("verify", {"valid": True, "user": {"user_id": "usr_admin"}})
    )
    router.post(f"{BASE}/v3/meta/team/list").mock(side_effect=record("team_list", {"items": [], "total": 0}))
    router.post(f"{BASE}/v3/meta/team/create").mock(side_effect=record("team_create", {"team_id": "t_1"}))
    router.post(f"{BASE}/v3/meta/agent/list").mock(side_effect=record("agent_list", {"items": [], "total": 0}))
    router.post(f"{BASE}/v3/meta/agent/create").mock(side_effect=record("agent_create", {"agent_id": "agt_1"}))
    router.post(f"{BASE}/v3/core/read").mock(side_effect=record("core_read", {"content": "Prefers short answers."}))
    router.post(f"{BASE}/v3/atomic/search").mock(
        side_effect=record("atomic_search", {"items": [{"id": "a1", "type": "persona", "content": "Works on CAP.", "score": 0.7}]})
    )
    router.post(f"{BASE}/v3/atomic/query").mock(side_effect=record("atomic_query", {"items": [], "total": 0}))
    router.post(f"{BASE}/v3/conversation/add").mock(
        side_effect=record("conv_add", {"accepted_ids": ["m1", "m2"], "total_count": 2})
    )


@respx.mock(assert_all_called=False)
def test_bootstrap_creates_team_and_agent(respx_mock: respx.MockRouter, settings: Settings) -> None:
    calls: dict[str, list[dict[str, Any]]] = {}
    mock_core(respx_mock, calls)
    svc = MemoryService(settings)
    assert asyncio.run(svc.bootstrap()) is True
    assert svc.status.team_id == "t_1" and svc.status.agent_id == "agt_1"
    assert svc.status.fts_available is True
    assert calls["team_create"][0]["owner_user_id"] == "usr_admin"
    assert calls["agent_create"][0]["team_id"] == "t_1"


@respx.mock(assert_all_called=False)
def test_agent_round_trip_with_real_sdk_client(respx_mock: respx.MockRouter, settings: Settings) -> None:
    """The live SDK client (with its httpx client) must survive the Agent run: no deep copies."""
    calls: dict[str, list[dict[str, Any]]] = {}
    mock_core(respx_mock, calls)

    async def go() -> dict[str, Any]:
        svc = MemoryService(settings)
        assert await svc.bootstrap()
        memory = svc.for_user("user-42", "sess-7")
        agent = build_agent(settings, KnowledgeBase(settings.knowledge_dir), chat_generator=MockChatGenerator("OK."))
        ctx: dict[str, Any] = {"memory": memory, "user_text": "Hello", "trace": {}}
        await agent.run_async(messages=[ChatMessage.from_user("Hello")], hook_context=ctx)
        await asyncio.gather(*list(_background))
        await svc.close()
        return ctx

    ctx = asyncio.run(go())
    # isolation triple sent on every data-plane call
    for name in ("core_read", "atomic_search", "conv_add"):
        body = calls[name][0]
        assert (body["team_id"], body["agent_id"], body["user_id"]) == ("t_1", "agt_1", "user-42"), name
    assert calls["conv_add"][0]["session_id"] == "sess-7"
    assert [m["role"] for m in calls["conv_add"][0]["messages"]] == ["user", "assistant"]
    assert ctx["trace"]["memory"]["persona_used"] is True
    assert ctx["trace"]["memory_write"] == "written"


def test_memory_disabled_without_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEMORY_CORE_URL", raising=False)
    svc = MemoryService(Settings())
    assert asyncio.run(svc.bootstrap()) is False
    assert svc.for_user("u", "s") is None
