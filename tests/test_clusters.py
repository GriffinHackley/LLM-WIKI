"""`wiki clusters`: densely linked groups of pages, and whether a hub page covers them."""

import json

import pytest

from wiki_cli.cache import Cache
from wiki_cli.cli import main
from wiki_cli.clusters import _reach, _pages, clusters, membership, overloaded_hubs
from wiki_cli.config import ConfigError, load_settings

CONFIG = """\
[types.module]
hub = true
[types.file]
[types.pr]
[types.note]
"""


def page(root, slug, page_type, summary, links):
    related = "\n".join(f"- [[{target}]] — related" for target in links)
    (root / f"{slug}.md").write_text(f"---\ntitle: {slug}\ntype: {page_type}\n---\n# {slug}\n\n## Summary\n"
                                     f"{summary}\n\n## Related\n{related}\n", encoding="utf-8")


@pytest.fixture
def root(tmp_path):
    """A covered group (cache files around a module page), an uncovered one (invoice
    files a PR touched, with no module page), and pairs of notes that are too small."""
    root = tmp_path / "w"
    root.mkdir()
    (root / ".wiki-cli.toml").write_text(CONFIG, encoding="utf-8")
    caches = [f"cache-{index}" for index in range(8)]
    invoices = [f"invoice-{index}" for index in range(8)]
    page(root, "cache-module", "module", "The cache layer.", [])
    for index, slug in enumerate(caches):
        page(root, slug, "file", f"Cache eviction part {index}.",
             ["cache-module", caches[(index + 1) % 8], caches[(index + 2) % 8]])
    page(root, "pr-7", "pr", "Invoice rounding change.", invoices)
    for index, slug in enumerate(invoices):
        page(root, slug, "file", f"Invoice rounding part {index}.",
             [invoices[(index + 1) % 8], invoices[(index + 2) % 8]] + (["cache-0"] if index == 0 else []))
    for index in range(8):
        page(root, f"note-{index}a", "note", f"Note {index}.", [f"note-{index}b"])
        page(root, f"note-{index}b", "note", f"Note {index} reply.", [])
    return root


def run(root, **kwargs):
    with Cache(load_settings(root)) as cache:
        cache.refresh()
        return clusters(cache, **kwargs)


def test_reports_only_clusters_no_hub_page_covers(root):
    result = run(root)
    assert result["hub_types"] == ["module"] and result["covered"] == 1
    [cluster] = result["clusters"]
    assert set(cluster["pages"]) == {"pr-7", *(f"invoice-{index}" for index in range(8))}
    assert cluster["covered_by"] is None
    assert cluster["most_linked"]["slug"] == "pr-7" and cluster["most_linked"]["type"] == "pr"
    assert cluster["terms"][:2] == ["invoice", "rounding"] and cluster["relations"] == {"links-to": 24}


def test_all_includes_covered_clusters(root):
    result = run(root, include_covered=True)
    covered = [cluster for cluster in result["clusters"] if cluster["covered_by"]]
    assert [cluster["covered_by"]["slug"] for cluster in covered] == ["cache-module"]
    assert covered[0]["covered_by"]["share"] == 1.0
    assert run(root, include_covered=True) == result  # the same wiki gives the same clusters


def test_without_hub_types_every_cluster_is_listed(root):
    (root / ".wiki-cli.toml").write_text("", encoding="utf-8")
    result = run(root)
    assert result["hub_types"] == [] and len(result["clusters"]) == 2
    assert {cluster["most_linked"]["slug"] for cluster in result["clusters"]} == {"cache-module", "pr-7"}


def test_small_wikis_are_skipped(tmp_path):
    root = tmp_path / "w"
    root.mkdir()
    for index in range(5):
        page(root, f"p{index}", "note", "A note.", [f"p{(index + 1) % 5}"])
    result = run(root)
    assert result["too_small"] == 30 and result["clusters"] == []


