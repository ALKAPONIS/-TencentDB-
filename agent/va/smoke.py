"""Live smoke test, run inside the agent container after the first deploy:

    railway run --service agent python -m va.smoke      # or: railway ssh, then the same command

Checks, with real credentials:
1. AI@EC Platform: both models are listed for the token.
2. gpt-5.1 on /responses answers and can call a tool.
3. Mistral on /chat/completions answers; and whether it honours `tools` (tells you if the shim is needed).
4. memory-core: /health, FTS5 availability, bootstrap (admin, team, agent), a write/read round trip.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid

import httpx

from .config import settings
from .memory import MemoryService

OK, FAIL, WARN = "\033[32mOK\033[0m", "\033[31mFAIL\033[0m", "\033[33mWARN\033[0m"
TOOL = {
    "type": "function",
    "function": {
        "name": "get_reference",
        "description": "Return the press release reference for a topic",
        "parameters": {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]},
    },
}


def report(ok: bool | None, label: str, detail: str = "") -> bool:
    print(f"[{OK if ok else (WARN if ok is None else FAIL)}] {label}" + (f" - {detail}" if detail else ""))
    return bool(ok) or ok is None


async def check_llm(http: httpx.AsyncClient) -> bool:
    base = settings.aiec_base_url.rstrip("/")
    h = {"Authorization": f"Bearer {settings.aiec_api_key}"}
    good = True

    r = await http.get(f"{base}/models", headers=h)
    ids = {m.get("id") for m in r.json().get("data", [])} if r.status_code == 200 else set()
    for model in {settings.assistant_model, settings.shim_plain_model, settings.shim_tools_model}:
        good &= report(model in ids, f"AI@EC lists model {model}", f"HTTP {r.status_code}")

    r = await http.post(
        f"{base}/responses",
        headers=h,
        json={
            "model": settings.assistant_model,
            "input": "Call get_reference for the topic 'farmers'.",
            "tools": [{"type": "function", **TOOL["function"]}],
            "store": False,
        },
    )
    called = r.status_code == 200 and any(o.get("type") == "function_call" for o in r.json().get("output", []))
    good &= report(called, f"{settings.assistant_model} tool calling on /responses", f"HTTP {r.status_code}")

    r = await http.post(
        f"{base}/chat/completions",
        headers=h,
        json={
            "model": settings.shim_plain_model,
            "messages": [{"role": "user", "content": "Call get_reference for the topic 'farmers'."}],
            "tools": [TOOL],
            "max_tokens": 200,
        },
    )
    good &= report(r.status_code == 200, f"{settings.shim_plain_model} on /chat/completions", f"HTTP {r.status_code}")
    if r.status_code == 200:
        msg = r.json()["choices"][0]["message"]
        report(
            None if not msg.get("tool_calls") else True,
            "Mistral honours `tools` on /chat/completions",
            "yes: the shim is optional" if msg.get("tool_calls") else "no: keep memory-core on the shim (default)",
        )
    return good


async def check_memory() -> bool:
    svc = MemoryService(settings)
    ok = await svc.bootstrap()
    report(ok, "memory-core bootstrap (admin, team, agent)", svc.status.error)
    if not ok:
        return False
    report(bool(svc.status.fts_available), "SQLite FTS5 available (BM25 recall)", str(svc.status.fts_available))
    user = f"smoke-{uuid.uuid4().hex[:8]}"
    mem = svc.for_user(user, f"{user}-s1")
    assert mem is not None
    err = await mem.add_turn("Smoke test: I prefer answers in Greek.", "Noted.")
    good = report(not err, "write a turn to L0", err)
    snap = await mem.snapshot()
    good &= report(snap["conversation_messages"] >= 1, "read it back", json.dumps({"l0": snap["conversation_messages"]}))
    report(None, "L1 extraction runs asynchronously", "check /api/memory for this user in a few minutes: " + user)
    await svc.close()
    return good


async def main() -> int:
    if not settings.aiec_api_key:
        report(False, "AIEC_API_KEY is set")
        return 1
    async with httpx.AsyncClient(timeout=120) as http:
        llm_ok = await check_llm(http)
    mem_ok = await check_memory()
    return 0 if llm_ok and mem_ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
