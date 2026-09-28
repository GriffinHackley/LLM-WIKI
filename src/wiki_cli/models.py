"""Embedding and reranking models.

Models load lazily and only from disk: nothing is downloaded unless the user
runs ``wiki models download``. Names starting with ``fake:`` select small
deterministic stand-ins used by tests and benchmarks.

fastembed is used only to download. Inference runs the downloaded ONNX files
directly with onnxruntime and tokenizers, reproducing fastembed's tokenization,
pooling and normalization: importing fastembed costs about 0.5 s per command,
mostly for download and image code that search never uses.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
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
    repo: str  # Hugging Face repo fastembed downloads the ONNX export from
    model_file: str
    dims: int
    pooling: str  # "cls", "mean" or "last", as fastembed pools this model
    normalize: bool
    query_prefix: str = ""
    document_prefix: str = ""


# Files, pooling and normalization match fastembed 0.8.1's description of each model;
# the prefixes are the ones each model was trained with (fastembed does not add them).
EMBEDDING_MODELS: dict[str, EmbeddingSpec] = {
    "BAAI/bge-small-en-v1.5": EmbeddingSpec(
        "Qdrant/bge-small-en-v1.5-onnx-Q", "model_optimized.onnx", 384, "cls", True,
        "Represent this sentence for searching relevant passages: "),
    "nomic-ai/nomic-embed-text-v1.5": EmbeddingSpec(
        "nomic-ai/nomic-embed-text-v1.5", "onnx/model.onnx", 768, "mean", False, "search_query: ", "search_document: "),
    "Qwen/Qwen3-Embedding-0.6B": EmbeddingSpec(
        "Qdrant/Qwen3-Embedding-0.6B-onnx", "onnx/model.onnx", 1024, "last", True, _QWEN_QUERY),
    "Qwen/Qwen3-Embedding-0.6B-Q": EmbeddingSpec(
        "Qdrant/Qwen3-Embedding-0.6B-onnx", "onnx/model_quantized.onnx", 1024, "last", True, _QWEN_QUERY),
}

RERANKERS: dict[str, str] = {  # name -> ONNX file in the Hugging Face repo of the same name
    "BAAI/bge-reranker-base": "onnx/model.onnx",
    "Xenova/ms-marco-MiniLM-L-12-v2": "onnx/model.onnx",
    "Xenova/ms-marco-MiniLM-L-6-v2": "onnx/model.onnx",
    "jinaai/jina-reranker-v1-turbo-en": "onnx/model.onnx",
}

FAKE_EMBEDDER = "fake:hash"
FAKE_RERANKER = "fake:overlap"


# Windows without Developer Mode cannot symlink; the Hugging Face cache still works.
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


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
    return OnnxEmbedder(name, models_dir)


def load_reranker(name: str, models_dir: Path) -> Reranker | None:
    if name == NO_RERANKER:
        return None
    if name == FAKE_RERANKER:
        return OverlapReranker()
    if name not in RERANKERS:
        raise ModelUnavailable(f"unknown reranker '{name}'; choose one of {', '.join(RERANKERS)} or 'none'")
    return OnnxReranker(name, models_dir)


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


def is_downloaded(name: str, models_dir: Path, *, reranker: bool) -> bool:
    """True when the model loads without downloading (built-in fakes and 'none' always do)."""
    if name.startswith("fake:") or name == NO_RERANKER:
        return True
    if reranker:
        return name in RERANKERS and _snapshot(models_dir, name, RERANKERS[name]) is not None
    spec = EMBEDDING_MODELS.get(name)
    return spec is not None and _snapshot(models_dir, spec.repo, spec.model_file) is not None


def _quiet_fastembed() -> None:
    """fastembed logs through loguru; missing models are reported by this tool instead."""
    try:
        from loguru import logger
    except ImportError:
        return
    logger.disable("fastembed")


_TOKENIZER_FILES = ("tokenizer.json", "tokenizer_config.json")


def _snapshot(models_dir: Path, repo: str, model_file: str) -> Path | None:
    """The downloaded snapshot of ``repo`` holding ``model_file``, in the Hugging Face
    cache layout fastembed downloads into."""
    base = models_dir / ("models--" + repo.replace("/", "--"))
    candidates = []
    ref = base / "refs" / "main"
    if ref.is_file():
        candidates.append(base / "snapshots" / ref.read_text(encoding="utf-8").strip())
    if (base / "snapshots").is_dir():
        candidates.extend(sorted((base / "snapshots").iterdir()))
    for snapshot in candidates:
        if all((snapshot / name).is_file() for name in (model_file, *_TOKENIZER_FILES)):
            return snapshot
    return None


def _positive(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= 2**62 else None


def _load_tokenizer(snapshot: Path):
    """Truncation, special tokens and batch-longest padding set up as fastembed 0.8.1 does."""
    from tokenizers import AddedToken, Tokenizer

    def read(name: str) -> dict:
        path = snapshot / name
        return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    config, tokenizer_config = read("config.json"), read("tokenizer_config.json")
    limits = [value for value in (_positive(tokenizer_config.get("model_max_length")),
                                  _positive(tokenizer_config.get("max_length"))) if value]
    if not limits:
        raise ModelUnavailable(f"no maximum context length in {snapshot / 'tokenizer_config.json'}")
    tokenizer = Tokenizer.from_file(str(snapshot / "tokenizer.json"))
    tokenizer.enable_truncation(max_length=min(limits))
    for value in read("special_tokens_map.json").values():
        for token in value if isinstance(value, list) else [value]:
            tokenizer.add_special_tokens([AddedToken(**token) if isinstance(token, dict) else token])
    padding = tokenizer.padding or {}
    pad_token = padding.get("pad_token") or tokenizer_config.get("pad_token")
    pad_id = padding.get("pad_id", config.get("pad_token_id"))
    if pad_id is None and pad_token is not None:
        pad_id = tokenizer.token_to_id(pad_token)
    if pad_token is None or pad_id is None:
        raise ModelUnavailable(f"no pad token for the model in {snapshot}")
    tokenizer.enable_padding(direction=padding.get("direction", "right"), pad_id=pad_id,
                             pad_type_id=padding.get("pad_type_id", 0), pad_token=pad_token,
                             pad_to_multiple_of=padding.get("pad_to_multiple_of"), length=None)
    return tokenizer


class _OnnxModel:
    """An ONNX session and its tokenizer, loaded on first use."""

    kind = "model"

    def __init__(self, name: str, models_dir: Path, repo: str, model_file: str):
        self.name = name
        self.models_dir = models_dir
        self._repo = repo
        self._model_file = model_file
        self._session = None
        self._tokenizer = None
        self._inputs: set[str] = set()

    def _load(self):
        if self._session is None:
            snapshot = _snapshot(self.models_dir, self._repo, self._model_file)
            if snapshot is None:
                raise ModelUnavailable(f"{self.kind} '{self.name}' is not downloaded; run 'wiki models download'")
            import onnxruntime as ort
            options = ort.SessionOptions()
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            self._tokenizer = _load_tokenizer(snapshot)
            self._session = ort.InferenceSession(str(snapshot / self._model_file), sess_options=options,
                                                 providers=["CPUExecutionProvider"])
            self._inputs = {node.name for node in self._session.get_inputs()}
        return self._session

    def _run(self, texts, *, token_types: bool):
        """Run one batch; returns the first output and the attention mask."""
        import numpy as np
        session = self._load()
        encodings = self._tokenizer.encode_batch(texts)
        ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        feed = {"input_ids": ids}
        if "attention_mask" in self._inputs:
            feed["attention_mask"] = mask
        if "token_type_ids" in self._inputs:  # sentence pairs keep their segment ids; single texts use zeros
            feed["token_type_ids"] = (np.array([e.type_ids for e in encodings], dtype=np.int64)
                                      if token_types else np.zeros_like(ids))
        return session.run(None, feed)[0], mask


class OnnxEmbedder(_OnnxModel):
    kind = "embedding model"

    def __init__(self, name: str, models_dir: Path):
        self.spec = EMBEDDING_MODELS[name]
        super().__init__(name, models_dir, self.spec.repo, self.spec.model_file)

    @property
    def dims(self) -> int:
        return self.spec.dims

    def _embed(self, texts: list[str], batch_size: int = 32) -> list[Sequence[float]]:
        import numpy as np
        vectors: list[Sequence[float]] = []
        for start in range(0, len(texts), batch_size):
            output, mask = self._run(texts[start:start + batch_size], token_types=False)
            if self.spec.pooling == "cls":
                pooled = output[:, 0] if output.ndim == 3 else output
            elif self.spec.pooling == "mean":
                weights = mask[..., None]
                pooled = (output * weights).sum(axis=1) / np.maximum(weights.sum(axis=1), 1e-9)
            else:  # the last non-padding token, whichever side the tokenizer pads on
                last = mask.shape[1] - 1 - np.argmax(mask[:, ::-1], axis=1)
                pooled = output[np.arange(output.shape[0]), last]
            if self.spec.normalize:
                pooled = pooled / np.maximum(np.linalg.norm(pooled, axis=1, keepdims=True), 1e-12)
            vectors.extend(pooled)
        return vectors

    def embed_documents(self, texts: Sequence[str]) -> list[Sequence[float]]:
        prefix = self.spec.document_prefix
        return self._embed([prefix + text for text in texts])

    def embed_query(self, text: str) -> Sequence[float]:
        return self._embed([self.spec.query_prefix + text])[0]


class OnnxReranker(_OnnxModel):
    kind = "reranker"

    def __init__(self, name: str, models_dir: Path):
        super().__init__(name, models_dir, name, RERANKERS[name])

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        scores: list[float] = []
        for start in range(0, len(documents), 16):
            pairs = [(query, document) for document in documents[start:start + 16]]
            output, _ = self._run(pairs, token_types=True)
            scores.extend(float(value) for value in output[:, 0])
        return scores


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
