"""
Link checker — verifies <a href> links found on each visited page.

For each unique URL visited during an agent session, opens a Playwright
browser, collects all on-page anchors, then probes them with JS fetch()
HEAD requests.  Links returning non-2xx/3xx status codes (after redirects)
are flagged as broken.

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
_OK_STATUSES = {0, 200, 201, 204, 301, 302, 303, 304, 307, 308}

_COLLECT_HREFS_JS = """
() => Array.from(document.querySelectorAll('a[href]'))
    .map(a => a.href)
    .filter(h => h.startsWith('http'))
"""

_FETCH_JS = """
async (url) => {
    try {
        const r = await fetch(url, {
            method: 'HEAD',
            redirect: 'follow',
            signal: AbortSignal.timeout(""" + str(_FETCH_TIMEOUT_MS) + """),
        });
        return { status: r.status, ok: r.ok };
    } catch (e) {
        return { status: 0, ok: false, error: String(e) };
    }
}
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
        broken: list[dict] = []
        errors: list[dict] = []

        for href in unique:
            try:
                resp: dict = await page.evaluate(_FETCH_JS, href)
                status = resp.get("status", 0)
                ok = resp.get("ok", False)
                if not ok and status not in _OK_STATUSES:
                    broken.append(
                        {
                            "url": href,
                            "status": status,
                            "error": resp.get("error"),
                        }
                    )
            except Exception as exc:
                errors.append({"url": href, "error": str(exc)})

        finding["broken"] = broken
        finding["check_errors"] = errors

    except Exception as exc:
        finding["error"] = str(exc)

    return finding


async def run_link_check(store: TraceStore, headless: bool = True) -> list[dict]:
    """
    Check links on visited pages.

    Reads URLs from store; saves findings via store.write_link_findings().
    Returns the findings list.
    """
    urls = store.visited_urls()[:_MAX_URLS]

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
