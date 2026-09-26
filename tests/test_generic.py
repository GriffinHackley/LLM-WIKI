"""The tool on wikis that are not the Politics wiki: no config, Markdown links, custom rules."""

import json
import tomllib

import pytest
from pathlib import Path

from conftest import bump
from wiki_cli import links
from wiki_cli.cache import Cache
from wiki_cli.cli import main
from wiki_cli.config import ConfigError, load_settings
from wiki_cli.models import HashEmbedder
from wiki_cli.pages import Resolver, load, resolve


class Repo(Path):
    def write(self, rel: str, text: str) -> Path:
        path = self / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        return path


@pytest.fixture
def repo(tmp_path):
    """A plain docs repo: no .wiki-cli.toml, Markdown links, notes at several depths."""
    root = Repo(tmp_path / "docs-repo")
    root.mkdir()
    root.write("README.md", "# Project\n\nStart with the [setup guide](guides/setup.md).\n")
    root.write("guides/setup.md", "---\ndescription: How to install and configure.\n---\n# Setup\n\n"
                                  "## Steps\n- [Install](install.md) — get the binaries\n"
                                  "- [Config reference](../reference/config%20file.md#options) — every option\n\n"
                                  "See the [homepage](https://example.com) and ![diagram](img/flow.png).\n")
    root.write("guides/install.md", "# Install\n\nRun the installer.\n")
    root.write("reference/config file.md", "# Config file\n\nOptions go here.\n")
    root.write("guides/img/flow.png", "PNG")
    return root


def run(root, *args):
    return main([*args, "--root", str(root), "--format", "json"])


def test_zero_config_markdown_links(repo, capsys):
    assert run(repo, "neighbors", "setup") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["neighbors"] == [
        {"slug": "config file", "direction": "outgoing", "type": "links-to", "title": "Config file",
         "reason": "every option"},
        {"slug": "install", "direction": "outgoing", "type": "links-to", "title": "Install",
         "reason": "get the binaries"},
        {"slug": "README", "direction": "incoming", "type": "linked-from", "title": "Project",
         "reason": "Project: Start with the setup guide."},
    ]


def test_zero_config_check_and_summary(repo, capsys):
    assert run(repo, "check", "--all") == 0  # no frontmatter required, image link exists
    result = json.loads(capsys.readouterr().out)
    assert result["errors"] == 0 and result["issues"] == []
    page = load(resolve("setup", load_settings(repo)), load_settings(repo))
    assert page.summary() == ("How to install and configure.", False)  # from `description:`


def test_zero_config_search(repo, capsys):
    assert run(repo, "search", "installer", "--keyword-only") == 0
    assert json.loads(capsys.readouterr().out)["results"][0]["slug"] == "install"


def test_no_raw_folder_is_fine(repo, capsys):
    assert run(repo, "search", "installer", "--keyword-only", "--include-raw") == 0
    assert json.loads(capsys.readouterr().out)["results"][0]["slug"] == "install"
    assert run(repo, "index", "status") == 0
    assert json.loads(capsys.readouterr().out)["raw"] == 0


def test_nested_raw_folder_slugs(repo, capsys):
    repo.write("raw/2024/interview.txt", "Transcript of the interview.")
    repo.write("guides/notes.md", "# Notes\n\nFrom [the interview](../raw/2024/interview.txt).\n")
    assert run(repo, "neighbors", "notes") == 0
    [entry] = json.loads(capsys.readouterr().out)["neighbors"]
    assert entry["slug"] == "raw/2024/interview" and "unresolved" not in entry


