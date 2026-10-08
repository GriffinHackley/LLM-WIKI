import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from wiki_cli.cli import main
from wiki_cli.config import ConfigError, HookCommand, load_settings

pytestmark = pytest.mark.skipif(not shutil.which("git"), reason="needs git")

PAGE = "---\ntitle: Ada\n---\n# Ada\n\n## Summary\nA mathematician.\n"


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=root,
                          capture_output=True, text=True)


@pytest.fixture
def wiki(tmp_path) -> Path:
    root = tmp_path / "w"
    root.mkdir()
    git(root, "init", "-q")
    write(root, ".wiki-cli.toml", 'pages = ["wiki/**/*.md"]\n\n[pending]\nignore = ["raw/SOURCES.md"]\n')
    write(root, "wiki/ada.md", PAGE)
    write(root, "raw/source.txt", "original\n")
    write(root, "raw/SOURCES.md", "- source.txt\n")
    write(root, "raw/README.md", "Sources go here.\n")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "start")
    return root


def precommit(root: Path, capsys) -> tuple[int, str]:
    code = main(["precommit", "--root", str(root)])
    return code, capsys.readouterr().err


def set_hooks(root: Path, hooks: str) -> None:
    config = root / ".wiki-cli.toml"
    config.write_text(config.read_text(encoding="utf-8") + "\n[hooks]\n" + hooks, encoding="utf-8")


def test_a_clean_commit_passes(wiki, capsys):
    write(wiki, "raw/new.txt", "a new source\n")
    git(wiki, "add", "-A")
    assert precommit(wiki, capsys) == (0, "")


def test_edited_sources_are_refused_but_readmes_and_ignored_files_may_change(wiki, capsys):
    write(wiki, "raw/SOURCES.md", "- source.txt\n- new.txt\n")
    write(wiki, "raw/README.md", "Sources, never edited.\n")
    git(wiki, "add", "-A")
    assert precommit(wiki, capsys)[0] == 0
    write(wiki, "raw/source.txt", "edited\n")
    git(wiki, "add", "-A")
    code, err = precommit(wiki, capsys)
    assert code == 1 and "  raw/source.txt" in err and "SOURCES.md" not in err and "README" not in err


def test_check_errors_are_refused(wiki, capsys):
    write(wiki, "wiki/ada.md", "---\ntitle: [unclosed\n---\n# Ada\n")
    git(wiki, "add", "-A")
    code, err = precommit(wiki, capsys)
    assert code == 1 and "invalid-frontmatter" in err and "wiki check --all" in err


def test_the_wikis_own_commands_run_and_can_refuse(wiki, capsys):
    script = Path(sys.executable).as_posix()
    write(wiki, "tools/ok.py", "print('fine')\n")
    write(wiki, "tools/args.py", "import sys\nprint('given', *sys.argv[1:])\nsys.exit(1)\n")
    set_hooks(wiki, f'pre_commit = [\n  "\\"{script}\\" tools/ok.py",\n'
                    f'  {{ run = "\\"{script}\\" tools/args.py", files = ["wiki/**/*.md"] }},\n]\n')
    write(wiki, "raw/new.txt", "no page changed\n")
    git(wiki, "add", "-A")
    result = precommit(wiki, capsys)
    assert result == (0, ""), result[1]  # the files command matched nothing staged
    write(wiki, "wiki/ada.md", PAGE + "\nMore.\n")
    write(wiki, "wiki/bob smith.md", PAGE.replace("Ada", "Bob"))
    git(wiki, "add", "-A")
    code, err = precommit(wiki, capsys)
    assert code == 1 and "tools/args.py' failed" in err
    assert "given wiki/ada.md wiki/bob smith.md" in err


def test_hooks_config_is_validated(wiki):
    set_hooks(wiki, 'pre_commit = ["python a.py", { run = "python b.py", files = ["x/*.md"] }]\n')
    assert load_settings(wiki).pre_commit == (HookCommand("python a.py"), HookCommand("python b.py", ("x/*.md",)))
    for bad in ('pre_commit = "python a.py"\n', 'pre_commit = [{ files = ["x"] }]\n',
                'pre_commit = [{ run = "a", glob = "x" }]\n', 'post_commit = []\n'):
        config = wiki / ".wiki-cli.toml"
        config.write_text('pages = ["wiki/**/*.md"]\n\n[hooks]\n' + bad, encoding="utf-8")
        with pytest.raises(ConfigError):
            load_settings(wiki)
