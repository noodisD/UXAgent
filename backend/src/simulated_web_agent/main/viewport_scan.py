"""
Viewport + live-DOM accessibility scan (METHODOLOGY_PLAN §3.4).

For each unique URL visited during an agent session, opens a browser at 375×812
(mobile), 768×1024 (tablet) and 1440×900 (desktop) and measures:
  - Horizontal scrollbar (content overflows viewport)
  - Fixed-width elements wider than the viewport
  - Very small font sizes (< 12px)
  - Tap-target size          — WCAG 2.2 SC 2.5.8
  - Text contrast            — WCAG 2.2 SC 1.4.3 (AA)
  - Drag-only interactions   — WCAG 2.2 SC 2.5.7 (human-check candidate)

These three need computed styles and layout, so they belong here; the checks that
can be read off the captured aria tree live in `a11y_checks.py`. As there, code
decides what is broken and the LLM only narrates it.

Scope honestly — this is a **mobile-web heuristic scan, not a WCAG conformance
audit**:
  - 24×24 is the normative SC 2.5.8 minimum. 48×48 is Material/HIG guidance and
    is reported separately as advisory: a 30px button is not a WCAG failure.
  - Contrast over a background image or gradient, and semi-transparent layers we
    would have to composite, are counted as `undetermined` — never scored. A
    guessed ratio is a fabricated finding.
  - SC 2.5.7 needs the *absence* of a pointer alternative, which we cannot
    observe. Drag affordances are reported with requires_human_check, never as
    failures.
  - A page that failed to load reports `None`, not zeros — zeros would read as
    "checked, nothing wrong".

Saves:  trace_dir/viewport_findings.json

Who consumes it
---------------
  - `reporter.py` — the per-agent report, the Markdown "Responsywność" section,
    the aggregate overflow statistics, and the aggregate LLM prompt.

The **dashboard deliberately does not.** `results_api` used to map these
findings into `personas[].viewport.findings`, and no template ever rendered it —
the mapping, its type, its CSS and its i18n keys were all built and then
orphaned. That path was removed on purpose in the 2026-09 consistency pass, not
overlooked: viewport was also removed from the client-facing report earlier, and
the measured numbers already reach the client through reporter.py above.

If you are about to rebuild a dashboard view for this, that is a UI decision to
take deliberately, not a gap to patch. Note it would need the normative/advisory
split back (24×24 is a WCAG failure, 48×48 is advice) — collapsing them is what
turned 4 real tap-target failures into 27 reported ones on an early run.
"""

import asyncio
import logging
from typing import Any

from playwright.async_api import async_playwright

from .trace_store import TraceStore

log = logging.getLogger(__name__)

