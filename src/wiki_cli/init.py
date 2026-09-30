"""Survey a wiki and draft a starter ``.wiki-cli.toml``.

Everything the tool needs works without a config file; the draft only proposes the
parts that describe a wiki's own conventions (relation rules, summary sources, page
types), commented out, for the user to name and keep. With a preset, it also carries the
preset's page types, relation rules and weekly notes, for a wiki adopting them.
"""

from __future__ import annotations

import re
import tomllib
from collections import Counter, defaultdict
from pathlib import Path

from wiki_cli import links
from wiki_cli.config import Settings
from wiki_cli.pages import PAGE, Resolver, load, scan_vault

SUMMARY_LIKE = ("summary", "overview", "abstract", "tl;dr", "tldr", "synopsis", "what this is", "description")
MIN_PAGES = 3  # a heading or field must appear on at least this many pages to be proposed
MAX_RULES = 12
MAIN_FOLDER_SHARE = 0.7  # propose `pages = ["<folder>/**/*.md"]` when one folder holds this share
# Instructions for agents, not wiki content: left out of the survey and the draft's pages.
AGENT_FILES = ("AGENTS.md", "CLAUDE.md", "GEMINI.md")
# Frontmatter that names a page's own identity or labels, never a relation to another page.
NOT_RELATIONS = {"title", "aliases", "alias", "tags", "tag", "cssclasses", "summary", "description"}
# The parts of a preset's config a wiki adopting it takes: what its pages are and how they relate.
PRESET_TABLES = re.compile(r"^\[(types\.[^\]]+|\[relations\]|weekly)\]\s*$")


def survey(settings: Settings) -> dict:
    files, others = scan_vault(settings)
    kept = [(page_file, entry) for page_file, entry in files if not _left_out(page_file.rel)]
    left = [page_file.rel for page_file, _ in files if _left_out(page_file.rel)]
    resolver = Resolver([(page_file.slug, page_file.rel) for page_file, _ in kept], others + left)
    pages = [load(page_file, settings) for page_file, _ in kept if page_file.kind == PAGE]

    keys: Counter[str] = Counter()
    types: Counter[str] = Counter()
    heading_pages: dict[str, set[str]] = defaultdict(set)
    heading_links: Counter[str] = Counter()
    heading_names: dict[str, str] = {}
    heading_types: dict[str, Counter[str]] = defaultdict(Counter)
    field_pages: dict[str, set[str]] = defaultdict(set)
    summary_headings: Counter[str] = Counter()
    with_frontmatter = 0
    not_relations = NOT_RELATIONS | {settings.page_type_field}

    for page in pages:
        if page.data is not None:
            with_frontmatter += 1
            keys.update(page.data.keys())
            for key, value in page.data.items():
                if key.lower() in not_relations:
                    continue
                values = value if isinstance(value, list) else [value]
                if any(isinstance(item, str) and _names_page(item, page.slug, resolver) for item in values):
                    field_pages[key].add(page.slug)
        if page.page_type:
            types[page.page_type] += 1
        seen_summary = set()
        for line, section, in_code in links.iter_lines(page.body):
            match = re.match(r"#{2,6}[ \t]+(.*?)[ \t]*#*[ \t]*$", line) if not in_code else None
            if match:
                name = match.group(1).strip()
                heading_names.setdefault(name.lower(), name)
                if name.lower() in SUMMARY_LIKE and name.lower() not in seen_summary:
                    summary_headings[name] += 1
                    seen_summary.add(name.lower())
        for link in links.extract_links(page.body, page.file.rel):
            if link.section:
                heading_pages[link.section].add(page.slug)
                heading_links[link.section] += 1
                if page.page_type:
                    heading_types[link.section][page.page_type] += 1

    main_folder, left_out = _main_folder(pages)
    headings = sorted(
        ((heading, len(slugs), heading_links[heading]) for heading, slugs in heading_pages.items()
         if len(slugs) >= MIN_PAGES),
        key=lambda item: (-item[1], item[0]))[:MAX_RULES]
    return {
        "pages": len(pages),
        "main_folder": main_folder,
        "left_out": left_out,
        "templates": any(rel.split("/", 1)[0].lower() == "templates" for rel in left),
        "agent_files": [rel for rel in left if rel in AGENT_FILES],
        "raw": sum(1 for page_file, _ in kept if page_file.kind != PAGE),
        "with_frontmatter": with_frontmatter,
        "type_field": settings.page_type_field if types else None,
        "types": types.most_common(),
        "folders": _type_folders(pages),
        "frontmatter_keys": keys.most_common(12),
        "link_fields": sorted(((key, len(slugs)) for key, slugs in field_pages.items() if len(slugs) >= MIN_PAGES),
                              key=lambda item: -item[1]),
        "summary_fields": [key for key in ("summary", "description") if keys.get(key, 0) >= MIN_PAGES],
        "summary_headings": [name for name, count in summary_headings.most_common() if count >= MIN_PAGES],
        "headings": [(heading_names.get(heading, heading), pages_count, link_count,
                      [t for t, _ in heading_types[heading].most_common(3)])
                     for heading, pages_count, link_count in headings],
    }


