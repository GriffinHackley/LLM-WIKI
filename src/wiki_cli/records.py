"""Records: items that live and change in another system, such as a ticket in a tracker.

A page of a record type (`record = true` in `[types]`) cites its record by `key:` and
`url:`, never by a file in `raw/`, and says in `synced:` which version of the record it
reflects: the tracker's own "last updated" time when the agent read it. The tool never
contacts the tracker. `wiki stale` lists the records due for a recheck (never synced, or
synced longer ago than `[records] recheck_days` and not in a final status), and the
agent looks each one up with whatever access the wiki's rules give it.
"""

from __future__ import annotations

import datetime
import re

from wiki_cli.config import Settings
from wiki_cli.model import WARNING, Issue
from wiki_cli.pages import PAGE, Page, load, scan_vault

FIELDS = ("key", "url")
_URL = re.compile(r"^https?://[^\s/]+\S*$")


def record_types(settings: Settings | None) -> set[str]:
    return {page_type.name for page_type in settings.types if page_type.record} if settings is not None else set()


def is_record(page: Page) -> bool:
    return page.page_type is not None and page.page_type in record_types(page.settings)


def synced_at(value) -> datetime.datetime | None:
    """`synced:` as a time: a date, or a date and time in ISO form, as trackers give it
    (`2026-10-01T14:02:11.000+0000` from Jira, `2026-10-01T14:02:11Z` from GitHub)."""
    if isinstance(value, datetime.datetime):
        return value if value.tzinfo else value.replace(tzinfo=datetime.timezone.utc)
    if isinstance(value, datetime.date):
        return datetime.datetime(value.year, value.month, value.day, tzinfo=datetime.timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    text = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)  # +0000 -> +00:00
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=datetime.timezone.utc)


def issues(page: Page) -> list[Issue]:
    """What a record page needs beyond its type's own fields: where the record lives, and a
    `synced:` that reads as a time."""
    if not is_record(page):
        return []
    data = page.data or {}
    found = []
    missing = [key for key in FIELDS if not str(data.get(key) or "").strip()]
    if missing:
        found.append(Issue(WARNING, "missing-field", f"frontmatter has no {' or '.join(repr(key) for key in missing)}; "
                                                     f"a {page.page_type} page names the record it describes by "
                                                     "key and url"))
    url = str(data.get("url") or "").strip()
    if url and not _URL.match(url):
        found.append(Issue(WARNING, "bad-url", f"'url' is {url!r}, not a web address: give the record's link "
                                               "in the tracker (https://...)"))
    if data.get("synced") not in (None, "") and synced_at(data.get("synced")) is None:
        found.append(Issue(WARNING, "bad-synced", f"'synced' is {data.get('synced')!r}, not a time: give the "
                                                  "tracker's last-updated time as it shows it, for example "
                                                  "2026-10-01T14:02:11Z"))
    return found


def due(settings: Settings, now: datetime.datetime | None = None) -> list[dict]:
    """Record pages due for a recheck, oldest first: never synced, or synced more than
    `recheck_days` ago and not in a final status."""
    types = record_types(settings)
    if not types:
        return []
    now = now or datetime.datetime.now(datetime.timezone.utc)
    limit = datetime.timedelta(days=settings.records_recheck_days)
    found = []
    scanned, _ = scan_vault(settings)
    for page_file, _ in scanned:
        if page_file.kind != PAGE:
            continue
        page = load(page_file, settings)
        if page.page_type not in types:
            continue
        data = page.data or {}
        status = str(data.get("status") or "").strip()
        synced = synced_at(data.get("synced"))
        entry = {"slug": page.slug, "path": page_file.rel, "key": str(data.get("key") or ""),
                 "url": str(data.get("url") or ""), "status": status, "synced": synced.isoformat() if synced else None}
        if synced is None:
            entry["reason"] = "never synced" if data.get("synced") in (None, "") else "synced: is not a time"
        elif status.lower() in settings.records_final:
            continue
        elif now - synced > limit:
            entry["reason"] = f"synced {(now - synced).days} days ago"
        else:
            continue
        found.append(entry)
    return sorted(found, key=lambda entry: (entry["synced"] is not None, entry["synced"] or "", entry["slug"]))
