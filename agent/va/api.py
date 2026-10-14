"""Extra REST routes for the eUI front end (health, turn trace, memory panel, quota)."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .memory import sanitize_id
from .runtime import runtime

router = APIRouter(prefix="/api", tags=["ec-va"])


def _jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj, default=lambda o: sorted(o) if isinstance(o, set) else str(o)))


@router.get("/health")
async def health() -> dict[str, Any]:
    s = runtime.settings
    return {
        "status": "ok",
        "assistant_model": s.assistant_model,
        "llm_configured": bool(s.aiec_api_key),
        "knowledge_documents": len(runtime.knowledge.sources),
        "memory": runtime.memory.status.as_dict(),
    }


@router.get("/sources")
async def sources() -> list[dict[str, Any]]:
    return runtime.knowledge.sources


@router.get("/turn/{turn_id}")
async def turn(turn_id: str) -> dict[str, Any]:
    trace = runtime.turns.get(sanitize_id(turn_id, "turn"))
    if trace is None:
        raise HTTPException(status_code=404, detail="unknown or expired turn")
    return _jsonable(trace)


def _user_memory(user_id: str):
    memory = runtime.memory.for_user(sanitize_id(user_id, "user"), None)
    if memory is None:
        raise HTTPException(status_code=503, detail=runtime.memory.status.error or "memory not ready")
    return memory


@router.get("/memory")
async def memory_snapshot(user_id: str) -> dict[str, Any]:
    return _jsonable(await _user_memory(user_id).snapshot())


@router.delete("/memory/{atom_id}")
async def memory_delete(atom_id: str, user_id: str) -> dict[str, Any]:
    return await _user_memory(user_id).delete_atoms([atom_id])


class ForgetRequest(BaseModel):
    user_id: str


@router.post("/memory/forget-all")
async def memory_forget_all(req: ForgetRequest) -> dict[str, Any]:
    return await _user_memory(req.user_id).forget_everything()


@router.get("/quota")
async def quota() -> dict[str, Any]:
    """Token consumption on the AI@EC Platform for the models this demo uses."""
    s = runtime.settings
    if not s.aiec_api_key:
        return {"error": "AIEC_API_KEY not set", "models": []}
    models = list(dict.fromkeys([s.assistant_model, s.shim_plain_model, s.shim_tools_model]))
    base = s.aiec_base_url.rstrip("/")
    headers = {"Authorization": f"Bearer {s.aiec_api_key}"}

    async def one(http: httpx.AsyncClient, model: str) -> dict[str, Any]:
        try:
            r = await http.get(f"{base}/quota-consumption/{model}", headers=headers)
            if r.status_code >= 400:
                return {"model": model, "error": f"HTTP {r.status_code}"}
            return {"model": model, **r.json()}
        except httpx.HTTPError as exc:
            return {"model": model, "error": type(exc).__name__}

    async with httpx.AsyncClient(timeout=10) as http:
        results = await asyncio.gather(*(one(http, m) for m in models))
    return {"models": results}
