import json

import pytest
import yaml

from conftest import bump
from wiki_cli import evaluate
from wiki_cli.cache import Cache
from wiki_cli.cli import main
from wiki_cli.models import HashEmbedder, OverlapReranker
from wiki_cli.search import keyword_query, reciprocal_rank_fusion, search


@pytest.fixture
def corpus(wiki):
    wiki.page("document", "save-pipeline", {
        "Validation": "Every edit is validated before commit.",
        "Persistence": "Changes are written to the mitigation table inside one transaction.",
    }, summary="Persists mitigation edits.", title="Save pipeline memo")
    wiki.page("person", "editor-admin", {"Documented role": "The admin screen lists mitigations and opens the widget."},
              summary="Admin screen for mitigations.")
    wiki.page("event", "nightly-backups", summary="Backups run nightly at two o'clock and are kept for thirty days.")
    wiki.raw_text("hearing-transcript.txt", "The witness described the zebra protocol in detail.\n")
    return wiki


def indexed(wiki):
    cache = Cache(wiki.settings())
    cache.refresh()
    cache.embed_pending(HashEmbedder())
    return cache


def slugs(result):
    return [hit.slug for hit in result.hits]


def test_keyword_query_drops_stopwords_and_quotes_terms():
    assert keyword_query("How are the mitigation edits saved?") == '"mitigation" OR "edits" OR "saved"'
    assert keyword_query("the a of") is None


def test_rrf_combines_rankings():
    assert [item for item, _ in reciprocal_rank_fusion([[1, 2, 3], [3, 1]])] == [1, 3, 2]


def test_keyword_only_search(corpus):
    with indexed(corpus) as cache:
        result = search(cache.conn, "nightly backups", embedder=None, reranker=None, embed_model=cache.embed_model)
    assert slugs(result)[0] == "nightly-backups"
    assert result.modes == ["keyword"]


def test_hybrid_search_with_rerank_returns_best_section(corpus):
    with indexed(corpus) as cache:
        result = search(cache.conn, "Where are mitigation changes written in a transaction?",
                        embedder=HashEmbedder(), reranker=OverlapReranker(), embed_model=cache.embed_model)
    assert result.modes == ["keyword", "vector", "rerank"]
    top = result.hits[0]
    assert (top.slug, top.section, top.summary, top.page_type) == (
        "save-pipeline", "Save pipeline memo > Persistence", "Persists mitigation edits.", "document")


def test_raw_text_only_with_include_raw(corpus):
    with indexed(corpus) as cache:
        assert "raw/hearing-transcript" not in slugs(
            search(cache.conn, "zebra protocol", embedder=None, reranker=None, embed_model=None, limit=10))
        result = search(cache.conn, "zebra protocol", embedder=None, reranker=None, embed_model=None, include_raw=True)
    assert slugs(result)[0] == "raw/hearing-transcript"
    assert result.hits[0].page_type == "raw"


def test_exclude_slugs(corpus):
    with indexed(corpus) as cache:
        result = search(cache.conn, "mitigation", embedder=None, reranker=None, embed_model=None,
                        exclude=frozenset({"save-pipeline"}), limit=10)
    assert "save-pipeline" not in slugs(result) and "editor-admin" in slugs(result)


def test_one_hit_per_page_and_limit(corpus):
    with indexed(corpus) as cache:
        result = search(cache.conn, "mitigation validated written transaction admin", embedder=HashEmbedder(),
                        reranker=OverlapReranker(), embed_model=cache.embed_model, limit=10)
        assert len(slugs(result)) == len(set(slugs(result)))
        assert len(search(cache.conn, "mitigation", embedder=None, reranker=None, embed_model=None, limit=1).hits) == 1


def test_mismatched_embed_model_skips_vectors(corpus):
    with indexed(corpus) as cache:
        other = HashEmbedder()
        other.name = "fake:other"
        result = search(cache.conn, "mitigation", embedder=other, reranker=None, embed_model=cache.embed_model)
    assert result.modes == ["keyword"] and "vector search skipped" in result.notes[0]


class TestEmbedding:
    def test_every_chunk_embedded(self, corpus):
        with indexed(corpus) as cache:
            chunks = cache.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            assert cache.conn.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0] == chunks
            assert cache.conn.execute("SELECT COUNT(*) FROM summary_vectors").fetchone()[0] == 4
            assert cache.pending_embeddings() == 0

    def test_changed_page_reembedded_without_orphans(self, corpus):
        with indexed(corpus) as cache:
            bump(corpus.page("event", "nightly-backups", {"One": "A.", "Two": "B.", "Three": "C."}))
            cache.refresh()
            assert cache.pending_embeddings() == 1
            assert cache.embed_pending(HashEmbedder()) == 1
            orphans = cache.conn.execute(
                "SELECT COUNT(*) FROM chunk_vectors WHERE rowid NOT IN (SELECT id FROM chunks)").fetchone()[0]
            assert orphans == 0

    def test_deleted_page_removes_vectors(self, corpus):
        with indexed(corpus) as cache:
            (corpus.root / "wiki" / "events" / "nightly-backups.md").unlink()
            cache.refresh()
            assert cache.conn.execute("SELECT COUNT(*) FROM summary_vectors").fetchone()[0] == 3

    def test_switching_model_resets_vectors(self, corpus):
        with indexed(corpus) as cache:
            other = HashEmbedder(dims=32)
            other.name = "fake:other"
            assert cache.embed_pending(other) == 4
            assert cache.conn.execute("SELECT value FROM metadata WHERE key = 'embed_dims'").fetchone()[0] == "32"


