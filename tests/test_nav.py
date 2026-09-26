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
    wiki.page("event", "situation-room-meeting", {
        "Participants": "- [[lauren-boebert]] — the member being lobbied\n- [[pam-bondi]] — reportedly present",
        "Sources": "- [[abc-report]] — anonymous sources",
    }, summary="Officials met Boebert in the Situation Room hours before the petition reached 218.")
    wiki.page("person", "lauren-boebert", {
        "Documented role": "Signed the discharge petition and never withdrew.",
        "Appearances in sources": "- [[abc-report]] — named as the target of the meeting",
    }, summary="Colorado Republican who signed the discharge petition.", aliases=["Rep. Boebert"])
    wiki.page("person", "pam-bondi", {"Documented role": "Attorney General; co-signed the production memo."},
              summary="United States Attorney General.")
    wiki.page("document", "abc-report", {
        "Summary": "ABC says Bondi, Blanche and Patel attended the Situation Room meeting with Lauren Boebert.",
        "Entities mentioned": "- [[lauren-boebert]] — the member\n- [[pam-bondi]] — present",
    }, summary="ABC News report on the Boebert meeting.")
    wiki.page("person", "kash-patel", {"Documented role": "FBI Director; reportedly attended the meeting."},
              summary="Director of the FBI.", title="Kash Patel")
    wiki.page("topic", "shutdown", summary="The 43-day government shutdown in autumn 2025.")
    return wiki


@pytest.fixture
def nav(wiki_graph):
    cache = Cache(wiki_graph.settings())
    cache.refresh()
    cache.embed_pending(HashEmbedder())
    yield Navigator(cache, embedder=HashEmbedder(), reranker=OverlapReranker())
    cache.close()


def start(nav, question="Who attended the Situation Room meeting with Boebert?", **kwargs):
    return nav.start(question, **kwargs)


def test_start_returns_results_and_session(nav):
    result = start(nav)
    assert len(result["session"]) == 6 and result["max_pages"] == 6
    assert result["results"][0]["slug"] in {"situation-room-meeting", "abc-report"}


def test_read_returns_best_section_and_section_list(nav):
    session = start(nav, "Which officials were reportedly present, and which member was being lobbied?")["session"]
    result = nav.read(session, "situation-room-meeting", WHY)
    assert result["section"] == "Participants"
    assert "[[pam-bondi]]" in result["content"] and "Officials met" not in result["content"]
    assert result["sections"][:2] == ["What happened", "Participants"]
    assert (result["pages_read"], result["pages_left"]) == (1, 5)


def test_read_picks_section_from_stored_vectors_without_the_reranker(nav):
    class Refuses(OverlapReranker):
        def score(self, query, documents):
            raise AssertionError("nav read should not run the reranker when vectors exist")

    session = start(nav, "Which officials were reportedly present, and which member was being lobbied?")["session"]
    nav.reranker = Refuses()
    assert nav.read(session, "situation-room-meeting", WHY)["section"] == "Participants"


def test_read_specific_section_and_full(nav):
    session = start(nav)["session"]
    assert "Officials met" in nav.read(session, "situation-room-meeting", WHY, section="What happened")["content"]
    full = nav.read(session, "situation-room-meeting", WHY, full=True)
    assert full["section"] == "full page" and "[[abc-report]]" in full["content"]
    assert full["pages_read"] == 1  # other parts of a visited page do not count again


def test_revisit_refused(nav):
    session = start(nav)["session"]
    nav.read(session, "pam-bondi", WHY)
    with pytest.raises(NavError) as info:
        nav.read(session, "pam-bondi", WHY)
    assert info.value.code == "already-read"


@pytest.mark.parametrize("why, code", [("", "why-required"), ("x" * (MAX_WHY + 1), "why-too-long")])
def test_why_enforced(nav, why, code):
    session = start(nav)["session"]
    with pytest.raises(NavError) as info:
        nav.read(session, "pam-bondi", why)
    assert info.value.code == code


def test_page_limit(nav):
    session = start(nav, max_pages=2)["session"]
    nav.read(session, "pam-bondi", WHY)
    nav.read(session, "kash-patel", WHY)
    with pytest.raises(NavError) as info:
        nav.read(session, "shutdown", WHY)
    assert info.value.code == "page-limit"


