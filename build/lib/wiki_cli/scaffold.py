"""`wiki new`: create a wiki from a preset, or add what is missing to an existing one.

A preset is a folder of starting files (`files/`), copied into the wiki as its own
editable files. Names starting with `dot-` become dotfiles (`dot-gitignore` ->
`.gitignore`), so presets survive packaging. Nothing that already exists is overwritten
or edited: an existing file is kept, and when it lacks something the wiki needs, the
text to add is returned for the user to add by hand.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import tomllib
from pathlib import Path

from wiki_cli import codebase, guide
from wiki_cli.config import CONFIG_FILENAME, ConfigError, load_settings
from wiki_cli.models import is_downloaded

PRESETS_DIR = Path(__file__).parent / "presets"
HOOKS_DIR = Path(__file__).parent / "hooks"
DEFAULT_PRESET = "research"
CODE_PRESET = "code"  # describes a code repo, which `wiki new` must be pointed at
AGENTS = ("claude",)
CLAUDE_PERMISSION = "Bash(wiki:*)"
CONTENT_FOLDERS = {"wiki", "templates", "raw"}  # a preset's pages and templates, not added to an established wiki


class ScaffoldError(Exception):
    pass


def presets() -> list[str]:
    return sorted(path.name for path in PRESETS_DIR.iterdir() if (path / "files").is_dir())


def preset_files(preset: str) -> tuple[str, Path]:
    """``(name, folder)`` of a built-in preset, or of a custom preset folder."""
    if preset in presets():
        return preset, PRESETS_DIR / preset
    folder = Path(preset).expanduser()
    if folder.is_dir():
        return folder.resolve().name, folder.resolve()
    raise ScaffoldError(f"no preset '{preset}': use one of {', '.join(presets())}, or a preset folder")


def scaffold(target: Path, preset: str = DEFAULT_PRESET, agent: str | None = None, *,
             git_hook: bool = False, code: Path | None = None) -> dict:
    target = target.expanduser().resolve()
    if target == Path.home().resolve() or target == Path(target.anchor):
        raise ScaffoldError(f"{target} is not a wiki folder; name a folder for the wiki")
    if target.exists() and not target.is_dir():
        raise ScaffoldError(f"{target} is a file")
    if agent is not None and agent not in AGENTS:
        raise ScaffoldError(f"no adapter for '{agent}': choose {', '.join(AGENTS)}")
    name, folder = preset_files(preset)
    files = folder / "files" if (folder / "files").is_dir() else folder
    if code is not None and name != CODE_PRESET:
        raise ScaffoldError(f"--code is for the {CODE_PRESET} preset")
    code_repo = _code_repo(code, target) if name == CODE_PRESET else None
    target.mkdir(parents=True, exist_ok=True)
    result = {"root": str(target), "preset": name, "created": [], "kept": [], "skipped": [], "add": [],
              "notes": []}
    variables = {"name": target.name, "today": datetime.date.today().isoformat()}
    if code_repo is not None:
        variables["code_repo"] = _relative(code_repo, target)
        variables["code_origin"] = codebase.origin(code_repo) or ""
    # A wiki with its own config from elsewhere keeps its own pages and templates: only the
    # files that connect agents to it are added.
    established = _config_preset(target) not in (None, name)

    for source in sorted(path for path in files.rglob("*") if path.is_file()):
        rel = "/".join(_dotted(part) for part in source.relative_to(files).parts)
        if established and (rel == "AGENTS.md" or rel.split("/", 1)[0] in CONTENT_FOLDERS):
            if rel == "AGENTS.md" and not (target / rel).exists():
                _add_section(folder, "your agent instructions (AGENTS.md, CLAUDE.md, ...)", result, variables)
            result["skipped"].append(rel)
            continue
        destination = target / rel
        content = _fill(source.read_bytes(), variables)
        if destination.exists():
            result["kept"].append(rel)
            if destination.read_bytes() != content:
                _missing_parts(rel, destination, folder, result, variables)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        result["created"].append(rel)

    result["git_init"] = _git_init(target, result)
    if git_hook:
        _git_hook(target, result)
    try:
        settings = load_settings(target)
    except ConfigError as exc:
        result["notes"].append(f"{CONFIG_FILENAME} could not be read: {exc}")
        settings = None
    if agent == "claude":
        _claude(target, settings, result, extra_dir=code_repo)
    if code_repo is not None:
        result["code_repo"] = str(code_repo)
        result["code_setup"] = _code_setup(code_repo, target, folder, agent)
    result["models_missing"] = settings is not None and not all((
        is_downloaded(settings.embed_model, settings.models_dir, reranker=False),
        is_downloaded(settings.reranker, settings.models_dir, reranker=True)))
    return result


def _code_repo(code: Path | None, target: Path) -> Path:
    """The code repo a code wiki describes: the top folder of a git repository, outside the wiki."""
    if code is None:
        raise ScaffoldError(f"the {CODE_PRESET} preset needs --code <path>: the top folder of the code's "
                            "git repository")
    path = Path(code).expanduser().resolve()
    if not path.is_dir():
        raise ScaffoldError(f"--code {path} is not a folder")
    top = codebase.top_level(path)
    if top is None:
        raise ScaffoldError(f"--code {path} is not in a git repository")
    if top != path:
        raise ScaffoldError(f"--code {path} is inside the git repository {top}; pass its top folder: {top}")
    if target == path or path in target.parents:
        raise ScaffoldError(f"the wiki must be its own repository, outside the code repo {path}; "
                            f"choose a folder beside it, such as {path.parent / (path.name + '-wiki')}")
    return path


def _relative(path: Path, start: Path) -> str:
    try:
        return Path(os.path.relpath(path, start)).as_posix()
    except ValueError:  # another drive on Windows
        return path.as_posix()


def _code_setup(code_repo: Path, target: Path, preset: Path, agent: str | None) -> list[dict]:
    """What the code repo needs so an agent working there finds and keeps the wiki. Never
    written by `wiki new`: the code repo is the user's to change."""
    wiki_path = _relative(target, code_repo)
    setup = []
    if _redirect_target(code_repo) != target:
        setup.append({"file": CONFIG_FILENAME, "text": f'wiki = "{wiki_path}"\n',
                      "why": "points wiki commands run in the code repo at the wiki"})
    agents = code_repo / "AGENTS.md"
    if not (agents.is_file() and "wiki guide sync" in agents.read_text(encoding="utf-8", errors="replace")):
        section = (preset / "code-repo-section.md").read_text(encoding="utf-8").replace("{{wiki_path}}", wiki_path)
        setup.append({"file": "AGENTS.md", "text": section,
                      "why": "tells the agent working on the code to keep the wiki current"})
    if agent == "claude":
        settings = {"permissions": {"allow": [CLAUDE_PERMISSION], "additionalDirectories": [target.as_posix()]}}
        setup.append({"file": ".claude/settings.json", "text": json.dumps(settings, indent=2) + "\n",
                      "why": "lets Claude Code run wiki commands and read and edit the wiki from the code repo"})
    return setup


