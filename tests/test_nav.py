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
    wiki.page("event", "mill-meeting", {
        "Participants": "- [[edith-thorne]] — the foreman questioned about the fire\n- [[silas-reed]] — reportedly present",
        "Sources": "- [[courier-report]] — anonymous sources",
    }, summary="Officials met Thorne at the North Mill the day after the fire to hear her account.")
    wiki.page("person", "edith-thorne", {
        "Documented role": "Ran the night shift when the fire started.",
        "Appearances in sources": "- [[courier-report]] — named as the target of the meeting",
    }, summary="Foreman of the North Mill's night shift.", aliases=["Mrs. Thorne"])
    wiki.page("person", "silas-reed", {"Documented role": "Council chair; led the inquiry into the fire."},
              summary="Chair of the town council.")
    wiki.page("document", "courier-report", {
        "Summary": "The Courier says Reed, Hale and Marsh attended the North Mill meeting with Edith Thorne.",
        "Entities mentioned": "- [[edith-thorne]] — the member\n- [[silas-reed]] — present",
    }, summary="Ashford Courier report on the Thorne meeting.")
    wiki.page("person", "ruth-marsh", {"Documented role": "Mill owner; attended the meeting."},
              summary="Owner of the North Mill.", title="Ruth Marsh")
    wiki.page("topic", "renovation", summary="The rebuilding of the North Mill in 1888.")
    return wiki


@pytest.fixture
def nav(wiki_graph):
    cache = Cache(wiki_graph.settings())
    cache.refresh()
    cache.embed_pending(HashEmbedder())
    yield Navigator(cache, embedder=HashEmbedder(), reranker=OverlapReranker())
    cache.close()


def start(nav, question="Who attended the North Mill meeting with Thorne?", **kwargs):
    return nav.start(question, **kwargs)


def test_start_returns_results_and_session(nav):
    result = start(nav)
    assert len(result["session"]) == 6 and result["max_pages"] == 6
    assert result["results"][0]["slug"] in {"mill-meeting", "courier-report"}


def test_read_returns_best_section_and_section_list(nav):
    session = start(nav, "Which officials were reportedly present, and which foreman was questioned about the fire?")["session"]
    result = nav.read(session, "mill-meeting", WHY)
    assert result["section"] == "Participants"
    assert "[[silas-reed]]" in result["content"] and "Officials met" not in result["content"]
    assert result["sections"][:2] == ["What happened", "Participants"]
    assert (result["pages_read"], result["pages_left"]) == (1, 5)


def test_read_picks_section_from_stored_vectors_without_the_reranker(nav):
    class Refuses(OverlapReranker):
        def score(self, query, documents):
            raise AssertionError("nav read should not run the reranker when vectors exist")

    session = start(nav, "Which officials were reportedly present, and which foreman was questioned about the fire?")["session"]
    nav.reranker = Refuses()
    assert nav.read(session, "mill-meeting", WHY)["section"] == "Participants"


def test_read_specific_section_and_full(nav):
    session = start(nav)["session"]
    assert "Officials met" in nav.read(session, "mill-meeting", WHY, section="What happened")["content"]
    full = nav.read(session, "mill-meeting", WHY, full=True)
    assert full["section"] == "full page" and "[[courier-report]]" in full["content"]
    assert full["pages_read"] == 1  # other parts of a visited page do not count again


def test_revisit_refused(nav):
    session = start(nav)["session"]
    nav.read(session, "silas-reed", WHY)
    with pytest.raises(NavError) as info:
        nav.read(session, "silas-reed", WHY)
    assert info.value.code == "already-read"


@pytest.mark.parametrize("why, code", [("", "why-required"), ("x" * (MAX_WHY + 1), "why-too-long")])
def test_why_enforced(nav, why, code):
    session = start(nav)["session"]
    with pytest.raises(NavError) as info:
        nav.read(session, "silas-reed", why)
    assert info.value.code == code


