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


def pl_plural(n: int, one: str, few: str, many: str) -> str:
    """The Polish noun (or verb) form for a count: 1 / 2-4 (not 12-14) / the rest.

    "1 wniosków nie trafiło" and "2 wniosków" were printed to clients; a fixed
    genitive-plural form after the number is right only from five up.
    """
    n = abs(int(n))
    if n == 1:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def decimal_text(value, digits: int = 1) -> str:
    """A number as the report prints it: fixed decimals, Polish comma.

    The score rationale printed "Ocena 3.0/5" and "−0.50" under a report whose
    averages read "3,07" and whose percentages read "22,0".
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "—" if value in (None, "") else str(value)
    return f"{value:.{digits}f}".replace(".", ",")


def sessions_in(k) -> str:
    """"w 1 sesji" / "w 2 sesjach" — the locative agrees with the count."""
    try:
        k = int(k)
    except (TypeError, ValueError):
        return "w sesjach"
    return f"w {k} sesji" if k == 1 else f"w {k} sesjach"


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
    try:
        parts = urlsplit(raw)
    except ValueError:
        # "https://[domena]/x": a model-written placeholder, not an address.
        return raw.split("#")[0].split("?")[0].lstrip("/") or raw
    route = parts.fragment if parts.fragment.startswith("/") else ""
    key = f"{parts.netloc}{parts.path}".rstrip("/")
    return f"{key}#{route}" if route else key


def display_url(url, limit: int = 90) -> str:
    """A readable form of a URL for a report: no scheme, no query string.

    The test-drive page's address is 300 characters of configurator share-code.
    """
    key = view_key(url)
    return clip_words(key, limit) if key else str(url or "")