class TestMarkdownTargets:
    @pytest.mark.parametrize("href, source, expected", [
        ("install.md", "guides/setup.md", ("guides/install", None)),
        ("../reference/config%20file.md#options", "guides/setup.md", ("reference/config file", "options")),
        ("</reference/config file.md>", "guides/setup.md", ("reference/config file", None)),
        ("/README.md", "guides/setup.md", ("README", None)),
        ("img/flow.png", "guides/setup.md", ("guides/img/flow.png", None)),
        ("https://example.com/a.md", "guides/setup.md", None),
        ("mailto:x@y.z", "a.md", None),
        ("#section", "a.md", None),
        ("../../outside.md", "guides/setup.md", None),
    ])
    def test_markdown_target(self, href, source, expected):
        assert links.markdown_target(href, source) == expected

    def test_extract_both_syntaxes(self):
        body = "## Links\n- [[wiki-page|Wiki]] and [Doc](docs/doc.md)\n![img](a.png)\n"
        found = [(l.target, l.display, l.is_path, l.embed) for l in links.extract_links(body, "index.md")]
        assert found == [("wiki-page", "Wiki", False, False), ("docs/doc", "Doc", True, False),
                         ("a.png", "img", True, True)]

    def test_path_links_never_resolve_by_bare_name(self):
        resolver = Resolver([("install", "guides/install.md")])
        assert resolver.resolve_path("install") is None  # a root-level "install.md" does not exist
        assert resolver.resolve_path("guides/install") == "install"
        assert resolver.resolve("install") == "install"


class TestConfig:
    def write_config(self, repo, text):
        (repo / ".wiki-cli.toml").write_text(text, encoding="utf-8")
        return load_settings(repo)

    @pytest.mark.parametrize("text, message", [
        ('[[relations]]\nheading = "A"\ntype = "x"\n', "'inverse'"),
        ('[[relations]]\nheading = "A"\nfield = "f"\ntype = "x"\ninverse = "y"\n', "exactly one"),
        ('[[relations]]\ntype = "x"\ninverse = "y"\n', "exactly one"),
        ('[[relations]]\nheading = "A"\ntype = "Bad Name"\ninverse = "y"\n', "'type'"),
        ('[[relations]]\nheading = "A"\ntype = "links-to"\ninverse = "y"\n', "built in"),
        ('[[relations]]\nheading = "A"\ntype = "x"\ninverse = "y"\n[[relations]]\nheading = "B"\ntype = "x"\n'
         'inverse = "z"\n', "already has inverse"),
        ('[[relations]]\nheading = "A"\ntype = "x"\ninverse = "y"\ncolour = "red"\n', "unknown key"),
        ('pagez = ["*.md"]\n', "unknown setting"),
        ('[page_type]\nfolders = ["a"]\n', "folders"),
    ])
    def test_invalid_config(self, repo, text, message):
        with pytest.raises(ConfigError, match=message):
            self.write_config(repo, text)

    def test_custom_rules_and_folder_types(self, repo, capsys):
        repo.write("guides/setup.md", "---\nsee_also: [install]\nowner: \"[[README]]\"\n---\n# Setup\n\n"
                                      "## Steps\n- [Install](install.md) — get the binaries\n")
        self.write_config(repo, '[page_type.folders]\nguides = "guide"\n\n'
                                '[[relations]]\nheading = "Steps"\npage_type = "guide"\ntype = "step"\ninverse = "step-of"\n\n'
                                '[[relations]]\nfield = "see_also"\ntype = "see-also"\ninverse = "seen-from"\n')
        assert run(repo, "neighbors", "setup", "--outgoing") == 0
        result = {e["slug"]: e for e in json.loads(capsys.readouterr().out)["neighbors"]}
        assert (result["install"]["type"], result["install"]["reason"]) == ("step", "get the binaries")
        assert (result["README"]["type"], result["README"]["reason"]) == ("links-to", "Frontmatter: owner.")
        assert run(repo, "neighbors", "install", "--incoming") == 0
        assert json.loads(capsys.readouterr().out)["neighbors"][0]["type"] == "step-of"
        assert run(repo, "search", "binaries", "--keyword-only") == 0
        assert json.loads(capsys.readouterr().out)["results"][0]["type"] == "guide"

    def test_changing_rules_rederives_without_reembedding(self, repo):
        settings = self.write_config(repo, "")
        with Cache(settings) as cache:
            cache.refresh()
            cache.embed_pending(HashEmbedder())
            assert cache.neighbors("setup", incoming=False)[0]["type"] == "links-to"
        settings = self.write_config(repo, '[[relations]]\nheading = "Steps"\ntype = "step"\ninverse = "step-of"\n')
        with Cache(settings) as cache:
            assert not cache.rebuilt and cache.needs_rederive
            assert cache.ensure_fresh("setup")
            types = {e["slug"]: e["type"] for e in cache.neighbors("setup", incoming=False)}
            assert types == {"install": "step", "config file": "step"}
            assert cache.pending_embeddings() == 0  # vectors kept

    def test_changing_summary_settings_rebuilds(self, repo):
        settings = self.write_config(repo, "")
        with Cache(settings) as cache:
            cache.refresh()
        settings = self.write_config(repo, '[summary]\nheadings = ["Steps"]\n')
        with Cache(settings) as cache:
            assert cache.rebuilt

    def test_check_settings(self, repo, capsys):
        repo.write("notes/bare.md", "No frontmatter here.\n")
        self.write_config(repo, '[check]\nrequire_frontmatter = true\nsummary_types = ["*"]\n')
        assert run(repo, "check", "bare") == 1
        codes = {i["code"] for i in json.loads(capsys.readouterr().out)["issues"]}
        assert codes == {"missing-frontmatter"}
        assert run(repo, "check", "install") == 1
        codes = {i["code"] for i in json.loads(capsys.readouterr().out)["issues"]}
        assert codes == {"missing-frontmatter"}


