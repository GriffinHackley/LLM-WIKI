from test_generic import repo  # noqa: F401  (fixture)
from wiki_cli.config import load_settings
from wiki_cli.edges import derive
from wiki_cli.pages import Resolver, load, resolve, scan_vault


def edges_of(wiki, slug):
    settings = wiki.settings()
    files, others = scan_vault(settings)
    resolver = Resolver([(f.slug, f.rel) for f, _ in files], others)
    return {edge.target: edge for edge in derive(load(resolve(slug, settings), settings), resolver)}


def test_document_sections(wiki):
    wiki.page("person", "charles-babbage")
    wiki.page("person", "ada-lovelace")
    wiki.page("claim", "N-049")
    wiki.page("document", "abc-doc", {
        "Entities mentioned": "- [[charles-babbage]] — Inventor who delayed the demonstration (p. 2)\n- [[ada-lovelace]]",
        "Claims supported": "- [[N-049]] — Babbage's stated rationale → sourced",
        "Summary": "Prose linking [[charles-babbage]] again. Raw file: [[abc-doc.txt]].",
    })
    wiki.raw_text("abc-doc.txt", "text")
    edges = edges_of(wiki, "abc-doc")
    assert (edges["charles-babbage"].type, edges["charles-babbage"].reason) == ("mentions", "Inventor who delayed the demonstration (p. 2)")
    assert (edges["ada-lovelace"].type, edges["ada-lovelace"].reason) == ("mentions", "Listed under Entities mentioned.")
    assert (edges["N-049"].type, edges["N-049"].reason) == ("supports", "Babbage's stated rationale → sourced")
    assert (edges["raw/abc-doc"].type, edges["raw/abc-doc"].resolved) == ("links-to", True)


def test_multi_link_line_drops_subject_list(wiki):
    wiki.page("person", "charles-babbage", {
        "Relationships": "- [[mary-somerville]], [[john-herschel]] — tutored them (doc, p. 2)"})
    edges = edges_of(wiki, "charles-babbage")
    assert edges["mary-somerville"].reason == edges["john-herschel"].reason == "tutored them (doc, p. 2)"
    assert edges["mary-somerville"].type == "associated-with"
    assert not edges["mary-somerville"].resolved  # page not written yet


def test_frontmatter_edges_and_specificity(wiki):
    wiki.page("document", "doc-1")
    wiki.page("person", "p", {"Appearances in sources": "- [[doc-1]] — named on p. 3"}, sources=["doc-1"])
    edge = edges_of(wiki, "p")["doc-1"]
    assert (edge.type, edge.reason) == ("appears-in", "named on p. 3")  # beats draws-on


def test_claim_page(wiki):
    wiki.page("claim", "N-125")
    wiki.page("document", "minutes")
    wiki.write("notebooks/ef/claims.md", {"title": "Claims"})
    wiki.write("wiki/claims/N-053.md", {"title": "N-053", "type": "claim", "rests_on": ["N-125"]},
               "# N-053\n\n## Claim\nThe delay departed from practice.\n\n## Sources\n[[minutes|Minutes]] pp. 1–2\n\n"
               "## Rests on\n- [[N-125]] — fact\n\n---\nBack to the ledger: [[notebooks/ef/claims|ledger]].\n")
    edges = edges_of(wiki, "N-053")
    assert edges["N-125"].type == "rests-on"
    assert edges["minutes"].type == "sourced-by"
    assert edges["claims"].type == "links-to"  # footer after the rule is not "rests on"


def test_quote_embed_and_generic_links(wiki):
    wiki.page("document", "memo")
    wiki.page("person", "tessa-brand")
    wiki.page("person", "oren-vale", {
        "Documented role": "She met [[tessa-brand]] on the 22nd. Later she resigned.\n\n![[memo#^q-no-list]]"})
    edges = edges_of(wiki, "oren-vale")
    assert (edges["memo"].type, edges["memo"].reason) == ("embeds", "Embeds block q-no-list.")
    assert (edges["tessa-brand"].type, edges["tessa-brand"].reason) == (
        "links-to", "Documented role: She met tessa-brand on the 22nd.")


def test_self_links_and_heading_links_ignored(wiki):
    wiki.page("person", "p", {"Timeline": "See [[p]] and [[#Summary]]."})
    assert edges_of(wiki, "p") == {}


