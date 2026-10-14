"""OpenAI chat/completions shim used by memory-core to reach the AI@EC Platform.

Why it exists: memory-core calls ``/chat/completions`` through an OpenAI-compatible
client. Its L1 extraction is plain text, but its L2 scenario and L3 persona steps use
tool calling (sandboxed file read/write). The AI@EC Platform forwards only a
whitelist of parameters on ``/chat/completions`` and ``tools`` is not on it, so those
steps would silently fail. The Responses API is not filtered.

Routing:
* no ``tools`` in the request -> AI@EC ``/chat/completions`` with ``SHIM_PLAIN_MODEL``
  (Mistral by default; ``max_completion_tokens`` is mapped to ``max_tokens``)
* ``tools`` present -> AI@EC ``/responses`` with ``SHIM_TOOLS_MODEL`` (gpt-5.1),
  translated in both directions

memory-core authenticates to the shim with ``LLM_SHIM_TOKEN`` (its ``TDAI_LLM_API_KEY``).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import Settings

log = logging.getLogger("va.shim")

MISTRAL_ALLOWED = {"temperature", "top_p", "max_tokens", "frequency_penalty", "stop", "n", "logprobs", "top_logprobs"}


# ----------------------------------------------------------------------------- translation
def chat_to_responses(body: dict[str, Any], model: str) -> dict[str, Any]:
    """Translate an OpenAI chat/completions request with tools into a Responses API request."""
    instructions: list[str] = []
    items: list[dict[str, Any]] = []
    for m in body.get("messages", []):
        role = m.get("role")
        content = m.get("content")
        if isinstance(content, list):
            content = "\n".join(p.get("text", "") for p in content if isinstance(p, dict))
        content = content or ""
        if role in ("system", "developer"):
            instructions.append(content)
        elif role == "user":
            items.append({"role": "user", "content": content})
        elif role == "assistant":
            if content:
                items.append({"role": "assistant", "content": content})
            for tc in m.get("tool_calls") or []:
                fn = tc.get("function", {})
                items.append(
                    {
                        "type": "function_call",
                        "call_id": tc.get("id"),
                        "name": fn.get("name"),
                        "arguments": fn.get("arguments") or "{}",
                    }
                )
        elif role == "tool":
            items.append({"type": "function_call_output", "call_id": m.get("tool_call_id"), "output": content})

    out: dict[str, Any] = {"model": model, "input": items, "store": False}
    if instructions:
        out["instructions"] = "\n\n".join(instructions)
    tools = []
    for t in body.get("tools") or []:
        fn = t.get("function", {})
        tools.append(
            {
                "type": "function",
                "name": fn.get("name"),
                "description": fn.get("description", ""),
                "parameters": fn.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    if tools:
        out["tools"] = tools
    tool_choice = body.get("tool_choice")
    if isinstance(tool_choice, str):
        out["tool_choice"] = tool_choice
    elif isinstance(tool_choice, dict) and tool_choice.get("function"):
        out["tool_choice"] = {"type": "function", "name": tool_choice["function"].get("name")}
    max_tokens = body.get("max_completion_tokens") or body.get("max_tokens")
    if max_tokens:
        out["max_output_tokens"] = int(max_tokens)
    if body.get("parallel_tool_calls") is not None:
        out["parallel_tool_calls"] = body["parallel_tool_calls"]
    return out


def responses_to_chat(resp: dict[str, Any], model: str) -> dict[str, Any]:
    """Translate a Responses API result into an OpenAI chat.completion object."""
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for item in resp.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") in ("output_text", "text"):
                    text_parts.append(c.get("text", ""))
        elif item.get("type") == "function_call":
            tool_calls.append(
                {
                    "id": item.get("call_id") or item.get("id"),
                    "type": "function",
                    "function": {"name": item.get("name"), "arguments": item.get("arguments") or "{}"},
                }
            )
    message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts) or None}
    if tool_calls:
        message["tool_calls"] = tool_calls
    usage = resp.get("usage") or {}
    status = resp.get("status")
    finish = "tool_calls" if tool_calls else ("length" if status == "incomplete" else "stop")
    return {
        "id": "chatcmpl-" + str(resp.get("id") or uuid.uuid4().hex),
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
    }


def plain_chat_body(body: dict[str, Any], model: str) -> dict[str, Any]:
    """Prepare a tool-free chat/completions request for a Mistral model on the AI@EC Platform."""
    out = {"model": model, "messages": body.get("messages", []), "stream": bool(body.get("stream"))}
    if body.get("max_completion_tokens") and not body.get("max_tokens"):
        out["max_tokens"] = body["max_completion_tokens"]
    for k in MISTRAL_ALLOWED:
        if k in body and k not in out:
            out[k] = body[k]
    return out


def _completion_as_sse(completion: dict[str, Any]) -> list[bytes]:
    """Emit a finished completion as a minimal SSE stream (for clients that asked for stream=true)."""
    choice = completion["choices"][0]
    delta: dict[str, Any] = {"role": "assistant", "content": choice["message"].get("content") or ""}
    if choice["message"].get("tool_calls"):
        delta["tool_calls"] = [{"index": i, **tc} for i, tc in enumerate(choice["message"]["tool_calls"])]
    base = {k: completion[k] for k in ("id", "created", "model")}
    first = {**base, "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
    last = {
        **base,
        "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}],
        "usage": completion.get("usage"),
    }
    return [f"data: {json.dumps(first)}\n\n".encode(), f"data: {json.dumps(last)}\n\n".encode(), b"data: [DONE]\n\n"]


# ----------------------------------------------------------------------------- router
def build_shim_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/llm-shim/v1", tags=["llm-shim"])

    def _auth(authorization: str | None) -> None:
        expected = settings.shim_token
        if not settings.shim_enabled or not expected:
            raise HTTPException(status_code=404, detail="shim disabled")
        if (authorization or "") != f"Bearer {expected}":
            raise HTTPException(status_code=401, detail="invalid shim token")

    def _upstream_headers() -> dict[str, str]:
        return {"Authorization": f"Bearer {settings.aiec_api_key}", "Content-Type": "application/json"}

    @router.get("/models")
    async def models(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        _auth(authorization)
        return {
            "object": "list",
            "data": [
                {"id": settings.shim_plain_model, "object": "model", "owned_by": "aiec"},
                {"id": settings.shim_tools_model, "object": "model", "owned_by": "aiec"},
            ],
        }

    @router.post("/chat/completions")
    async def chat_completions(request: Request, authorization: str | None = Header(default=None)) -> Any:
        _auth(authorization)
        body = await request.json()
        base = settings.aiec_base_url.rstrip("/")
        async with httpx.AsyncClient(timeout=300) as http:
            if body.get("tools"):
                req = chat_to_responses(body, settings.shim_tools_model)
                r = await http.post(f"{base}/responses", headers=_upstream_headers(), json=req)
                if r.status_code >= 400:
                    log.warning("shim /responses upstream %s: %s", r.status_code, r.text[:300])
                    return JSONResponse(status_code=r.status_code, content=_error_body(r))
                completion = responses_to_chat(r.json(), settings.shim_tools_model)
                if body.get("stream"):
                    return StreamingResponse(iter(_completion_as_sse(completion)), media_type="text/event-stream")
                return completion

            req = plain_chat_body(body, settings.shim_plain_model)
            if req["stream"]:
                async def relay() -> Any:
                    async with httpx.AsyncClient(timeout=300) as s_http:
                        async with s_http.stream(
                            "POST", f"{base}/chat/completions", headers=_upstream_headers(), json=req
                        ) as resp:
                            async for chunk in resp.aiter_raw():
                                yield chunk

                return StreamingResponse(relay(), media_type="text/event-stream")
            r = await http.post(f"{base}/chat/completions", headers=_upstream_headers(), json=req)
            if r.status_code >= 400:
                log.warning("shim /chat/completions upstream %s: %s", r.status_code, r.text[:300])
            return JSONResponse(status_code=r.status_code, content=_error_body(r) if r.status_code >= 400 else r.json())

    return router


def _error_body(r: httpx.Response) -> dict[str, Any]:
    try:
        return r.json()
    except ValueError:
        return {"error": {"message": r.text[:500], "type": "upstream_error", "code": r.status_code}}
