"""`--group` on `wiki clusters` and `wiki map`: the pages that name a group, and those they
link to or are linked from."""

import json

import pytest

from wiki_cli.cache import Cache
from wiki_cli.cli import main
from wiki_cli.clusters import membership
from wiki_cli.config import ConfigError, load_settings
from wiki_cli.groups import GroupError, members


def page(root, slug, links=(), **front):
    meta = "".join(f"{key}: {json.dumps(value)}\n" for key, value in {"title": slug, "type": "note", **front}.items())
    related = "\n".join(f"- [[{target}]] — related" for target in links)
    (root / f"{slug}.md").write_text(f"---\n{meta}---\n# {slug}\n\n## Summary\nAbout {slug}.\n\n## Related\n{related}\n",
                                     encoding="utf-8")


@pytest.fixture
def root(tmp_path):
    """Two projects of 32 tagged pages each, ring-linked within the project; a person page
    both projects link to, untagged; a page that links into one project; and an unrelated
    pair."""
    root = tmp_path / "w"
    root.mkdir()
    (root / ".wiki-cli.toml").write_text("[types.note]\n", encoding="utf-8")
    for project, tag in (("alpha", ["Alpha"]), ("beta", "#beta")):  # a list, and a scalar with '#'
        ring = [f"{project}-{index}" for index in range(32)]
        for index, slug in enumerate(ring):
            page(root, slug, [ring[(index + 1) % 32], ring[(index + 2) % 32]] + (["ada"] if index == 0 else []),
                 tags=tag)
    page(root, "ada")
    page(root, "reading-list", ["beta-3"])
    page(root, "lone-a", ["lone-b"])
    page(root, "lone-b")
    return root


def group(root, name):
    with Cache(load_settings(root)) as cache:
        cache.refresh()
        return members(cache, name)


def test_a_group_is_its_tagged_pages_and_their_neighbours(root):
    alpha = group(root, "alpha")  # case and '#' are ignored
    assert alpha == {f"alpha-{index}" for index in range(32)} | {"ada"}
    beta = group(root, "#Beta")
    assert beta == {f"beta-{index}" for index in range(32)} | {"ada", "reading-list"}  # linked from, too
    assert "lone-a" not in alpha | beta


def test_an_unknown_group_lists_the_groups(root):
    with pytest.raises(GroupError, match=r"no page names the group 'gamma' in its tags frontmatter; groups: alpha, beta"):
        group(root, "gamma")


def test_group_fields_come_from_the_config(root):
    for slug in ("lone-a", "lone-b"):
        page(root, slug, ["lone-b"] if slug == "lone-a" else [], dossier="side", tags="ignored-here")
    (root / ".wiki-cli.toml").write_text('[types.note]\n[groups]\nfields = ["dossier"]\n', encoding="utf-8")
    assert group(root, "side") == {"lone-a", "lone-b"}
    with pytest.raises(GroupError, match="in its dossier frontmatter; groups: side"):
        group(root, "alpha")


@pytest.mark.parametrize("table, message", [
    ('[groups]\nfields = "tags"\n', r"'\[groups\] fields' must be a list of strings"),
    ("[groups]\nfields = []\n", "must name at least one frontmatter field"),
    ("[groups]\nfield = []\n", r"\[groups\]: unknown key\(s\) field"),
])
def test_groups_settings_are_checked(tmp_path, table, message):
    (tmp_path / ".wiki-cli.toml").write_text(table, encoding="utf-8")
    with pytest.raises(ConfigError, match=message):
        load_settings(tmp_path)


def test_clusters_of_one_group(root, capsys):
    assert main(["clusters", "--group", "alpha", "--root", str(root), "--format", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["group"] == "alpha" and result["pages"] == 33
    assert all(slug.startswith("alpha-") or slug == "ada" for cluster in result["clusters"] for slug in cluster["pages"])
    assert main(["clusters", "--group", "alpha", "--root", str(root)]) == 0
    assert "Group 'alpha': 33 pages" in capsys.readouterr().out
    page(root, "small-0", ["small-1"], tags="small")
    page(root, "small-1")
    assert main(["clusters", "--group", "small", "--root", str(root)]) == 0
    assert "2 pages in group 'small': too few" in capsys.readouterr().out
    assert main(["clusters", "--group", "gamma", "--root", str(root)]) == 2
    assert "groups: alpha, beta" in capsys.readouterr().err
    with Cache(load_settings(root)) as cache:
        assignment, _ = membership(cache, only=members(cache, "beta"))
    assert assignment and all(slug.startswith("beta-") or slug in ("ada", "reading-list") for slug in assignment)


def test_map_of_one_group(root, capsys):
    assert main(["index", "refresh", "--root", str(root), "--format", "json"]) == 0
    capsys.readouterr()
    assert main(["map", "--group", "beta", "--method", "pca", "--root", str(root), "--format", "json"]) == 0
    result = json.loads(capsys.readouterr().out)
    slugs = {point["slug"] for point in result["points"]}
    assert result["group"] == "beta" and slugs == {f"beta-{index}" for index in range(32)} | {"ada", "reading-list"}
    assert all(result["points"][edge["s"]]["slug"] in slugs for edge in result["edges"])
    assert main(["map", "--group", "beta", "--method", "pca", "--root", str(root)]) == 0
    assert "in group 'beta'" in capsys.readouterr().out
    html = (root / ".cache" / "map.html").read_text(encoding="utf-8")
    assert '"group": "beta"' in html or '"group":"beta"' in html  # the viewer's title names it