class TestInit:
    def test_draft_is_valid_toml_and_proposes_rules(self, repo, capsys):
        for index in range(4):
            repo.write(f"people/p{index}.md", f"---\ntype: person\nsources: [install]\n---\n# P{index}\n\n"
                                              f"## Summary\nPerson {index}.\n\n## Appearances\n- [[install]] — cited\n")
        assert main(["init", "--root", str(repo)]) == 0
        draft = capsys.readouterr().out
        config = tomllib.loads(draft)  # commented suggestions keep it valid as-is
        assert config["summary"]["headings"] == ["Summary"]
        assert config["page_type"]["field"] == "type"
        assert "# heading = \"Appearances\"" in draft and "# field = \"sources\"" in draft

    def test_proposes_the_main_folder_and_excludes_templates(self, repo, capsys):
        for index in range(12):  # the fixture has README.md and three pages in guides/ and reference/
            repo.write(f"notes/n{index}.md", f"# N{index}\n")
        repo.write("templates/person.md", "# Template\n")
        assert main(["init", "--root", str(repo)]) == 0
        draft = capsys.readouterr().out
        config = tomllib.loads(draft)
        assert config["pages"] == ["notes/**/*.md"] and config["exclude"] == ["templates/**"]
        assert "leaves out: README.md, guides/ (2 pages)" in draft

    def test_write_never_overwrites(self, repo, capsys):
        assert main(["init", "--root", str(repo), "--write"]) == 0
        assert (repo / ".wiki-cli.toml").is_file()
        load_settings(repo)  # the written draft loads
        assert main(["init", "--root", str(repo), "--write"]) == 2
        assert "already exists" in capsys.readouterr().err


def test_rules_apply_after_a_file_changes(repo):
    (repo / ".wiki-cli.toml").write_text('[[relations]]\nheading = "Steps"\ntype = "step"\ninverse = "step-of"\n',
                                         encoding="utf-8")
    settings = load_settings(repo)
    with Cache(settings) as cache:
        cache.refresh()
        bump(repo.write("guides/setup.md", "# Setup\n\n## Steps\n- [Install](install.md) — new wording\n"))
        assert cache.ensure_fresh("setup")
        [entry] = cache.neighbors("setup", incoming=False)
        assert (entry["type"], entry["reason"]) == ("step", "new wording")
