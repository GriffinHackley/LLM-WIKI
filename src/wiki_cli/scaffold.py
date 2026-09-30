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
from collections.abc import Sequence
from pathlib import Path

from wiki_cli import codebase, guide, init
from wiki_cli.config import CONFIG_FILENAME, ConfigError, load_settings
from wiki_cli.models import is_downloaded
from wiki_cli.sources import raw_dirs

PRESETS_DIR = Path(__file__).parent / "presets"
HOOKS_DIR = Path(__file__).parent / "hooks"
DEFAULT_PRESET = "research"
CODE_PRESET = "code"  # describes a code repo, which `wiki new` must be pointed at
AGENTS = ("claude", "copilot", "opencode")  # agent platforms with adapter files
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


def scaffold(target: Path, preset: str = DEFAULT_PRESET, agent: str | Sequence[str] | None = None, *,
             git_hook: bool = False, code: Path | None = None) -> dict:
    target = target.expanduser().resolve()
    if target == Path.home().resolve() or target == Path(target.anchor):
        raise ScaffoldError(f"{target} is not a wiki folder; name a folder for the wiki")
    if target.exists() and not target.is_dir():
        raise ScaffoldError(f"{target} is a file")
    agents = (agent,) if isinstance(agent, str) else tuple(agent or ())
    for name in agents:
        if name not in AGENTS:
            raise ScaffoldError(f"no adapter for '{name}': choose {', '.join(AGENTS)}")
    name, folder = preset_files(preset)
    files = folder / "files" if (folder / "files").is_dir() else folder
    if code is not None and name != CODE_PRESET:
        raise ScaffoldError(f"--code is for the {CODE_PRESET} preset")
    code_repo = _code_repo(code, target) if name == CODE_PRESET else None
    target.mkdir(parents=True, exist_ok=True)
    result = {"root": str(target), "preset": name, "created": [], "kept": [], "skipped": [], "add": [],
              "notes": [], "established": False, "adopted": 0}
    variables = {"name": target.name, "today": datetime.date.today().isoformat()}
    if code_repo is not None:
        variables["code_repo"] = _relative(code_repo, target)
        variables["code_origin"] = codebase.origin(code_repo) or ""
    if not (target / CONFIG_FILENAME).exists():
        _adopt(target, (name, folder), variables if code_repo is not None else None, result)
    # A wiki with a config keeps its own pages and templates: only the files that connect
    # agents to it are added, and the templates its config names but lacks.
    established = result["established"] = (target / CONFIG_FILENAME).exists()
    if established:
        _instructions(target, folder, result, variables)
        wanted = _named_templates(target)

    for source in sorted(path for path in files.rglob("*") if path.is_file()):
        rel = "/".join(_dotted(part) for part in source.relative_to(files).parts)
        if established and (rel in ("AGENTS.md", CONFIG_FILENAME) or
                            (rel.split("/", 1)[0] in CONTENT_FOLDERS and rel not in wanted)):
            if (target / rel).exists():
                if rel not in result["created"]:
                    result["kept"].append(rel)
            elif rel != "AGENTS.md":
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
    if established and settings is not None:
        missing = preset_types(folder) - {page_type.name for page_type in settings.types}
        if missing:
            result["preset_types"] = sorted(missing)
    if "claude" in agents:
        _claude(target, settings, result, extra_dir=code_repo)
    if "copilot" in agents:
        _copilot(target, settings, result, with_claude="claude" in agents)
    if "opencode" in agents:
        _opencode(target, settings, result)
    if code_repo is not None:
        result["code_repo"] = str(code_repo)
        result["code_setup"] = _code_setup(code_repo, target, folder, agents, _raw_folders(settings))
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


def _code_setup(code_repo: Path, target: Path, preset: Path, agents: tuple[str, ...],
                raw_folders: list[str]) -> list[dict]:
    """What the code repo needs so an agent working there finds and keeps the wiki. Never
    written by `wiki new`: the code repo is the user's to change."""
    wiki_path = _relative(target, code_repo)
    setup = []
    if _redirect_target(code_repo) != target:
        setup.append({"file": CONFIG_FILENAME, "text": f'wiki = "{wiki_path}"\n',
                      "why": "points wiki commands run in the code repo at the wiki"})
    instructions = code_repo / "AGENTS.md"
    if not (instructions.is_file() and "wiki guide sync" in instructions.read_text(encoding="utf-8", errors="replace")):
        section = (preset / "code-repo-section.md").read_text(encoding="utf-8").replace("{{wiki_path}}", wiki_path)
        setup.append({"file": "AGENTS.md", "text": section,
                      "why": "tells the agent working on the code to keep the wiki current"})
    if "claude" in agents:
        # From the code repo the wiki is another folder, so its raw folders are named absolutely.
        deny = [f"Edit(/{_posix_absolute(target)}/{raw}/**)" for raw in raw_folders]
        settings = {"permissions": {"allow": [CLAUDE_PERMISSION], "deny": deny,
                                    "additionalDirectories": [target.as_posix()]}}
        setup.append({"file": ".claude/settings.json", "text": json.dumps(settings, indent=2) + "\n",
                      "why": "lets Claude Code run wiki commands and read and edit the wiki from the code repo, "
                             "but not change the sources in its raw/"})
    return setup


