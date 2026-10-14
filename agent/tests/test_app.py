"""End-to-end through FastAPI/Hayhooks: streaming chat, turn trace, health, shim auth."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from haystack.components.generators.chat import MockChatGenerator
from haystack.dataclasses import ChatMessage, ToolCall


@pytest.fixture(scope="module")
def client():
    import va.assistant as assistant

    replies = iter(
        [
            ChatMessage.from_assistant(
                tool_calls=[ToolCall(tool_name="search_press_releases", arguments={"query": "car recycling cartel fine"})]
            ),
            ChatMessage.from_assistant("The Commission fined 15 car makers and ACEA about €458 million (IP/25/881)."),
        ]
    )
    original = assistant.build_generator
    assistant.build_generator = lambda settings: MockChatGenerator(response_fn=lambda msgs: next(replies))
    import app as app_module

    with TestClient(app_module.app) as c:
        yield c
    assistant.build_generator = original


def test_health(client: TestClient) -> None:
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["knowledge_documents"] == 5
    assert body["memory"]["enabled"] is False  # no MEMORY_CORE_URL in tests


def test_streaming_chat_and_turn_trace(client: TestClient) -> None:
    payload = {
        "model": "ec_assistant",
        "stream": True,
        "user": "browser-abc",
        "metadata": {"session_id": "sess-1", "turn_id": "turn-1"},
        "messages": [{"role": "user", "content": "How much was the ELV cartel fine?"}],
    }
    text = ""
    with client.stream("POST", "/v1/chat/completions", json=payload) as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                chunk = json.loads(line[6:])
                for choice in chunk.get("choices", []):
                    text += (choice.get("delta") or {}).get("content") or ""
    assert "458 million" in text

    trace = client.get("/api/turn/turn-1").json()
    assert trace["status"] == "done"
    assert trace["sources"][0]["reference"] == "IP/25/881"
    assert trace["tools"][0]["name"] == "search_press_releases"


def test_unknown_turn_is_404(client: TestClient) -> None:
    assert client.get("/api/turn/nope").status_code == 404


def test_memory_routes_report_unavailable(client: TestClient) -> None:
    assert client.get("/api/memory", params={"user_id": "x"}).status_code == 503


def test_admin_routes_are_removed(client: TestClient) -> None:
    assert client.post("/deploy_files", json={}).status_code == 404
    assert client.post("/deploy-yaml", json={}).status_code == 404
    assert client.post("/undeploy/ec_assistant").status_code == 404
    assert client.get("/v1/files").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_shim_requires_token(client: TestClient) -> None:
    r = client.post("/llm-shim/v1/chat/completions", json={"messages": []})
    assert r.status_code in (401, 404)
