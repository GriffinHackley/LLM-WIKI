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
        "Participants": "- [[edith-thorne]] — the foreman questioned about the fire\n- [[silas-reed]] — led the panel",
        "Sources": "- [[courier-report]] — the reporter's account",
    }, summary="Officials met Thorne at the North Mill the day after the fire to hear her account.")
    wiki.page("person", "edith-thorne", {
        "Documented role": "Ran the night shift when the fire started.",
        "Appearances in sources": "- [[courier-report]] — quoted on how the fire spread",
    }, summary="Foreman of the North Mill's night shift.", aliases=["Mrs. Thorne"])
    wiki.page("person", "silas-reed", {"Documented role": "Council chair; led the inquiry into the fire."},
              summary="Chair of the town council.")
    wiki.page("document", "courier-report", {
        "Summary": "The Courier says Reed, Hale and Marsh attended the North Mill meeting with Edith Thorne.",
        "Entities mentioned": "- [[edith-thorne]] — the foreman\n- [[silas-reed]] — present",
    }, summary="Ashford Courier report on the Thorne meeting.")
    wiki.page("person", "rmarsh", {"Documented role": "Mill owner; attended the meeting."},
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
    session = start(nav, "Which officials led the panel, and which foreman was questioned about the fire?")["session"]
    result = nav.read(session, "mill-meeting", WHY)
    assert result["section"] == "Participants"
    assert "[[silas-reed]]" in result["content"] and "Officials met" not in result["content"]
    assert [item["name"] for item in result["sections"][:2]] == ["What happened", "Participants"]
    assert result["sections"][1]["read"] and "read" not in result["sections"][0]
    assert (result["pages_read"], result["pages_left"]) == (1, 5)


def test_read_picks_section_from_stored_vectors_without_the_reranker(nav):
    class Refuses(OverlapReranker):
        def score(self, query, documents):
            raise AssertionError("nav read should not run the reranker when vectors exist")

    session = start(nav, "Which officials led the panel, and which foreman was questioned about the fire?")["session"]
    nav.reranker = Refuses()
    assert nav.read(session, "mill-meeting", WHY)["section"] == "Participants"


def test_read_specific_section_and_full(nav):
    session = start(nav)["session"]
    assert "Officials met" in nav.read(session, "mill-meeting", WHY, sections=["What happened"])["content"]
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
    nav.read(session, "rmarsh", WHY)
    with pytest.raises(NavError) as info:
        nav.read(session, "renovation", WHY)
    assert info.value.code == "page-limit"


def test_unknown_page_and_section(nav):
    session = start(nav)["session"]
    with pytest.raises(NavError) as info:
        nav.read(session, "nobody", WHY)
    assert info.value.code == "no-such-page"
    with pytest.raises(NavError) as info:
        nav.read(session, "silas-reed", WHY, sections=["Nope"])
    assert info.value.code == "no-such-section"


def test_candidates_links_first_then_similar_excluding_visited(nav):
    session = start(nav)["session"]
    nav.read(session, "mill-meeting", WHY)
    result = nav.candidates(session)
    linked = {item["slug"]: item for item in result["linked"]}
    assert set(linked) == {"edith-thorne", "silas-reed", "courier-report"}
    assert linked["silas-reed"] == {"slug": "silas-reed", "type": "person", "summary": "Chair of the town council.",
                                   "relation": "involves", "reason": "led the panel"}
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
                      "cited_without_reading": ["silas-reed"],
                      "sources": "From the wiki: courier-report. From page summaries only: silas-reed."}
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
        assert read["section"] in [item["name"] for item in read["sections"]]
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
    assert results["rmarsh"]["reasons"][0] == "Named in the text as 'Ruth Marsh' but not linked."
    assert results["edith-thorne"]["reasons"][0] == "Named in the text as 'Mrs. Thorne' but not linked."
    assert "silas-reed" not in results  # already linked


def test_suggest_discounts_hub_pages(wiki):
    """Pages sharing only hubs that most pages link to are not leads; sharing rarer pages is."""
    def links(*targets):
        return {"Related": "\n".join(f"- [[{target}]] — related" for target in targets)}
    for hub in ("council", "archive"):
        wiki.page("topic", hub, summary=f"The {hub}.")
    for index in range(12):
        wiki.page("topic", f"filler-{index}", links("council", "archive"), summary=f"Filler {index}.")
    for rare in ("fire-report", "north-mill"):
        wiki.page("topic", rare, summary=f"The {rare}.")
    wiki.page("topic", "subject", links("council", "archive", "fire-report", "north-mill"), summary="Subject.")
    wiki.page("topic", "close", links("fire-report", "north-mill"), summary="Close.")
    wiki.page("topic", "mixed", links("council", "fire-report"), summary="Mixed.")
    with Cache(wiki.settings()) as cache:
        cache.refresh()
        results = [item["slug"] for item in suggest(cache, "subject", limit=20)
                   if any(reason.startswith("Shares") for reason in item["reasons"])]
    assert results == ["close", "mixed"]  # no filler: they share only the two hubs


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


