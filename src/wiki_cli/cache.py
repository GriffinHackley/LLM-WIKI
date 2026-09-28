"""Disposable SQLite cache: pages, derived relations, chunks, keyword index, vectors.

Everything here is derived from the wiki files and can be rebuilt at any time.
Agents never read this database directly; they use CLI commands.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import Callable

from wiki_cli import vec
from wiki_cli.chunking import chunk_page
from wiki_cli.config import Settings
from wiki_cli.edges import derive
from wiki_cli.models import Embedder
from wiki_cli.pages import PAGE, Page, PageFile, Resolver, content_hash, parse, scan, scan_vault, vault_files

SCHEMA_VERSION = "4"
BODY_HASH_VERSION = "2"  # 2: line endings normalized; a change rehashes bodies on the next refresh
EMBED_BATCH_PAGES = 32
LARGE_CHANGE = 500  # pages; beyond this, truncate the write-ahead log afterwards

_SCHEMA = """
CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE pages (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    slug TEXT NOT NULL,
    kind TEXT NOT NULL,
    mtime_ns INTEGER NOT NULL,
    size INTEGER NOT NULL,
    content_hash TEXT NOT NULL,
    body_hash TEXT,
    title TEXT,
    page_type TEXT,
    summary TEXT,
    summary_is_placeholder INTEGER NOT NULL DEFAULT 0,
    summary_body_hash TEXT,
    embedded_hash TEXT
);
CREATE INDEX pages_by_slug ON pages(slug);

CREATE TABLE relations (
    source_path TEXT NOT NULL,
    source_slug TEXT NOT NULL,
    target_slug TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    reason TEXT NOT NULL,
    resolved INTEGER NOT NULL,
    PRIMARY KEY (source_path, target_slug)
);
CREATE INDEX relations_by_source ON relations(source_slug);
CREATE INDEX relations_by_target ON relations(target_slug);
CREATE INDEX relations_by_type ON relations(relation_type);

CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    page_id INTEGER NOT NULL,
    ordinal INTEGER NOT NULL,
    heading_path TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    UNIQUE (page_id, ordinal)
);
CREATE INDEX chunks_by_page ON chunks(page_id);