def _redirect_target(code_repo: Path) -> Path | None:
    try:
        value = tomllib.loads((code_repo / CONFIG_FILENAME).read_text(encoding="utf-8")).get("wiki")
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return (code_repo / value).resolve() if isinstance(value, str) else None


def _adopt(target: Path, preset: tuple[str, Path], code: dict | None, result: dict) -> None:
    """A folder that already holds pages but no config is an existing wiki: draft its
    config from a survey of its pages, as `wiki init --write` does, rather than lay the
    preset's layout over it (whose config would index only the preset's own folder)."""
    try:
        settings = load_settings(target)
    except ConfigError:
        return
    count = init.existing_pages(settings)
    if not count:
        return
    code_config = {"repo": code["code_repo"], "origin": code["code_origin"]} if code else None
    survey = init.survey(settings)
    if "AGENTS.md" not in survey["agent_files"]:  # written next, when the wiki has no instructions yet
        survey["agent_files"] = ["AGENTS.md", *survey["agent_files"]]
    draft = init.render(survey, preset=preset, code=code_config)
    (target / CONFIG_FILENAME).write_text(draft, encoding="utf-8", newline="\n")
    result["created"].append(CONFIG_FILENAME)
    result["adopted"] = count


INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md", ".github/copilot-instructions.md")


def _instructions(target: Path, preset: Path, result: dict, variables: dict[str, str]) -> None:
    """An established wiki gets the workflow section in its agent instructions: a new
    AGENTS.md holding just the section when it has none, else the section to add to the
    first instructions file it has, unless one already sends agents to the guides."""
    existing = [rel for rel in INSTRUCTION_FILES if (target / rel).is_file()]
    if any("wiki guide" in (target / rel).read_text(encoding="utf-8", errors="replace") for rel in existing):
        return
    section = preset / "agents-section.md"
    if not existing:
        if section.is_file():
            (target / "AGENTS.md").write_bytes(_fill(section.read_bytes(), variables))
            result["created"].append("AGENTS.md")
        return
    _add_section(preset, existing[0], result, variables)


def _named_templates(target: Path) -> set[str]:
    """Templates the wiki's config names (its types', and weekly notes') that it lacks."""
    try:
        settings = load_settings(target)
    except ConfigError:
        return set()
    named = [page_type.template for page_type in settings.types if page_type.template]
    if settings.weekly is not None:
        named.append(settings.weekly.template)
    return {rel for rel in named if not (target / rel).exists()}


def preset_types(folder: Path) -> set[str]:
    """The page types a preset's config declares."""
    try:
        return set(tomllib.loads((folder / "files" / "dot-wiki-cli.toml").read_text(encoding="utf-8"))
                   .get("types", {}))
    except (OSError, tomllib.TOMLDecodeError):
        return set()


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
    deny = _raw_deny(settings)
    permissions = {"allow": [CLAUDE_PERMISSION], "deny": deny}
    if extra_dir is not None:  # a code wiki reads the code repo
        permissions["additionalDirectories"] = [extra_dir.as_posix()]
    if not settings_file.exists():
        settings_file.parent.mkdir(parents=True, exist_ok=True)
        settings_file.write_text(json.dumps({"permissions": permissions}, indent=2) + "\n", encoding="utf-8")
        result["created"].append(".claude/settings.json")
    else:
        result["kept"].append(".claude/settings.json")
        try:
            existing = json.loads(settings_file.read_text(encoding="utf-8")).get("permissions", {})
            allowed, denied = existing.get("allow", []), existing.get("deny", [])
        except (ValueError, AttributeError):
            allowed, denied = [], []
        missing = {}
        if CLAUDE_PERMISSION not in allowed:
            missing["allow"] = [CLAUDE_PERMISSION]
        if any(rule not in denied for rule in deny):
            missing["deny"] = [rule for rule in deny if rule not in denied]
        if missing:
            result["add"].append({"file": ".claude/settings.json",
                                  "text": f'"permissions": {json.dumps(missing)}\n'})

    _write_skills(target, ".claude/skills", settings, result)