# Desktop is 1440×900 to match _DEVICE_PROFILES["desktop"] in experiment.py.
# Until the device toggle landed, every session ran desktop-sized while the scan
# measured only mobile and tablet — so the one width the walkthrough findings
# actually came from was the one width never measured. A reader comparing the
# two sections was comparing different screens without being told.
VIEWPORTS = [
    {"label": "mobile", "width": 375, "height": 812},
    {"label": "tablet", "width": 768, "height": 1024},
    {"label": "desktop", "width": 1440, "height": 900},
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


# Tap-target size (WCAG 2.2 SC 2.5.8) and drag-only interaction (SC 2.5.7).
#
# 24x24 is the normative AA minimum; 48x48 is Material/HIG guidance, so it is
# reported separately as advisory — calling a 30px button a WCAG failure would be
# false. SC 2.5.8 exempts targets inline in a sentence, which is why anchors with
# text-flow siblings are skipped: without that, every link in the body copy
# drowns out the real failures.
_TAP_TARGET_JS = r"""
() => {
  const SEL = 'a[href], button, input:not([type=hidden]), select, textarea, ' +
              '[role=button], [role=link], [role=checkbox], [role=radio], [role=tab]';
  const isInlineInText = (el) => {
    if (el.tagName.toLowerCase() !== 'a') return false;
    const p = el.parentElement;
    if (!p) return false;
    // Inline element whose parent holds prose around it.
    const display = getComputedStyle(el).display;
    if (display !== 'inline' && display !== 'inline-block') return false;
    const own = (el.innerText || '').trim().length;
    const parentText = (p.innerText || '').trim().length;
    return parentText > own;
  };
  const label = (el) => {
    const tag = el.tagName.toLowerCase();
    const id = el.id ? `#${el.id}` : '';
    const txt = (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 30);
    return `${tag}${id}${txt ? ` "${txt}"` : ''}`;
  };

  // Collect the targets first: the spacing exception needs every target's
  // geometry, not just the one under test.
  const targets = [];
  document.querySelectorAll(SEL).forEach(el => {
    if (!el.offsetParent && getComputedStyle(el).position !== 'fixed') return;
    const r = el.getBoundingClientRect();
    if (r.width === 0 || r.height === 0) return;
    // Visually hidden, screen-reader-only elements ("Skip to main content",
    // the 1x1 px .sr-only pattern). Nobody taps them, and they were every one
    // of the Hyundai run's tap-target examples.
    if (r.width <= 2 || r.height <= 2) return;
    // The same pattern with a real width: .sr-navigator is 100% wide but
    // clipped to nothing, so "Skip to content" still counted as an 8px target.
    const cs = getComputedStyle(el);
    if (/^rect\(\s*[01](px)?\s*,?\s*[01](px)?\s*,?\s*[01](px)?\s*,?\s*[01](px)?\s*\)$/.test(cs.clip) ||
        /^inset\((50|100)%\)$/.test(cs.clipPath)) return;
    if (isInlineInText(el)) return;
    targets.push({ el, r, cx: r.left + r.width / 2, cy: r.top + r.height / 2 });
  });

  // SC 2.5.8 spacing exception: an undersized target passes if a 24px-diameter
  // circle centred on it intersects no other target's circle. Without this, any
  // well-spaced 22px link is reported as a WCAG failure when it conforms.
  const spacedApart = (t) => targets.every(o => {
    if (o === t) return true;
    const dx = o.cx - t.cx, dy = o.cy - t.cy;
    return Math.sqrt(dx * dx + dy * dy) >= 24;
  });

  let below24 = 0, below48 = 0;
  const examples = [];
  const keys = [];
  targets.forEach(t => {
    const min = Math.min(t.r.width, t.r.height);
    if (min < 24 && !spacedApart(t)) {
      below24++;
      if (keys.length < 300) keys.push(label(t.el));
      if (examples.length < 5)
        examples.push({ el: label(t.el), w: Math.round(t.r.width),
                        h: Math.round(t.r.height) });
    } else if (min < 48) {
      // Includes undersized targets saved by the spacing exception: they
      // conform to SC 2.5.8, but a 10px button is still hard to hit, and
      // dropping them entirely would hide that.
      below48++;
    }
  });
  return { below_24px: below24, below_48px_advisory: below48,
           elements_checked: targets.length, examples, keys };
}
"""

# Text contrast (WCAG 2.2 SC 1.4.3 AA): 4.5:1 normal, 3:1 large
# (>=24px, or >=18.66px bold).
#
# Honest limits, encoded as `undetermined` rather than guessed: we cannot
# compute contrast over a background image or gradient, and we do not composite
# semi-transparent layers. Anything we cannot compute is counted, never scored.
_CONTRAST_JS = r"""
() => {
  const parse = (s) => {
    const m = String(s).match(/rgba?\(([^)]+)\)/);
    if (!m) return null;
    const p = m[1].split(',').map(v => parseFloat(v));
    return { rgb: [p[0], p[1], p[2]], a: p.length > 3 ? p[3] : 1 };
  };
  const lum = (rgb) => {
    const a = rgb.map(v => {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2];
  };
  const ratio = (l1, l2) => (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);

  // Walk ancestors for the first opaque background. Bail out to `unknown` on an
  // image/gradient or a translucent layer rather than inventing a colour.
  const backdrop = (el) => {
    let e = el;
    while (e && e !== document.documentElement.parentElement) {
      const s = getComputedStyle(e);
      if (s.backgroundImage && s.backgroundImage !== 'none') return null;
      const bg = parse(s.backgroundColor);
      if (bg && bg.a === 1) return bg.rgb;
      if (bg && bg.a > 0) return null;  // translucent: would need compositing
      e = e.parentElement;
    }
    return [255, 255, 255];  // canvas default
  };

  let below = 0, undetermined = 0, checked = 0;
  const examples = [];
  const keys = [];
  document.querySelectorAll('p, li, span, a, h1, h2, h3, h4, h5, h6, td, th, label, button').forEach(el => {
    if (!el.offsetParent) return;
    const text = (el.textContent || '').trim();
    if (!text) return;
    // Only leaf-ish nodes, or we score the same text once per ancestor.
    if (el.children.length && !Array.from(el.childNodes).some(
        n => n.nodeType === 3 && n.textContent.trim())) return;

    const s = getComputedStyle(el);
    const fg = parse(s.color);
    if (!fg || fg.a < 1) { undetermined++; return; }
    const bg = backdrop(el);
    if (!bg) { undetermined++; return; }

    const r = ratio(lum(fg.rgb), lum(bg));
    // Text the same colour as its backdrop is not the page's defect but ours:
    // white text over a photo painted by a sibling layer, or a hidden carousel
    // slide, has no opaque ancestor, so the walk above falls back to a white
    // canvas and scores it 1.0. Counted as undetermined, never as a failure.
    if (r < 1.05) { undetermined++; return; }
    checked++;
    const size = parseFloat(s.fontSize);
    const weight = parseInt(s.fontWeight, 10) || 400;
    const isLarge = size >= 24 || (size >= 18.66 && weight >= 700);
    const need = isLarge ? 3 : 4.5;
    if (r < need) {
      below++;
      // The same footer link fails on every page; a key lets aggregation count
      // it once. Hyundai's single footer colour was reported as 73 "cases".
      if (keys.length < 300) keys.push(text.slice(0, 40) + '|' + (Math.round(r * 100) / 100));
      if (examples.length < 5)
        examples.push({ text: text.slice(0, 30), ratio: Math.round(r * 100) / 100,
                        required: need, size_px: size });
    }
  });
  return { below_threshold: below, undetermined, elements_checked: checked, examples, keys };
}
"""

# Drag-only interaction (SC 2.5.7). We can see a drag affordance but cannot prove
# no single-pointer alternative exists, so this is always a human-check
# candidate, never an asserted failure.
_DRAG_JS = r"""
() => {
  const els = document.querySelectorAll('[draggable="true"]');
  const examples = [];
  els.forEach(el => {
    if (examples.length >= 5) return;
    const tag = el.tagName.toLowerCase();
    const txt = (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 30);
    examples.push(`${tag}${txt ? ` "${txt}"` : ''}`);
  });
  return { count: els.length, examples };
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
        # §3.4 live-DOM heuristics: need computed style + layout, so they cannot
        # be read off the aria tree in a11y_checks.py.
        finding["tap_targets"] = await page.evaluate(_TAP_TARGET_JS)
        finding["contrast"] = await page.evaluate(_CONTRAST_JS)
        finding["drag_interactions"] = {
            **await page.evaluate(_DRAG_JS),
            # We see the affordance, not the absence of an alternative.
            "requires_human_check": True,
        }
        finding["error"] = None
        # Deterministic live measurement at this URL/viewport — self-evidencing.
        finding["verified"] = True
    except Exception as e:
        finding["error"] = str(e)
        finding["horizontal_overflow"] = None
        finding["wide_elements"] = []
        finding["small_fonts"] = []
        # None, not zero: nothing was measured here. Zeros would read as
        # "we checked and the page is fine".
        finding["tap_targets"] = None
        finding["contrast"] = None
        finding["drag_interactions"] = None
        finding["verified"] = False
    return finding


async def run_viewport_scan(store: TraceStore, headless: bool = True) -> list[dict]:
    """
    Scan each visited URL at every viewport in VIEWPORTS.

    Reads URLs from store; saves findings via store.write_viewport_findings().
    Returns the findings list.
    """
    # On-site pages only, first 5 to keep the scan fast.
    from .site_scope import same_site, target_url_for
    target = target_url_for(store)
    urls = [u for u in store.visited_urls() if not target or same_site(u, target)][:5]

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
