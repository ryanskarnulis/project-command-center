"""Embeddings client for the shared workspace service (``../embeddings``, #415).

EmbeddingGemma 2 behind a llama.cpp server on 8600, speaking the OpenAI
``/v1/embeddings`` shape: one POST, a list of strings in, a list of vectors
out (768-dim, L2-normalised, so a dot product is the cosine). The server adds
no prompt prefix — the model wants an asymmetric pair, so the client builds
them: :func:`query_text` for the user's words and :func:`document_text` for a
row. Plain httpx, no SDK; the client is injected in tests
(``httpx.MockTransport``).

The service is optional: any failure — refused, slow, a non-200, a body that
doesn't parse — is one :class:`EmbeddingsUnavailable`, which the gather turns
into keyword search alone. Borrowed from chess (``chessapp/embeddings.py``).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import httpx

# The query a run waits on: the service answers in ~10 ms warm, so 0.3 s is
# slack for a busy box and still a cost a person won't notice when the
# service is down and the run falls back to keywords.
GATHER_TIMEOUT = httpx.Timeout(0.3, connect=0.2)
# Warming documents off the run's path: a cold service and a big batch.
BATCH_TIMEOUT = httpx.Timeout(120.0, connect=2.0)
# Documents per request when warming.
BATCH_SIZE = 16


def query_text(query: str) -> str:
    """The user's words as EmbeddingGemma's retrieval query."""
    return f"task: search result | query: {query}"


def document_text(text: str, title: str | None = None) -> str:
    """A row as EmbeddingGemma's retrieval document (``title: none`` when it
    has no separate title)."""
    body = " ".join(text.split())
    return f"title: {title or 'none'} | text: {body}"


class EmbeddingsUnavailable(Exception):
    """No usable vectors: the service is down, slow, or answered nonsense."""


@dataclass(frozen=True)
class Embedded:
    """Vectors in input order, and the model the server says made them."""

    model: str
    vectors: list[list[float]]


class Embedder:
    """``POST {base_url}/embeddings`` for a batch of texts."""

    def __init__(self, base_url: str, client: httpx.Client | None = None) -> None:
        self._url = base_url.rstrip("/") + "/embeddings"
        self._client = client if client is not None else httpx.Client()

    def embed(self, texts: Sequence[str], *, timeout: httpx.Timeout) -> Embedded:
        if not texts:
            raise EmbeddingsUnavailable("nothing to embed")
        try:
            response = self._client.post(
                self._url, json={"input": list(texts)}, timeout=timeout
            )
            response.raise_for_status()
            body = response.json()
            data = sorted(body["data"], key=lambda item: item["index"])
            vectors = [[float(x) for x in item["embedding"]] for item in data]
            model = str(body["model"])
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise EmbeddingsUnavailable(f"{type(exc).__name__}: {exc}") from exc
        if len(vectors) != len(texts) or any(not vector for vector in vectors):
            raise EmbeddingsUnavailable(
                f"asked for {len(texts)} vectors, got {len(vectors)}"
            )
        return Embedded(model=model, vectors=vectors)

    def embed_documents(self, texts: Sequence[str]) -> Embedded:
        """Many documents, :data:`BATCH_SIZE` at a time, one model throughout."""
        model: str | None = None
        vectors: list[list[float]] = []
        for start in range(0, len(texts), BATCH_SIZE):
            batch = self.embed(texts[start : start + BATCH_SIZE], timeout=BATCH_TIMEOUT)
            if model is not None and batch.model != model:
                raise EmbeddingsUnavailable(f"model changed mid-batch: {model} → {batch.model}")
            model = batch.model
            vectors.extend(batch.vectors)
        if model is None:
            raise EmbeddingsUnavailable("nothing to embed")
        return Embedded(model=model, vectors=vectors)
