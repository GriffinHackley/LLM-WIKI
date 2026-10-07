"""`wiki clusters`: groups of pages that link each other densely, and whether a page
about what they share exists.

Communities come from Louvain over the relations graph (undirected; typed relations
weigh more than plain links), with a fixed seed so the same wiki gives the same
clusters. A cluster is *covered* when most of its pages link to (or from) one page of a
hub type (`hub = true` in `[types]`): a module, a concept. The ones left over are leads
for pages worth writing. With no hub types declared, every cluster is listed with the
page most of it links to, for the agent to judge.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict

from wiki_cli.cache import Cache
from wiki_cli.model import WARNING, Issue

MIN_WIKI_PAGES = 30  # below this, communities are noise
MIN_CLUSTER = 4
COVER_SHARE = 0.5  # a hub page covers a cluster when this share of its pages link to it
MAX_THROUGH = 3  # steps of a hub type's `hub_through` relations a page may be from its hub
PLAIN = {"links-to", "embeds"}  # built-in relations that say nothing about why pages relate
PLAIN_WEIGHT, TYPED_WEIGHT = 1.0, 2.0
SEED = 0
MAX_TERMS = 5
NAME_TERMS = 3  # shared terms in a cluster's name when no hub page covers it
STOPWORDS = set("""a an the and or of to in on for with by from at as is are was were be been it its this that
these those which who what when where how why not no but into over under about than then so such can may
will would should one two page pages wiki""".split())


def clusters(cache: Cache, *, min_size: int = MIN_CLUSTER, include_covered: bool = False) -> dict:
    import networkx as nx  # only this command needs it

    pages = {slug: (title, page_type, summary) for slug, title, page_type, summary in cache.conn.execute(
        "SELECT slug, title, page_type, summary FROM pages WHERE kind = 'page'")}
    hub_types = sorted(page_type.name for page_type in cache.settings.types if page_type.hub)
    result = {"pages": len(pages), "hub_types": hub_types, "clusters": [], "covered": 0}
    if len(pages) < MIN_WIKI_PAGES:
        result["too_small"] = MIN_WIKI_PAGES
        return result

    graph, relations = _graph(cache, pages)
    if not graph.number_of_edges():
        return result
    neighbors = {node: set(graph[node]) for node in graph}
    reach = _reach(cache, pages)

    terms = _Terms(pages)
    found = []
    for members in nx.community.louvain_communities(graph, weight="weight", seed=SEED):
        if len(members) < min_size:
            continue
        most_linked = _most_linked(members, neighbors, pages)
        covered_by = _most_linked(members, neighbors, pages, types=set(hub_types), reach=reach) if hub_types else None
        if covered_by is not None and covered_by["share"] < COVER_SHARE:
            covered_by = None
        if covered_by is not None:
            result["covered"] += 1
            if not include_covered:
                continue
        inside = Counter(relation for (a, b), relation in relations.items() if a in members and b in members)
        strength = {slug: sum(graph[slug][other]["weight"] for other in neighbors[slug] if other in members)
                    for slug in members}
        found.append({
            "size": len(members),
            "pages": sorted(members, key=lambda slug: (-strength[slug], slug)),
            "covered_by": covered_by,
            "most_linked": most_linked,
            "terms": terms.shared(members),
            "relations": dict(inside.most_common()),
        })
    result["clusters"] = sorted(found, key=lambda item: (item["covered_by"] is not None, -item["size"],
                                                           item["pages"][0]))
    return result


def membership(cache: Cache, *, min_size: int = MIN_CLUSTER) -> tuple[dict[str, int], list[str]]:
    """Each page's community, numbered from 0 by size (largest first), and a short name
    for each: the title of the hub page that covers it, else the terms its pages share,
    else "cluster N". Pages in no community of ``min_size`` or more are left out."""
    import networkx as nx

    pages = {slug: (title, page_type, summary) for slug, title, page_type, summary in cache.conn.execute(
        "SELECT slug, title, page_type, summary FROM pages WHERE kind = 'page'")}
    graph, _ = _graph(cache, pages)
    if not graph.number_of_edges():
        return {}, []
    found = [members for members in nx.community.louvain_communities(graph, weight="weight", seed=SEED)
             if len(members) >= min_size]
    found.sort(key=lambda members: (-len(members), min(members)))
    neighbors = {node: set(graph[node]) for node in graph}
    reach = _reach(cache, pages)
    hub_types = {page_type.name for page_type in cache.settings.types if page_type.hub}
    terms = _Terms(pages)
    names = []
    for number, members in enumerate(found, start=1):
        hub = _most_linked(members, neighbors, pages, types=hub_types, reach=reach) if hub_types else None
        if hub is not None and hub["share"] >= COVER_SHARE:
            names.append(pages[hub["slug"]][0] or hub["slug"])
        else:
            names.append(", ".join(terms.shared(members)[:NAME_TERMS]) or f"cluster {number}")
    return {slug: number for number, members in enumerate(found) for slug in members}, names


def overloaded_hubs(cache: Cache, *, min_size: int = MIN_CLUSTER) -> dict[str, list[dict]]:
    """Hub pages that cover two or more clusters, each with the clusters it covers
    (largest first). One hub over several separate groups of pages is usually several
    areas described on one page."""
    by_hub: dict[str, list[dict]] = defaultdict(list)
    for cluster in clusters(cache, min_size=min_size, include_covered=True)["clusters"]:
        if cluster["covered_by"] is not None:
            by_hub[cluster["covered_by"]["slug"]].append(cluster)
    return {slug: found for slug, found in sorted(by_hub.items()) if len(found) > 1}


def hub_issues(cache: Cache, paths: dict[str, str]) -> list[Issue]:
    """A `hub-covers-clusters` warning for each page in ``paths`` (slug -> path) that is
    a hub over several clusters. Record pages (an epic) are exempt: a feature spans
    several areas, and the page mirrors an item in the tracker, so it cannot be split."""
    records = {page_type.name for page_type in cache.settings.types if page_type.record}
    types = dict(cache.conn.execute("SELECT slug, page_type FROM pages WHERE kind = 'page'"))
    issues = []
    for slug, found in overloaded_hubs(cache).items():
        if slug not in paths or types.get(slug) in records:
            continue
        groups = []
        for cluster in found:
            members = [page for page in cluster["pages"] if page != slug]
            around = f" around {', '.join(cluster['terms'][:NAME_TERMS])}" if cluster["terms"] else ""
            groups.append(f"{cluster['size']} pages{around} ({', '.join(members[:3])}"
                          + (", ..." if len(members) > 3 else "") + ")")
        issues.append(Issue(
            WARNING, "hub-covers-clusters",
            f"this page is the hub of {len(found)} separate clusters of linked pages: {'; '.join(groups)}. Each may be "
            "an area of its own: consider splitting this page into one page per cluster, and keeping this one "
            "as a short overview that links them (wiki clusters --all lists the clusters)",
            path=paths[slug], slug=slug))
    return issues


def _graph(cache: Cache, pages) -> tuple:
    """The undirected relations graph between ``pages`` (typed relations weigh more),
    and each directed pair's relation type."""
    import networkx as nx

    weights: dict[tuple[str, str], float] = defaultdict(float)
    relations: dict[tuple[str, str], str] = {}
    for source, target, relation in cache.conn.execute(
            "SELECT source_slug, target_slug, relation_type FROM relations WHERE resolved = 1"):
        if source == target or source not in pages or target not in pages:
            continue
        pair = (source, target) if source < target else (target, source)
        weights[pair] += PLAIN_WEIGHT if relation in PLAIN else TYPED_WEIGHT
        relations[(source, target)] = relation
    graph = nx.Graph()
    for (a, b), weight in sorted(weights.items()):
        graph.add_edge(a, b, weight=weight)
    return graph, relations


