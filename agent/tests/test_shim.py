"""The chat/completions -> AI@EC shim used by memory-core."""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from va.config import Settings
from va.llm_shim import build_shim_router, chat_to_responses, responses_to_chat

AIEC = "https://aiec.test/ecgpt/v1"


@pytest.fixture
def shim(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AIEC_BASE_URL", AIEC)
    monkeypatch.setenv("LLM_SHIM_TOKEN", "shim-secret")
    app = FastAPI()
    app.include_router(build_shim_router(Settings()))
    return TestClient(app)


TOOL_REQUEST = {
    "model": "anything",
    "max_tokens": 4096,
    "messages": [
        {"role": "system", "content": "You maintain scene files."},
        {"role": "user", "content": "Update the scene."},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "read", "arguments": '{"path":"a.md"}'}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "# Scene A"},
    ],
    "tools": [{"type": "function", "function": {"name": "write", "description": "Write a file", "parameters": {"type": "object"}}}],
}


def test_translation_to_responses() -> None:
    req = chat_to_responses(TOOL_REQUEST, "gpt-5.1")
    assert req["instructions"] == "You maintain scene files."
    assert req["input"][1] == {"type": "function_call", "call_id": "call_1", "name": "read", "arguments": '{"path":"a.md"}'}
    assert req["input"][2] == {"type": "function_call_output", "call_id": "call_1", "output": "# Scene A"}
    assert req["tools"][0]["name"] == "write" and req["max_output_tokens"] == 4096 and req["store"] is False


def test_translation_back_to_chat() -> None:
    resp = {
        "id": "resp_1",
        "status": "completed",
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "function_call", "call_id": "call_9", "name": "write", "arguments": '{"path":"b.md"}'},
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }
    out = responses_to_chat(resp, "gpt-5.1")
    choice = out["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    assert choice["message"]["tool_calls"][0]["function"]["name"] == "write"
    assert out["usage"]["total_tokens"] == 15


def test_shim_rejects_bad_token(shim: TestClient) -> None:
    r = shim.post("/llm-shim/v1/chat/completions", json={}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


@respx.mock
def test_tool_requests_go_to_responses_api(shim: TestClient) -> None:
    route = respx.post(f"{AIEC}/responses").respond(
        200,
        json={"id": "r", "status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}]},
    )
    r = shim.post("/llm-shim/v1/chat/completions", json=TOOL_REQUEST, headers={"Authorization": "Bearer shim-secret"})
    assert r.status_code == 200 and r.json()["choices"][0]["message"]["content"] == "done"
    sent = json.loads(route.calls[0].request.content)
    assert sent["model"] == "gpt-5.1"


@respx.mock
def test_plain_requests_go_to_mistral_chat(shim: TestClient) -> None:
    route = respx.post(f"{AIEC}/chat/completions").respond(
        200, json={"id": "c", "choices": [{"index": 0, "message": {"role": "assistant", "content": "[]"}, "finish_reason": "stop"}]}
    )
    body = {"model": "x", "messages": [{"role": "user", "content": "extract"}], "max_completion_tokens": 512, "seed": 1}
    r = shim.post("/llm-shim/v1/chat/completions", json=body, headers={"Authorization": "Bearer shim-secret"})
    assert r.status_code == 200
    sent = json.loads(route.calls[0].request.content)
    assert sent["model"] == "mistral-small-3.2-24b-instruct-2506"
    assert sent["max_tokens"] == 512 and "seed" not in sent and "max_completion_tokens" not in sent


@respx.mock
def test_upstream_errors_are_passed_through(shim: TestClient) -> None:
    respx.post(f"{AIEC}/chat/completions").mock(return_value=httpx.Response(429, json={"error": {"message": "quota"}}))
    r = shim.post(
        "/llm-shim/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "x"}]},
        headers={"Authorization": "Bearer shim-secret"},
    )
    assert r.status_code == 429
