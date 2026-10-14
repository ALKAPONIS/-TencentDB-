# TencentDB Agent Memory - memory-core (MemoryCore gateway, port 8420).
# Pin the image: set the MEMORY_CORE_IMAGE service variable (Railway passes service variables as build args),
# e.g. agentmemory/memory-core:<tag> or agentmemory/memory-core@sha256:<digest>.
ARG MEMORY_CORE_IMAGE=agentmemory/memory-core:latest
FROM ${MEMORY_CORE_IMAGE}

# Our gateway configuration. Secrets are NOT in this file: they come from TDAI_* environment variables,
# which override the file. Data lives on the Railway volume mounted at /data/tdai-memory.
COPY tdai-gateway.yaml /data/config/tdai-gateway.yaml