def test_a_named_section_can_be_read_again(nav):
    """An agent whose shell cut the output can ask for the section again; a bare reread is refused."""
    session = start(nav)["session"]
    first = nav.read(session, "mill-meeting", WHY, sections=["Participants"])
    again = nav.read(session, "mill-meeting", WHY, sections=["mill-meeting > Participants"])
    assert again["content"] == first["content"] and again["pages_read"] == 1
    with pytest.raises(NavError) as info:
        nav.read(session, "mill-meeting", WHY)
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
    wiki.page("person", "rmarsh", summary="Owner of the North Mill.", title="Ruth Marsh")
    wiki.page("person", "silas-reed", summary="Council chair.")  # title is the slug: left alone
    wiki.page("topic", "meeting", {
        "Who was there": "Present: [[rmarsh]], [[rmarsh|the Owner]], [[silas-reed]], [[ghost]].\n"
                         "![[rmarsh#^q-1]]\n\n| Person | Role |\n|---|---|\n| [[rmarsh]] | Owner |"},
        summary="A meeting.")
    cache = Cache(wiki.settings())
    cache.refresh()
    nav = Navigator(cache, embedder=HashEmbedder())
    session = nav.start("who was at the meeting")["session"]
    content = nav.read(session, "meeting", WHY, sections=["Who was there"])["content"]
    cache.close()
    assert "Present: [[rmarsh|Ruth Marsh]], [[rmarsh|the Owner]], [[silas-reed]], [[ghost]]." in content
    assert "![[rmarsh#^q-1]]" in content
    assert r"| [[rmarsh\|Ruth Marsh]] | Owner |" in content
    assert "Present: [[rmarsh]], " in (wiki.root / "wiki/topics/meeting.md").read_text(encoding="utf-8")  # file untouched


# -- parts, coverage, sized reads and the gap check ---------------------------

def test_start_searches_each_part_and_labels_coverage(nav):
    result = nav.start("Who attended the North Mill meeting, and what did the bakery sell?",
                       parts=["who attended the North Mill meeting", "bakery bread prices"])
    covered, missing = result["parts"]
    assert covered["coverage"] == "good" and covered["pages"]
    assert missing["coverage"] == "none"
    slugs = [hit["slug"] for hit in result["results"]]
    assert len(slugs) == len(set(slugs))  # each page listed once across the parts
    assert set(covered["pages"]) <= set(slugs)


def test_start_refuses_too_many_parts(nav):
    with pytest.raises(NavError) as info:
        nav.start("everything", parts=[f"part {n}" for n in range(6)])
    assert info.value.code == "too-many-parts"


def test_coverage_is_unknown_without_a_measured_reranker(wiki_graph):
    with Cache(wiki_graph.settings()) as cache:
        cache.refresh()
        assert Navigator(cache).start("North Mill meeting")["coverage"] == "unknown"


def test_read_returns_the_section_search_showed_and_never_the_summary(nav):
    started = nav.start("Which panel did the council chair lead?")
    read = nav.read(started["session"], "silas-reed", WHY)
    assert read["section"] == "Documented role"
    assert "Chair of the town council" not in read["content"]  # the summary comes once, in "summary"
    assert read["summary"] == "Chair of the town council."


def test_read_returns_the_section_start_showed(nav):
    """The section a search hit names is what the read returns, even where vectors would pick another."""
    started = nav.start("Ashford Courier reporter account of the meeting")
    hit = next(hit for hit in started["results"] if hit["slug"] == "mill-meeting")
    assert hit["section"] == "mill-meeting > Sources"
    # Pretend the search had matched another section: the read follows the search, not its own pick.
    nav.conn.execute("UPDATE nav_frontier SET reason = 'mill-meeting > Participants' WHERE session_id = ? "
                     "AND slug = 'mill-meeting'", (started["session"],))
    assert nav.read(started["session"], "mill-meeting", WHY)["section"] == "Participants"


def test_read_several_sections_in_one_call(nav):
    session = start(nav)["session"]
    read = nav.read(session, "mill-meeting", WHY, sections=["Participants", "Sources"])
    assert read["section"] == "Participants, Sources"
    assert "[[silas-reed]]" in read["content"] and "[[courier-report]]" in read["content"]
    assert {item["name"] for item in read["sections"] if item.get("read")} == {"Participants", "Sources"}


@pytest.fixture
def big_nav(wiki):
    lines = "\n".join(f"- Step {n}: the press moves sheet {n} through roller {n % 7} before drying." for n in range(140))
    wiki.page("topic", "printing-press", {"Process": lines, "History": "Built in 1851 by the Ashford works."},
              summary="How the Courier's printing press works, step by step.")
    wiki.page("topic", "bakery", summary="The bakery on Mill Street.")
    cache = Cache(wiki.settings())
    cache.refresh()
    cache.embed_pending(HashEmbedder())
    yield Navigator(cache, embedder=HashEmbedder(), reranker=OverlapReranker())
    cache.close()


