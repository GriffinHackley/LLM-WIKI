import json

import pytest

from wiki_cli.cache import Cache
from wiki_cli.cli import main
from wiki_cli.models import HashEmbedder, OverlapReranker
from wiki_cli.nav import MAX_WHY, NavError, Navigator
from wiki_cli.suggest import suggest

WHY = "Most likely to name who attended the meeting."


@pytest.fixture
def wiki_graph(wiki):
    wiki.page("event", "gallery-meeting", {
        "Participants": "- [[mara-quill]] — the curator being lobbied\n- [[oren-vale]] — reportedly present",
        "Sources": "- [[gazette-report]] — anonymous sources",
    }, summary="Officials met Quill in the East Gallery hours before the loan request reached the board.")
    wiki.page("person", "mara-quill", {
        "Documented role": "Signed the loan request and never withdrew.",
        "Appearances in sources": "- [[gazette-report]] — named as the target of the meeting",
    }, summary="Curator who signed the loan request.", aliases=["Dr. Quill"])
    wiki.page("person", "oren-vale", {"Documented role": "Board chair; co-signed the loan memo."},
              summary="Chair of the museum board.")
    wiki.page("document", "gazette-report", {
        "Summary": "The Gazette says Vale, Stone and Brand attended the East Gallery meeting with Mara Quill.",
        "Entities mentioned": "- [[mara-quill]] — the curator\n- [[oren-vale]] — present",
    }, summary="Harbor Gazette report on the Quill meeting.")
    wiki.page("person", "tessa-brand", {"Documented role": "Museum director; reportedly attended the meeting."},
              summary="Director of the museum.", title="Tessa Brand")
    wiki.page("topic", "renovation", summary="The 43-day gallery renovation in autumn 2025.")
    return wiki


@pytest.fixture
def nav(wiki_graph):
    cache = Cache(wiki_graph.settings())
    cache.refresh()
    cache.embed_pending(HashEmbedder())
    yield Navigator(cache, embedder=HashEmbedder(), reranker=OverlapReranker())
    cache.close()


def start(nav, question="Who attended the East Gallery meeting with Quill?", **kwargs):
    return nav.start(question, **kwargs)


def test_start_returns_results_and_session(nav):
    result = start(nav)
    assert len(result["session"]) == 6 and result["max_pages"] == 6
    assert result["results"][0]["slug"] in {"gallery-meeting", "gazette-report"}


def test_read_returns_best_section_and_section_list(nav):
    session = start(nav, "Which officials were reportedly present, and which curator was being lobbied?")["session"]
    result = nav.read(session, "gallery-meeting", WHY)
    assert result["section"] == "Participants"
    assert "[[oren-vale]]" in result["content"] and "Officials met" not in result["content"]
    assert result["sections"][:2] == ["What happened", "Participants"]
    assert (result["pages_read"], result["pages_left"]) == (1, 5)


def test_read_picks_section_from_stored_vectors_without_the_reranker(nav):
    class Refuses(OverlapReranker):
        def score(self, query, documents):
            raise AssertionError("nav read should not run the reranker when vectors exist")

    session = start(nav, "Which officials were reportedly present, and which curator was being lobbied?")["session"]
    nav.reranker = Refuses()
    assert nav.read(session, "gallery-meeting", WHY)["section"] == "Participants"


def test_read_specific_section_and_full(nav):
    session = start(nav)["session"]
    assert "Officials met" in nav.read(session, "gallery-meeting", WHY, section="What happened")["content"]
    full = nav.read(session, "gallery-meeting", WHY, full=True)
    assert full["section"] == "full page" and "[[gazette-report]]" in full["content"]
    assert full["pages_read"] == 1  # other parts of a visited page do not count again


def test_revisit_refused(nav):
    session = start(nav)["session"]
    nav.read(session, "oren-vale", WHY)
    with pytest.raises(NavError) as info:
        nav.read(session, "oren-vale", WHY)
    assert info.value.code == "already-read"


@pytest.mark.parametrize("why, code", [("", "why-required"), ("x" * (MAX_WHY + 1), "why-too-long")])
def test_why_enforced(nav, why, code):
    session = start(nav)["session"]
    with pytest.raises(NavError) as info:
        nav.read(session, "oren-vale", why)
    assert info.value.code == code


def test_page_limit(nav):
    session = start(nav, max_pages=2)["session"]
    nav.read(session, "oren-vale", WHY)
    nav.read(session, "tessa-brand", WHY)
    with pytest.raises(NavError) as info:
        nav.read(session, "renovation", WHY)
    assert info.value.code == "page-limit"


