"""TencentDB Agent Memory integration.

Thin wrapper around Tencent's official Python SDK (``tencentdb_agent_memory.v3``):

* ``MemoryService`` bootstraps the memory-core instance once (admin user, demo
  team, demo agent) and owns the shared HTTP client.
* ``UserMemory`` is a per-request view bound to one (team, agent, user, session)
  tuple. Every call is time-boxed and never raises: if memory-core is down the
  assistant keeps answering, just without memory.

v3 data plane isolation: every record is keyed by team_id + agent_id + user_id
(session_id optional). The SDK enforces that the three are present.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from .config import Settings

log = logging.getLogger("va.memory")

_USER_ID_RE = re.compile(r"[^A-Za-z0-9_.-]")


def sanitize_id(raw: str | None, prefix: str) -> str:
    """Keep IDs coming from the browser short and free of separators memory-core rejects (e.g. '|')."""
    cleaned = _USER_ID_RE.sub("", (raw or "").strip())[:64]
    return cleaned or f"{prefix}-anonymous"


@dataclass
class MemoryStatus:
    enabled: bool = False
    ready: bool = False
    team_id: str = ""
    agent_id: str = ""
    owner_user_id: str = ""
    fts_available: bool | None = None
    version: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "ready": self.ready,
            "team_id": self.team_id,
            "agent_id": self.agent_id,
            "fts_available": self.fts_available,
            "version": self.version,
            "error": self.error,
        }


@dataclass
class Recall:
    """What the before_run hook found for the current user."""

    persona: str = ""
    atoms: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""

    def is_empty(self) -> bool:
        return not self.persona and not self.atoms


class MemoryService:
    """Process-wide owner of the memory-core connection."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.status = MemoryStatus(enabled=settings.memory_enabled)
        self._base_client: Any = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ bootstrap
    async def bootstrap(self) -> bool:
        """Idempotently prepare admin user, team and agent. Safe to call repeatedly."""
        if not self.status.enabled:
            self.status.error = "MEMORY_CORE_URL / MEMORY_CORE_TOKEN not set"
            return False
        async with self._lock:
            if self.status.ready:
                return True
            try:
                await self._bootstrap_inner()
                self.status.ready = True
                self.status.error = ""
                log.info(
                    "memory-core ready: team=%s agent=%s fts=%s",
                    self.status.team_id,
                    self.status.agent_id,
                    self.status.fts_available,
                )
                return True
            except Exception as exc:  # noqa: BLE001 - surfaced through /api/health
                self.status.error = f"{type(exc).__name__}: {exc}"
                log.warning("memory-core bootstrap failed: %s", self.status.error)
                return False

    async def bootstrap_forever(self, interval_s: float = 15.0) -> None:
        """Background task: keep retrying until memory-core answers (it may boot after us)."""
        while not await self.bootstrap():
            if not self.status.enabled:
                return
            await asyncio.sleep(interval_s)

    async def _bootstrap_inner(self) -> None:
        from tencentdb_agent_memory.v3 import AsyncMemoryClient, AsyncMetadataClient

        s = self.settings
        base = s.memory_url.rstrip("/")
        headers = {
            "Authorization": f"Bearer {s.memory_token}",
            "x-tdai-service-id": s.memory_service_id,
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(timeout=s.memory_timeout_s) as http:
            health = (await http.get(f"{base}/health")).json()
            stores = health.get("stores") or {}
            self.status.fts_available = stores.get("ftsAvailable")
            self.status.version = str(health.get("version") or "")
            if self.status.fts_available is False:
                log.warning("memory-core reports ftsAvailable=false: BM25 keyword recall will return nothing")

            if not s.memory_admin_user_key:
                raise RuntimeError("MEMORY_ADMIN_USER_KEY is not set")
            resp = await http.post(
                f"{base}/v3/internal/meta/user/init-admin",
                headers=headers,
                json={"username": s.memory_admin_username, "user_key": s.memory_admin_user_key},
            )
            if resp.status_code not in (200, 409):
                body = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
                # 409 is reported in-envelope by some versions
                if str(body.get("message", "")).find("already_initialized") < 0:
                    raise RuntimeError(f"init-admin returned HTTP {resp.status_code}: {resp.text[:200]}")

        meta = AsyncMetadataClient(
            s.memory_url,
            s.memory_token,
            s.memory_service_id,
            user_key=s.memory_admin_user_key,
            timeout=s.memory_timeout_s,
        )
        try:
            verified = await meta.verify_auth(s.memory_admin_user_key)
            if not verified.get("valid"):
                raise RuntimeError(
                    "MEMORY_ADMIN_USER_KEY is not valid for this memory-core volume "
                    "(the admin was initialised with a different key)"
                )
            owner_id = verified["user"]["user_id"]
            self.status.owner_user_id = owner_id

            teams = await meta.list_teams(owner_id, pagination={"limit": 100, "offset": 0})
            team = next((t for t in teams.get("items", []) if t.get("name") == s.memory_team_name), None)
            if team is None:
                team = await meta.create_team(
                    {
                        "name": s.memory_team_name,
                        "owner_user_id": owner_id,
                        "description": "Proof of concept: EC virtual assistant on Haystack 3 with long-term memory.",
                    }
                )
            team_id = team["team_id"]

            agents = await meta.list_agents({"team_id": team_id, "limit": 100, "offset": 0})
            agent = next((a for a in agents.get("items", []) if a.get("name") == s.memory_agent_name), None)
            if agent is None:
                agent = await meta.create_agent(
                    {
                        "team_id": team_id,
                        "owner_user_id": owner_id,
                        "name": s.memory_agent_name,
                        "description": "Answers questions about European Commission press releases and speeches.",
                    }
                )
            agent_id = agent["agent_id"]
        finally:
            await meta.close()

        self.status.team_id = team_id
        self.status.agent_id = agent_id
        # A placeholder user; per-request clients are cloned with with_isolation(user_id=...).
        self._base_client = AsyncMemoryClient(
            s.memory_url,
            s.memory_token,
            s.memory_service_id,
            team_id=team_id,
            agent_id=agent_id,
            user_id="bootstrap",
            timeout=s.memory_timeout_s,
        )

    async def close(self) -> None:
        if self._base_client is not None:
            await self._base_client.close()
            self._base_client = None

    # ------------------------------------------------------------------ per user
    def for_user(self, user_id: str, session_id: str | None) -> "UserMemory | None":
        if not self.status.ready or self._base_client is None:
            return None
        client = self._base_client.with_isolation(user_id=user_id, session_id=session_id)
        return UserMemory(client, user_id=user_id, session_id=session_id, timeout_s=self.settings.memory_timeout_s)


class UserMemory:
    """Memory operations for one user. All methods are time-boxed and never raise."""

    def __init__(self, client: Any, *, user_id: str, session_id: str | None, timeout_s: float) -> None:
        self._client = client
        self.user_id = user_id
        self.session_id = session_id
        self._timeout = timeout_s

    async def _call(self, coro: Any, default: Any) -> tuple[Any, str]:
        try:
            return await asyncio.wait_for(coro, timeout=self._timeout), ""
        except Exception as exc:  # noqa: BLE001
            log.warning("memory call failed for user %s: %s", self.user_id, exc)
            return default, f"{type(exc).__name__}: {exc}"

    # -- recall ---------------------------------------------------------------
    async def recall(self, query: str, *, limit: int = 5) -> Recall:
        """L3 persona + L1 atoms matching the query + the latest stable preferences."""
        core_task = self._call(self._client.read_core(), {})
        search_task = self._call(self._client.search_atomic(query[:2000] or "user", limit=limit), {})
        prefs_task = self._call(self._client.query_atomic(type="persona", limit=limit), {})
        instr_task = self._call(self._client.query_atomic(type="instruction", limit=limit), {})
        (core, e1), (hits, e2), (prefs, e3), (instr, e4) = await asyncio.gather(
            core_task, search_task, prefs_task, instr_task
        )
        atoms: dict[str, dict[str, Any]] = {}
        for item in (hits or {}).get("items", []) + (instr or {}).get("items", []) + (prefs or {}).get("items", []):
            if item.get("id") and item["id"] not in atoms:
                atoms[item["id"]] = _slim_atom(item)
        persona = ((core or {}).get("content") or "").strip()
        error = "; ".join(e for e in (e1, e2, e3, e4) if e)
        return Recall(persona=persona, atoms=list(atoms.values())[: limit * 2], error=error)

    async def search(self, query: str, *, limit: int = 5) -> dict[str, Any]:
        """Used by the recall_memory tool: L1 atoms first, raw L0 wording as fallback."""
        hits, err = await self._call(self._client.search_atomic(query[:2000], limit=limit), {})
        atoms = [_slim_atom(i) for i in (hits or {}).get("items", [])]
        messages: list[dict[str, Any]] = []
        if not atoms:
            conv, err2 = await self._call(self._client.search_conversation(query[:2000], limit=limit), {})
            err = err or err2
            messages = [
                {
                    "id": m.get("id"),
                    "role": m.get("role"),
                    "content": (m.get("content") or "")[:500],
                    "session_id": m.get("session_id"),
                }
                for m in (conv or {}).get("messages", [])
            ]
        return {"atoms": atoms, "conversation": messages, "error": err}

    # -- write ------------------------------------------------------------------
    async def add_turn(self, user_text: str, assistant_text: str) -> str:
        messages = [
            {"role": "user", "content": user_text[:8000]},
            {"role": "assistant", "content": assistant_text[:8000]},
        ]
        messages = [m for m in messages if m["content"].strip()]
        if not messages or not self.session_id:
            return "nothing to write"
        _, err = await self._call(self._client.add_conversation(messages, session_id=self.session_id), {})
        return err

    # -- inspect / erase --------------------------------------------------------
    async def snapshot(self) -> dict[str, Any]:
        """Everything the 'What I remember about you' panel shows."""
        (atoms, e1), (core, e2), (scen, e3), (conv, e4) = await asyncio.gather(
            self._call(self._client.query_atomic(limit=100), {}),
            self._call(self._client.read_core(), {}),
            self._call(self._client.list_scenarios(), {}),
            self._call(self._client.count_conversation(), {}),
        )
        return {
            "user_id": self.user_id,
            "atoms": [_slim_atom(a) for a in (atoms or {}).get("items", [])],
            "atoms_total": (atoms or {}).get("total", 0),
            "persona": ((core or {}).get("content") or ""),
            "scenarios": [
                {"path": e.get("path"), "summary": e.get("summary"), "updated_at": e.get("updated_at")}
                for e in (scen or {}).get("entries", [])
                if not str(e.get("path", "")).endswith("/")
            ],
            "conversation_messages": (conv or {}).get("total", 0),
            "error": "; ".join(e for e in (e1, e2, e3, e4) if e),
        }

    async def delete_atoms(self, ids: list[str]) -> dict[str, Any]:
        if not ids:
            return {"deleted_count": 0}
        res, err = await self._call(self._client.delete_atomic(ids), {})
        return {"deleted_count": (res or {}).get("deleted_count", 0), "error": err}

    async def forget_everything(self) -> dict[str, Any]:
        """Erase this user's L0 sessions, L1 atoms, L2 scenarios and L3 persona."""
        report: dict[str, Any] = {"atoms": 0, "sessions": 0, "scenarios": 0, "persona": False, "errors": []}

        # L1
        while True:
            page, err = await self._call(self._client.query_atomic(limit=100), {})
            ids = [a["id"] for a in (page or {}).get("items", []) if a.get("id")]
            if err:
                report["errors"].append(err)
            if not ids:
                break
            res = await self.delete_atoms(ids)
            report["atoms"] += res.get("deleted_count", 0)
            if res.get("error") or res.get("deleted_count", 0) == 0:
                break

        # L0 - collect the user's sessions, then delete them by session id
        sessions: set[str] = set()
        offset = 0
        while True:
            page, err = await self._call(self._client.query_conversation(limit=100, offset=offset), {})
            msgs = (page or {}).get("messages", [])
            sessions.update(m["session_id"] for m in msgs if m.get("session_id"))
            if err or len(msgs) < 100:
                break
            offset += 100
        sess_list = sorted(sessions)
        for i in range(0, len(sess_list), 100):
            _, err = await self._call(self._client.delete_conversation(session_ids=sess_list[i : i + 100]), {})
            if err:
                report["errors"].append(err)
        report["sessions"] = len(sess_list)

        # L2
        scen, err = await self._call(self._client.list_scenarios(), {})
        for entry in (scen or {}).get("entries", []):
            path = entry.get("path") or ""
            if path and not path.endswith("/"):
                _, err2 = await self._call(self._client.rm_scenario(path), {})
                if not err2:
                    report["scenarios"] += 1

        # L3 - overwrite the persona with an empty marker (an empty body is rejected)
        _, err = await self._call(self._client.write_core("(cleared by user)"), {})
        report["persona"] = not err
        return report


def _slim_atom(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "type": item.get("type"),
        "content": (item.get("content") or "")[:600],
        "background": (item.get("background") or "")[:300],
        "score": item.get("score"),
        "updated_at": item.get("updated_at"),
    }