CREATE VIRTUAL TABLE chunks_fts USING fts5(title, heading_path, text, tokenize = 'porter unicode61');
"""

_TABLES = ("nav_frontier", "nav_events", "nav_sessions", "summary_vectors", "chunk_vectors", "chunks_fts", "chunks",
           "relations", "pages", "metadata")
_VECTOR_TABLES = ("chunk_vectors", "summary_vectors")


class CacheUnavailable(Exception):
    pass


@dataclass
class RefreshStats:
    added: int = 0
    changed: int = 0
    touched: int = 0  # metadata changed, content identical
    removed: int = 0
    moved: int = 0
    unchanged: int = 0

    def to_dict(self) -> dict:
        return dict(self.__dict__)


class Cache:
    def __init__(self, settings: Settings, *, readonly: bool = False):
        self.settings = settings
        self.readonly = readonly
        self.rebuilt = False  # schema was reset when this instance opened the cache
        self.needs_full_refresh = False
        self.needs_rederive = False
        self.needs_rehash = False  # body hashes predate BODY_HASH_VERSION
        self.vocabulary = settings.vocabulary
        self.conn = self._connect()
        self._ensure_schema()

    # -- lifecycle -----------------------------------------------------------

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Cache":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _connect(self) -> sqlite3.Connection:
        path = self.settings.cache_path
        if self.readonly:
            if not path.is_file():
                raise CacheUnavailable(f"no cache at {path}; run 'wiki index rebuild'")
            conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=30, isolation_level=None)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(path, timeout=30, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        vec.load(conn)
        return conn

    def _ensure_schema(self) -> None:
        version = self._meta("schema_version")
        root = self._meta("root")
        summary = self._meta("summary_config")
        if (version == SCHEMA_VERSION and root == str(self.settings.root)
                and summary == self.settings.summary_fingerprint()):
            # Relation rules or page-type settings changed: re-derive edges on the next
            # refresh (no re-embedding needed).
            if not self.readonly and self._meta("graph_config") != self.settings.fingerprint():
                self.needs_rederive = True
            if not self.readonly and self._meta("body_hash_version") != BODY_HASH_VERSION:
                self.needs_rederive = self.needs_rehash = True
            return
        if self.readonly:
            raise CacheUnavailable("cache is missing, from another schema version, or built with other "
                                   "summary settings; run 'wiki index rebuild'")
        self._reset()

    def _reset(self) -> None:
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            for table in _TABLES:
                self.conn.execute(f"DROP TABLE IF EXISTS {table}")
            for statement in _SCHEMA.split(";"):
                if statement.strip():
                    self.conn.execute(statement)
            self.conn.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?)",
                [("schema_version", SCHEMA_VERSION), ("root", str(self.settings.root)),
                 ("summary_config", self.settings.summary_fingerprint()),
                 ("graph_config", self.settings.fingerprint()), ("body_hash_version", BODY_HASH_VERSION)],
            )
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.rebuilt = True
        self.needs_full_refresh = True

    def _meta(self, key: str) -> str | None:
        try:
            row = self.conn.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
        except sqlite3.OperationalError:
            return None
        return row[0] if row else None

    def checkpoint(self) -> None:
        """Fold the write-ahead log back into the database and truncate it (best effort)."""
        self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    # -- indexing ------------------------------------------------------------

    def rebuild(self) -> RefreshStats:
        self._reset()
        stats = self.refresh()
        self.conn.execute("VACUUM")
        self.checkpoint()
        return stats

    def refresh(self) -> RefreshStats:
        """Index new, changed, moved, and deleted files in one transaction."""
        stats = RefreshStats()
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            cached = {
                row[0]: row[1:]
                for row in self.conn.execute("SELECT path, mtime_ns, size, content_hash, slug FROM pages")
            }
            files, others = scan_vault(self.settings)
            resolver = Resolver([(page_file.slug, page_file.rel) for page_file, _ in files], others)
            seen: set[str] = set()
            added_hashes: list[str] = []
            for page_file, entry in files:
                try:
                    stat = entry.stat()
                except FileNotFoundError:
                    continue
                seen.add(page_file.rel)
                previous = cached.get(page_file.rel)
                if (previous and previous[0] == stat.st_mtime_ns and previous[1] == stat.st_size
                        and previous[3] == page_file.slug):
                    stats.unchanged += 1
                    continue
                same_slug = previous is not None and previous[3] == page_file.slug
                outcome = self._index_file(page_file, stat, previous[2] if same_slug else None, resolver)
                if outcome == "touched":
                    stats.touched += 1
                elif previous:
                    stats.changed += 1
                else:
                    stats.added += 1
                    added_hashes.append(self._hash_of(page_file.rel))
            removed = sorted(set(cached) - seen)
            removed_hashes = {cached[rel][2] for rel in removed}
            for rel in removed:
                self._delete(rel)
            stats.removed = len(removed)
            stats.moved = sum(1 for digest in added_hashes if digest in removed_hashes)
            if self.needs_rederive:
                self._rederive(files, resolver)
            # Always: attachments can appear without any page changing, and the query only
            # touches unresolved edges or edges whose target vanished.
            self._reresolve(resolver)
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.needs_full_refresh = False
        self.needs_rederive = False
        if stats.added + stats.changed + stats.removed > LARGE_CHANGE:
            self.checkpoint()
        return stats

    def refresh_file(self, page_file: PageFile) -> None:
        """Re-index one file (or remove it if deleted), resolving links against the cache."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            try:
                stat = page_file.path.stat()
            except FileNotFoundError:
                self._delete(page_file.rel)
            else:
                row = self.conn.execute("SELECT content_hash FROM pages WHERE path = ?", (page_file.rel,)).fetchone()
                known = self.conn.execute("SELECT slug, path FROM pages").fetchall()
                resolver = Resolver([*known, (page_file.slug, page_file.rel)],
                                    lambda: vault_files(self.settings))  # walked only if a link is unresolved
                self._index_file(page_file, stat, row[0] if row else None, resolver)
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def _index_file(self, page_file: PageFile, stat: os.stat_result, previous_hash: str | None,
                    resolver: Resolver) -> str:
        data = page_file.path.read_bytes()
        if previous_hash == content_hash(data):
            self.conn.execute(
                "UPDATE pages SET mtime_ns = ?, size = ? WHERE path = ?",
                (stat.st_mtime_ns, stat.st_size, page_file.rel),
            )
            return "touched"
        self._store(parse(page_file, data, self.settings), stat, resolver)
        return "changed"

    def _store(self, page: Page, stat: os.stat_result, resolver: Resolver) -> None:
        rel = page.file.rel
        previous = self.conn.execute(
            "SELECT summary, summary_is_placeholder, summary_body_hash FROM pages WHERE path = ?", (rel,)
        ).fetchone()
        summary, fallback = page.summary()
        placeholder = int(fallback or summary is None)
        body_hash = page.body_hash if page.text is not None else None
        # Keep the old body hash while the summary text is unchanged, so a changed
        # body with an unchanged summary is detectable as a stale summary.
        summary_body_hash = body_hash
        if previous and not placeholder and not previous[1] and previous[0] == summary and previous[2]:
            summary_body_hash = previous[2]

        # Upsert keeps the page id stable, so vector rowids stay valid.
        self.conn.execute(
            """INSERT INTO pages
               (path, slug, kind, mtime_ns, size, content_hash, body_hash, title, page_type,
                summary, summary_is_placeholder, summary_body_hash, embedded_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
               ON CONFLICT(path) DO UPDATE SET
                 slug = excluded.slug, kind = excluded.kind, mtime_ns = excluded.mtime_ns,
                 size = excluded.size, content_hash = excluded.content_hash, body_hash = excluded.body_hash,
                 title = excluded.title, page_type = excluded.page_type, summary = excluded.summary,
                 summary_is_placeholder = excluded.summary_is_placeholder,
                 summary_body_hash = excluded.summary_body_hash, embedded_hash = NULL""",
            (rel, page.slug, page.file.kind, stat.st_mtime_ns, stat.st_size, page.content_hash, body_hash,
             page.title, page.page_type, summary, placeholder, summary_body_hash),
        )
        page_id = self.conn.execute("SELECT id FROM pages WHERE path = ?", (rel,)).fetchone()[0]
        self._delete_chunks(page_id)
        self.conn.execute("DELETE FROM relations WHERE source_path = ?", (rel,))
        if page.text is not None:
            self._store_chunks(page_id, page, summary)
        self.conn.executemany(
            """INSERT OR REPLACE INTO relations
               (source_path, source_slug, target_slug, relation_type, reason, resolved)
               VALUES (?, ?, ?, ?, ?, ?)""",
            [(rel, page.slug, edge.target, edge.type, edge.reason, int(edge.resolved))
             for edge in derive(page, resolver, self.vocabulary)],
        )

    def _rederive(self, files, resolver: Resolver) -> None:
        """Recompute every page's edges and type after the relation or page-type settings changed."""
        for page_file, _ in files:
            try:
                page = parse(page_file, page_file.path.read_bytes(), self.settings)
            except FileNotFoundError:
                continue
            self.conn.execute("UPDATE pages SET page_type = ? WHERE path = ?", (page.page_type, page_file.rel))
            if self.needs_rehash and page_file.kind == PAGE:
                # Old hashes are not comparable with new ones: start each page's summary afresh.
                self.conn.execute("UPDATE pages SET body_hash = ?1, summary_body_hash = CASE WHEN "
                                  "summary_body_hash IS NULL THEN NULL ELSE ?1 END WHERE path = ?2",
                                  (page.body_hash, page_file.rel))
            self.conn.execute("DELETE FROM relations WHERE source_path = ?", (page_file.rel,))
            self.conn.executemany(
                """INSERT OR REPLACE INTO relations
                   (source_path, source_slug, target_slug, relation_type, reason, resolved)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [(page_file.rel, page.slug, edge.target, edge.type, edge.reason, int(edge.resolved))
                 for edge in derive(page, resolver, self.vocabulary)])
        self.conn.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES ('graph_config', ?)",
                          (self.settings.fingerprint(),))
        if self.needs_rehash:
            self.conn.execute("INSERT OR REPLACE INTO metadata(key, value) VALUES ('body_hash_version', ?)",
                              (BODY_HASH_VERSION,))
            self.needs_rehash = False

    def _reresolve(self, resolver: Resolver) -> None:
        """After pages appear or disappear, update which edges point at real pages."""
        slugs = {row[0] for row in self.conn.execute("SELECT slug FROM pages")}
        # Only unresolved edges, or resolved ones whose target vanished, can change.
        rows = self.conn.execute(
            """SELECT source_path, target_slug, resolved FROM relations
               WHERE (resolved = 0 OR target_slug NOT IN (SELECT slug FROM pages))
                 AND relation_type != 'refers-to-code'"""
        ).fetchall()
        for source_path, target, resolved in rows:
            new_target = resolver.resolve(target)
            if new_target is None and resolver.is_other_file(target):
                # The target turned out to be an attachment, not a page to write.
                self.conn.execute("DELETE FROM relations WHERE source_path = ? AND target_slug = ?",
                                  (source_path, target))
                continue
            if new_target and new_target in slugs:
                if new_target != target:
                    clash = self.conn.execute(
                        "SELECT relation_type FROM relations WHERE source_path = ? AND target_slug = ?",
                        (source_path, new_target)).fetchone()
                    if clash:
                        self.conn.execute("DELETE FROM relations WHERE source_path = ? AND target_slug = ?",
                                          (source_path, target))
                        continue
                self.conn.execute(
                    "UPDATE relations SET target_slug = ?, resolved = 1 WHERE source_path = ? AND target_slug = ?",
                    (new_target, source_path, target))
            elif resolved:
                self.conn.execute(
                    "UPDATE relations SET resolved = 0 WHERE source_path = ? AND target_slug = ?", (source_path, target))

    def _store_chunks(self, page_id: int, page: Page, summary: str | None) -> None:
        for chunk in chunk_page(page.text, page.body_offset, summary):
            cursor = self.conn.execute(
                "INSERT INTO chunks (page_id, ordinal, heading_path, start_offset, end_offset) VALUES (?, ?, ?, ?, ?)",
                (page_id, chunk.ordinal, chunk.heading_path, chunk.start, chunk.end),
            )
            self.conn.execute(
                "INSERT INTO chunks_fts (rowid, title, heading_path, text) VALUES (?, ?, ?, ?)",
                (cursor.lastrowid, page.title, chunk.heading_path, chunk.text),
            )

    def _delete_chunks(self, page_id: int) -> None:
        chunk_ids = [row[0] for row in self.conn.execute("SELECT id FROM chunks WHERE page_id = ?", (page_id,))]
        vectors = self.has_vectors
        if chunk_ids:
            marks = ",".join("?" * len(chunk_ids))
            self.conn.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({marks})", chunk_ids)
            if vectors:
                self.conn.execute(f"DELETE FROM chunk_vectors WHERE rowid IN ({marks})", chunk_ids)
            self.conn.execute("DELETE FROM chunks WHERE page_id = ?", (page_id,))
        if vectors:
            self.conn.execute("DELETE FROM summary_vectors WHERE rowid = ?", (page_id,))

    def _delete(self, rel: str) -> None:
        row = self.conn.execute("SELECT id FROM pages WHERE path = ?", (rel,)).fetchone()
        if row:
            self._delete_chunks(row[0])
        self.conn.execute("DELETE FROM relations WHERE source_path = ?", (rel,))
        self.conn.execute("DELETE FROM pages WHERE path = ?", (rel,))

    def _hash_of(self, rel: str) -> str:
        return self.conn.execute("SELECT content_hash FROM pages WHERE path = ?", (rel,)).fetchone()[0]

    # -- vectors -------------------------------------------------------------

    @property
    def embed_model(self) -> str | None:
        return self._meta("embed_model")

    @property
    def has_vectors(self) -> bool:
        return self.embed_model is not None

    def _ensure_vector_tables(self, embedder: Embedder) -> None:
        """Create vector tables for ``embedder``; a different model resets all vectors."""
        if self.embed_model == embedder.name:
            return
        embedder.embed_query("ready")  # fail before touching tables if the model is unavailable
        dims = int(embedder.dims)
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            for table in _VECTOR_TABLES:
                self.conn.execute(f"DROP TABLE IF EXISTS {table}")
                self.conn.execute(
                    f"CREATE VIRTUAL TABLE {table} USING vec0(embedding float[{dims}] distance_metric=cosine)")
            self.conn.execute("UPDATE pages SET embedded_hash = NULL")
            self.conn.executemany(
                "INSERT OR REPLACE INTO metadata(key, value) VALUES (?, ?)",
                [("embed_model", embedder.name), ("embed_dims", str(dims))],
            )
            self.conn.execute("COMMIT")
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise

    def pending_embeddings(self) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) FROM pages WHERE embedded_hash IS NULL OR embedded_hash != content_hash"
        ).fetchone()[0]

    def embed_pending(self, embedder: Embedder, progress: Callable[[int, int], None] | None = None) -> int:
        """Embed chunks of files whose vectors are missing or stale. Returns files embedded."""
        self._ensure_vector_tables(embedder)
        total = self.pending_embeddings()
        done = 0
        skipped: set[int] = set()
        while True:
            exclude = ",".join(str(page_id) for page_id in skipped) or "-1"
            pages = self.conn.execute(
                f"""SELECT id, content_hash, COALESCE(title, slug) FROM pages
                    WHERE (embedded_hash IS NULL OR embedded_hash != content_hash) AND id NOT IN ({exclude})
                    ORDER BY id LIMIT ?""",
                (EMBED_BATCH_PAGES,),
            ).fetchall()
            if not pages:
                break
            ids = [page_id for page_id, _, _ in pages]
            marks = ",".join("?" * len(ids))
            rows = self.conn.execute(
                f"""SELECT c.id, c.page_id, c.ordinal, c.heading_path, f.text
                    FROM chunks c JOIN chunks_fts f ON f.rowid = c.id
                    WHERE c.page_id IN ({marks}) ORDER BY c.page_id, c.ordinal""",
                ids,
            ).fetchall()
            titles = {page_id: title for page_id, _, title in pages}
            texts = [embedding_text(titles[page_id], heading, text) for _, page_id, _, heading, text in rows]
            vectors = list(embedder.embed_documents(texts)) if texts else []
            by_page: dict[int, list[tuple[int, int, bytes]]] = {}
            for (chunk_id, page_id, ordinal, _, _), vector in zip(rows, vectors):
                by_page.setdefault(page_id, []).append((chunk_id, ordinal, vec.serialize(vector)))

            self.conn.execute("BEGIN IMMEDIATE")
            try:
                for page_id, digest, _ in pages:
                    current = self.conn.execute("SELECT content_hash FROM pages WHERE id = ?", (page_id,)).fetchone()
                    if not current or current[0] != digest:
                        skipped.add(page_id)  # changed underneath us; the next run re-queues it
                        continue
                    self.conn.execute("DELETE FROM summary_vectors WHERE rowid = ?", (page_id,))
                    for chunk_id, ordinal, blob in by_page.get(page_id, []):
                        self.conn.execute("DELETE FROM chunk_vectors WHERE rowid = ?", (chunk_id,))
                        self.conn.execute(
                            "INSERT INTO chunk_vectors (rowid, embedding) VALUES (?, ?)", (chunk_id, blob))
                        if ordinal == 0:
                            self.conn.execute(
                                "INSERT INTO summary_vectors (rowid, embedding) VALUES (?, ?)", (page_id, blob))
                    self.conn.execute("UPDATE pages SET embedded_hash = ? WHERE id = ?", (digest, page_id))
                    done += 1
                self.conn.execute("COMMIT")
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            if progress:
                progress(done, total)
        if done > LARGE_CHANGE:
            self.checkpoint()
        return done

    # -- freshness -----------------------------------------------------------

    def ensure_fresh(self, slug: str) -> bool:
        """Make the cache current for ``slug``, scanning the whole wiki only when needed."""
        rows = self.conn.execute("SELECT path, mtime_ns, size FROM pages WHERE slug = ?", (slug,)).fetchall()
        if self.needs_full_refresh or self.needs_rederive or not rows:
            self.refresh()
            return self.page_exists(slug)
        for rel, mtime_ns, size in rows:
            path = self.settings.root / rel
            try:
                stat = path.stat()
            except FileNotFoundError:
                self.refresh()
                return self.page_exists(slug)
            if stat.st_mtime_ns != mtime_ns or stat.st_size != size:
                kind = self.conn.execute("SELECT kind FROM pages WHERE path = ?", (rel,)).fetchone()[0]
                self.refresh_file(PageFile(path, rel, slug, kind))
        return self.page_exists(slug)

    def page_exists(self, slug: str) -> bool:
        return self.conn.execute("SELECT 1 FROM pages WHERE slug = ?", (slug,)).fetchone() is not None

    # -- queries -------------------------------------------------------------

    def page_info(self, slug: str) -> dict | None:
        row = self.conn.execute(
            "SELECT slug, COALESCE(title, slug), page_type, summary, summary_is_placeholder, path, kind "
            "FROM pages WHERE slug = ?", (slug,)).fetchone()
        if not row:
            return None
        return {"slug": row[0], "title": row[1], "type": row[2], "summary": row[3],
                "placeholder": bool(row[4]), "path": row[5], "kind": row[6]}

    def neighbors(
        self,
        slug: str,
        *,
        outgoing: bool = True,
        incoming: bool = True,
        relation_type: str | None = None,
        limit: int | None = None,
        include_unresolved: bool = True,
    ) -> list[dict]:
        results: list[dict] = []
        type_filter = " AND r.relation_type = ?" if relation_type else ""
        type_args = (relation_type,) if relation_type else ()

        if outgoing:
            rows = self.conn.execute(
                f"""SELECT r.target_slug, r.relation_type, r.reason, r.resolved, p.title
                    FROM relations r LEFT JOIN pages p ON p.slug = r.target_slug
                    WHERE r.source_slug = ?{type_filter}""",
                (slug, *type_args),
            ).fetchall()
            for target, rtype, reason, resolved, title in rows:
                if not resolved and not include_unresolved:
                    continue
                entry = {"slug": target, "direction": "outgoing", "type": rtype}
                if title and title != target:
                    entry["title"] = title
                entry["reason"] = reason
                if not resolved:
                    entry["unresolved"] = True
                results.append(entry)

        if incoming:
            rows = self.conn.execute(
                f"""SELECT r.source_slug, r.relation_type, r.reason, p.title
                    FROM relations r LEFT JOIN pages p ON p.slug = r.source_slug
                    WHERE r.target_slug = ? AND r.resolved = 1 AND r.source_slug != ?{type_filter}""",
                (slug, slug, *type_args),
            ).fetchall()
            for source, rtype, reason, title in rows:
                entry = {"slug": source, "direction": "incoming", "type": self.vocabulary.inverse(rtype)}
                if title and title != source:
                    entry["title"] = title
                entry["reason"] = reason
                results.append(entry)

        results.sort(key=lambda entry: (entry["direction"] != "outgoing", entry.get("unresolved", False),
                                        self.vocabulary.rank_label(entry["type"]), entry["slug"]))
        return results[:limit] if limit is not None else results

    def status(self) -> dict:
        count = lambda sql: self.conn.execute(sql).fetchone()[0]  # noqa: E731
        cached = {row[0]: row[1:] for row in self.conn.execute("SELECT path, mtime_ns, size FROM pages")}
        stale = 0
        seen: set[str] = set()
        for page_file, entry in scan(self.settings):
            seen.add(page_file.rel)
            try:
                stat = entry.stat()
            except FileNotFoundError:
                continue
            if cached.get(page_file.rel) != (stat.st_mtime_ns, stat.st_size):
                stale += 1
        stale += len(set(cached) - seen)
        status = {
            "version": SCHEMA_VERSION,
            "pages": count(f"SELECT COUNT(*) FROM pages WHERE kind = '{PAGE}'"),
            "raw": count(f"SELECT COUNT(*) FROM pages WHERE kind != '{PAGE}'"),
            "relations": count("SELECT COUNT(*) FROM relations"),
            "unresolved": count("SELECT COUNT(*) FROM relations WHERE resolved = 0"),
            "chunks": count("SELECT COUNT(*) FROM chunks"),
            "stale": stale,
            "pending_embedding": self.pending_embeddings(),
        }
        if self.embed_model:
            status["embed_model"] = self.embed_model
        return status

    def snapshot(self) -> dict[str, tuple[str, set]]:
        """``{path: (content_hash, {(target, type)})}`` for cache verification."""
        result: dict[str, tuple[str, set]] = {
            path: (digest, set()) for path, digest in self.conn.execute("SELECT path, content_hash FROM pages")
        }
        for path, target, rtype in self.conn.execute("SELECT source_path, target_slug, relation_type FROM relations"):
            if path in result:
                result[path][1].add((target, rtype))
        return result

    def stale_summaries(self) -> dict[str, str]:
        """``{path: content_hash}`` for pages whose body changed while the summary did not."""
        return {
            path: digest
            for path, digest in self.conn.execute(
                """SELECT path, content_hash FROM pages
                   WHERE summary_is_placeholder = 0 AND summary_body_hash IS NOT NULL
                     AND summary_body_hash != body_hash"""
            )
        }


def embedding_text(title: str, heading_path: str, text: str) -> str:
    """Text sent to the embedding model and reranker for one chunk."""
    header = f"{title} > {heading_path}" if heading_path else title
    return f"{header}\n{text}"