def test_unknown_page_and_section(nav):
    session = start(nav)["session"]
    with pytest.raises(NavError) as info:
        nav.read(session, "nobody", WHY)
    assert info.value.code == "no-such-page"
    with pytest.raises(NavError) as info:
        nav.read(session, "oren-vale", WHY, section="Nope")
    assert info.value.code == "no-such-section"


def test_candidates_links_first_then_similar_excluding_visited(nav):
    session = start(nav)["session"]
    nav.read(session, "gallery-meeting", WHY)
    result = nav.candidates(session)
    linked = {item["slug"]: item for item in result["linked"]}
    assert set(linked) == {"mara-quill", "oren-vale", "gazette-report"}
    assert linked["oren-vale"] == {"slug": "oren-vale", "type": "person", "summary": "Chair of the museum board.",
                                   "relation": "involves", "reason": "reportedly present"}
    similar = {item["slug"] for item in result["similar"]}
    assert "gallery-meeting" not in similar and not similar & set(linked)

    nav.read(session, "gazette-report", WHY)
    after = nav.candidates(session)
    assert "gallery-meeting" not in {item["slug"] for item in after["linked"]}


def test_candidates_require_a_read(nav):
    session = start(nav)["session"]
    with pytest.raises(NavError) as info:
        nav.candidates(session)
    assert info.value.code == "nothing-read"


def test_earlier_candidates_offered_for_backtracking(nav, monkeypatch):
    import wiki_cli.nav as nav_module
    monkeypatch.setattr(nav_module, "SIMILAR_LIMIT", 0)  # tiny corpus: keep "similar" from absorbing everything
    session = start(nav)["session"]
    nav.read(session, "gallery-meeting", WHY)
    nav.candidates(session)  # shows mara-quill, oren-vale, gazette-report
    nav.read(session, "renovation", WHY)  # a dead end
    earlier = {item["slug"] for item in nav.candidates(session)["earlier"]}
    assert earlier & {"mara-quill", "oren-vale", "gazette-report"}


def test_requery_excludes_visited_and_limits_repeats(nav):
    session = start(nav)["session"]
    nav.read(session, "gazette-report", WHY)
    result = nav.requery(session, "Which museum director attended?")
    assert "gazette-report" not in {hit["slug"] for hit in result["results"]}
    with pytest.raises(NavError) as info:
        nav.requery(session, "which museum  director attended?")
    assert info.value.code == "repeated-search"


def test_end_and_log(nav):
    session = start(nav)["session"]
    nav.read(session, "gazette-report", WHY)
    result = nav.end(session, ["gazette-report", "oren-vale"])
    assert result == {"session": session, "pages_read": ["gazette-report"], "cited": ["gazette-report", "oren-vale"],
                      "cited_without_reading": ["oren-vale"]}
    with pytest.raises(NavError) as info:
        nav.read(session, "oren-vale", WHY)
    assert info.value.code == "session-ended"
    kinds = [event["kind"] for event in nav.log(session)["events"]]
    assert kinds == ["start", "read", "end"]
    assert nav.log(session)["events"][1]["why"] == WHY


def test_works_without_vectors(wiki_graph):
    with Cache(wiki_graph.settings()) as cache:
        cache.refresh()
        navigator = Navigator(cache)
        session = navigator.start("East Gallery meeting Quill")["session"]
        read = navigator.read(session, "gallery-meeting", WHY)
        assert read["section"] in read["sections"]
        assert navigator.candidates(session)["linked"]


def test_sessions_expire(nav, monkeypatch):
    session = start(nav)["session"]
    import wiki_cli.nav as nav_module
    monkeypatch.setattr(nav_module.time, "time", lambda: 10 ** 12)
    Navigator(nav.cache)
    with pytest.raises(NavError) as info:
        nav.read(session, "oren-vale", WHY)
    assert info.value.code == "no-such-session"


def test_suggest(wiki_graph):
    wiki_graph.page("document", "herald-report", {
        "Summary": "The Herald says Tessa Brand and Dr. Quill were at the museum.",
        "Entities mentioned": "- [[oren-vale]] — present",
    }, summary="Herald report on the meeting.")
    with Cache(wiki_graph.settings()) as cache:
        cache.refresh()
        cache.embed_pending(HashEmbedder())
        results = {item["slug"]: item for item in suggest(cache, "herald-report")}
    assert results["tessa-brand"]["reasons"][0] == "Named in the text as 'Tessa Brand' but not linked."
    assert results["mara-quill"]["reasons"][0] == "Named in the text as 'Dr. Quill' but not linked."
    assert "oren-vale" not in results  # already linked