def _redirect_target(code_repo: Path) -> Path | None:
    try:
        value = tomllib.loads((code_repo / CONFIG_FILENAME).read_text(encoding="utf-8")).get("wiki")
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return (code_repo / value).resolve() if isinstance(value, str) else None


def _config_preset(target: Path) -> str | None:
    """The preset an existing config names; "" for a config that names none; None for no config."""
    path = target / CONFIG_FILENAME
    if not path.is_file():
        return None
    try:
        with path.open("rb") as handle:
            value = tomllib.load(handle).get("preset")
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    return value if isinstance(value, str) else ""


def _dotted(part: str) -> str:
    return "." + part[4:] if part.startswith("dot-") else part


def _fill(data: bytes, variables: dict[str, str]) -> bytes:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    for key, value in variables.items():
        text = text.replace("{{" + key + "}}", value)
    return text.encode("utf-8")


def _missing_parts(rel: str, path: Path, preset: Path, result: dict, variables: dict[str, str]) -> None:
    """For a kept file the wiki depends on, say what to add to it."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if rel == "AGENTS.md" and "wiki guide" not in text:
        _add_section(preset, rel, result, variables)
    elif rel == ".gitignore" and ".cache" not in text:
        result["add"].append({"file": rel, "text": ".cache/\n"})
    elif rel == CONFIG_FILENAME:
        result["notes"].append(f"kept your {CONFIG_FILENAME}: the preset's page types and relation rules "
                               "were not added to it, nor its templates and pages")


def _add_section(preset: Path, where: str, result: dict, variables: dict[str, str]) -> None:
    section = preset / "agents-section.md"
    if section.is_file():
        result["add"].append({"file": where, "text": _fill(section.read_bytes(), variables).decode("utf-8")})


def _git_init(target: Path, result: dict) -> bool:
    if any((directory / ".git").exists() for directory in (target, *target.parents)):
        return False
    try:
        subprocess.run(["git", "init", "-q"], cwd=target, check=True, capture_output=True)
    except (OSError, subprocess.CalledProcessError) as exc:
        result["notes"].append(f"could not run 'git init' ({exc}); the wiki works without git, but "
                               "the workflows commit after each unit of work")
        return False
    return True


def _git_hook(target: Path, result: dict) -> None:
    """Install the pre-commit backstop as `.githooks/pre-commit`, versioned with the wiki,
    and point this clone's `core.hooksPath` at it unless it already points elsewhere."""
    rel = ".githooks/pre-commit"
    path = target / rel
    if path.exists():
        result["kept"].append(rel)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes((HOOKS_DIR / "pre-commit").read_bytes().replace(b"\r\n", b"\n"))
        path.chmod(0o755)
        result["created"].append(rel)
    # A shell script checked out with CRLF line endings (Windows, core.autocrlf) fails elsewhere.
    attributes, line = target / ".gitattributes", ".githooks/* text eol=lf"
    if not attributes.exists():
        attributes.write_text(line + "\n", encoding="utf-8")
        result["created"].append(".gitattributes")
    elif ".githooks" not in attributes.read_text(encoding="utf-8", errors="replace"):
        result["kept"].append(".gitattributes")
        result["add"].append({"file": ".gitattributes", "text": line + "\n"})
    try:
        current = subprocess.run(["git", "config", "--get", "core.hooksPath"], cwd=target,
                                 capture_output=True, text=True).stdout.strip()
        if current and current != ".githooks":
            result["notes"].append(f"core.hooksPath is already '{current}'; the hook in {rel} is not active. "
                                   "Call it from your hooks, or run 'git config core.hooksPath .githooks'")
        elif not current:
            subprocess.run(["git", "config", "core.hooksPath", ".githooks"], cwd=target, check=True,
                           capture_output=True)
            result["notes"].append("pre-commit hook enabled for this clone; other clones enable it with "
                                   "'git config core.hooksPath .githooks'")
    except (OSError, subprocess.CalledProcessError):
        result["notes"].append(f"wrote {rel}, but could not enable it: run 'git config core.hooksPath .githooks'")