def test_page_limit(nav):
    session = start(nav, max_pages=2)["session"]
    nav.read(session, "silas-reed", WHY)
    nav.read(session, "ruth-marsh", WHY)
    with pytest.raises(NavError) as info:
        nav.read(session, "renovation", WHY)
    assert info.value.code == "page-limit"


def test_unknown_page_and_section(nav):
    session = start(nav)["session"]
    with pytest.raises(NavError) as info:
        nav.read(session, "nobody", WHY)
    assert info.value.code == "no-such-page"
    with pytest.raises(NavError) as info:
        nav.read(session, "silas-reed", WHY, section="Nope")
    assert info.value.code == "no-such-section"


def test_candidates_links_first_then_similar_excluding_visited(nav):
    session = start(nav)["session"]
    nav.read(session, "mill-meeting", WHY)
    result = nav.candidates(session)
    linked = {item["slug"]: item for item in result["linked"]}
    assert set(linked) == {"edith-thorne", "silas-reed", "courier-report"}
    assert linked["silas-reed"] == {"slug": "silas-reed", "type": "person", "summary": "Chair of the town council.",
                                   "relation": "involves", "reason": "reportedly present"}
    similar = {item["slug"] for item in result["similar"]}
    assert "mill-meeting" not in similar and not similar & set(linked)

    nav.read(session, "courier-report", WHY)
    after = nav.candidates(session)
    assert "mill-meeting" not in {item["slug"] for item in after["linked"]}


def test_candidates_require_a_read(nav):
    session = start(nav)["session"]
    with pytest.raises(NavError) as info:
        nav.candidates(session)
    assert info.value.code == "nothing-read"


def test_earlier_candidates_offered_for_backtracking(nav, monkeypatch):
    import wiki_cli.nav as nav_module
    monkeypatch.setattr(nav_module, "SIMILAR_LIMIT", 0)  # tiny corpus: keep "similar" from absorbing everything
    session = start(nav)["session"]
    nav.read(session, "mill-meeting", WHY)
    nav.candidates(session)  # shows edith-thorne, silas-reed, courier-report
    nav.read(session, "renovation", WHY)  # a dead end
    earlier = {item["slug"] for item in nav.candidates(session)["earlier"]}
    assert earlier & {"edith-thorne", "silas-reed", "courier-report"}


def test_requery_excludes_visited_and_limits_repeats(nav):
    session = start(nav)["session"]
    nav.read(session, "courier-report", WHY)
    result = nav.requery(session, "Which mill owner attended?")
    assert "courier-report" not in {hit["slug"] for hit in result["results"]}
    with pytest.raises(NavError) as info:
        nav.requery(session, "which mill  owner attended?")
    assert info.value.code == "repeated-search"


def test_end_and_log(nav):
    session = start(nav)["session"]
    nav.read(session, "courier-report", WHY)
    result = nav.end(session, ["courier-report", "silas-reed"])
    assert result == {"session": session, "pages_read": ["courier-report"], "cited": ["courier-report", "silas-reed"],
                      "cited_without_reading": ["silas-reed"]}
    with pytest.raises(NavError) as info:
        nav.read(session, "silas-reed", WHY)
    assert info.value.code == "session-ended"
    kinds = [event["kind"] for event in nav.log(session)["events"]]
    assert kinds == ["start", "read", "end"]
    assert nav.log(session)["events"][1]["why"] == WHY


def test_works_without_vectors(wiki_graph):
    with Cache(wiki_graph.settings()) as cache:
        cache.refresh()
        navigator = Navigator(cache)
        session = navigator.start("North Mill meeting Thorne")["session"]
        read = navigator.read(session, "mill-meeting", WHY)
        assert read["section"] in read["sections"]
        assert navigator.candidates(session)["linked"]


def test_sessions_expire(nav, monkeypatch):
    session = start(nav)["session"]
    import wiki_cli.nav as nav_module
    monkeypatch.setattr(nav_module.time, "time", lambda: 10 ** 12)
    Navigator(nav.cache)
    with pytest.raises(NavError) as info:
        nav.read(session, "silas-reed", WHY)
    assert info.value.code == "no-such-session"