@pytest.fixture
def run(corpus, capsys):
    def invoke(*args):
        code = main([*args, "--root", str(corpus.root)])
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return invoke


def test_cli_search_json(run):
    run("index", "refresh")
    code, out, _ = run("search", "mitigation changes transaction", "--format", "json", "--limit", "2")
    payload = json.loads(out)
    assert code == 0 and payload["results"][0]["slug"] == "save-pipeline"
    assert payload["results"][0]["type"] == "document" and "notes" not in payload


def test_cli_search_include_raw(run):
    code, out, _ = run("search", "zebra protocol", "--include-raw", "--keyword-only", "--format", "json")
    assert json.loads(out)["results"][0]["slug"] == "raw/hearing-transcript"


def test_cli_search_before_embedding_notes_it(run):
    run("index", "refresh", "--no-embed")
    payload = json.loads(run("search", "backups", "--format", "json")[1])
    assert payload["results"][0]["slug"] == "nightly-backups"
    assert "no embeddings yet" in payload["notes"][0]


def test_missing_real_model_falls_back(run, tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_MODELS_DIR", str(tmp_path / "empty-models"))
    code, out, _ = run("index", "refresh", "--embed-model", "BAAI/bge-small-en-v1.5", "--format", "json")
    assert code == 0 and "not downloaded" in json.loads(out)["embedding_skipped"]
    code, out, _ = run("search", "nightly backups", "--format", "json",
                       "--embed-model", "BAAI/bge-small-en-v1.5", "--reranker", "BAAI/bge-reranker-base")
    payload = json.loads(out)
    assert code == 0 and payload["results"][0]["slug"] == "nightly-backups"
    assert any("reranking skipped" in note for note in payload["notes"])


def test_unknown_model_is_usage_error(run):
    code, _, err = run("search", "x", "--embed-model", "not/a-model")
    assert code == 2 and "unknown embedding model" in err


class TestEval:
    def write_questions(self, corpus, items):
        path = corpus.root / "eval" / "questions.yaml"
        path.parent.mkdir(exist_ok=True)
        path.write_text(yaml.safe_dump(items), encoding="utf-8")
        return path

    def test_eval_run_metrics(self, corpus, run):
        self.write_questions(corpus, [
            {"id": "q1", "question": "When do nightly backups run?", "answers": ["nightly-backups"], "kind": "single"},
            {"id": "q2", "question": "Which admin screen lists mitigations?", "answers": ["editor-admin"],
             "kind": "single"},
            {"id": "q3", "question": "How are admin mitigation edits persisted?",
             "answers": ["editor-admin", "save-pipeline"], "kind": "multi"},
            {"id": "q4", "question": "What is the office wifi password?", "answers": [], "kind": "unanswerable"},
            {"id": "q5", "question": "Tuning only", "answers": ["nightly-backups"], "kind": "single", "split": "tune"},
        ])
        code, out, _ = run("eval", "run", "--format", "json")
        summary = json.loads(out)
        assert code == 0 and summary["questions"] == 4
        assert summary["hit_at_1"] == 1.0 and summary["multi_all_in_top5"] == 1.0 and summary["misses"] == []
        assert (corpus.root / ".cache" / "eval-fake-hash.sqlite3").is_file()
        assert not corpus.cache_path.exists()  # eval never touches the main cache

    def test_eval_rejects_unknown_answer_pages(self, corpus, run):
        self.write_questions(corpus, [{"id": "q", "question": "x", "answers": ["nope"], "kind": "single"}])
        code, _, err = run("eval", "run")
        assert code == 2 and "nope" in err

    @pytest.mark.parametrize("item, message", [
        ({"id": "q", "question": "x", "answers": [], "kind": "single"}, "need at least one"),
        ({"id": "q", "question": "x", "answers": ["a"], "kind": "unanswerable"}, "have no answers"),
        ({"id": "q", "question": "x", "answers": ["a"], "kind": "other"}, "kind must be"),
        ({"question": "x", "answers": ["a"], "kind": "single"}, "unique id"),
    ])
    def test_question_validation(self, corpus, item, message):
        with pytest.raises(evaluate.EvalError, match=message):
            evaluate.load_questions(self.write_questions(corpus, [item]))

    def test_eval_sample(self, corpus, run):
        corpus.page("person", "linked-a", {"Relationships": "- [[linked-b]] — colleague"})
        corpus.page("person", "linked-b")
        code, out, _ = run("eval", "sample", "--single", "2", "--multi", "5")
        payload = json.loads(out)
        assert code == 0 and len(payload["single"]) == 2
        assert payload["multi"] == [{"pages": ["linked-a", "linked-b"], "type": "associated-with", "reason": "colleague"}]