def test_event_and_place_sections(wiki):
    wiki.page("event", "engine-demo", {"Participants": "- [[charles-babbage]] — presented the engine",
                                        "Location": "[[royal-society]]"})
    wiki.page("place", "royal-society", {"Events here": "- [[engine-demo]]"})
    event = edges_of(wiki, "engine-demo")
    assert (event["charles-babbage"].type, event["royal-society"].type) == ("involves", "located-at")
    assert edges_of(wiki, "royal-society")["engine-demo"].type == "hosted"


def test_long_reason_clipped(wiki):
    wiki.page("person", "p", {"Relationships": "- [[q]] — " + "detail " * 60})
    reason = edges_of(wiki, "p")["q"].reason
    assert len(reason) <= 161 and reason.endswith("…")


def test_links_to_existing_attachments_are_not_edges(wiki):
    wiki.write("raw/scan-0001.pdf", raw="%PDF")
    wiki.page("document", "board-memo", {"What this is": "Scan: [[scan-0001.pdf]]. See [[ghost-page]]."})
    edges = edges_of(wiki, "board-memo")
    assert "scan-0001.pdf" not in edges
    assert not edges["ghost-page"].resolved


def test_embeds_outrank_field_only_types(wiki):
    wiki.page("document", "memo")
    wiki.page("person", "p", {"Documented role": "![[memo#^q-key]]"}, sources=["memo"])
    edge = edges_of(wiki, "p")["memo"]
    assert (edge.type, edge.reason) == ("embeds", "Embeds block q-key.")


def test_reason_joins_hard_wrapped_lines(wiki):
    wiki.page("topic", "t", {"Background": "Written by [[ada]]. She wrote the [[scheduler]] design and\n"
                                           "reviews every change to it."})
    edges = edges_of(wiki, "t")
    assert edges["scheduler"].reason == "Background: She wrote the scheduler design and reviews every change to it."
    assert edges["ada"].reason == "Background: Written by ada."


def test_list_item_keeps_a_link_inside_its_sentence(wiki):
    wiki.page("topic", "t", {"Background": "- Demo date fixed for March on the [[engine]].\n- Co-lead: [[ada]]\n"
                                           "- [[bob]] and the [[scheduler]] team — reviewers\n- [[carol]]"})
    edges = edges_of(wiki, "t")
    assert edges["carol"].reason == "Listed under Background."
    assert edges["engine"].reason == "Demo date fixed for March on the engine."
    assert edges["ada"].reason == "Co-lead"
    assert edges["scheduler"].reason == "the scheduler team — reviewers"


def test_reason_finds_the_link_not_an_earlier_mention_of_its_name(wiki):
    wiki.page("topic", "t", {"Background": "The report (commission-report-1994) came first. Then the "
                                           "[[commission]] recommended separate gates."})
    assert edges_of(wiki, "t")["commission"].reason == "Background: Then the commission recommended separate gates."


def test_long_sentence_reason_ends_at_the_link(wiki):
    lead = "In a long and winding sentence that keeps going " * 4
    wiki.page("topic", "t", {"Background": f"{lead}the engineer [[bob]] said it plainly."})
    reason = edges_of(wiki, "t")["bob"].reason
    assert reason.startswith("Background: …") and "the engineer bob" in reason and len(reason) <= 160


def test_citation_in_parentheses_is_not_the_subject(wiki):
    wiki.page("person", "charles-babbage", {
        "Relationships": "- [[john-herschel]] — tutored him ([[journal-doc]], p. 2)"})
    edges = edges_of(wiki, "charles-babbage")
    assert edges["john-herschel"].type == "associated-with"
    assert edges["journal-doc"].type == "links-to"  # a citation supports the line; it is not its subject


def test_field_relation_borrows_the_citing_sentence(repo):
    (repo / ".wiki-cli.toml").write_text(
        '[[relations]]\nfield = "sources"\ntype = "draws-on"\ninverse = "drawn-on-by"\n', encoding="utf-8")
    repo.write("report.md", "# Report\n")
    repo.write("other.md", "# Other\n")
    repo.write("ada.md", "---\nsources: [report, other]\n---\n# Ada\n\n"
                         "Ada signed the treaty in March ([[report]], p. 4).\n")
    settings = load_settings(repo)
    files, others = scan_vault(settings)
    resolver = Resolver([(f.slug, f.rel) for f, _ in files], others)
    edges = {edge.target: edge for edge in derive(load(resolve("ada", settings), settings), resolver)}
    assert edges["report"].type == "draws-on"
    assert edges["report"].reason == "Ada: Ada signed the treaty in March (report, p. 4)."
    assert edges["other"].reason == "Listed in sources."  # cited nowhere in the body