def test_suggest(wiki_graph):
    wiki_graph.page("document", "chronicle-report", {
        "Summary": "The Chronicle says Ruth Marsh and Mrs. Thorne were at the mill.",
        "Entities mentioned": "- [[silas-reed]] — present",
    }, summary="Chronicle report on the meeting.")
    with Cache(wiki_graph.settings()) as cache:
        cache.refresh()
        cache.embed_pending(HashEmbedder())
        results = {item["slug"]: item for item in suggest(cache, "chronicle-report")}
    assert results["ruth-marsh"]["reasons"][0] == "Named in the text as 'Ruth Marsh' but not linked."
    assert results["edith-thorne"]["reasons"][0] == "Named in the text as 'Mrs. Thorne' but not linked."
    assert "silas-reed" not in results  # already linked


@pytest.fixture
def run(wiki_graph, capsys):
    def invoke(*args):
        code = main([*args, "--root", str(wiki_graph.root), "--format", "json"])
        captured = capsys.readouterr()
        return code, json.loads(captured.out) if captured.out.strip() else None, captured.err
    return invoke


def test_cli_session_flow(run):
    run("index", "refresh")
    code, started, _ = run("nav", "start", "Who attended the North Mill meeting?", "--max-pages", "3")
    session = started["session"]
    assert code == 0
    code, read, _ = run("nav", "read", session, "mill-meeting", "--why", WHY)
    assert code == 0 and read["pages_left"] == 2
    code, candidates, _ = run("nav", "candidates", session)
    assert code == 0 and candidates["linked"]
    code, refused, _ = run("nav", "read", session, "mill-meeting", "--why", WHY)
    assert code == 1 and refused["error"] == "already-read"
    code, ended, _ = run("nav", "end", session, "--cited", "mill-meeting")
    assert code == 0 and ended["pages_read"] == ["mill-meeting"]
    code, log, _ = run("nav", "log", session)
    assert [event["kind"] for event in log["events"]] == ["start", "read", "candidates", "end"]
    code, suggested, _ = run("suggest", "courier-report")
    assert code == 0 and isinstance(suggested["suggestions"], list)


def test_cli_read_requires_why(run):
    code, started, _ = run("nav", "start", "meeting")
    with pytest.raises(SystemExit):  # argparse: --why is required
        main(["nav", "read", started["session"], "silas-reed"])


def test_unwritten_and_orphans(wiki_graph, run):
    wiki_graph.page("person", "loner")
    wiki_graph.page("person", "linker-a", {"Timeline": "- [[ghost]] — first"})
    wiki_graph.page("person", "linker-b", {"Timeline": "- [[ghost]] — second"})
    code, unwritten, _ = run("unwritten")
    assert code == 0 and unwritten["unwritten"][0] == {"target": "ghost", "linked_from": ["linker-a", "linker-b"]}
    code, orphans, _ = run("orphans")
    slugs = {item["slug"] for item in orphans["orphans"]}
    assert {"loner", "linker-a", "linker-b", "renovation"} <= slugs
    assert "silas-reed" not in slugs


def test_rereading_a_section_by_full_path_is_refused(nav):
    session = start(nav)["session"]
    nav.read(session, "mill-meeting", WHY, section="Participants")
    with pytest.raises(NavError) as info:
        nav.read(session, "mill-meeting", WHY, section="mill-meeting > Participants")
    assert info.value.code == "already-read"


def test_candidate_summaries_are_short(nav, wiki_graph):
    wiki_graph.page("person", "verbose", summary="word " * 80)
    wiki_graph.page("person", "hub", {"Relationships": "- [[verbose]] — long-winded colleague"})
    nav.cache.refresh()
    session = start(nav)["session"]
    nav.read(session, "hub", WHY)
    [item] = [item for item in nav.candidates(session)["linked"] if item["slug"] == "verbose"]
    assert len(item["summary"]) <= 201 and item["summary"].endswith("…")