def existing_pages(settings: Settings) -> int:
    """Markdown files a folder already holds as wiki content: not a README, agent
    instructions or templates. `wiki new` adopts a folder with any."""
    files, _ = scan_vault(settings)
    return sum(1 for page_file, _ in files if page_file.kind == PAGE and not _left_out(page_file.rel)
               and page_file.rel.lower() not in ("readme.md", "index.md"))


def _left_out(rel: str) -> bool:
    return rel in AGENT_FILES or rel.split("/", 1)[0].lower() == "templates"


def _main_folder(pages) -> tuple[str | None, list[str]]:
    """The top-level folder holding most pages, and what indexing only it would leave out
    (root files like README.md individually, other folders as "name/ (N pages)")."""
    tops = Counter(page.file.rel.split("/", 1)[0] if "/" in page.file.rel else "" for page in pages)
    folder, count = next(((top, n) for top, n in tops.most_common() if top), (None, 0))
    if folder is None or count == len(pages) or count < MAIN_FOLDER_SHARE * len(pages):
        return None, []
    root_files = sorted(page.file.rel for page in pages if "/" not in page.file.rel)
    other_folders = [f"{top}/ ({n} pages)" for top, n in tops.most_common() if top and top != folder]
    return folder, root_files + other_folders


def _type_folders(pages) -> list[tuple[str, int]]:
    """Folders that could stand for page types: each page's folder, at most two deep, so
    `notes/people/ada.md` proposes `notes/people` rather than `notes`."""
    folders = Counter("/".join(page.file.rel.split("/")[:-1][:2]) for page in pages if "/" in page.file.rel)
    return folders.most_common(8)


def _singular(folder: str) -> str:
    name = folder.rsplit("/", 1)[-1].lower()
    irregular = {"people": "person", "analyses": "analysis", "indices": "index", "children": "child"}
    if name in irregular:
        return irregular[name]
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith(("sses", "xes", "ches", "shes")):
        return name[:-2]
    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name


def _names_page(value: str, own_slug: str, resolver: Resolver) -> bool:
    match = re.fullmatch(r"\s*\[\[([^\[\]\n]+?)\]\]\s*", value)
    target = links.split_target(match.group(1))[0] if match else value
    if not target.strip():
        return False
    found = resolver.resolve(target)
    return found is not None and found != own_slug


def _pages_lines(result: dict) -> list[str]:
    lines = []
    if result.get("main_folder"):
        left = result["left_out"]
        shown = ", ".join(left[:6]) + (f" and {len(left) - 6} more" if len(left) > 6 else "")
        lines += [f"# Most pages are under {result['main_folder']}/. Indexing only that folder leaves out: {shown}.",
                  "# Delete this line to index every *.md file instead.",
                  f'pages = ["{result["main_folder"]}/**/*.md"]']
    else:
        lines.append('# pages = ["**/*.md"]')
    exclude = (["templates/**"] if result.get("templates") else []) + list(result.get("agent_files", []))
    if exclude:
        lines.append("# Templates and agent instructions are not wiki pages.")
        lines.append(f"exclude = {_toml_list(exclude)}")
    else:
        lines.append('# exclude = ["templates/**"]')
    return lines