def test_cli(root, capsys):
    assert main(["clusters", "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert out.startswith("Clusters of 4+ pages no hub page covers (1)\n"
                          "Hub types: module. 1 more cluster is covered (--all lists them).\n")
    assert "most linked  pr-7 (pr), linked with 8 of them" in out and "terms        invoice" in out
    assert main(["clusters", "--root", str(root), "--all", "--format", "json"]) == 0
    assert len(json.loads(capsys.readouterr().out)["clusters"]) == 2


def test_hub_must_be_a_boolean(tmp_path):
    (tmp_path / ".wiki-cli.toml").write_text('[types.module]\nhub = "yes"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="hub must be true or false"):
        load_settings(tmp_path)


def test_membership_names_a_covered_cluster_after_its_hub_page(root):
    with Cache(load_settings(root)) as cache:
        cache.refresh()
        assignment, names = membership(cache)
    assert names[assignment["cache-0"]] == "cache-module"  # the hub page's title
    invoice_name = names[assignment["invoice-0"]]
    assert set(invoice_name.split(", ")) <= {"invoice", "rounding", "part"} and "invoice" in invoice_name


@pytest.fixture
def split_root(tmp_path):
    """One module page that two separate rings of files (a parser and a renderer) both
    point to, one module with a single ring, and notes to pass the size threshold."""
    root = tmp_path / "split"
    root.mkdir()
    (root / ".wiki-cli.toml").write_text(CONFIG, encoding="utf-8")
    page(root, "core", "module", "Everything in src/core.", [])
    page(root, "cache-module", "module", "The cache layer.", [])
    for area, hub in (("parser", "core"), ("renderer", "core"), ("cache", "cache-module")):
        ring = [f"{area}-{index}" for index in range(8)]
        for index, slug in enumerate(ring):
            page(root, slug, "file", f"{area.title()} part {index}.",
                 [hub, ring[(index + 1) % 8], ring[(index + 2) % 8]])
    for index in range(4):
        page(root, f"note-{index}a", "note", f"Note {index}.", [f"note-{index}b"])
        page(root, f"note-{index}b", "note", f"Note {index} reply.", [])
    return root


def check_json(root, capsys, *args):
    main(["check", *args, "--root", str(root), "--format", "json"])
    return [issue for issue in json.loads(capsys.readouterr().out)["issues"] if issue["code"] == "hub-covers-clusters"]


def test_check_warns_on_a_hub_over_several_clusters(split_root, capsys):
    assert main(["index", "refresh", "--root", str(split_root), "--format", "json"]) == 0
    capsys.readouterr()
    [issue] = check_json(split_root, capsys, "--all")
    assert issue["slug"] == "core" and issue["severity"] == "warning" and issue["path"] == "core.md"
    message = issue["message"]
    assert "hub of 2 separate clusters" in message and "splitting this page" in message
    assert "parser-" in message and "renderer-" in message and "cache-" not in message

    assert [issue["slug"] for issue in check_json(split_root, capsys, "core")] == ["core"]
    assert check_json(split_root, capsys, "cache-module") == []  # a hub over one cluster is fine
    assert check_json(split_root, capsys, "parser-0") == []  # not a hub page


def test_no_hub_warning_in_a_small_wiki(tmp_path, capsys):
    root = tmp_path / "small"
    root.mkdir()
    (root / ".wiki-cli.toml").write_text(CONFIG, encoding="utf-8")
    page(root, "core", "module", "Everything.", [])
    for area in ("parser", "renderer"):
        for index in range(4):
            page(root, f"{area}-{index}", "file", f"{area} {index}.", ["core", f"{area}-{(index + 1) % 4}"])
    assert main(["index", "refresh", "--root", str(root), "--format", "json"]) == 0
    capsys.readouterr()
    assert check_json(root, capsys, "--all") == []


def test_ignored_pages_are_left_out_of_clusters(root, capsys):
    page(root, "open-questions", "note", "Questions.", ["cache-0", "cache-4", "invoice-0", "invoice-4", "note-0a"])
    (root / ".wiki-cli.toml").write_text(CONFIG + '[clusters]\nignore = ["open-questions.md"]\n', encoding="utf-8")
    result = run(root, include_covered=True)
    assert all("open-questions" not in cluster["pages"] for cluster in result["clusters"])
    with Cache(load_settings(root)) as cache:
        total = cache.conn.execute("SELECT COUNT(*) FROM pages WHERE kind = 'page'").fetchone()[0]
        assignment, _ = membership(cache)
    assert result["pages"] == total - 1 and "open-questions" not in assignment


def test_a_higher_resolution_splits_more(root, capsys):
    default = run(root, include_covered=True)
    finer = run(root, include_covered=True, resolution=4.0, min_size=2)
    assert default["resolution"] == 1.0 and finer["resolution"] == 4.0
    assert len(finer["clusters"]) > len(default["clusters"])
    (root / ".wiki-cli.toml").write_text(CONFIG + "[clusters]\nresolution = 4.0\n", encoding="utf-8")
    assert run(root, include_covered=True, min_size=2)["clusters"] == finer["clusters"]  # the setting, too
    capsys.readouterr()
    assert main(["clusters", "--all", "--resolution", "4", "--min-size", "2", "--root", str(root)]) == 0
    assert "Resolution 4 (1 is the default; higher splits more readily)." in capsys.readouterr().out


@pytest.mark.parametrize("table, message", [
    ("[clusters]\nresolution = 0\n", "resolution must be a number above 0"),
    ("[clusters]\nresolution = true\n", "resolution must be a number above 0"),
    ("[clusters]\nignore = \"open-questions.md\"\n", r"'\[clusters\] ignore' must be a list of strings"),
    ("[clusters]\nhubs = []\n", r"\[clusters\]: unknown key\(s\) hubs"),
])
def test_clusters_settings_are_checked(tmp_path, table, message):
    (tmp_path / ".wiki-cli.toml").write_text(table, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_settings(tmp_path)


# -- hub_through with inverse names: hubs whose relations point outward ------------------

RESEARCH = """\
[types.event]
hub = true
hub_through = {through}
[types.claim]
[types.document]
[types.org]
hub = true
hub_through = ["associated-with"]
[types.person]
[types.note]

[[relations]]
heading = "Sources"
page_type = "event"
type = "appears-in"
inverse = "features"

[[relations]]
heading = "Sources"
page_type = "claim"
type = "sourced-by"
inverse = "source-for"

[[relations]]
heading = "Relationships"
page_type = ["org", "person"]
type = "associated-with"
inverse = "associated-with"
"""


def research_page(root, slug, page_type, sections):
    body = "".join(f"\n## {heading}\n" + "\n".join(f"- [[{target}]] — why" for target in links) + "\n"
                   for heading, links in sections.items())
    (root / f"{slug}.md").write_text(f"---\ntitle: {slug}\ntype: {page_type}\n---\n# {slug}\n\n## Summary\n"
                                     f"About {slug}.\n{body}", encoding="utf-8")


def research_wiki(root, through):
    """An event whose relations all point outward (event -appears-in-> document), claims
    sourced by those documents (claim -sourced-by-> document), and nothing pointing into
    the event; an org tied to people by a symmetric relation, stored in both directions;
    and notes to pass the size threshold."""
    root.mkdir()
    (root / ".wiki-cli.toml").write_text(RESEARCH.format(through=json.dumps(through)), encoding="utf-8")
    documents = ["doc-0", "doc-1"]
    claims = [f"claim-{index}" for index in range(8)]
    research_page(root, "meeting", "event", {"Sources": documents})
    for slug in documents:
        research_page(root, slug, "document", {})
    for index, slug in enumerate(claims):
        research_page(root, slug, "claim", {"Sources": [documents[index % 2]],
                                            "Related": [claims[(index + step) % 8] for step in (1, 2, 3)]})
    people = [f"person-{index}" for index in range(10)]
    research_page(root, "acme", "org", {"Relationships": people[:2]})  # acme -> person-0, person-1
    for index, slug in enumerate(people[2:], start=2):
        linked = [people[index % 2]] if index < 6 else []  # person-2..5 -> person-0 or person-1
        research_page(root, slug, "person", {"Relationships": linked,
                                             "Related": [people[2 + (index - 1) % 8], people[2 + index % 8]]})
    research_page(root, "person-0", "person", {"Relationships": people[6:8]})  # person-0 -> person-6, 7
    research_page(root, "person-1", "person", {"Relationships": people[8:]})  # person-1 -> person-8, 9
    for index in range(4):
        page(root, f"note-{index}a", "note", f"Note {index}.", [f"note-{index}b"])
        page(root, f"note-{index}b", "note", f"Note {index} reply.", [])
    return root


def cluster_of(result, slug):
    [found] = [cluster for cluster in result["clusters"] if slug in cluster["pages"]]
    return found


def reach_of(root):
    with Cache(load_settings(root)) as cache:
        cache.refresh()
        return _reach(cache, _pages(cache))


def test_an_inverse_name_lets_an_outward_hub_cover_its_cluster(tmp_path):
    root = research_wiki(tmp_path / "w", ["features", "sourced-by"])
    assert reach_of(root)["meeting"] == {"doc-0", "doc-1"} | {f"claim-{index}" for index in range(8)}
    cluster = cluster_of(run(root, include_covered=True), "claim-0")
    assert cluster["covered_by"]["slug"] == "meeting" and cluster["covered_by"]["share"] == 1.0
    assert cluster["covered_by"]["through"] == 8  # every claim, by its document
    with Cache(load_settings(root)) as cache:
        assignment, names = membership(cache)
        assert names[assignment["claim-0"]] == "meeting"  # wiki map names the cluster after the event
        assert overloaded_hubs(cache) == {}


def test_forward_names_alone_do_not_reach_an_outward_hub(tmp_path):
    root = research_wiki(tmp_path / "w", ["appears-in", "sourced-by"])
    assert "meeting" not in reach_of(root)
    assert cluster_of(run(root, include_covered=True), "claim-0")["covered_by"] is None


def test_a_symmetric_relation_is_walked_both_ways_once(tmp_path):
    root = research_wiki(tmp_path / "w", ["features", "sourced-by"])
    # person-2..5 point at person-0/1, which acme points at; person-6..9 are pointed at by them
    assert reach_of(root)["acme"] == {f"person-{index}" for index in range(10)}
    covered_by = cluster_of(run(root, include_covered=True), "person-5")["covered_by"]
    assert covered_by["slug"] == "acme" and covered_by["share"] == 1.0  # each person counted once


@pytest.mark.parametrize("through, message", [
    ('["features", "source-for"]', None),
    ('["appears-in", "sourced-by"]', None),
    ('["featured"]', "rule defines as its type or inverse: featured"),
    ('["linked-from"]', "rule defines as its type or inverse: linked-from"),
])
def test_hub_through_accepts_types_and_inverses(tmp_path, through, message):
    (tmp_path / ".wiki-cli.toml").write_text(RESEARCH.format(through=through), encoding="utf-8")
    if message is None:
        event = next(page_type for page_type in load_settings(tmp_path).types if page_type.name == "event")
        assert event.hub_through == tuple(json.loads(through))
    else:
        with pytest.raises(ConfigError, match=message):
            load_settings(tmp_path)
