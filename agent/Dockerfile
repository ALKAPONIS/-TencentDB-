FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HAYSTACK_TELEMETRY_ENABLED=False \
    HAYHOOKS_PIPELINES_DIR=/app/pipelines \
    PORT=8000

# git is needed to install the Tencent SDK from its tagged GitHub release
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY va ./va
COPY pipelines ./pipelines
COPY data ./data
COPY app.py .

RUN useradd --create-home appuser && mkdir -p /tmp/tool_results && chown appuser /tmp/tool_results
USER appuser

EXPOSE 8000
CMD ["sh", "-c", "uvicorn app:app --host :: --port ${PORT}"]