@pytest.fixture
def run(wiki_graph, capsys):
    def invoke(*args):
        code = main([*args, "--root", str(wiki_graph.root), "--format", "json"])
        captured = capsys.readouterr()
        return code, json.loads(captured.out) if captured.out.strip() else None, captured.err
    return invoke


def test_cli_session_flow(run):
    run("index", "refresh")
    code, started, _ = run("nav", "start", "Who attended the East Gallery meeting?", "--max-pages", "3")
    session = started["session"]
    assert code == 0
    code, read, _ = run("nav", "read", session, "gallery-meeting", "--why", WHY)
    assert code == 0 and read["pages_left"] == 2
    code, candidates, _ = run("nav", "candidates", session)
    assert code == 0 and candidates["linked"]
    code, refused, _ = run("nav", "read", session, "gallery-meeting", "--why", WHY)
    assert code == 1 and refused["error"] == "already-read"
    code, ended, _ = run("nav", "end", session, "--cited", "gallery-meeting")
    assert code == 0 and ended["pages_read"] == ["gallery-meeting"]
    code, log, _ = run("nav", "log", session)
    assert [event["kind"] for event in log["events"]] == ["start", "read", "candidates", "end"]
    code, suggested, _ = run("suggest", "gazette-report")
    assert code == 0 and isinstance(suggested["suggestions"], list)


def test_cli_read_requires_why(run):
    code, started, _ = run("nav", "start", "meeting")
    with pytest.raises(SystemExit):  # argparse: --why is required
        main(["nav", "read", started["session"], "oren-vale"])


def test_unwritten_and_orphans(wiki_graph, run):
    wiki_graph.page("person", "loner")
    wiki_graph.page("person", "linker-a", {"Timeline": "- [[ghost]] — first"})
    wiki_graph.page("person", "linker-b", {"Timeline": "- [[ghost]] — second"})
    code, unwritten, _ = run("unwritten")
    assert code == 0 and unwritten["unwritten"][0] == {"target": "ghost", "linked_from": ["linker-a", "linker-b"]}
    code, orphans, _ = run("orphans")
    slugs = {item["slug"] for item in orphans["orphans"]}
    assert {"loner", "linker-a", "linker-b", "renovation"} <= slugs
    assert "oren-vale" not in slugs


def test_rereading_a_section_by_full_path_is_refused(nav):
    session = start(nav)["session"]
    nav.read(session, "gallery-meeting", WHY, section="Participants")
    with pytest.raises(NavError) as info:
        nav.read(session, "gallery-meeting", WHY, section="gallery-meeting > Participants")
    assert info.value.code == "already-read"


def test_candidate_summaries_are_short(nav, wiki_graph):
    wiki_graph.page("person", "verbose", summary="word " * 80)
    wiki_graph.page("person", "hub", {"Relationships": "- [[verbose]] — long-winded colleague"})
    nav.cache.refresh()
    session = start(nav)["session"]
    nav.read(session, "hub", WHY)
    [item] = [item for item in nav.candidates(session)["linked"] if item["slug"] == "verbose"]
    assert len(item["summary"]) <= 201 and item["summary"].endswith("…")


def test_read_shows_linked_page_titles(wiki):
    wiki.page("person", "tessa-brand", summary="Director of the museum.", title="Tessa Brand")
    wiki.page("person", "oren-vale", summary="Board chair.")  # title is the slug: left alone
    wiki.page("topic", "meeting", {
        "Who was there": "Present: [[tessa-brand]], [[tessa-brand|the Director]], [[oren-vale]], [[ghost]].\n"
                         "![[tessa-brand#^q-1]]\n\n| Person | Role |\n|---|---|\n| [[tessa-brand]] | Museum |"},
        summary="A meeting.")
    cache = Cache(wiki.settings())
    cache.refresh()
    nav = Navigator(cache, embedder=HashEmbedder())
    session = nav.start("who was at the meeting")["session"]
    content = nav.read(session, "meeting", WHY, section="Who was there")["content"]
    cache.close()
    assert "Present: [[tessa-brand|Tessa Brand]], [[tessa-brand|the Director]], [[oren-vale]], [[ghost]]." in content
    assert "![[tessa-brand#^q-1]]" in content
    assert r"| [[tessa-brand\|Tessa Brand]] | Museum |" in content
    assert "Present: [[tessa-brand]], " in (wiki.root / "wiki/topics/meeting.md").read_text(encoding="utf-8")  # file untouched