def test_unknown_page_and_section(nav):
    session = start(nav)["session"]
    with pytest.raises(NavError) as info:
        nav.read(session, "nobody", WHY)
    assert info.value.code == "no-such-page"
    with pytest.raises(NavError) as info:
        nav.read(session, "pam-bondi", WHY, section="Nope")
    assert info.value.code == "no-such-section"


def test_candidates_links_first_then_similar_excluding_visited(nav):
    session = start(nav)["session"]
    nav.read(session, "situation-room-meeting", WHY)
    result = nav.candidates(session)
    linked = {item["slug"]: item for item in result["linked"]}
    assert set(linked) == {"lauren-boebert", "pam-bondi", "abc-report"}
    assert linked["pam-bondi"] == {"slug": "pam-bondi", "type": "person", "summary": "United States Attorney General.",
                                   "relation": "involves", "reason": "reportedly present"}
    similar = {item["slug"] for item in result["similar"]}
    assert "situation-room-meeting" not in similar and not similar & set(linked)

    nav.read(session, "abc-report", WHY)
    after = nav.candidates(session)
    assert "situation-room-meeting" not in {item["slug"] for item in after["linked"]}


def test_candidates_require_a_read(nav):
    session = start(nav)["session"]
    with pytest.raises(NavError) as info:
        nav.candidates(session)
    assert info.value.code == "nothing-read"


def test_earlier_candidates_offered_for_backtracking(nav, monkeypatch):
    import wiki_cli.nav as nav_module
    monkeypatch.setattr(nav_module, "SIMILAR_LIMIT", 0)  # tiny corpus: keep "similar" from absorbing everything
    session = start(nav)["session"]
    nav.read(session, "situation-room-meeting", WHY)
    nav.candidates(session)  # shows lauren-boebert, pam-bondi, abc-report
    nav.read(session, "shutdown", WHY)  # a dead end
    earlier = {item["slug"] for item in nav.candidates(session)["earlier"]}
    assert earlier & {"lauren-boebert", "pam-bondi", "abc-report"}


def test_requery_excludes_visited_and_limits_repeats(nav):
    session = start(nav)["session"]
    nav.read(session, "abc-report", WHY)
    result = nav.requery(session, "Which FBI director attended?")
    assert "abc-report" not in {hit["slug"] for hit in result["results"]}
    with pytest.raises(NavError) as info:
        nav.requery(session, "which FBI  director attended?")
    assert info.value.code == "repeated-search"


def test_end_and_log(nav):
    session = start(nav)["session"]
    nav.read(session, "abc-report", WHY)
    result = nav.end(session, ["abc-report", "pam-bondi"])
    assert result == {"session": session, "pages_read": ["abc-report"], "cited": ["abc-report", "pam-bondi"],
                      "cited_without_reading": ["pam-bondi"]}
    with pytest.raises(NavError) as info:
        nav.read(session, "pam-bondi", WHY)
    assert info.value.code == "session-ended"
    kinds = [event["kind"] for event in nav.log(session)["events"]]
    assert kinds == ["start", "read", "end"]
    assert nav.log(session)["events"][1]["why"] == WHY


def test_works_without_vectors(wiki_graph):
    with Cache(wiki_graph.settings()) as cache:
        cache.refresh()
        navigator = Navigator(cache)
        session = navigator.start("Situation Room meeting Boebert")["session"]
        read = navigator.read(session, "situation-room-meeting", WHY)
        assert read["section"] in read["sections"]
        assert navigator.candidates(session)["linked"]


def test_sessions_expire(nav, monkeypatch):
    session = start(nav)["session"]
    import wiki_cli.nav as nav_module
    monkeypatch.setattr(nav_module.time, "time", lambda: 10 ** 12)
    Navigator(nav.cache)
    with pytest.raises(NavError) as info:
        nav.read(session, "pam-bondi", WHY)
    assert info.value.code == "no-such-session"


