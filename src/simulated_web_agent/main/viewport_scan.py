"""
Lightweight viewport scan pass.

For each unique URL visited during an agent session, opens a new browser context
at 375×812 (mobile) and 768×1024 (tablet) and checks for obvious responsiveness issues:
  - Horizontal scrollbar (content overflows viewport)
  - Fixed-width elements wider than the viewport
  - Very small font sizes (< 12px) that appear on desktop but are harder to see

Saves:  trace_dir/viewport_findings.json
"""

import asyncio
import logging
from typing import Any

from playwright.async_api import async_playwright

from .trace_store import TraceStore

log = logging.getLogger(__name__)

VIEWPORTS = [
    {"label": "mobile", "width": 375, "height": 812},
    {"label": "tablet", "width": 768, "height": 1024},
]

# JS to detect horizontal overflow and collect oversized fixed-width elements
_OVERFLOW_JS = r"""
() => {
  const body = document.body;
  const html = document.documentElement;
  const vw = window.innerWidth;
  const hasHScroll = body.scrollWidth > vw || html.scrollWidth > vw;
  const wideEls = [];
  document.querySelectorAll('*').forEach(el => {
    const rect = el.getBoundingClientRect();
    if (rect.width > vw + 2) {
      const tag = el.tagName.toLowerCase();
      const id = el.id ? `#${el.id}` : '';
      const cls = el.className && typeof el.className === 'string'
        ? '.' + el.className.trim().split(/\s+/).slice(0, 2).join('.')
        : '';
      wideEls.push(`${tag}${id}${cls} (${Math.round(rect.width)}px)`);
    }
  });
  return { hasHScroll, wideEls: [...new Set(wideEls)].slice(0, 10) };
}
"""

# JS to sample small font sizes in body content
_FONT_JS = """
() => {
  const small = [];
  document.querySelectorAll('p, li, span, a, div, td, th, label').forEach(el => {
    if (!el.offsetParent) return; // invisible
    const size = parseFloat(window.getComputedStyle(el).fontSize);
    if (size > 0 && size < 12) {
      const text = el.innerText?.trim().slice(0, 40);
      if (text) small.push({ size, text });
    }
  });
  // deduplicate by size bucket
  const seen = new Set();
  return small.filter(x => {
    const k = Math.round(x.size);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  }).slice(0, 5);
}
"""


async def _scan_url(page, url: str, viewport: dict) -> dict[str, Any]:
    finding: dict[str, Any] = {"url": url, "viewport": viewport["label"]}
    try:
        await page.set_viewport_size(
            {"width": viewport["width"], "height": viewport["height"]}
        )
        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        await asyncio.sleep(1)

        overflow = await page.evaluate(_OVERFLOW_JS)
        small_fonts = await page.evaluate(_FONT_JS)

        finding["horizontal_overflow"] = overflow.get("hasHScroll", False)
        finding["wide_elements"] = overflow.get("wideEls", [])
        finding["small_fonts"] = small_fonts
        finding["error"] = None
    except Exception as e:
        finding["error"] = str(e)
        finding["horizontal_overflow"] = None
        finding["wide_elements"] = []
        finding["small_fonts"] = []
    return finding


async def run_viewport_scan(store: TraceStore, headless: bool = True) -> list[dict]:
    """
    Scan each visited URL at mobile and tablet viewports.

    Reads URLs from store; saves findings via store.write_viewport_findings().
    Returns the findings list.
    """
    urls = store.visited_urls()[:5]  # limit to first 5 to keep scan fast

    if not urls:
        log.warning("[viewport] No URLs found in %s", store.trace_dir)
        store.write_viewport_findings([])
        return []

    findings: list[dict] = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless)
        ctx = await browser.new_context()
        page = await ctx.new_page()

        for url in urls:
            for vp in VIEWPORTS:
                finding = await _scan_url(page, url, vp)
                findings.append(finding)
                log.info(
                    "[viewport] %s @ %s: overflow=%s wide=%d",
                    url,
                    vp["label"],
                    finding.get("horizontal_overflow"),
                    len(finding.get("wide_elements") or []),
                )

        await browser.close()

    store.write_viewport_findings(findings)
    return findings
