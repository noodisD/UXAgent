"""
PDF rendering of the aggregated HTML report.

Chromium's own print engine renders `aggregated_report.html`, rather than a
PDF-drawing library. Three reasons:

  - No new dependency. Playwright and Chromium are already installed for the
    agent sessions and the viewport scan.
  - Fidelity. The report is styled HTML with a `@media print` block that was
    already written for it (reporter_html._CSS); a drawing library would mean
    re-implementing that layout and letting the two drift.
  - Evidence screenshots are referenced as paths relative to the report's own
    directory (`<persona_dir>/screenshot/screenshot_4.png`). Loading the file
    from disk over `file://` resolves them exactly as a browser would — no
    rewriting, no embedding, no base-href tricks.

The PDF is generated on demand and cached next to the HTML, so runs nobody
downloads cost nothing, and reports produced before this existed still work.

Deliberately uses a standalone `async_playwright()` context rather than
WebAgentEnv's shared refcounted driver. This is a short, request-scoped render
that must not participate in the agent sessions' driver lifecycle — the same
choice viewport_scan and link_checker make.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from playwright.async_api import async_playwright

log = logging.getLogger(__name__)

HTML_NAME = "aggregated_report.html"
PDF_NAME = "aggregated_report.pdf"

# A4 with real margins; the header band is full-bleed by design, so the top
# margin is smaller than the rest.
_PDF_OPTIONS = {
    "format": "A4",
    "print_background": True,  # without it the header, chips and cards go white
    "margin": {"top": "10mm", "right": "10mm", "bottom": "14mm", "left": "10mm"},
    "prefer_css_page_size": False,
}


async def render_report_pdf(html_path: Path, pdf_path: Path) -> Path:
    """Render one HTML report to PDF. Raises on failure — the caller decides."""
    url = html_path.resolve().as_uri()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        try:
            page = await browser.new_page()
            # "load" rather than "networkidle": every asset is local, so there is
            # no network to go idle, and networkidle would just burn the timeout.
            await page.goto(url, wait_until="load", timeout=60_000)
            # Screenshots are decoded after load; without this the first page of
            # evidence can render as blank boxes.
            await page.evaluate(
                "Promise.all(Array.from(document.images)"
                ".filter(i => !i.complete)"
                ".map(i => new Promise(r => { i.onload = i.onerror = r; })))"
            )
            await page.emulate_media(media="print")
            pdf_path.parent.mkdir(parents=True, exist_ok=True)
            await page.pdf(path=str(pdf_path), **_PDF_OPTIONS)
        finally:
            await browser.close()
    return pdf_path


def ensure_report_pdf(run_dir: Path) -> Path | None:
    """The run's report PDF, rendering it if missing or out of date.

    Returns None when there is no HTML report to render from — the caller turns
    that into a 404 rather than serving a stale or empty file.
    """
    html_path = Path(run_dir) / HTML_NAME
    if not html_path.exists():
        return None

    pdf_path = Path(run_dir) / PDF_NAME
    # Regenerate when the HTML is newer: reports are re-rendered whenever the
    # styling or the data changes, and a stale PDF would silently outlive that.
    if pdf_path.exists() and pdf_path.stat().st_mtime >= html_path.stat().st_mtime:
        return pdf_path

    log.info("[report_pdf] rendering %s", pdf_path)
    asyncio.run(render_report_pdf(html_path, pdf_path))
    return pdf_path