def test_suggest(wiki_graph):
    wiki_graph.page("document", "cnn-report", {
        "Summary": "CNN says Kash Patel and Rep. Boebert were at the White House.",
        "Entities mentioned": "- [[pam-bondi]] — present",
    }, summary="CNN report on the meeting.")
    with Cache(wiki_graph.settings()) as cache:
        cache.refresh()
        cache.embed_pending(HashEmbedder())
        results = {item["slug"]: item for item in suggest(cache, "cnn-report")}
    assert results["kash-patel"]["reasons"][0] == "Named in the text as 'Kash Patel' but not linked."
    assert results["lauren-boebert"]["reasons"][0] == "Named in the text as 'Rep. Boebert' but not linked."
    assert "pam-bondi" not in results  # already linked


@pytest.fixture
def run(wiki_graph, capsys):
    def invoke(*args):
        code = main([*args, "--root", str(wiki_graph.root), "--format", "json"])
        captured = capsys.readouterr()
        return code, json.loads(captured.out) if captured.out.strip() else None, captured.err
    return invoke


def test_cli_session_flow(run):
    run("index", "refresh")
    code, started, _ = run("nav", "start", "Who attended the Situation Room meeting?", "--max-pages", "3")
    session = started["session"]
    assert code == 0
    code, read, _ = run("nav", "read", session, "situation-room-meeting", "--why", WHY)
    assert code == 0 and read["pages_left"] == 2
    code, candidates, _ = run("nav", "candidates", session)
    assert code == 0 and candidates["linked"]
    code, refused, _ = run("nav", "read", session, "situation-room-meeting", "--why", WHY)
    assert code == 1 and refused["error"] == "already-read"
    code, ended, _ = run("nav", "end", session, "--cited", "situation-room-meeting")
    assert code == 0 and ended["pages_read"] == ["situation-room-meeting"]
    code, log, _ = run("nav", "log", session)
    assert [event["kind"] for event in log["events"]] == ["start", "read", "candidates", "end"]
    code, suggested, _ = run("suggest", "abc-report")
    assert code == 0 and isinstance(suggested["suggestions"], list)


def test_cli_read_requires_why(run):
    code, started, _ = run("nav", "start", "meeting")
    with pytest.raises(SystemExit):  # argparse: --why is required
        main(["nav", "read", started["session"], "pam-bondi"])


def test_unwritten_and_orphans(wiki_graph, run):
    wiki_graph.page("person", "loner")
    wiki_graph.page("person", "linker-a", {"Timeline": "- [[ghost]] — first"})
    wiki_graph.page("person", "linker-b", {"Timeline": "- [[ghost]] — second"})
    code, unwritten, _ = run("unwritten")
    assert code == 0 and unwritten["unwritten"][0] == {"target": "ghost", "linked_from": ["linker-a", "linker-b"]}
    code, orphans, _ = run("orphans")
    slugs = {item["slug"] for item in orphans["orphans"]}
    assert {"loner", "linker-a", "linker-b", "shutdown"} <= slugs
    assert "pam-bondi" not in slugs


def test_rereading_a_section_by_full_path_is_refused(nav):
    session = start(nav)["session"]
    nav.read(session, "situation-room-meeting", WHY, section="Participants")
    with pytest.raises(NavError) as info:
        nav.read(session, "situation-room-meeting", WHY, section="situation-room-meeting > Participants")
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
    wiki.page("person", "kash-patel", summary="Director of the FBI.", title="Kash Patel")
    wiki.page("person", "pam-bondi", summary="Attorney General.")  # title is the slug: left alone
    wiki.page("topic", "meeting", {
        "Who was there": "Present: [[kash-patel]], [[kash-patel|the Director]], [[pam-bondi]], [[ghost]].\n"
                         "![[kash-patel#^q-1]]\n\n| Person | Role |\n|---|---|\n| [[kash-patel]] | FBI |"},
        summary="A meeting.")
    cache = Cache(wiki.settings())
    cache.refresh()
    nav = Navigator(cache, embedder=HashEmbedder())
    session = nav.start("who was at the meeting")["session"]
    content = nav.read(session, "meeting", WHY, section="Who was there")["content"]
    cache.close()
    assert "Present: [[kash-patel|Kash Patel]], [[kash-patel|the Director]], [[pam-bondi]], [[ghost]]." in content
    assert "![[kash-patel#^q-1]]" in content
    assert r"| [[kash-patel\|Kash Patel]] | FBI |" in content
    assert "Present: [[kash-patel]], " in (wiki.root / "wiki/topics/meeting.md").read_text(encoding="utf-8")  # file untouched