def _most_linked(members: set[str], neighbors: dict[str, set[str]], pages: dict,
                 types: set[str] | None = None, reach: dict[str, set[str]] | None = None) -> dict | None:
    """The page (in the cluster or outside it) related to the most of its pages, with the
    share of them it is related to; only pages of ``types`` when given. A hub in ``reach``
    also counts the pages that reach it through its type's `hub_through` relations."""
    counts: Counter[str] = Counter()
    for slug in members:
        counts.update(neighbors.get(slug, ()))
    through: dict[str, int] = {}
    for hub, reached in (reach or {}).items():
        extra = (reached & members) - neighbors.get(hub, set())
        if extra:
            counts[hub] += len(extra)
            through[hub] = len(extra)
    best = None
    for slug, count in counts.items():
        page_type = pages[slug][1]
        if types is not None and page_type not in types:
            continue
        share = count / max(len(members) - (slug in members), 1)
        key = (share, count, _neg(slug))
        if best is None or key > best[0]:
            found = {"slug": slug, "type": page_type, "linked_from": count, "share": round(share, 2)}
            if through.get(slug):
                found["through"] = through[slug]  # of linked_from: reached, not linked directly
            best = (key, found)
    return best[1] if best else None


def _reach(cache: Cache, pages: dict) -> dict[str, set[str]]:
    """For each page of a hub type with `hub_through`, the pages that reach it through a
    chain of those relations, up to MAX_THROUGH steps (a pull request that implements a
    story that is a child of an epic)."""
    through = {page_type.name: set(page_type.hub_through) for page_type in cache.settings.types
               if page_type.hub_through}
    if not through:
        return {}
    wanted = set().union(*through.values())
    toward: dict[str, set[tuple[str, str]]] = defaultdict(set)  # target -> (source, relation)
    for source, target, relation in cache.conn.execute(
            "SELECT source_slug, target_slug, relation_type FROM relations WHERE resolved = 1"):
        if relation in wanted and source != target and source in pages and target in pages:
            toward[target].add((source, relation))
    reach = {}
    for slug, (_, page_type, _) in pages.items():
        kinds = through.get(page_type)
        if not kinds:
            continue
        found: set[str] = set()
        frontier = {slug}
        for _ in range(MAX_THROUGH):
            frontier = {source for target in frontier for source, relation in toward.get(target, ())
                        if relation in kinds} - found - {slug}
            if not frontier:
                break
            found |= frontier
        if found:
            reach[slug] = found
    return reach


def _neg(slug: str) -> tuple[int, ...]:
    """Sorts slugs in reverse, so ties go to the alphabetically first slug under ``max``."""
    return tuple(-ord(char) for char in slug)


class _Terms:
    """Words a cluster's titles and summaries share that are rare in the rest of the wiki."""

    def __init__(self, pages: dict):
        self.words = {slug: self._words(f"{title or ''} {summary or ''}") for slug, (title, _, summary) in
                      pages.items()}
        self.frequency: Counter[str] = Counter(word for words in self.words.values() for word in words)
        self.total = len(pages)

    @staticmethod
    def _words(text: str) -> set[str]:
        return {word for word in re.findall(r"[a-z][a-z0-9-]{2,}", text.lower()) if word not in STOPWORDS}

    def shared(self, members: set[str]) -> list[str]:
        inside = Counter(word for slug in members for word in self.words.get(slug, ()))
        scored = [(count * math.log(self.total / self.frequency[word]), word)
                  for word, count in inside.items() if count >= 2 and self.frequency[word] < self.total]
        return [word for _, word in sorted(scored, key=lambda item: (-item[0], item[1]))[:MAX_TERMS]]
