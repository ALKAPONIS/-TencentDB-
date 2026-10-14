"""Agent tools.

Each tool that needs request-scoped data (who is the user, what memory client to
use, where to record what happened) reads ``hook_context`` from the Agent's
State through ``inputs_from_state``. That parameter is hidden from the model.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

from haystack.tools import Tool, tool

from . import context
from .knowledge import KnowledgeBase


def _trace(ctx: dict[str, Any]) -> dict[str, Any]:
    return ctx.setdefault("trace", {})


def build_tools(kb: KnowledgeBase) -> list[Tool]:
    @tool(inputs_from_state={"request_id": "request_id"})
    def search_press_releases(
        query: Annotated[str, "Keywords or a short question about Commission press releases or speeches"],
        request_id: str | None = None,
    ) -> str:
        """Search the European Commission press releases and speeches available to this assistant.

        Returns the best matching excerpts with title, date, reference and link. Always use it before
        answering a factual question about Commission announcements, and cite the reference.
        """
        ctx = context.lookup(request_id)
        hits = kb.search(query, top_k=4)
        trace = _trace(ctx)
        trace.setdefault("tools", []).append({"name": "search_press_releases", "query": query, "hits": len(hits)})
        seen = trace.setdefault("sources", [])
        for h in hits:
            if not any(s.get("reference") == h["reference"] for s in seen):
                seen.append({k: h[k] for k in ("title", "date", "reference", "url", "type")})
        if not hits:
            return "No matching press release or speech was found in the knowledge base."
        return json.dumps(hits, ensure_ascii=False)

    @tool(inputs_from_state={"request_id": "request_id"})
    async def recall_memory(
        query: Annotated[str, "What to look for in the long-term memory about this user"],
        request_id: str | None = None,
    ) -> str:
        """Search long-term memory about the current user: preferences, facts they shared, earlier discussions.

        Use it when the user refers to something from a previous conversation ("as I told you",
        "last time", "my usual format") and it is not already in the context you were given.
        """
        ctx = context.lookup(request_id)
        trace = _trace(ctx)
        memory = ctx.get("memory")
        if memory is None:
            trace.setdefault("tools", []).append({"name": "recall_memory", "query": query, "hits": 0})
            return "Long-term memory is not available right now."
        result = await memory.search(query)
        trace.setdefault("tools", []).append(
            {"name": "recall_memory", "query": query, "hits": len(result["atoms"]) + len(result["conversation"])}
        )
        seen_ids: set[str] = ctx.setdefault("seen_memory_ids", set())
        for atom in result["atoms"]:
            if atom.get("id"):
                seen_ids.add(atom["id"])
        trace.setdefault("memory_recalled", []).extend(result["atoms"])
        if not result["atoms"] and not result["conversation"]:
            return "Nothing relevant found in long-term memory."
        return json.dumps(
            {"memories": result["atoms"], "earlier_messages": result["conversation"]},
            ensure_ascii=False,
        )

    @tool(inputs_from_state={"request_id": "request_id"})
    async def forget_memory(
        memory_id: Annotated[str, "The id of a memory item shown to you in this conversation"],
        request_id: str | None = None,
    ) -> str:
        """Delete one long-term memory item about the user, when the user explicitly asks you to forget it.

        Only ids that were shown to you in this conversation (from the user context or recall_memory) can
        be deleted. For erasing everything, tell the user to use the "Forget everything" button.
        """
        ctx = context.lookup(request_id)
        trace = _trace(ctx)
        memory = ctx.get("memory")
        seen_ids: set[str] = ctx.get("seen_memory_ids", set())
        if memory is None:
            return "Long-term memory is not available right now."
        if memory_id not in seen_ids:
            return "Refused: that id was not shown in this conversation. Ask the user which memory to delete."
        res = await memory.delete_atoms([memory_id])
        trace.setdefault("tools", []).append({"name": "forget_memory", "memory_id": memory_id})
        trace.setdefault("memory_deleted", []).append(memory_id)
        if res.get("error"):
            return f"Deletion failed: {res['error']}"
        return f"Deleted {res.get('deleted_count', 0)} memory item."

    return [search_press_releases, recall_memory, forget_memory]
