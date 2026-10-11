"""`wiki connect <code-repo>`: set up a code repo so the agents working there use its wiki.

Two steps. The first writes the files the wiki owns, and rewrites them when the tool's
version of them changed: the pointer `.wiki-cli.toml` (with the agents), `WIKI.md` (how to
work with the wiki), the workflow skills, and each agent's own hook file. The second is
the user's, by hand: the lines their own files need (`AGENTS.md`, `CLAUDE.md`,
`.claude/settings.json`), which `wiki connect` prints and never writes. `--check` writes
nothing and reports what is missing or out of date, for both steps.
"""

from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from wiki_cli import agents as agents_module
from wiki_cli import guide, scaffold
from wiki_cli.config import AGENTS_KEY, CONFIG_FILENAME, REDIRECT_KEY, Settings

WIKI_MD = "WIKI.md"
AGENTS_LINE = "Read @WIKI.md: this code has a wiki, and every task on the code goes through it."
COPILOT_INSTRUCTIONS = ".github/instructions/wiki.instructions.md"


class ConnectError(Exception):
    pass


@dataclass
class Connection:
    code_repo: Path
    wiki: Path
    agents: tuple[str, ...]
    files: list[dict] = field(default_factory=list)  # {"file", "status": created|updated|current|missing|outdated}
    by_hand: list[dict] = field(default_factory=list)  # {"file", "text", "why"}

    @property
    def ok(self) -> bool:
        return not self.by_hand and all(item["status"] not in ("missing", "outdated") for item in self.files)

    def to_dict(self) -> dict:
        return {"code_repo": str(self.code_repo), "wiki": str(self.wiki), "agents": list(self.agents),
                "files": self.files, "by_hand": self.by_hand, "ok": self.ok}


def connect(settings: Settings, code_repo: Path | None, agent_names: list[str] | None, *,
            check: bool = False) -> Connection:
    code_repo = (code_repo or settings.code_repo)
    if code_repo is None:
        raise ConnectError("name the code repo: wiki connect <code-repo> (or set [code] repo in the wiki's config)")
    code_repo = code_repo.expanduser().resolve()
    if not code_repo.is_dir():
        raise ConnectError(f"{code_repo} is not a folder")
    if code_repo == settings.root:
        raise ConnectError("that is the wiki itself: name the code repo the wiki describes")
    pointer = _read_pointer(code_repo)
    try:
        names = agents_module.resolve(agent_names) if agent_names else tuple(pointer.get(AGENTS_KEY, ()))
    except agents_module.AgentError as exc:
        raise ConnectError(str(exc)) from exc
    if not names:
        raise ConnectError(f"name the agents working in the code repo: --agent {'|'.join(agents_module.IDS)} "
                           "(repeatable)")
    result = Connection(code_repo, settings.root, names)
    wiki_path = scaffold._relative(settings.root, code_repo)

    owned = {CONFIG_FILENAME: _pointer_text(wiki_path, names), WIKI_MD: _wiki_md(settings, code_repo, wiki_path)}
    guides = guide.available(settings)
    if agents_module.CLAUDE_CODE.id in names:
        owned.update({f".claude/skills/wiki-{name}/SKILL.md": scaffold.skill_stub(name, title)
                      for name, title in guides})
    if agents_module.COPILOT.id in names:
        owned[agents_module.COPILOT.hook_file] = json.dumps(agents_module.COPILOT.hook_entries(), indent=2) + "\n"
        owned[COPILOT_INSTRUCTIONS] = _copilot_instructions()
        if agents_module.CLAUDE_CODE.id not in names:  # Copilot reads .claude/skills/ too
            owned.update({f".github/skills/wiki-{name}/SKILL.md": scaffold.skill_stub(name, title)
                          for name, title in guides})
    for rel, text in owned.items():
        result.files.append({"file": rel, "status": _write(code_repo / rel, text, check)})

    _by_hand(settings, result, wiki_path)
    return result


def _read_pointer(code_repo: Path) -> dict:
    path = code_repo / CONFIG_FILENAME
    if not path.is_file():
        return {}
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConnectError(f"{path} is not valid TOML: {exc}") from exc
    if set(data) - {REDIRECT_KEY, AGENTS_KEY}:
        raise ConnectError(f"{path} is a wiki's own config, not a pointer to one: is {code_repo} a wiki?")
    try:
        return {**data, AGENTS_KEY: agents_module.resolve(data.get(AGENTS_KEY, []))}
    except agents_module.AgentError:
        return {k: v for k, v in data.items() if k != AGENTS_KEY}


