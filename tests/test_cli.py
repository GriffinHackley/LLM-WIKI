import json

import pytest

from wiki_cli.cli import main


@pytest.fixture
def run(wiki, capsys):
    def invoke(*args):
        code = main([*args, "--wiki-root", str(wiki.root), "--cache", str(wiki.cache_path)])
        captured = capsys.readouterr()
        return code, captured.out, captured.err
    return invoke


def run_json(run, *args):
    code, out, _ = run(*args, "--format", "json")
    assert "\n" not in out.strip(), "JSON output must be compact"
    return code, json.loads(out)


def test_sync_check_neighbors_round_trip(wiki, run):
    wiki.page("store")
    wiki.page("tms/pipeline", ("store", "depends-on", "Persists to the store."))

    code, check = run_json(run, "check", "tms/pipeline")
    assert code == 1 and check["issues"][0]["code"] == "missing-block"

    code, sync = run_json(run, "rel", "sync", "--all")
    assert code == 0 and sync == {"updated": ["tms/pipeline.md"], "unchanged": 1}

    code, check = run_json(run, "check", "--all", "--verify-cache", "--no-warnings")
    assert code == 0 and check["ok"] and check["errors"] == 0

    code, result = run_json(run, "rel", "neighbors", "store")
    assert result == {"page": "store", "neighbors": [
        {"slug": "tms/pipeline", "direction": "incoming", "type": "depends-on", "inverse": "used-by",
         "reason": "Persists to the store."}]}


def test_sync_is_idempotent_via_cli(wiki, run):
    wiki.page("b")
    wiki.page("a", ("b", "depends-on", "x"))
    run("rel", "sync", "a")
    code, result = run_json(run, "rel", "sync", "a")
    assert code == 0 and result == {"updated": [], "unchanged": 1}


def test_sync_failure_exit_code(wiki, run):
    wiki.write("a", {"title": "a", "relations": [{"target": "nope", "type": "depends-on", "reason": "x"}]})
    code, result = run_json(run, "rel", "sync", "a")
    assert code == 1
    assert result["issues"][0]["code"] == "noncanonical-target"


def test_strict_fails_on_warnings(wiki, run):
    wiki.page("a")
    assert run("check", "a")[0] == 0
    assert run("check", "a", "--strict")[0] == 1


def test_unknown_page_is_usage_error(wiki, run):
    code, _, err = run("rel", "neighbors", "missing")
    assert code == 2 and "missing" in err


def test_verify_cache_requires_all(wiki, run):
    wiki.page("a")
    assert run("check", "a", "--verify-cache")[0] == 2


def test_verify_cache_without_cache_fails(wiki, run):
    wiki.page("a")
    code, result = run_json(run, "check", "--all", "--verify-cache")
    assert code == 1 and result["issues"][0]["code"] == "cache-mismatch"


def test_check_all_reports_stale_summary(wiki, run):
    wiki.page("a", summary="S.")
    run("index", "refresh")
    wiki.write("a", {"title": "a", "summary": "S."}, body="Completely new body.\n")
    run("index", "refresh")
    code, result = run_json(run, "check", "--all")
    assert "summary-stale" in [issue["code"] for issue in result["issues"]]


def test_index_commands(wiki, run):
    wiki.page("a", ("b", "depends-on", "x"))
    wiki.page("b")
    code, stats = run_json(run, "index", "rebuild")
    assert code == 0 and stats["added"] == 2
    code, status = run_json(run, "index", "status")
    assert status == {"version": "1", "pages": 2, "relations": 1, "placeholder_summaries": 0, "stale": 0}


def test_text_output(wiki, run):
    wiki.page("b")
    wiki.page("a", ("b", "depends-on", "Needs b."))
    run("rel", "sync", "a")
    code, out, _ = run("rel", "neighbors", "a")
    assert code == 0 and out.strip() == "-> depends-on      b  Needs b."


def test_vocab(wiki, run):
    code, result = run_json(run, "vocab")
    assert code == 0 and result["types"][0] == {"type": "depends-on", "inverse": "used-by"}
