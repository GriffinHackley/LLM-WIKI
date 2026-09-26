import json

import pytest
import yaml

from wiki_cli import evaluate
from wiki_cli.cache import Cache
from wiki_cli.cli import main
from wiki_cli.models import HashEmbedder, OverlapReranker
from wiki_cli.search import keyword_query, reciprocal_rank_fusion, search


def bump(path):
    import os
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


@pytest.fixture
def corpus(wiki):
    wiki.write("tms/save-pipeline", {"title": "Mitigation Save Pipeline", "summary": "Persists mitigation edits."},
               body="# Validation\n\nEvery edit is validated before commit.\n\n# Persistence\n\n"
                    "Changes are written to the mitigation table inside one transaction.\n")
    wiki.write("tms/editor", {"title": "Mitigation Editor", "summary": "Admin screen for mitigations."},
               body="# Screen\n\nThe admin screen lists mitigations and opens the realtime widget.\n")
    wiki.write("ops/backups", {"title": "Nightly Backups", "summary": "Database backup schedule."},
               body="Backups run nightly at two o'clock and are kept for thirty days.\n")
    wiki.write("plain", raw="# no frontmatter\n\nmitigation table transaction\n")
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
    fused = reciprocal_rank_fusion([[1, 2, 3], [3, 1]])
    assert [item for item, _ in fused] == [1, 3, 2]


def test_keyword_only_search(corpus):
    with indexed(corpus) as cache:
        result = search(cache.conn, "backup schedule", embedder=None, reranker=None, embed_model=cache.embed_model)
    assert slugs(result)[0] == "ops/backups"
    assert result.modes == ["keyword"]


def test_hybrid_search_with_rerank_returns_best_section(corpus):
    with indexed(corpus) as cache:
        result = search(cache.conn, "Where are mitigation changes written in a transaction?",
                        embedder=HashEmbedder(), reranker=OverlapReranker(), embed_model=cache.embed_model)
    assert result.modes == ["keyword", "vector", "rerank"]
    top = result.hits[0]
    assert top.slug == "tms/save-pipeline"
    assert top.section == "Persistence"
    assert top.summary == "Persists mitigation edits."


def test_pages_without_frontmatter_never_returned(corpus):
    with indexed(corpus) as cache:
        result = search(cache.conn, "mitigation table transaction", embedder=HashEmbedder(), reranker=None,
                        embed_model=cache.embed_model, limit=10)
    assert "plain" not in slugs(result)


def test_exclude_slugs(corpus):
    with indexed(corpus) as cache:
        result = search(cache.conn, "mitigation", embedder=None, reranker=None, embed_model=None,
                        exclude=frozenset({"tms/save-pipeline"}), limit=10)
    assert "tms/save-pipeline" not in slugs(result)
    assert "tms/editor" in slugs(result)


def test_limit_and_one_hit_per_page(corpus):
    with indexed(corpus) as cache:
        result = search(cache.conn, "mitigation validated written transaction admin", embedder=HashEmbedder(),
                        reranker=OverlapReranker(), embed_model=cache.embed_model, limit=10)
    assert len(slugs(result)) == len(set(slugs(result)))
    with indexed(corpus) as cache:
        assert len(search(cache.conn, "mitigation", embedder=None, reranker=None, embed_model=None, limit=1).hits) == 1


def test_mismatched_embed_model_skips_vectors(corpus):
    with indexed(corpus) as cache:
        other = HashEmbedder()
        other.name = "fake:other"
        result = search(cache.conn, "mitigation", embedder=other, reranker=None, embed_model=cache.embed_model)
    assert result.modes == ["keyword"]
    assert "vector search skipped" in result.notes[0]


class TestEmbedding:
    def test_embeds_every_chunk_and_summary(self, corpus):
        with indexed(corpus) as cache:
            chunks = cache.conn.execute("SELECT COUNT(*) FROM chunks c JOIN pages p ON p.id = c.page_id "
                                        "WHERE p.is_page = 1").fetchone()[0]
            assert cache.conn.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0] == chunks
            assert cache.conn.execute("SELECT COUNT(*) FROM summary_vectors").fetchone()[0] == 3
            assert cache.pending_embeddings() == 0

    def test_changed_page_is_reembedded_and_old_vectors_removed(self, corpus):
        with indexed(corpus) as cache:
            corpus.write("ops/backups", {"title": "Nightly Backups", "summary": "Backup schedule."},
                         body="# One\n\nA.\n\n# Two\n\nB.\n\n# Three\n\nC.\n")
            bump(corpus.root / "ops" / "backups.md")
            cache.refresh()
            assert cache.pending_embeddings() == 1
            assert cache.embed_pending(HashEmbedder()) == 1
            chunk_count = cache.conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
            vector_count = cache.conn.execute("SELECT COUNT(*) FROM chunk_vectors").fetchone()[0]
            plain_chunks = 0  # pages without frontmatter have no chunks
            assert vector_count == chunk_count - plain_chunks

    def test_deleted_page_removes_vectors(self, corpus):
        with indexed(corpus) as cache:
            (corpus.root / "ops" / "backups.md").unlink()
            cache.refresh()
            assert cache.conn.execute("SELECT COUNT(*) FROM summary_vectors").fetchone()[0] == 2
            orphans = cache.conn.execute(
                "SELECT COUNT(*) FROM chunk_vectors WHERE rowid NOT IN (SELECT id FROM chunks)").fetchone()[0]
            assert orphans == 0

    def test_touched_page_keeps_vectors(self, corpus):
        with indexed(corpus) as cache:
            bump(corpus.root / "tms" / "editor.md")
            cache.refresh()
            assert cache.pending_embeddings() == 0

    def test_switching_model_resets_vectors(self, corpus):
        with indexed(corpus) as cache:
            other = HashEmbedder(dims=32)
            other.name = "fake:other"
            assert cache.embed_pending(other) == 3
            assert cache.embed_model == "fake:other"
            dims = cache.conn.execute("SELECT value FROM metadata WHERE key = 'embed_dims'").fetchone()[0]
            assert dims == "32"

    def test_page_ids_stable_across_updates(self, corpus):
        with indexed(corpus) as cache:
            before = cache.conn.execute("SELECT id FROM pages WHERE slug = 'tms/editor'").fetchone()[0]
            corpus.write("tms/editor", {"title": "Mitigation Editor", "summary": "Changed."})
            bump(corpus.root / "tms" / "editor.md")
            cache.refresh()
            after = cache.conn.execute("SELECT id FROM pages WHERE slug = 'tms/editor'").fetchone()[0]
            assert before == after


