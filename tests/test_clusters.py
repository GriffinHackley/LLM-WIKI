"""`wiki clusters`: densely linked groups of pages, and whether a hub page covers them."""

import json

import pytest

from wiki_cli.cache import Cache
from wiki_cli.cli import main
from wiki_cli.clusters import clusters
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
