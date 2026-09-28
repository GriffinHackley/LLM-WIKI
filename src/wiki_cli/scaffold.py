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
import subprocess
import tomllib
from pathlib import Path

from wiki_cli import guide
from wiki_cli.config import CONFIG_FILENAME, ConfigError, load_settings
from wiki_cli.models import is_downloaded

PRESETS_DIR = Path(__file__).parent / "presets"
DEFAULT_PRESET = "research"
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


def scaffold(target: Path, preset: str = DEFAULT_PRESET, agent: str | None = None) -> dict:
    target = target.expanduser().resolve()
    if target == Path.home().resolve() or target == Path(target.anchor):
        raise ScaffoldError(f"{target} is not a wiki folder; name a folder for the wiki")
    if target.exists() and not target.is_dir():
        raise ScaffoldError(f"{target} is a file")
    if agent is not None and agent not in AGENTS:
        raise ScaffoldError(f"no adapter for '{agent}': choose {', '.join(AGENTS)}")
    name, folder = preset_files(preset)
    files = folder / "files" if (folder / "files").is_dir() else folder
    target.mkdir(parents=True, exist_ok=True)
    result = {"root": str(target), "preset": name, "created": [], "kept": [], "skipped": [], "add": [],
              "notes": []}
    variables = {"name": target.name, "today": datetime.date.today().isoformat()}
    # A wiki with its own config from elsewhere keeps its own pages and templates: only the
    # files that connect agents to it are added.
    established = _config_preset(target) not in (None, name)

    for source in sorted(path for path in files.rglob("*") if path.is_file()):
        rel = "/".join(_dotted(part) for part in source.relative_to(files).parts)
        if established and (rel == "AGENTS.md" or rel.split("/", 1)[0] in CONTENT_FOLDERS):
            if rel == "AGENTS.md" and not (target / rel).exists():
                _add_section(folder, "your agent instructions (AGENTS.md, CLAUDE.md, ...)", result)
            result["skipped"].append(rel)
            continue
        destination = target / rel
        content = _fill(source.read_bytes(), variables)
        if destination.exists():
            result["kept"].append(rel)
            if destination.read_bytes() != content:
                _missing_parts(rel, destination, folder, result)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
        result["created"].append(rel)

    result["git_init"] = _git_init(target, result)
    try:
        settings = load_settings(target)
    except ConfigError as exc:
        result["notes"].append(f"{CONFIG_FILENAME} could not be read: {exc}")
        settings = None
    if agent == "claude":
        _claude(target, settings, result)
    result["models_missing"] = settings is not None and not all((
        is_downloaded(settings.embed_model, settings.models_dir, reranker=False),
        is_downloaded(settings.reranker, settings.models_dir, reranker=True)))
    return result


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


def _missing_parts(rel: str, path: Path, preset: Path, result: dict) -> None:
    """For a kept file the wiki depends on, say what to add to it."""
    text = path.read_text(encoding="utf-8", errors="replace")
    if rel == "AGENTS.md" and "wiki guide" not in text:
        _add_section(preset, rel, result)
    elif rel == ".gitignore" and ".cache" not in text:
        result["add"].append({"file": rel, "text": ".cache/\n"})
    elif rel == CONFIG_FILENAME:
        result["notes"].append(f"kept your {CONFIG_FILENAME}: the preset's page types and relation rules "
                               "were not added to it, nor its templates and pages")


def _add_section(preset: Path, where: str, result: dict) -> None:
    section = preset / "agents-section.md"
    if section.is_file():
        result["add"].append({"file": where, "text": section.read_text(encoding="utf-8")})


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


def _claude(target: Path, settings, result: dict) -> None:
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
    if not settings_file.exists():
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(json.dumps({"permissions": {"allow": [CLAUDE_PERMISSION]}}, indent=2) + "\n",
                                 encoding="utf-8")
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
