"""The code a `code` wiki describes: its repository, and what changed in it since a page
was last checked against it. Everything here reads the code repo through git; nothing
writes to it.

A page lists the files it describes in `covers:` (globs relative to the code repo's top
level) and the code commit it was last checked against in `verified:`. A page is stale
when a covered file changed between that commit and the code repo's HEAD, or has
uncommitted changes.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from wiki_cli.config import CONFIG_FILENAME, Settings

CODE_PREFIX = "code:"
PART_OF = "part-of"  # the relation a file or nested module names its module with (code preset)


class CodeRepoError(Exception):
    pass


def git(repo: Path, *args: str) -> str:
    try:
        done = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8")
    except OSError as exc:
        raise CodeRepoError(f"cannot run git: {exc}") from exc
    if done.returncode != 0:
        raise CodeRepoError(done.stderr.strip() or f"git {' '.join(args)} failed")
    return done.stdout


def top_level(path: Path) -> Path | None:
    """The top folder of the git repository holding ``path``, or None if it is not in one."""
    try:
        return Path(git(path, "rev-parse", "--show-toplevel").strip()).resolve()
    except CodeRepoError:
        return None


def origin(repo: Path) -> str | None:
    try:
        return git(repo, "remote", "get-url", "origin").strip() or None
    except CodeRepoError:
        return None


def repo(settings: Settings) -> Path:
    """The code repo, checked: it exists, is a git repository's top level, and has the
    origin the wiki recorded. Raises with what to fix when not."""
    if settings.code_repo is None:
        raise CodeRepoError(f"this wiki names no code repo; add [code] repo = \"<path>\" to {CONFIG_FILENAME} "
                            "or set WIKI_CODE_REPO")
    path = settings.code_repo
    how = "WIKI_CODE_REPO" if _from_env() else f"[code] repo in {CONFIG_FILENAME}"
    if not path.is_dir():
        raise CodeRepoError(f"code repo {path} does not exist (from {how}); fix the path, or set "
                            "WIKI_CODE_REPO to where the code is checked out on this machine")
    top = top_level(path)
    if top != path.resolve():
        raise CodeRepoError(f"code repo {path} (from {how}) is not the top of a git repository"
                            + (f"; use {top}" if top else ""))
    if settings.code_origin:
        actual = origin(path)
        if actual and _same_remote(actual) != _same_remote(settings.code_origin):
            raise CodeRepoError(f"code repo {path} has origin {actual}, but this wiki describes "
                                f"{settings.code_origin}; point [code] repo or WIKI_CODE_REPO at the right checkout")
    return path


def head(path: Path) -> str:
    return git(path, "rev-parse", "HEAD").strip()


def pathspecs(globs: list[str]) -> list[str]:
    return [f":(glob){pattern.strip().lstrip('/')}" for pattern in globs if pattern.strip()]


def matching_files(path: Path, globs: list[str]) -> list[str]:
    """Tracked files in the code repo matching any of ``globs``."""
    specs = pathspecs(globs)
    return git(path, "ls-files", "--", *specs).splitlines() if specs else []


def exists(path: Path, rel: str) -> bool:
    return (path / rel.split("#", 1)[0]).is_file()


@dataclass
class Staleness:
    slug: str
    path: str
    verified: str | None
    changed: list[str] = field(default_factory=list)  # committed since `verified`
    uncommitted: list[str] = field(default_factory=list)
    problem: str | None = None  # e.g. an unknown `verified` commit

    def to_dict(self) -> dict:
        return {key: value for key, value in self.__dict__.items() if value not in (None, [])}


def staleness(path: Path, slug: str, rel: str, covers: list[str], verified: str | None) -> Staleness:
    result = Staleness(slug, rel, verified)
    specs = pathspecs(covers)
    if not specs:
        return result
    if verified:
        try:
            git(path, "cat-file", "-e", f"{verified}^{{commit}}")
            result.changed = git(path, "diff", "--name-only", f"{verified}..HEAD", "--", *specs).splitlines()
        except CodeRepoError:
            result.problem = f"verified commit {verified} is not in the code repo"
    result.uncommitted = sorted({line[3:] for line in git(path, "status", "--porcelain", "--", *specs).splitlines()})
    return result


def _from_env() -> bool:
    import os
    return bool(os.environ.get("WIKI_CODE_REPO"))


def _same_remote(url: str) -> str:
    """Compare remotes loosely: https and ssh forms of one repository are the same."""
    url = url.strip().removesuffix("/").removesuffix(".git").lower()
    for prefix in ("https://", "http://", "ssh://", "git@"):
        url = url.removeprefix(prefix)
    return url.replace(":", "/")


def covers(page) -> list[str]:
    value = (page.data or {}).get("covers")
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value if isinstance(item, str)] if isinstance(value, list) else []


def verified(page) -> str | None:
    value = (page.data or {}).get("verified")
    return str(value).strip() or None if value is not None else None


def check_pages(pages, settings: Settings) -> list:
    """Code checks for `wiki check`: `code:` links to files that no longer exist, and
    `covers:` globs that match nothing. One warning when the code repo is unusable."""
    from wiki_cli import links
    from wiki_cli.model import WARNING, Issue

    using = []
    for page in pages:
        if page.text is None or page.file.kind != "page":
            continue
        refs = sorted({link.target[len(CODE_PREFIX):] for link in links.extract_links(page.body, page.file.rel)
                       if link.is_code})
        globs = covers(page)
        if refs or globs:
            using.append((page, refs, globs))
    if not using:
        return []
    try:
        path = repo(settings)
    except CodeRepoError as exc:
        return [Issue(WARNING, "code-repo", f"code links and covers: not checked: {exc}")]
    tracked = set(git(path, "ls-files").splitlines())
    issues = []
    for page, refs, globs in using:
        missing = [ref for ref in refs if ref not in tracked and not exists(path, ref)]
        if missing:
            issues.append(Issue(WARNING, "missing-code-file", f"code links to files not in the code repo: "
                                f"{', '.join(missing)}", path=page.file.rel, slug=page.slug))
        empty = [pattern for pattern in globs if not matching_files(path, [pattern])]
        if empty:
            issues.append(Issue(WARNING, "covers-nothing", f"covers: patterns match no file in the code repo: "
                                f"{', '.join(empty)}", path=page.file.rel, slug=page.slug))
        if globs and not verified(page):
            issues.append(Issue(WARNING, "not-verified", "covers: files but has no verified: commit",
                                path=page.file.rel, slug=page.slug))
    return issues


def check_part_of(pages, modules, settings: Settings, resolver) -> list:
    """`part-of-broader-module`: a page whose `## Part of` names a module when another
    module covers all of the page's files more specifically (fewer files in all). ``modules``
    are the pages that can be named there: hub pages with `covers:`."""
    from wiki_cli.edges import derive
    from wiki_cli.model import WARNING, Issue

    modules = {module.slug: covers(module) for module in modules if covers(module)}
    checked = []
    for page in pages:
        if page.text is None or page.file.kind != "page" or page.slug in modules or not covers(page):
            continue
        named = [edge.target for edge in derive(page, resolver, settings.vocabulary)
                 if edge.type == PART_OF and edge.resolved and edge.target in modules]
        if named:
            checked.append((page, named))
    if not checked:
        return []
    try:
        path = repo(settings)
    except CodeRepoError:
        return []  # check_pages reports the unusable repo
    tracked = git(path, "ls-files").splitlines()
    covered = {slug: _matching(tracked, globs) for slug, globs in modules.items()}
    issues = []
    for page, named in checked:
        files = _matching(tracked, covers(page))
        if not files:
            continue
        holders = sorted((len(found), slug) for slug, found in covered.items() if files <= found)
        if not holders:
            continue
        size, best = holders[0]
        if best in named or any(len(covered[slug]) <= size for slug in named):
            continue
        broader = ", ".join(f"[[{slug}]]" for slug in named)
        issues.append(Issue(
            WARNING, "part-of-broader-module",
            f"## Part of names {broader}, but [[{best}]] covers this page's files more specifically "
            f"({size} files against {min(len(covered[slug]) for slug in named)}); name [[{best}]] under "
            "## Part of instead", path=page.file.rel, slug=page.slug))
    return issues


def _matching(tracked: list[str], globs: list[str]) -> set[str]:
    """Tracked files matching any of ``globs``, as git's :(glob) pathspecs match them."""
    patterns = [pattern.strip().lstrip("/") for pattern in globs if pattern.strip()]
    return {name for name in tracked if any(PurePosixPath(name).full_match(pattern) for pattern in patterns)}
