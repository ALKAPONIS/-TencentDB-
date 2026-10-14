"""In-memory stand-in for TencentDB Agent Memory's memory-core, for local UI work without Docker.

Implements only the v3 endpoints this PoC calls, with a toy "extraction": a user message that
states a preference or a fact about the user becomes an L1 atom immediately. The real service
does this asynchronously with an LLM.

    uvicorn dev.mock_memory_core:app --port 8420
"""

from __future__ import annotations

import re
import time
import uuid
from collections import defaultdict
from typing import Any

from fastapi import FastAPI, Request

app = FastAPI(title="mock memory-core")
STATE: dict[str, Any] = {
    "admin": None,
    "teams": [],
    "agents": [],
    "conv": defaultdict(list),  # (team, agent, user) -> messages
    "atoms": defaultdict(list),
    "core": {},
}
PREF = re.compile(r"\b(prefer|from now on|always|answer me|call me|i am|i'm|i work|my name)\b", re.I)


def ok(data: Any) -> dict[str, Any]:
    return {"code": 0, "message": "ok", "request_id": uuid.uuid4().hex, "data": data}


def key(b: dict[str, Any]) -> tuple[str, str, str]:
    return (b.get("team_id", "default"), b.get("agent_id", "default"), b.get("user_id", "default"))


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "version": "mock", "stores": {"ftsAvailable": True}}


@app.post("/v3/internal/meta/user/init-admin")
async def init_admin(req: Request) -> Any:
    b = await req.json()
    if STATE["admin"]:
        return {"code": 409, "message": "already_initialized"}
    STATE["admin"] = {"user_id": "usr_admin", "user_key": b["user_key"], "username": b["username"]}
    return ok({"user_id": "usr_admin", "user_key": b["user_key"]})


@app.post("/v3/meta/auth/verify")
async def verify(req: Request) -> Any:
    b = await req.json()
    admin = STATE["admin"]
    valid = bool(admin and admin["user_key"] == b.get("user_key"))
    return ok({"valid": valid, "user": {"user_id": admin["user_id"]} if valid else None})


@app.post("/v3/meta/team/list")
def team_list() -> Any:
    return ok({"items": STATE["teams"], "total": len(STATE["teams"])})


@app.post("/v3/meta/team/create")
async def team_create(req: Request) -> Any:
    b = await req.json()
    team = {"team_id": f"t_{len(STATE['teams']) + 1}", **b}
    STATE["teams"].append(team)
    return ok(team)


@app.post("/v3/meta/agent/list")
async def agent_list(req: Request) -> Any:
    b = await req.json()
    items = [a for a in STATE["agents"] if a["team_id"] == b.get("team_id")]
    return ok({"items": items, "total": len(items)})


@app.post("/v3/meta/agent/create")
async def agent_create(req: Request) -> Any:
    b = await req.json()
    agent = {"agent_id": f"agt_{len(STATE['agents']) + 1}", **b}
    STATE["agents"].append(agent)
    return ok(agent)


@app.post("/v3/conversation/add")
async def conv_add(req: Request) -> Any:
    b = await req.json()
    k = key(b)
    ids = []
    for m in b["messages"]:
        mid = uuid.uuid4().hex[:12]
        ids.append(mid)
        STATE["conv"][k].append({"id": mid, "session_id": b.get("session_id"), **m, "timestamp": time.time()})
        if m["role"] == "user" and PREF.search(m["content"]):
            kind = "instruction" if re.search(r"answer|always|prefer|from now on", m["content"], re.I) else "persona"
            STATE["atoms"][k].append(
                {"id": f"rec_{uuid.uuid4().hex[:8]}", "type": kind, "content": m["content"][:300], "version": 1}
            )
    return ok({"accepted_ids": ids, "total_count": len(ids)})


@app.post("/v3/conversation/query")
async def conv_query(req: Request) -> Any:
    b = await req.json()
    msgs = STATE["conv"][key(b)]
    off, lim = b.get("offset", 0), b.get("limit", 20)
    return ok({"messages": msgs[off : off + lim], "total": len(msgs)})


@app.post("/v3/conversation/search")
async def conv_search(req: Request) -> Any:
    b = await req.json()
    words = set(b["query"].lower().split())
    hits = [dict(m, score=1.0) for m in STATE["conv"][key(b)] if words & set(m["content"].lower().split())]
    return ok({"messages": hits[: b.get("limit", 5)]})


@app.post("/v3/conversation/count")
async def conv_count(req: Request) -> Any:
    return ok({"total": len(STATE["conv"][key(await req.json())])})


@app.post("/v3/conversation/delete")
async def conv_delete(req: Request) -> Any:
    b = await req.json()
    k = key(b)
    before = len(STATE["conv"][k])
    sessions = set(b.get("session_ids") or [])
    STATE["conv"][k] = [m for m in STATE["conv"][k] if m["session_id"] not in sessions]
    return ok({"deleted_count": before - len(STATE["conv"][k])})


@app.post("/v3/atomic/search")
async def atomic_search(req: Request) -> Any:
    b = await req.json()
    words = {w for w in re.findall(r"\w+", b["query"].lower()) if len(w) > 3}
    hits = [dict(a, score=0.8) for a in STATE["atoms"][key(b)] if words & set(re.findall(r"\w+", a["content"].lower()))]
    return ok({"items": hits[: b.get("limit", 5)]})


@app.post("/v3/atomic/query")
async def atomic_query(req: Request) -> Any:
    b = await req.json()
    items = [a for a in STATE["atoms"][key(b)] if not b.get("type") or a["type"] == b["type"]]
    return ok({"items": items[: b.get("limit", 20)], "total": len(items)})


@app.post("/v3/atomic/delete")
async def atomic_delete(req: Request) -> Any:
    b = await req.json()
    k = key(b)
    before = len(STATE["atoms"][k])
    STATE["atoms"][k] = [a for a in STATE["atoms"][k] if a["id"] not in set(b["ids"])]
    return ok({"deleted_count": before - len(STATE["atoms"][k])})


@app.post("/v3/core/read")
async def core_read(req: Request) -> Any:
    return ok({"content": STATE["core"].get(key(await req.json()))})


@app.post("/v3/core/write")
async def core_write(req: Request) -> Any:
    b = await req.json()
    STATE["core"][key(b)] = b["content"]
    return ok({"version": "v1"})


@app.post("/v3/scenario/ls")
def scenario_ls() -> Any:
    return ok({"entries": [], "total": 0})


@app.post("/v3/scenario/rm")
def scenario_rm() -> Any:
    return ok(None)
