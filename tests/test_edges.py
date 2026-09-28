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
    wiki.page("claim", "F-049")
    wiki.page("document", "abc-doc", {
        "Entities mentioned": "- [[charles-babbage]] — Inventor who delayed the demonstration (p. 2)\n- [[ada-lovelace]]",
        "Claims supported": "- [[F-049]] — Babbage's stated rationale → sourced",
        "Summary": "Prose linking [[charles-babbage]] again. Raw file: [[abc-doc.txt]].",
    })
    wiki.raw_text("abc-doc.txt", "text")
    edges = edges_of(wiki, "abc-doc")
    assert (edges["charles-babbage"].type, edges["charles-babbage"].reason) == ("mentions", "Inventor who delayed the demonstration (p. 2)")
    assert (edges["ada-lovelace"].type, edges["ada-lovelace"].reason) == ("mentions", "Listed under Entities mentioned.")
    assert (edges["F-049"].type, edges["F-049"].reason) == ("supports", "Babbage's stated rationale → sourced")
    assert (edges["raw/abc-doc"].type, edges["raw/abc-doc"].resolved) == ("links-to", True)


def test_multi_link_line_drops_subject_list(wiki):
    wiki.page("person", "charles-babbage", {
        "Relationships": "- [[grace-hopper]], [[alan-turing]] — tutored them (doc, p. 2)"})
    edges = edges_of(wiki, "charles-babbage")
    assert edges["grace-hopper"].reason == edges["alan-turing"].reason == "tutored them (doc, p. 2)"
    assert edges["grace-hopper"].type == "associated-with"
    assert not edges["grace-hopper"].resolved  # page not written yet


def test_frontmatter_edges_and_specificity(wiki):
    wiki.page("document", "doc-1")
    wiki.page("person", "p", {"Appearances in sources": "- [[doc-1]] — named on p. 3"}, sources=["doc-1"])
    edge = edges_of(wiki, "p")["doc-1"]
    assert (edge.type, edge.reason) == ("appears-in", "named on p. 3")  # beats draws-on


def test_claim_page(wiki):
    wiki.page("claim", "F-125")
    wiki.page("document", "minutes")
    wiki.write("projects/f/claims.md", {"title": "Claims"})
    wiki.write("wiki/claims/F-053.md", {"title": "F-053", "type": "claim", "rests_on": ["F-125"]},
               "# F-053\n\n## Claim\nThe delay departed from practice.\n\n## Sources\n[[minutes|Minutes]] pp. 1–2\n\n"
               "## Rests on\n- [[F-125]] — fact\n\n---\nBack to the ledger: [[projects/f/claims|ledger]].\n")
    edges = edges_of(wiki, "F-053")
    assert edges["F-125"].type == "rests-on"
    assert edges["minutes"].type == "sourced-by"
    assert edges["claims"].type == "links-to"  # footer after the rule is not "rests on"


def test_quote_embed_and_generic_links(wiki):
    wiki.page("document", "memo")
    wiki.page("person", "ruth-marsh")
    wiki.page("person", "silas-reed", {
        "Documented role": "She met [[ruth-marsh]] on the 22nd. Later she resigned.\n\n![[memo#^q-no-list]]"})
    edges = edges_of(wiki, "silas-reed")
    assert (edges["memo"].type, edges["memo"].reason) == ("embeds", "Embeds block q-no-list.")
    assert (edges["ruth-marsh"].type, edges["ruth-marsh"].reason) == (
        "links-to", "Documented role: She met ruth-marsh on the 22nd.")


def test_self_links_and_heading_links_ignored(wiki):
    wiki.page("person", "p", {"Timeline": "See [[p]] and [[#Summary]]."})
    assert edges_of(wiki, "p") == {}


def test_event_and_place_sections(wiki):
    wiki.page("event", "engine-demo", {"Participants": "- [[charles-babbage]] — presided",
                                        "Location": "[[guild-hall]]"})
    wiki.page("place", "guild-hall", {"Events here": "- [[engine-demo]]"})
    event = edges_of(wiki, "engine-demo")
    assert (event["charles-babbage"].type, event["guild-hall"].type) == ("involves", "located-at")
    assert edges_of(wiki, "guild-hall")["engine-demo"].type == "hosted"


def test_long_reason_clipped(wiki):
    wiki.page("person", "p", {"Relationships": "- [[q]] — " + "detail " * 60})
    reason = edges_of(wiki, "p")["q"].reason
    assert len(reason) <= 161 and reason.endswith("…")


def test_links_to_existing_attachments_are_not_edges(wiki):
    wiki.write("raw/scan-0001.pdf", raw="%PDF")
    wiki.page("document", "council-memo", {"What this is": "Scan: [[scan-0001.pdf]]. See [[ghost-page]]."})
    edges = edges_of(wiki, "council-memo")
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
    wiki.page("topic", "t", {"Background": f"{lead}the minister [[bob]] said it plainly."})
    reason = edges_of(wiki, "t")["bob"].reason
    assert reason.startswith("Background: …") and "the minister bob" in reason and len(reason) <= 160


def test_citation_in_parentheses_is_not_the_subject(wiki):
    wiki.page("person", "charles-babbage", {
        "Relationships": "- [[alan-turing]] — tutored him ([[minutes-doc]], p. 2)"})
    edges = edges_of(wiki, "charles-babbage")
    assert edges["alan-turing"].type == "associated-with"
    assert edges["minutes-doc"].type == "links-to"  # a citation supports the line; it is not its subject


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