def _claude(target: Path, settings, result: dict, extra_dir: Path | None = None) -> None:
    """Claude Code adapter: CLAUDE.md importing AGENTS.md, the `wiki` permission, and one
    skill stub per guide that runs the guide."""
    claude_md = target / "CLAUDE.md"
    has_agents = (target / "AGENTS.md").exists()
    if not claude_md.exists():
        if has_agents:
            claude_md.write_text("@AGENTS.md\n", encoding="utf-8")
            result["created"].append("CLAUDE.md")
    elif has_agents and "@AGENTS.md" not in claude_md.read_text(encoding="utf-8", errors="replace"):
        result["kept"].append("CLAUDE.md")
        result["add"].append({"file": "CLAUDE.md", "text": "@AGENTS.md\n"})

    settings_file = target / ".claude" / "settings.json"
    permissions = {"allow": [CLAUDE_PERMISSION]}
    if extra_dir is not None:  # a code wiki reads the code repo
        permissions["additionalDirectories"] = [extra_dir.as_posix()]
    if not settings_file.exists():
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(json.dumps({"permissions": permissions}, indent=2) + "\n", encoding="utf-8")
        result["created"].append(".claude/settings.json")
    else:
        result["kept"].append(".claude/settings.json")
        try:
            allowed = json.loads(settings_file.read_text(encoding="utf-8")).get("permissions", {}).get("allow", [])
        except (ValueError, AttributeError):
            allowed = []
        if CLAUDE_PERMISSION not in allowed:
            result["add"].append({"file": ".claude/settings.json",
                                  "text": f'"permissions": {{"allow": ["{CLAUDE_PERMISSION}"]}}\n'})

    for name, title in guide.available(settings):
        rel = f".claude/skills/wiki-{name}/SKILL.md"
        path = target / rel
        if path.exists():
            result["kept"].append(rel)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(skill_stub(name, title), encoding="utf-8")
        result["created"].append(rel)


def skill_stub(name: str, title: str) -> str:
    purpose = title.split(": ", 1)[-1].replace('"', "'")
    return (f"---\nname: wiki-{name}\ndescription: \"{purpose[:1].upper() + purpose[1:]}. Runs `wiki guide {name}` "
            f"and follows the steps it prints.\"\n---\n\n# /wiki-{name}\n\nRun `wiki guide {name}` and follow the steps it prints, "
            "applying the rules in AGENTS.md. The steps come from the installed `wiki` command, so they "
            "always match it.\n")