def test_a_large_section_is_cut_and_continued_with_more(big_nav):
    session = big_nav.start("What steps does the printing press take?")["session"]
    first = big_nav.read(session, "printing-press", WHY, max_chars=2000)
    assert first["section"] == "Process" and len(first["content"]) <= 2000
    assert first["content"].endswith("drying.") and "--more" in first["more"]
    seen = [first["content"]]
    result = first
    while "more" in result:
        result = big_nav.read(session, "printing-press", WHY, more=True, max_chars=2000)
        assert len(result["content"]) <= 2000 and "summary" not in result
        seen.append(result["content"])
    text = "\n".join(seen)
    assert "Step 0:" in text and "Step 139:" in text and text.count("Step 70:") == 1
    assert "History" not in text  # --more continues the section asked for, not the rest of the page
    with pytest.raises(NavError) as info:
        big_nav.read(session, "printing-press", WHY, more=True)
    assert info.value.code == "nothing-more"
    assert big_nav.read(session, "printing-press", WHY, sections=["History"])["pages_read"] == 1


def test_end_reports_a_page_cut_short(big_nav):
    session = big_nav.start("What steps does the printing press take?")["session"]
    big_nav.read(session, "printing-press", WHY, full=True, max_chars=2000)
    ended = big_nav.end(session, ["printing-press"])
    [cut] = ended["cut_short"]
    assert cut["slug"] == "printing-press" and cut["chars_not_read"] > 5000
    assert "partly read" in ended["sources"]


def test_end_refuses_a_gap_the_wiki_covers_until_it_is_read(nav):
    session = start(nav, "Who led the panel?")["session"]
    nav.read(session, "mill-meeting", WHY)
    with pytest.raises(NavError) as info:
        nav.end(session, ["mill-meeting"], gaps=["Ruth Marsh mill owner documented role"])
    assert info.value.code == "possible-answer" and "rmarsh" in str(info.value)
    nav.read(session, "rmarsh", WHY)
    ended = nav.end(session, ["mill-meeting", "rmarsh"], gaps=["Ruth Marsh mill owner documented role"])
    assert ended["not_covered"][0]["checked"].startswith("searched the wiki")


def test_end_records_a_real_gap_with_what_was_checked(nav):
    session = start(nav)["session"]
    nav.read(session, "mill-meeting", WHY)
    ended = nav.end(session, ["mill-meeting"], gaps=["bakery bread prices"])
    assert ended["not_covered"] == [{"part": "bakery bread prices", "checked": ended["not_covered"][0]["checked"]}]
    assert "(none)" in ended["not_covered"][0]["checked"] or "no match" in ended["not_covered"][0]["checked"]
    assert ended["sources"].endswith("Not found in the wiki: bakery bread prices.")


def test_end_with_a_lead_is_allowed_at_the_page_limit(nav):
    session = start(nav, max_pages=1)["session"]
    nav.read(session, "mill-meeting", WHY)
    assert nav.end(session, [], gaps=["Ruth Marsh mill owner documented role"])["not_covered"]


def test_cli_parts_sections_more_and_gaps(run, wiki_graph):
    run("index", "refresh")
    code, started, _ = run("nav", "start", "Who attended the meeting?", "--part", "who attended", "--part", "bakery")
    assert code == 0 and [part["part"] for part in started["parts"]] == ["who attended", "bakery"]
    session = started["session"]
    code, read, _ = run("nav", "read", session, "mill-meeting", "--why", WHY,
                        "--section", "Participants", "--section", "Sources", "--max-chars", "500")
    assert code == 0 and read["section"] == "Participants, Sources"
    code, refused, _ = run("nav", "read", session, "mill-meeting", "--why", WHY, "--more")
    assert code == 1 and refused["error"] == "nothing-more"
    code, ended, _ = run("nav", "end", session, "--cited", "mill-meeting", "--gap", "bakery bread prices")
    assert code == 0 and ended["not_covered"][0]["part"] == "bakery bread prices"


def test_cli_text_output(wiki_graph, capsys):
    root = ["--root", str(wiki_graph.root)]
    main(["index", "refresh", *root])
    capsys.readouterr()
    main(["nav", "start", "Who attended the meeting?", "--part", "who attended", *root, "--format", "json"])
    session = json.loads(capsys.readouterr().out)["session"]
    main(["nav", "read", session, "mill-meeting", "--why", WHY, *root])
    text = capsys.readouterr().out
    assert "Sections: " in text and "read)" in text
    main(["nav", "end", session, "--cited", "mill-meeting", "--gap", "bakery bread prices", *root])
    text = capsys.readouterr().out
    assert "Gap:" in text and "From the wiki: mill-meeting." in text
