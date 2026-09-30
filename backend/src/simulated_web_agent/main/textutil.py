"""Small text helpers the report renderers and the aggregation share."""
from __future__ import annotations

from urllib.parse import urlsplit


def clip_words(text, limit: int = 160) -> str:
    """Shorten to a word boundary with an ellipsis.

    A hard slice printed "…ni (https://…)" and "…przycisk, które ni" to clients.
    """
    text = str(text or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0].rstrip(" ,;:—-") + "…"


def view_key(url) -> str:
    """Identity of a page for counting distinct pages: host, path, and a
    single-page app's own route (#/summary), but not the query string.

    The same page opened by three participants carries three different query
    strings (a model code, a share link), and three sessions of five pages each
    read as "15 pages scanned".
    """
    raw = str(url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    route = parts.fragment if parts.fragment.startswith("/") else ""
    key = f"{parts.netloc}{parts.path}".rstrip("/")
    return f"{key}#{route}" if route else key


def display_url(url, limit: int = 90) -> str:
    """A readable form of a URL for a report: no scheme, no query string.

    The test-drive page's address is 300 characters of configurator share-code.
    """
    key = view_key(url)
    return clip_words(key, limit) if key else str(url or "")
