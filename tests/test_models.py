import sys

import pytest

from wiki_cli.models import ModelUnavailable, _snapshot, load_embedder, load_reranker


def make_snapshot(models_dir, repo, commit, model_file):
    snapshot = models_dir / ("models--" + repo.replace("/", "--")) / "snapshots" / commit
    (snapshot / model_file).parent.mkdir(parents=True)
    for name in (model_file, "tokenizer.json", "tokenizer_config.json"):
        (snapshot / name).write_text("{}", encoding="utf-8")
    return snapshot


def test_snapshot_prefers_refs_main_and_needs_every_file(tmp_path):
    repo = "Qdrant/bge-small-en-v1.5-onnx-Q"
    make_snapshot(tmp_path, repo, "aaa", "model_optimized.onnx")
    current = make_snapshot(tmp_path, repo, "bbb", "model_optimized.onnx")
    (tmp_path / "models--Qdrant--bge-small-en-v1.5-onnx-Q" / "refs").mkdir()
    (tmp_path / "models--Qdrant--bge-small-en-v1.5-onnx-Q" / "refs" / "main").write_text("bbb\n", encoding="utf-8")
    assert _snapshot(tmp_path, repo, "model_optimized.onnx") == current
    (current / "tokenizer.json").unlink()
    assert _snapshot(tmp_path, repo, "model_optimized.onnx").name == "aaa"
    assert _snapshot(tmp_path, repo, "onnx/other.onnx") is None


def test_missing_models_raise_model_unavailable_without_importing_fastembed(tmp_path):
    embedder = load_embedder("BAAI/bge-small-en-v1.5", tmp_path)
    assert embedder.dims == 384
    with pytest.raises(ModelUnavailable, match="wiki models download"):
        embedder.embed_query("x")
    with pytest.raises(ModelUnavailable, match="reranker 'jinaai/jina-reranker-v1-turbo-en' is not downloaded"):
        load_reranker("jinaai/jina-reranker-v1-turbo-en", tmp_path).score("q", ["d"])
    assert "fastembed" not in sys.modules
