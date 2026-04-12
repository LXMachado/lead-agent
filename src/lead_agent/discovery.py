from __future__ import annotations

import re
from html import unescape
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse
from urllib.request import Request, urlopen

SEARCH_ENDPOINT = "https://duckduckgo.com/html/?q={query}"
ANCHOR_REGEX = re.compile(r"<a\s+[^>]*>", re.IGNORECASE)
HREF_REGEX = re.compile(r'href="([^"]+)"', re.IGNORECASE)


class DiscoveryError(Exception):
    pass


def _extract_target_url(raw_href: str) -> str | None:
    raw_href = unescape(raw_href)

    if raw_href.startswith("//"):
        return f"https:{raw_href}"

    parsed = urlparse(raw_href)
    if parsed.netloc and parsed.scheme in {"http", "https"}:
        return raw_href

    if raw_href.startswith("/"):
        full = urljoin("https://duckduckgo.com", raw_href)
        parsed_full = urlparse(full)
        if parsed_full.path == "/l/":
            q = parse_qs(parsed_full.query)
            target = q.get("uddg", [None])[0]
            return target
        return full

    return None


def search_urls(query: str, user_agent: str, timeout_seconds: int, limit: int) -> list[str]:
    url = SEARCH_ENDPOINT.format(query=quote_plus(query))
    req = Request(url, headers={"User-Agent": user_agent})

    try:
        with urlopen(req, timeout=timeout_seconds) as response:
            html = response.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        raise DiscoveryError(f"search request failed for query='{query}': {exc}") from exc

    found: list[str] = []
    seen: set[str] = set()

    for anchor in ANCHOR_REGEX.findall(html):
        if "result__a" not in anchor:
            continue

        href_match = HREF_REGEX.search(anchor)
        if not href_match:
            continue

        raw = href_match.group(1)
        target = _extract_target_url(raw)
        if not target:
            continue
        parsed = urlparse(target)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
        if normalized in seen:
            continue
        seen.add(normalized)
        found.append(target)
        if len(found) >= limit:
            break

    return found
