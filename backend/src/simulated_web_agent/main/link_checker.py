"""
Link checker — verifies <a href> links found on each visited page.

For each unique URL visited during an agent session, opens a Playwright
browser, collects all on-page anchors, then probes them with Playwright's
request client (HEAD, GET where HEAD is refused). Links returning non-2xx/3xx
status codes (after redirects) are flagged as broken — except the ones that
mean the server refused this client rather than the link being dead (see
_REFUSED_US_STATUSES). A link whose server did not answer is "unchecked".

Limits: first 5 visited URLs × first 40 links each, to keep scan fast.

Saves:  trace_dir/link_findings.json
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from playwright.async_api import async_playwright

from .trace_store import TraceStore

log = logging.getLogger(__name__)

_MAX_URLS = 5
_MAX_LINKS_PER_PAGE = 40
_FETCH_TIMEOUT_MS = 8_000
_NAV_TIMEOUT_MS = 15_000

# HTTP statuses that are NOT considered broken
_OK_STATUSES = {200, 201, 204, 301, 302, 303, 304, 307, 308}

# ...and statuses that mean the server refused *us*, not that the link is dead.
# The hyundai re-run reported the client's own homepage as a broken link, and
# the report told them to fix it: hyundai.com/pl/pl.html answered 403 to our
# automated request, as did their Twitter profile. A visitor clicking either
# one gets the page. A UX specialist checks a "broken link" claim by clicking
# it, so a false one here costs more credibility than a missed real one.
#
#   400     — the server rejected the form of our request: facebook.com
#             answers 400 to an automated request for a profile a person
#             opens fine, and the first live run of the request client
#             reported Hyundai's Facebook link as broken on 10 pages
#   401/403 — refused this client (bot protection, auth wall)
#   405     — the server dislikes our request method, not the URL
#   429     — we asked too often
#
# Genuinely broken still is: 404, 410, any 5xx, and a domain that does not
# resolve. A timeout or a refused connection is "could not check", reported as
# such — never counted as a working link, which is what status 0 used to mean.
_REFUSED_US_STATUSES = {400, 401, 403, 405, 429}

# Exception texts that mean the host does not exist — dead for any visitor.
_NO_SUCH_HOST = ("err_name_not_resolved", "getaddrinfo", "enotfound", "name or service not known",
                 "nodename nor servname")


def classify(status: int | None, error: str | None = None) -> str:
    """'ok', 'broken', 'refused' (the server turned us away) or 'unchecked'."""
    if status is None or status == 0:
        low = str(error or "").lower()
        return "broken" if any(m in low for m in _NO_SUCH_HOST) else "unchecked"
    if status in _OK_STATUSES or 200 <= status < 400:
        return "ok"
    if status in _REFUSED_US_STATUSES:
        return "refused"
    return "broken"


_COLLECT_HREFS_JS = """
() => Array.from(document.querySelectorAll('a[href]'))
    .map(a => a.href)
    .filter(h => h.startsWith('http'))
"""


async def _check_links_on_page(page, url: str) -> dict[str, Any]:
    finding: dict[str, Any] = {
        "url": url,
        "links_checked": 0,
        "broken": [],
        "check_errors": [],
        "error": None,
    }
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT_MS)
        await asyncio.sleep(0.5)

        hrefs: list[str] = await page.evaluate(_COLLECT_HREFS_JS)

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for h in hrefs:
            if h not in seen:
                seen.add(h)
                unique.append(h)
            if len(unique) >= _MAX_LINKS_PER_PAGE:
                break

        finding["links_checked"] = len(unique)
        # Which links, so aggregation can count distinct ones. The header and
        # footer repeat on every page and every persona revisits pages, so the
        # summed count ("brak niedziałających linków na 313 sprawdzonych") was
        # the same links over and over.
        finding["checked_urls"] = unique
        broken: list[dict] = []
        errors: list[dict] = []
        unchecked: list[dict] = []

        # Playwright's request client, not fetch() inside the page: a page's
        # fetch of another origin is blocked by CORS and came back as status 0
        # — counted as a working link, like every timeout and refused
        # connection. The client is not subject to CORS, so cross-origin links
        # (45 in the archive: facebook, youtube, privacy.hyundai.com) get a
        # real status too.
        request = page.context.request
        for href in unique:
            status, error = None, None
            try:
                resp = await request.head(href, timeout=_FETCH_TIMEOUT_MS, max_redirects=5)
                status = resp.status
                if status in (400, 405):  # some servers refuse HEAD; ask the way a browser does
                    resp = await request.get(href, timeout=_FETCH_TIMEOUT_MS, max_redirects=5)
                    status = resp.status
            except Exception as exc:
                error = str(exc)
            verdict = classify(status, error)
            if verdict == "broken":
                broken.append({"url": href, "status": status or 0, "error": error})
            elif verdict == "unchecked":
                unchecked.append({"url": href, "error": (error or "")[:200]})

        finding["broken"] = broken
        finding["check_errors"] = errors
        # Links that could not be checked: not working, not broken — unknown.
        finding["unchecked"] = unchecked
        finding["checked_urls"] = [u for u in unique
                                   if u not in {x["url"] for x in unchecked}]
        finding["links_checked"] = len(finding["checked_urls"])

    except Exception as exc:
        finding["error"] = str(exc)

    return finding


async def run_link_check(store: TraceStore, headless: bool = True) -> list[dict]:
    """
    Check links on visited pages.

    Reads URLs from store; saves findings via store.write_link_findings().
    Returns the findings list.
    """
    # On-site pages only: a tab or redirect to another domain must not have its
    # links counted as the client's.
    from .site_scope import same_site, target_url_for
    target = target_url_for(store)
    urls = [u for u in store.visited_urls() if not target or same_site(u, target)][:_MAX_URLS]

    if not urls:
        log.warning("[link_checker] No URLs in %s", store.trace_dir)
        store.write_link_findings([])
        return []

    findings: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        for url in urls:
            log.info("[link_checker] Scanning %s", url)
            result = await _check_links_on_page(page, url)
            findings.append(result)
            log.info(
                "[link_checker] %s — %d links checked, %d broken",
                url,
                result.get("links_checked", 0),
                len(result.get("broken") or []),
            )

        await browser.close()

    store.write_link_findings(findings)
    return findings
