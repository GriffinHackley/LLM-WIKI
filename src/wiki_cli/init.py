"""Survey a wiki and draft a starter ``.wiki-cli.toml``.

Everything the tool needs works without a config file; the draft only proposes the
parts that describe a wiki's own conventions (relation rules, summary sources, page
types), commented out, for the user to name and keep.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from wiki_cli import links
from wiki_cli.config import Settings
from wiki_cli.pages import PAGE, Resolver, load, scan_vault

SUMMARY_LIKE = ("summary", "overview", "abstract", "tl;dr", "tldr", "synopsis", "what this is", "description")
MIN_PAGES = 3  # a heading or field must appear on at least this many pages to be proposed
MAX_RULES = 12
MAIN_FOLDER_SHARE = 0.7  # propose `pages = ["<folder>/**/*.md"]` when one folder holds this share


def survey(settings: Settings) -> dict:
    files, others = scan_vault(settings)
    resolver = Resolver([(page_file.slug, page_file.rel) for page_file, _ in files], others)
    pages = [load(page_file, settings) for page_file, _ in files if page_file.kind == PAGE]

    keys: Counter[str] = Counter()
    types: Counter[str] = Counter()
    folders: Counter[str] = Counter()
    heading_pages: dict[str, set[str]] = defaultdict(set)
    heading_links: Counter[str] = Counter()
    heading_names: dict[str, str] = {}
    heading_types: dict[str, Counter[str]] = defaultdict(Counter)
    field_pages: dict[str, set[str]] = defaultdict(set)
    summary_headings: Counter[str] = Counter()
    with_frontmatter = 0

    for page in pages:
        if page.data is not None:
            with_frontmatter += 1
            keys.update(page.data.keys())
            for key, value in page.data.items():
                values = value if isinstance(value, list) else [value]
                if any(isinstance(item, str) and _looks_like_page(item, resolver) for item in values):
                    field_pages[key].add(page.slug)
        if page.page_type:
            types[page.page_type] += 1
        if "/" in page.file.rel:
            folders[page.file.rel.split("/", 1)[0]] += 1
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
        "templates": any(page.file.rel.split("/", 1)[0].lower() == "templates" for page in pages),
        "raw": sum(1 for page_file, _ in files if page_file.kind != PAGE),
        "with_frontmatter": with_frontmatter,
        "type_field": settings.page_type_field if types else None,
        "types": types.most_common(),
        "folders": folders.most_common(8),
        "frontmatter_keys": keys.most_common(12),
        "link_fields": sorted(((key, len(slugs)) for key, slugs in field_pages.items() if len(slugs) >= MIN_PAGES),
                              key=lambda item: -item[1]),
        "summary_fields": [key for key in ("summary", "description") if keys.get(key, 0) >= MIN_PAGES],
        "summary_headings": [name for name, count in summary_headings.most_common() if count >= MIN_PAGES],
        "headings": [(heading_names.get(heading, heading), pages_count, link_count,
                      [t for t, _ in heading_types[heading].most_common(3)])
                     for heading, pages_count, link_count in headings],
    }


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


def _looks_like_page(value: str, resolver: Resolver) -> bool:
    match = re.fullmatch(r"\s*\[\[([^\[\]\n]+?)\]\]\s*", value)
    target = links.split_target(match.group(1))[0] if match else value
    return bool(target.strip()) and resolver.resolve(target) is not None


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
    lines.append('exclude = ["templates/**"]' if result.get("templates") else '# exclude = ["templates/**"]')
    return lines


def render(result: dict) -> str:
    """A commented starter config for this wiki."""
    lines = [
        "# .wiki-cli.toml, drafted by `wiki init`. Everything is optional: the defaults index",
        "# every *.md file (skipping hidden folders) and raw/**/*.txt as searchable source text.",
        f"# Survey: {result['pages']} pages ({result['with_frontmatter']} with frontmatter), "
        f"{result['raw']} raw text files.",
        "",
        *_pages_lines(result),
        '# raw = ["raw/**/*.txt"]',
        "",
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
    elif result["folders"]:
        lines += ["[page_type]", "# No type field found. Optionally map folders to page types:",
                  "# [page_type.folders]"]
        lines += [f'# "{folder}" = "{folder.rstrip("s")}"  # {count} pages' for folder, count in result["folders"]]
        lines.append("")
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
    return "\n".join(lines).rstrip() + "\n"


def _toml_list(values: list[str]) -> str:
    return "[" + ", ".join('"' + value.replace('"', '\\"') + '"' for value in values) + "]"