def _raw_folders(settings) -> list[str]:
    return raw_dirs(settings) if settings is not None else ["raw"]


def _raw_deny(settings) -> list[str]:
    """Claude Code rules that stop its file tools, and the Bash file commands it recognizes,
    from changing sources. A leading `/` anchors a project rule at the folder Claude Code
    was started in."""
    return [f"Edit(/{raw}/**)" for raw in _raw_folders(settings)]


def _posix_absolute(path: Path) -> str:
    """A path as Claude Code matches it: `C:\\Users\\me` is `/c/Users/me` on Windows."""
    posix = path.resolve().as_posix()
    if len(posix) > 1 and posix[1] == ":":
        posix = f"/{posix[0].lower()}{posix[2:]}"
    return posix


def _copilot(target: Path, settings, result: dict, *, with_claude: bool) -> None:
    """GitHub Copilot adapter: instructions pointing at AGENTS.md, and the workflow skills.
    Copilot also reads `.claude/skills/`, so with the Claude adapter the skills are not
    written twice."""
    rel = ".github/copilot-instructions.md"
    path = target / rel
    if path.exists():
        result["kept"].append(rel)
        if "AGENTS.md" not in path.read_text(encoding="utf-8", errors="replace"):
            result["add"].append({"file": rel, "text": COPILOT_INSTRUCTIONS})
    elif (target / "AGENTS.md").exists():
        _write(target, rel, COPILOT_INSTRUCTIONS, result)
    if with_claude:
        result["notes"].append("Copilot uses the workflow skills in .claude/skills/, so none were written "
                               "to .github/skills/")
    else:
        _write_skills(target, ".github/skills", settings, result)


def _opencode(target: Path, settings, result: dict) -> None:
    """OpenCode adapter: one command per guide, run by the build agent (which may edit files
    and run commands)."""
    for name, title in guide.available(settings):
        _write(target, f".opencode/commands/wiki-{name}.md", opencode_command(name, title), result)


def _write_skills(target: Path, folder: str, settings, result: dict) -> None:
    for name, title in guide.available(settings):
        _write(target, f"{folder}/wiki-{name}/SKILL.md", skill_stub(name, title), result)


def _write(target: Path, rel: str, text: str, result: dict) -> None:
    path = target / rel
    if path.exists():
        result["kept"].append(rel)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    result["created"].append(rel)


COPILOT_INSTRUCTIONS = ("This repository is an LLM-maintained wiki. Read and follow `AGENTS.md` at the "
                        "repository root:\nit says how the wiki is laid out and which workflow to run for "
                        "each task.\n")

# What each workflow acts on, for the text after the slash command.
_SUBJECTS = {"ingest": "The source to ingest", "query": "The question to answer",
             "lint": "What to audit", "sync": "The code change to sync"}


def adapter_body(name: str, title: str) -> str:
    """The steps of a guide as a prompt. Agents that run `` !`command` `` lines when a
    skill or command loads (Claude Code, OpenCode) get the guide inlined; others see the
    line itself, and the sentence before it tells them to run it. The guide still comes from
    the installed `wiki` command, never a copy."""
    subject = _SUBJECTS.get(name, "What to work on")
    return (f"# /wiki-{name}: {title.split(': ', 1)[-1]}\n\n"
            f"The steps below come from `wiki guide {name}`. If the next line is a command rather than the\n"
            "steps, run that command once and use what it prints as the steps.\n\n"
            f"!`wiki guide {name}`\n\n"
            "Carry out those steps yourself, with your own tools, applying the rules in AGENTS.md.\n"
            f"{subject}, if the user gave one after the command: $ARGUMENTS\n")


def skill_stub(name: str, title: str) -> str:
    """A `SKILL.md` for Claude Code, and for GitHub Copilot (which reads the same format)."""
    purpose = title.split(": ", 1)[-1].replace('"', "'")
    return (f"---\nname: wiki-{name}\ndescription: \"{purpose[:1].upper() + purpose[1:]}. Runs the steps from "
            f"`wiki guide {name}`.\"\nallowed-tools: Bash(wiki *)\n---\n\n" + adapter_body(name, title))


def opencode_command(name: str, title: str) -> str:
    purpose = title.split(": ", 1)[-1].replace('"', "'")
    return (f"---\ndescription: \"{purpose[:1].upper() + purpose[1:]} (wiki guide {name})\"\nagent: build\n---\n\n"
            + adapter_body(name, title))
