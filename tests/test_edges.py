from wiki_cli.edges import derive
from wiki_cli.pages import Resolver, discover, load, resolve


def edges_of(wiki, slug):
    settings = wiki.settings()
    files = discover(settings)
    resolver = Resolver([(f.slug, f.rel) for f in files])
    return {edge.target: edge for edge in derive(load(resolve(slug, settings)), resolver)}


def test_document_sections(wiki):
    wiki.page("person", "mike-johnson")
    wiki.page("person", "adelita-grijalva")
    wiki.page("claim", "EF-049")
    wiki.page("document", "abc-doc", {
        "Entities mentioned": "- [[mike-johnson]] — Speaker who delayed the oath (p. 2)\n- [[adelita-grijalva]]",
        "Claims supported": "- EF-049 — Johnson's stated rationale → sourced",
        "Summary": "Prose linking [[mike-johnson]] again.",
    }, source_path="raw/abc-doc.html")
    wiki.raw_text("abc-doc.txt", "text")
    edges = edges_of(wiki, "abc-doc")
    assert (edges["mike-johnson"].type, edges["mike-johnson"].reason) == ("mentions", "Speaker who delayed the oath (p. 2)")
    assert (edges["adelita-grijalva"].type, edges["adelita-grijalva"].reason) == ("mentions", "Listed under Entities mentioned.")
    assert (edges["EF-049"].type, edges["EF-049"].reason) == ("supports", "Johnson's stated rationale → sourced")
    assert (edges["raw/abc-doc"].type, edges["raw/abc-doc"].resolved) == ("transcribes", True)


def test_multi_link_line_drops_subject_list(wiki):
    wiki.page("person", "mike-johnson", {
        "Relationships": "- [[jimmy-patronis]], [[randy-fine]] — administered their oaths (doc, p. 2)"})
    edges = edges_of(wiki, "mike-johnson")
    assert edges["jimmy-patronis"].reason == edges["randy-fine"].reason == "administered their oaths (doc, p. 2)"
    assert edges["jimmy-patronis"].type == "associated-with"
    assert not edges["jimmy-patronis"].resolved  # page not written yet


def test_frontmatter_edges_and_specificity(wiki):
    wiki.page("document", "doc-1")
    wiki.page("person", "p", {"Appearances in sources": "- [[doc-1]] — named on p. 3"}, sources=["doc-1"])
    edge = edges_of(wiki, "p")["doc-1"]
    assert (edge.type, edge.reason) == ("appears-in", "named on p. 3")  # beats draws-on


def test_claim_page(wiki):
    wiki.page("claim", "EF-125")
    wiki.page("document", "crec")
    wiki.write("dossiers/ef/claims.md", {"title": "Claims"})
    wiki.write("wiki/claims/EF-053.md", {"title": "EF-053", "type": "claim", "rests_on": ["EF-125"]},
               "# EF-053\n\n## Claim\nThe delay departed from practice.\n\n## Sources\n[[crec|CREC]] pp. 1–2\n\n"
               "## Rests on\n- [[EF-125]] — fact\n\n---\nBack to the ledger: [[dossiers/ef/claims|ledger]].\n")
    edges = edges_of(wiki, "EF-053")
    assert edges["EF-125"].type == "rests-on"
    assert edges["crec"].type == "sourced-by"
    assert edges["claims"].type == "links-to"  # footer after the rule is not "rests on"


def test_quote_embed_and_generic_links(wiki):
    wiki.page("document", "memo")
    wiki.page("person", "kash-patel")
    wiki.page("person", "pam-bondi", {
        "Documented role": "She met [[kash-patel]] on the 22nd. Later she resigned.\n\n![[memo#^q-no-list]]"})
    edges = edges_of(wiki, "pam-bondi")
    assert (edges["memo"].type, edges["memo"].reason) == ("quotes", "Embeds quote q-no-list.")
    assert (edges["kash-patel"].type, edges["kash-patel"].reason) == (
        "links-to", "Documented role: She met kash-patel on the 22nd.")


def test_self_links_and_heading_links_ignored(wiki):
    wiki.page("person", "p", {"Timeline": "See [[p]] and [[#Summary]]."})
    assert edges_of(wiki, "p") == {}


def test_event_and_place_sections(wiki):
    wiki.page("event", "swearing-in", {"Participants": "- [[mike-johnson]] — administered the oath",
                                        "Location": "[[us-capitol]]"})
    wiki.page("place", "us-capitol", {"Events here": "- [[swearing-in]]"})
    event = edges_of(wiki, "swearing-in")
    assert (event["mike-johnson"].type, event["us-capitol"].type) == ("involves", "located-at")
    assert edges_of(wiki, "us-capitol")["swearing-in"].type == "hosted"


def test_long_reason_clipped(wiki):
    wiki.page("person", "p", {"Relationships": "- [[q]] — " + "detail " * 60})
    reason = edges_of(wiki, "p")["q"].reason
    assert len(reason) <= 161 and reason.endswith("…")


def test_links_to_existing_attachments_are_not_edges(wiki):
    wiki.write("raw/scan-EFTA01.pdf", raw="%PDF")
    wiki.page("document", "fbi-memo", {"What this is": "Scan: [[scan-EFTA01.pdf]]. See [[ghost-page]]."})
    settings = wiki.settings()
    from wiki_cli.pages import scan_vault
    files, others = scan_vault(settings)
    resolver = Resolver([(f.slug, f.rel) for f, _ in files], others)
    edges = {edge.target: edge for edge in derive(load(resolve("fbi-memo", settings)), resolver)}
    assert "scan-EFTA01.pdf" not in edges
    assert not edges["ghost-page"].resolved