@pytest.fixture
def run(corpus, capsys):
    def invoke(*args):
        code = main([*args, "--wiki-root", str(corpus.root), "--cache", str(corpus.cache_path)])
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return invoke


def test_cli_search_json(run):
    run("index", "refresh")
    code, out, _ = run("search", "mitigation changes transaction", "--format", "json", "--limit", "2")
    payload = json.loads(out)
    assert code == 0
    assert payload["results"][0]["slug"] == "tms/save-pipeline"
    assert len(payload["results"]) <= 2
    assert "notes" not in payload


def test_cli_search_before_embedding_notes_it(run):
    run("index", "refresh", "--no-embed")
    code, out, _ = run("search", "backups", "--format", "json")
    payload = json.loads(out)
    assert payload["results"][0]["slug"] == "ops/backups"
    assert "no embeddings yet" in payload["notes"][0]


def test_cli_search_text(run):
    run("index", "refresh")
    code, out, _ = run("search", "nightly backups")
    assert code == 0
    assert out.startswith("1. ops/backups")


def test_unknown_model_is_usage_error(run):
    code, _, err = run("search", "x", "--embed-model", "not/a-model")
    assert code == 2 and "unknown embedding model" in err


def test_missing_real_model_skips_embedding(run, tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_MODELS_DIR", str(tmp_path / "empty-models"))
    code, out, _ = run("index", "refresh", "--embed-model", "BAAI/bge-small-en-v1.5", "--format", "json")
    stats = json.loads(out)
    assert code == 0 and stats["embedded"] == 0
    assert "not downloaded" in stats["embedding_skipped"]

    code, out, _ = run("search", "nightly backups", "--format", "json",
                       "--embed-model", "BAAI/bge-small-en-v1.5", "--reranker", "BAAI/bge-reranker-base")
    payload = json.loads(out)
    assert code == 0, "a missing model must fall back to keyword search"
    assert payload["results"][0]["slug"] == "ops/backups"
    assert any("no embeddings yet" in note for note in payload["notes"])
    assert any("reranking skipped" in note for note in payload["notes"])


def test_missing_model_after_embedding_falls_back(run, tmp_path, monkeypatch):
    run("index", "refresh")  # vectors built with fake:hash
    monkeypatch.setenv("WIKI_MODELS_DIR", str(tmp_path / "empty-models"))
    code, out, _ = run("search", "nightly backups", "--format", "json", "--reranker", "BAAI/bge-reranker-base")
    payload = json.loads(out)
    assert code == 0 and "rerank" not in json.dumps(payload["results"])
    assert payload["results"][0]["slug"] == "ops/backups"


class TestEval:
    def write_questions(self, corpus, items):
        path = corpus.repo / "eval" / "questions.yaml"
        path.parent.mkdir(exist_ok=True)
        path.write_text(yaml.safe_dump(items), encoding="utf-8")
        return path

    def test_eval_run_metrics(self, corpus, run):
        self.write_questions(corpus, [
            {"id": "q1", "question": "When do nightly backups run?", "answers": ["ops/backups"], "kind": "single"},
            {"id": "q2", "question": "Which admin screen lists mitigations?", "answers": ["tms/editor"],
             "kind": "single"},
            {"id": "q3", "question": "How is an edit from the admin screen saved?",
             "answers": ["tms/editor", "tms/save-pipeline"], "kind": "multi"},
            {"id": "q4", "question": "What is the office wifi password?", "answers": [], "kind": "unanswerable"},
            {"id": "q5", "question": "Tuning only", "answers": ["ops/backups"], "kind": "single", "split": "tune"},
        ])
        code, out, _ = run("eval", "run", "--format", "json")
        summary = json.loads(out)
        assert code == 0
        assert summary["questions"] == 4
        assert summary["hit_at_1"] == 1.0 and summary["mrr"] == 1.0
        assert summary["multi_all_in_top5"] == 1.0
        assert summary["misses"] == []
        assert (corpus.repo / ".cache" / "eval-fake-hash.sqlite3").is_file()
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
        path = self.write_questions(corpus, [item])
        with pytest.raises(evaluate.EvalError, match=message):
            evaluate.load_questions(path)

    def test_eval_sample(self, corpus, run):
        corpus.page("linked-a", ("linked-b", "depends-on", "Needs b."))
        corpus.page("linked-b")
        code, out, _ = run("eval", "sample", "--single", "2", "--multi", "5")
        payload = json.loads(out)
        assert code == 0
        assert len(payload["single"]) == 2
        assert payload["multi"] == [{"pages": ["linked-a", "linked-b"], "type": "depends-on", "reason": "Needs b."}]
        assert all("path" in entry for entry in payload["single"])
