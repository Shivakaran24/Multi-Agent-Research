"""Clean up search results before researchers read them: dedupe, drop junk, rank by authority."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

MIN_CONTENT_CHARS = 80
AUTHORITY_SUFFIXES = (".gov", ".edu", ".int", ".mil", ".ac.uk", ".gov.uk", ".gov.in", ".ac.in", ".edu.au")
_TRACKING = ("utm_", "fbclid", "gclid", "mc_", "ref", "source", "igshid")


def host_of(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def normalize_url(url: str) -> str:
    """Stable key for duplicate detection: no www, fragment, tracking params or trailing slash."""
    parts = urlparse(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query) if not k.lower().startswith(_TRACKING)]
    path = parts.path.rstrip("/") or ""
    return urlunparse(("https", host_of(url), path, "", urlencode(sorted(query)), ""))


def _matches(host: str, domains: Iterable[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in (x.lower().lstrip(".") for x in domains))


def authority_bonus(url: str, preferred: Iterable[str]) -> float:
    host = host_of(url)
    if host.endswith(AUTHORITY_SUFFIXES):
        return 0.2
    return 0.15 if _matches(host, preferred) else 0.0


def filter_and_rank(
    results: list[dict[str, Any]],
    *,
    limit: int,
    blocked: Iterable[str] = (),
    preferred: Iterable[str] = (),
) -> list[dict[str, Any]]:
    """Keep the best `limit` results: unique pages with real text, not on a blocked domain.

    Ranking is the search engine's own relevance score plus a bonus for authoritative hosts.
    When filtering would remove everything, the original top results are returned instead so a
    narrow topic still gets researched.
    """
    blocked = list(blocked)
    preferred = list(preferred)
    seen: set[str] = set()
    kept: list[tuple[float, int, dict[str, Any]]] = []
    for index, result in enumerate(results):
        url = str(result.get("url") or "")
        if not url:
            continue
        key = normalize_url(url)
        text = str(result.get("content") or result.get("snippet") or "").strip()
        if key in seen or _matches(host_of(url), blocked) or len(text) < MIN_CONTENT_CHARS:
            continue
        seen.add(key)
        try:
            score = float(result.get("score") or 0.0)
        except (TypeError, ValueError):
            score = 0.0
        kept.append((score + authority_bonus(url, preferred), -index, result))
    if not kept:
        return [r for r in results if r.get("url")][:limit]
    kept.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [item[2] for item in kept[:limit]]
