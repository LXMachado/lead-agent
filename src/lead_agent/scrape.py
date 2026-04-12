from __future__ import annotations

import re
from html import unescape
from urllib.request import Request, urlopen

from .utils import EMAIL_REGEX, PHONE_REGEX, first_match

TITLE_REGEX = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
META_DESC_REGEX = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']',
    re.IGNORECASE | re.DOTALL,
)
TAG_REGEX = re.compile(r"<[^>]+>")
WHITESPACE_REGEX = re.compile(r"\s+")


class ScrapeError(Exception):
    pass


def fetch_html(url: str, user_agent: str, timeout_seconds: int) -> str:
    req = Request(url, headers={"User-Agent": user_agent})
    try:
        with urlopen(req, timeout=timeout_seconds) as response:
            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type:
                raise ScrapeError(f"non-html content type for {url}: {content_type}")
            return response.read().decode("utf-8", errors="ignore")
    except Exception as exc:
        raise ScrapeError(f"failed to fetch url='{url}': {exc}") from exc


def _strip_html(html: str) -> str:
    no_script = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.IGNORECASE)
    no_style = re.sub(r"<style[\s\S]*?</style>", " ", no_script, flags=re.IGNORECASE)
    text = TAG_REGEX.sub(" ", no_style)
    text = unescape(text)
    return WHITESPACE_REGEX.sub(" ", text).strip()


def extract_page_signals(html: str) -> dict[str, str | int | None]:
    text = _strip_html(html)

    title_match = TITLE_REGEX.search(html)
    title = WHITESPACE_REGEX.sub(" ", unescape(title_match.group(1))).strip() if title_match else None

    desc_match = META_DESC_REGEX.search(html)
    description = (
        WHITESPACE_REGEX.sub(" ", unescape(desc_match.group(1))).strip() if desc_match else None
    )

    email = first_match(EMAIL_REGEX, html)
    phone = first_match(PHONE_REGEX, html)

    word_count = len(text.split())

    return {
        "title": title,
        "description": description,
        "email": email,
        "phone": phone,
        "text": text,
        "word_count": word_count,
    }
