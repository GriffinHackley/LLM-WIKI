"""Embedding and reranking models.

Models load lazily and only from disk: nothing is downloaded unless the user
runs ``wiki models download``. Names starting with ``fake:`` select small
deterministic stand-ins used by tests and benchmarks.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence

DEFAULT_EMBED_MODEL = "BAAI/bge-small-en-v1.5"
DEFAULT_RERANKER = "jinaai/jina-reranker-v1-turbo-en"  # chosen in docs/model-selection.md
NO_RERANKER = "none"

_QWEN_QUERY = "Instruct: Given a question, retrieve wiki passages that answer the question\nQuery:"


@dataclass(frozen=True)
class EmbeddingSpec:
    query_prefix: str = ""
    document_prefix: str = ""


# Prefixes each model was trained with. fastembed does not add them itself.
EMBEDDING_MODELS: dict[str, EmbeddingSpec] = {
    "BAAI/bge-small-en-v1.5": EmbeddingSpec("Represent this sentence for searching relevant passages: "),
    "nomic-ai/nomic-embed-text-v1.5": EmbeddingSpec("search_query: ", "search_document: "),
    "Qwen/Qwen3-Embedding-0.6B": EmbeddingSpec(_QWEN_QUERY),
    "Qwen/Qwen3-Embedding-0.6B-Q": EmbeddingSpec(_QWEN_QUERY),
}

RERANKERS = (
    "BAAI/bge-reranker-base",
    "Xenova/ms-marco-MiniLM-L-12-v2",
    "Xenova/ms-marco-MiniLM-L-6-v2",
    "jinaai/jina-reranker-v1-turbo-en",
)

FAKE_EMBEDDER = "fake:hash"
FAKE_RERANKER = "fake:overlap"


class ModelUnavailable(Exception):
    pass


class Embedder(Protocol):
    name: str

    @property
    def dims(self) -> int: ...

    def embed_documents(self, texts: Sequence[str]) -> list[Sequence[float]]: ...

    def embed_query(self, text: str) -> Sequence[float]: ...


class Reranker(Protocol):
    name: str

    def score(self, query: str, documents: Sequence[str]) -> list[float]: ...


def load_embedder(name: str, models_dir: Path) -> Embedder:
    if name == FAKE_EMBEDDER:
        return HashEmbedder()
    if name not in EMBEDDING_MODELS:
        raise ModelUnavailable(f"unknown embedding model '{name}'; choose one of {', '.join(EMBEDDING_MODELS)}")
    return FastEmbedEmbedder(name, models_dir)


def load_reranker(name: str, models_dir: Path) -> Reranker | None:
    if name == NO_RERANKER:
        return None
    if name == FAKE_RERANKER:
        return OverlapReranker()
    if name not in RERANKERS:
        raise ModelUnavailable(f"unknown reranker '{name}'; choose one of {', '.join(RERANKERS)} or 'none'")
    return FastEmbedReranker(name, models_dir)


def download(name: str, models_dir: Path, *, reranker: bool) -> None:
    """Fetch a model into ``models_dir``. The only code path that downloads."""
    if name.startswith("fake:") or name == NO_RERANKER:
        return
    models_dir.mkdir(parents=True, exist_ok=True)
    if reranker:
        from fastembed.rerank.cross_encoder import TextCrossEncoder
        TextCrossEncoder(name, cache_dir=str(models_dir))
    else:
        from fastembed import TextEmbedding
        TextEmbedding(name, cache_dir=str(models_dir))


def _quiet_fastembed() -> None:
    """fastembed logs through loguru; missing models are reported by this tool instead."""
    try:
        from loguru import logger
    except ImportError:
        return
    logger.disable("fastembed")


class FastEmbedEmbedder:
    def __init__(self, name: str, models_dir: Path):
        self.name = name
        self.spec = EMBEDDING_MODELS[name]
        self.models_dir = models_dir
        self._model = None

    @property
    def dims(self) -> int:
        from fastembed import TextEmbedding
        return TextEmbedding.get_embedding_size(self.name)

    def _load(self):
        if self._model is None:
            _quiet_fastembed()
            from fastembed import TextEmbedding
            try:
                self._model = TextEmbedding(self.name, cache_dir=str(self.models_dir), local_files_only=True)
            except Exception as exc:  # noqa: BLE001 - fastembed raises assorted errors when files are absent
                raise ModelUnavailable(
                    f"embedding model '{self.name}' is not downloaded; run 'wiki models download'") from exc
        return self._model

    def embed_documents(self, texts: Sequence[str]) -> list[Sequence[float]]:
        model = self._load()
        prefix = self.spec.document_prefix
        return list(model.embed([prefix + text for text in texts], batch_size=32))

    def embed_query(self, text: str) -> Sequence[float]:
        return next(iter(self._load().embed([self.spec.query_prefix + text])))


class FastEmbedReranker:
    def __init__(self, name: str, models_dir: Path):
        self.name = name
        self.models_dir = models_dir
        self._model = None

    def _load(self):
        if self._model is None:
            _quiet_fastembed()
            from fastembed.rerank.cross_encoder import TextCrossEncoder
            try:
                self._model = TextCrossEncoder(self.name, cache_dir=str(self.models_dir), local_files_only=True)
            except Exception as exc:  # noqa: BLE001
                raise ModelUnavailable(f"reranker '{self.name}' is not downloaded; run 'wiki models download'") from exc
        return self._model

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        return [float(value) for value in self._load().rerank(query, list(documents), batch_size=16)]


_TOKEN = re.compile(r"[a-z0-9]+")


def tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class HashEmbedder:
    """Deterministic bag-of-words hashing embedder for tests (not semantic)."""

    name = FAKE_EMBEDDER

    def __init__(self, dims: int = 64):
        self._dims = dims

    @property
    def dims(self) -> int:
        return self._dims

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self._dims
        for token in tokens(text):
            digest = hashlib.blake2b(token.encode(), digest_size=4).digest()
            index = int.from_bytes(digest[:3], "little") % self._dims
            vector[index] += 1.0 if digest[3] & 1 else -1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    def embed_documents(self, texts: Sequence[str]) -> list[Sequence[float]]:
        return [self._vector(text) for text in texts]

    def embed_query(self, text: str) -> Sequence[float]:
        return self._vector(text)


class OverlapReranker:
    """Deterministic token-overlap reranker for tests."""

    name = FAKE_RERANKER

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        wanted = set(tokens(query))
        return [len(wanted & set(tokens(document))) / (len(wanted) or 1) for document in documents]
