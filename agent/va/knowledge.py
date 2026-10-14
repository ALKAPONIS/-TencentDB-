"""Demo knowledge base: public European Commission press releases and speeches.

Documents live in ``data/press``. Two formats are accepted:

* ``.md`` files with a one-line JSON front matter block (title, date, type, reference)
* ``.pdf`` files downloaded from the Press Corner (metadata is parsed from the text)

They are split into paragraph windows and indexed with BM25 in a Haystack
``InMemoryDocumentStore``. No embeddings, no external vector database.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from haystack import Document
from haystack.components.retrievers.in_memory import InMemoryBM25Retriever
from haystack.document_stores.in_memory import InMemoryDocumentStore

log = logging.getLogger("va.knowledge")

_REF_RE = re.compile(r"\b(?:IP|SPEECH|STATEMENT|QANDA)/\d\d/\d+\b")
_DATE_RE = re.compile(r"Brussels, (\d{1,2} \w+ \d{4})")
CHUNK_WORDS = 180
OVERLAP_WORDS = 40


def _read_md(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        _, header, body = text.split("---\n", 2)
        return json.loads(header.strip()), body
    return {"title": path.stem}, text


def _read_pdf(path: Path) -> tuple[dict[str, Any], str]:
    from pypdf import PdfReader

    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    start = next((i for i, ln in enumerate(lines) if ln.startswith("Brussels,")), 0)
    header = [
        ln
        for ln in lines[:start]
        if not ln.startswith("European Commission") and ln not in ("Speech", "[Check Against Delivery]")
    ]
    refs = _REF_RE.findall(text)
    date = _DATE_RE.search(text)
    meta = {
        "title": " ".join(header) or path.stem,
        "date": date.group(1) if date else "",
        "type": "Speech" if refs and refs[0].startswith("SPEECH") else "Press release",
        "reference": refs[0] if refs else "",
    }
    return meta, "\n".join(lines[start + 1 :])


def _chunks(body: str) -> list[str]:
    # Re-flow hard-wrapped PDF lines into paragraphs, then window by words.
    words = re.sub(r"\s+", " ", body).strip().split(" ")
    out, i = [], 0
    while i < len(words):
        out.append(" ".join(words[i : i + CHUNK_WORDS]))
        if i + CHUNK_WORDS >= len(words):
            break
        i += CHUNK_WORDS - OVERLAP_WORDS
    return [c for c in out if c]


def press_corner_url(reference: str) -> str:
    """Public Press Corner link for a reference such as IP/25/1307."""
    if not reference:
        return ""
    return f"https://ec.europa.eu/commission/presscorner/detail/en/{reference.lower().replace('/', '_')}"


class KnowledgeBase:
    def __init__(self, directory: str) -> None:
        self.store = InMemoryDocumentStore()
        self.retriever = InMemoryBM25Retriever(document_store=self.store, top_k=4)
        self.sources: list[dict[str, Any]] = []
        self._load(Path(directory))

    def _load(self, directory: Path) -> None:
        docs: list[Document] = []
        if not directory.is_dir():
            log.warning("knowledge directory %s not found; knowledge search will return nothing", directory)
            return
        for path in sorted(directory.iterdir()):
            try:
                if path.suffix.lower() == ".md":
                    meta, body = _read_md(path)
                elif path.suffix.lower() == ".pdf":
                    meta, body = _read_pdf(path)
                else:
                    continue
            except Exception as exc:  # noqa: BLE001
                log.warning("skipping %s: %s", path.name, exc)
                continue
            meta = {k: meta.get(k, "") for k in ("title", "date", "type", "reference")}
            meta["url"] = press_corner_url(meta["reference"])
            self.sources.append(meta)
            for n, chunk in enumerate(_chunks(body)):
                docs.append(Document(content=f"{meta['title']}\n{chunk}", meta={**meta, "chunk": n}))
        if docs:
            self.store.write_documents(docs)
        log.info("knowledge base: %d documents, %d chunks", len(self.sources), len(docs))

    def search(self, query: str, top_k: int = 4) -> list[dict[str, Any]]:
        if not query.strip() or self.store.count_documents() == 0:
            return []
        docs = self.retriever.run(query=query, top_k=top_k)["documents"]
        return [
            {
                "title": d.meta.get("title"),
                "date": d.meta.get("date"),
                "type": d.meta.get("type"),
                "reference": d.meta.get("reference"),
                "url": d.meta.get("url"),
                "excerpt": (d.content or "")[:1400],
                "score": round(d.score or 0.0, 3),
            }
            for d in docs
        ]