def render(result: dict, preset: tuple[str, Path] | None = None, code: dict | None = None) -> str:
    """A commented starter config for this wiki. ``preset`` is ``(name, folder)``: the
    config names it, and carries its page types, relation rules and weekly notes when
    ``result["adopt_tables"]`` is set. ``code`` is ``{"repo", "origin"}`` for a code wiki."""
    lines = [
        "# .wiki-cli.toml, drafted by `wiki init`. Everything is optional: the defaults index",
        "# every *.md file (skipping hidden folders) and raw/**/*.txt as searchable source text.",
        f"# Survey: {result['pages']} pages ({result['with_frontmatter']} with frontmatter), "
        f"{result['raw']} raw text files.",
        "",
    ]
    if preset is not None:
        lines += ["# The preset whose workflow guides this wiki follows.", f'preset = "{preset[0]}"', ""]
    lines += [
        *_pages_lines(result),
        '# raw = ["raw/**/*.txt"]',
        "",
    ]
    if code is not None or (preset is not None and preset[0] == "code"):
        lines += _code_lines(code)
    lines += [
        "[summary]",
        "# Where a page's one-paragraph summary comes from, in order; otherwise its first paragraph.",
        f"fields = {_toml_list(result['summary_fields'] or ['summary', 'description'])}",
        f"headings = {_toml_list(result['summary_headings'] or ['Summary'])}",
        "",
    ]
    if result["types"]:
        shown = ", ".join(f"{name} ({count})" for name, count in result["types"][:8])
        lines += ["[page_type]", f"# Page types found in the '{result['type_field']}' field: {shown}.",
                  f'field = "{result["type_field"]}"', ""]
    elif result["folders"] and not (preset is not None and result.get("adopt_tables")):
        lines += ["[page_type]", "# No type field found. Optionally map folders to page types:",
                  "# [page_type.folders]"]
        lines += [f'# "{folder}" = "{_singular(folder)}"  # {count} pages' for folder, count in result["folders"]]
        lines.append("")
    lines += [
        "# Pages returned by `wiki search`, `nav start` and `nav search` (1-20; --limit overrides).",
        "# [search]",
        "# results = 3",
        "",
    ]
    if not (preset is not None and result.get("adopt_tables")):
        lines += [
            "# Weekly notes: `wiki weekly` writes a note per finished week of work, and a timeline,",
            "# from the git history, into their own folder (never searched). See docs/config.md.",
            "# [weekly]",
            '# folder = "weekly"',
            "",
        ]
    lines += [
        "# Relations. Without rules every link is `links-to`, with the sentence around it as the",
        "# reason. Uncomment and name the rules that fit; rules higher up take precedence.",
        "",
    ]
    for heading, page_count, link_count, types in result["headings"]:
        name = re.sub(r"[^a-z0-9]+", "-", heading.lower()).strip("-") or "related"
        scope = f"  # mostly on: {', '.join(types)}" if types else ""
        lines += [f"# Heading '{heading}': {link_count} links on {page_count} pages{scope}",
                  "# [[relations]]", f'# heading = "{heading}"', f'# type = "{name}"', f'# inverse = "{name}-of"', ""]
    for field, page_count in result["link_fields"]:
        name = re.sub(r"[^a-z0-9]+", "-", field.lower()).strip("-")
        lines += [f"# Frontmatter field '{field}' names other pages on {page_count} pages",
                  "# [[relations]]", f'# field = "{field}"', f'# type = "{name}"', f'# inverse = "{name}-of"', ""]
    if preset is not None and result.get("adopt_tables"):
        lines += preset_lines(preset, result.get("main_folder"))
    return "\n".join(lines).rstrip() + "\n"


def _code_lines(code: dict | None) -> list[str]:
    if code is None:
        return ["# The code this wiki describes, relative to the wiki; WIKI_CODE_REPO overrides it.",
                "# [code]", '# repo = "../my-app"', ""]
    lines = ["# The code this wiki describes, relative to the wiki; WIKI_CODE_REPO overrides it.",
             "[code]", f'repo = "{code["repo"]}"']
    if code.get("origin"):
        lines.append(f'origin = "{code["origin"]}"')
    return lines + [""]


def preset_lines(preset: tuple[str, Path], main_folder: str | None, *,
                 declared_types: set[str] = frozenset(), declared_relations: set[str] = frozenset(),
                 weekly: bool = False) -> list[str]:
    """A preset's page types, relation rules and weekly notes, as config text for a wiki
    adopting them. Each type's folder moves under the wiki's own page folder (the preset
    keeps pages under `wiki/`), so pages created there are indexed. What the wiki already
    declares is left out."""
    name, folder = preset
    path = folder / "files" / "dot-wiki-cli.toml"
    if not path.is_file():
        return []
    text = path.read_text(encoding="utf-8")
    blocks, comments, current = [], [], None
    for line in text.splitlines():
        if line.startswith("["):
            current = {"header": line.strip(), "lines": [*comments, line]}
            comments = []
            blocks.append(current)
        elif line.startswith("#") and (current is None or not current["lines"][-1].strip()):
            comments.append(line)
        elif current is not None:
            if comments:  # comments inside a table, not before the next one
                current["lines"] += comments
                comments = []
            current["lines"].append(line)
    prefix = f"{main_folder}/" if main_folder else ""
    out = [f"# From the {name} preset: its page types, relation rules and weekly notes. Each type's",
           "# folder is where the agent creates its pages (change it to suit), and",
           f"# `wiki new . --preset {name}` adds the templates they name.", ""]
    for block in blocks:
        match = PRESET_TABLES.match(block["header"])
        if not match:
            continue
        table = match.group(1)
        body = "\n".join(block["lines"])
        if table.startswith("types.") and table[6:] in declared_types:
            continue
        if table == "[relations]":
            rule = tomllib.loads(body.replace("[[relations]]", "", 1))
            if rule.get("type") in declared_relations:
                continue
        if table == "weekly" and weekly:
            continue
        body = re.sub(r'^(folder = ")wiki/', lambda m: m.group(1) + prefix, body, flags=re.MULTILINE)
        out += body.rstrip().splitlines() + [""]
    return out


def _toml_list(values: list[str]) -> str:
    return "[" + ", ".join('"' + value.replace('"', '\\"') + '"' for value in values) + "]"
