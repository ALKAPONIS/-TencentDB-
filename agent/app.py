"""Agent service entry point.

One FastAPI app:
* Hayhooks' OpenAI-compatible ``/v1/chat/completions`` (``model: "ec_assistant"``,
  the pipeline in ``pipelines/ec_assistant``); Hayhooks' deploy/file routes are removed
* ``/api/*`` routes for the eUI front end
* ``/llm-shim/v1/*`` used by memory-core to reach the AI@EC Platform

Run: ``uvicorn app:app --host 0.0.0.0 --port $PORT``
"""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress
from pathlib import Path

# Hayhooks reads its settings from the environment at import time.
os.environ.setdefault("HAYHOOKS_PIPELINES_DIR", str(Path(__file__).parent / "pipelines"))
os.environ.setdefault("HAYSTACK_TELEMETRY_ENABLED", "False")

from fastapi import Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from hayhooks.server.app import create_app  # noqa: E402

from va.api import router as api_router  # noqa: E402
from va.llm_shim import build_shim_router  # noqa: E402
from va.runtime import runtime  # noqa: E402

logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))

app = create_app()

# The service is only reachable on Railway's private network, but drop Hayhooks' admin and
# file routes anyway: nobody should be able to deploy code or upload files through it.
_BLOCKED_PREFIXES = ("/deploy", "/undeploy", "/files", "/v1/files", "/draw", "/traces", "/docs", "/openapi")


@app.middleware("http")
async def block_admin_routes(request: Request, call_next):  # type: ignore[no-untyped-def]
    if request.url.path.startswith(_BLOCKED_PREFIXES):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    return await call_next(request)


app.include_router(api_router)
app.include_router(build_shim_router(runtime.settings))

_hayhooks_lifespan = app.router.lifespan_context


@asynccontextmanager
async def lifespan(a):  # type: ignore[no-untyped-def]
    async with _hayhooks_lifespan(a):
        bootstrap = asyncio.create_task(runtime.memory.bootstrap_forever())
        try:
            yield
        finally:
            bootstrap.cancel()
            with suppress(asyncio.CancelledError):
                await bootstrap
            await runtime.memory.close()


app.router.lifespan_context = lifespan