def _pointer_text(wiki_path: str, names: tuple[str, ...]) -> str:
    return (f"# Points `wiki` commands run in this repo at its wiki, and names the agents working here.\n"
            f"# Written by `wiki connect`.\n"
            f'{REDIRECT_KEY} = "{wiki_path}"\n{AGENTS_KEY} = [{", ".join(json.dumps(name) for name in names)}]\n')


def _wiki_md(settings: Settings, code_repo: Path, wiki_path: str) -> str:
    template = (scaffold.PRESETS_DIR / scaffold.CODE_PRESET / "code-repo-wiki.md").read_bytes()
    variables = {"wiki_path": wiki_path, **scaffold._git_layout(code_repo, settings.root)}
    return scaffold._fill(template, variables).decode("utf-8")


def _copilot_instructions() -> str:
    return ('---\napplyTo: "**"\n---\n'
            f"This code has an LLM-maintained wiki. Read `{WIKI_MD}` at the repository root and work as it says:\n"
            "every task on the code goes through `wiki guide work`.\n")


def _write(path: Path, text: str, check: bool) -> str:
    current = path.read_text(encoding="utf-8") if path.is_file() else None
    if current is not None and current.replace("\r\n", "\n") == text:
        return "current"
    if check:
        return "missing" if current is None else "outdated"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="\n")
    return "created" if current is None else "updated"


# -- step 2, by hand ------------------------------------------------------------------------

def _by_hand(settings: Settings, result: Connection, wiki_path: str) -> None:
    code_repo = result.code_repo
    agents_md = _text(code_repo / "AGENTS.md")
    if "WIKI.md" not in agents_md:
        result.by_hand.append({"file": "AGENTS.md", "text": AGENTS_LINE + "\n",
                               "why": "sends every agent working on the code to WIKI.md"})
    if agents_module.CLAUDE_CODE.id not in result.agents:
        return
    claude_md = _text(code_repo / "CLAUDE.md")
    if "@WIKI.md" not in claude_md and "@AGENTS.md" not in claude_md:
        result.by_hand.append({"file": "CLAUDE.md", "text": "@AGENTS.md\n",
                               "why": "Claude Code reads CLAUDE.md, not AGENTS.md: this imports it"})
    wanted = _claude_settings(settings, code_repo)
    try:
        have = json.loads(_text(code_repo / ".claude/settings.json") or "{}")
    except ValueError as exc:
        result.by_hand.append({"file": ".claude/settings.json", "text": f"{exc}\n",
                               "why": "is not valid JSON, so it can't be checked, and Claude Code may not read it "
                                      "either (JSON has no comments): fix it, then check again"})
        return
    missing = _missing(wanted, have if isinstance(have, dict) else {})
    if missing:
        result.by_hand.append({"file": ".claude/settings.json", "text": json.dumps(missing, indent=2) + "\n",
                               "why": "merge these into it: Claude Code's hooks for the wiki, permission to run "
                                      "`wiki`" + ("" if scaffold._nested(code_repo, settings.root) else
                                                  ", access to the wiki's folder")
                                      + ", and no edits to the wiki's raw sources"})


def _claude_settings(settings: Settings, code_repo: Path) -> dict:
    raw = scaffold._raw_folders(settings)
    if scaffold._nested(code_repo, settings.root):
        wiki_path = scaffold._relative(settings.root, code_repo)
        permissions = {"allow": [scaffold.CLAUDE_PERMISSION], "deny": [f"Edit(/{wiki_path}/{folder}/**)"
                                                                          for folder in raw]}
    else:
        permissions = {"allow": [scaffold.CLAUDE_PERMISSION],
                       "deny": [f"Edit(/{scaffold._posix_absolute(settings.root)}/{folder}/**)" for folder in raw],
                       "additionalDirectories": [settings.root.as_posix()]}
    return {"permissions": permissions, "hooks": agents_module.CLAUDE_CODE.hook_entries()}


def _missing(wanted: dict, have: dict) -> dict:
    """The parts of ``wanted`` that ``have`` lacks: list items not there, and for hooks, each
    event whose `wiki hook` command is not configured."""
    missing: dict = {}
    for key, value in wanted.get("permissions", {}).items():
        present = have.get("permissions", {}).get(key, []) if isinstance(have.get("permissions"), dict) else []
        absent = [item for item in value if item not in present]
        if absent:
            missing.setdefault("permissions", {})[key] = absent
    hooks = have.get("hooks") if isinstance(have.get("hooks"), dict) else {}
    for event, entries in wanted.get("hooks", {}).items():
        command = entries[0]["hooks"][0]["command"]
        if command not in json.dumps(hooks.get(event, [])):
            missing.setdefault("hooks", {})[event] = entries
    return missing


def _text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""
