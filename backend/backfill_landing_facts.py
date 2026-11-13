"""
Measure the landing screen of a finished run from its stored markup (no browsing
session, no model call) and write landing_facts.json into each persona's trace dir.

The stored raw_html_0.html is the DOM as the persona's browser had it on landing. It is
opened in headless Chromium at the persona's device viewport with a <base href> of the page
it came from, so the site's live stylesheets load and the layout is the one the persona saw;
then the same script the sessions run (main/landing_facts.py) measures it.

Run from the backend directory:
    uv run python backfill_landing_facts.py <run_id> [--runs-dir runs] [--force] [--js]
        [--dry-run] [--shots DIR]
A persona that already has landing_facts.json is skipped unless --force.
"""
import argparse
import asyncio
import json
import re
import sys
from html import escape as html_escape
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright

from simulated_web_agent.main.landing_facts import LANDING_FACTS_JS, finalize_facts, summarize_landing_facts
from simulated_web_agent.main.trace_store import TraceStore

# What the sessions ran at (main/experiment.py _DEVICE_PROFILES); read from there when it
# imports, so the two cannot drift.
_FALLBACK_PROFILES = {
    "desktop": {"width": 1440, "height": 900, "is_mobile": False, "user_agent": None},
    "mobile": {"width": 390, "height": 844, "is_mobile": True, "user_agent": (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")},
}


# Consent layers a re-run page opens again: the sessions measured after consent was handled, so
# these are taken off before measuring (only with --js; the stored markup has no such layer).
_CONSENT_CSS = ("#onetrust-consent-sdk, .onetrust-pc-dark-filter, #CybotCookiebotDialog, #didomi-host, "
                "#usercentrics-root, .cc-window {display: none !important}")


def device_profiles() -> dict:
    try:
        from simulated_web_agent.main.experiment import _DEVICE_PROFILES
        return _DEVICE_PROFILES
    except Exception:  # noqa: BLE001
        return _FALLBACK_PROFILES


def device_scale_factor() -> float:
    try:
        from simulated_web_agent.config import load_cfg
        return float(load_cfg().environment.browser.context_options.device_scale_factor)
    except Exception:  # noqa: BLE001
        return 1.0


def with_base(html: str, href: str) -> str:
    """`html` with a <base href> first in <head>, so relative stylesheets load from the site."""
    tag = f'<base href="{html_escape(href, quote=True)}">'
    if re.search(r"<base\b", html, re.IGNORECASE):
        return html
    head = re.search(r"<head\b[^>]*>", html, re.IGNORECASE)
    if head:
        return html[:head.end()] + tag + html[head.end():]
    return tag + html


def page_url(store: TraceStore, run_dir: Path) -> str:
    """The page the persona landed on; the run's start URL when the trace does not say."""
    url = store.url_at_step(0)
    if url:
        return url
    try:
        return json.loads((run_dir / "run_manifest.json").read_text(encoding="utf-8")).get("start_url", "")
    except (OSError, ValueError):
        return ""


async def measure(browser, html: str, url: str, profile: dict, dpr: float, js: bool,
                  idle_cap_ms: int = 25000, shot: Path | None = None) -> dict | None:
    context = await browser.new_context(
        viewport={"width": profile["width"], "height": profile["height"]},
        device_scale_factor=dpr, is_mobile=bool(profile.get("is_mobile")),
        has_touch=bool(profile.get("is_mobile")), user_agent=profile.get("user_agent") or None,
        java_script_enabled=js, bypass_csp=True)
    try:
        page = await context.new_page()
        # The stored markup is served AS the page's own address: stylesheets and web fonts then
        # load same-origin (set_content gives an about:blank origin, where the fonts are refused
        # and the text reflows in a fallback face). The <base> keeps relative links honest for
        # a page that was reached through a redirect.
        await page.route(url, lambda route: route.fulfill(
            status=200, content_type="text/html; charset=utf-8", body=with_base(html, url)))
        try:
            await page.goto(url, wait_until="networkidle", timeout=idle_cap_ms)
        except PlaywrightTimeout:
            print("    network never went idle; measuring what has loaded", file=sys.stderr)
        await page.wait_for_timeout(800)
        if js:
            await page.add_style_tag(content=_CONSENT_CSS)
            await page.wait_for_timeout(200)
        raw = await page.evaluate(LANDING_FACTS_JS)
        if shot is not None:
            await page.screenshot(path=str(shot))
        return finalize_facts(raw)
    finally:
        await context.close()


def describe(name: str, device: str, facts: dict | None) -> None:
    print(f"\n{name} ({device})")
    if not facts:
        print("  no measurement")
        return
    vp = facts["viewport"]
    print(f"  viewport {vp['width']}x{vp['height']} dpr {vp['dpr']}")
    for n in facts["notices"]:
        print(f"  notice: {n['kind']} {n['position']} share {n['viewport_share']:.3f} "
              f"(height {n['viewport_height_share']:.3f}) rect {n['rect']} font {n['font_size_px']}px "
              f"color {n['color']} bg {n['background_color']} border {n['border_px']}px {n['border_color']} "
              f"mandated {n['mandated']} covers_nav {n['covers_navigation']} covers_ctas {n['covers_ctas']} "
              f"covers_content {n['covers_content']} close {n['has_close_control']}")
        print(f"          {n['text'][:110]!r}")
    for w in facts["floating_widgets"]:
        print(f"  widget: {w['tag']}#{w['id']}.{w['class'][:30]} {w['shape']} rect {w['rect']} "
              f"share {w['viewport_share']}")
        for o in w["overlaps"]:
            print(f"          overlaps {o['cta']!r}: {o['overlap_share']:.3f} of the button, "
                  f"label overlapped {o['label_overlapped']} ({o['label_overlap_share']:.3f})")
    print(f"  nav {facts['nav_labels'][:8]}  ctas {[c['text'] for c in facts['ctas']][:8]}")


async def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("run_id")
    ap.add_argument("--runs-dir", default="runs")
    ap.add_argument("--force", action="store_true", help="overwrite an existing landing_facts.json")
    ap.add_argument("--js", action="store_true",
                    help="run the stored page's scripts again, which brings back widgets built by script "
                         "(a chat bubble) but also re-builds a DOM that is already built; known consent "
                         "layers are hidden before measuring")
    ap.add_argument("--dry-run", action="store_true", help="measure and print, write nothing")
    ap.add_argument("--shots", help="also save each rendered page here, to compare with screenshot_landing.png")
    args = ap.parse_args(argv)

    run_dir = Path(args.runs_dir) / args.run_id
    if not run_dir.is_dir():
        print(f"no such run: {run_dir}", file=sys.stderr)
        return 1
    profiles, dpr = device_profiles(), device_scale_factor()
    shots = Path(args.shots) if args.shots else None
    if shots:
        shots.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            for persona_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
                if not (persona_dir / "raw_html" / "raw_html_0.html").exists():
                    continue
                store = TraceStore(persona_dir)
                card = store.read_persona_card() or {}
                device = str(card.get("device") or "desktop").lower()
                if store.read_landing_facts() is not None and not args.force and not args.dry_run:
                    print(f"\n{persona_dir.name}: already has landing_facts.json, skipped")
                    continue
                profile = profiles.get(device) or profiles["desktop"]
                try:
                    facts = await measure(
                        browser, store.read_raw_html(0), page_url(store, run_dir), profile, dpr,
                        js=args.js, shot=shots / f"{persona_dir.name}.png" if shots else None)
                except Exception as exc:  # noqa: BLE001 - one persona's page must not stop the rest
                    print(f"\n{persona_dir.name}: not measured ({exc!r:.200})", file=sys.stderr)
                    continue
                describe(f"{card.get('name') or persona_dir.name} {persona_dir.name}", device, facts)
                if facts:
                    if not args.dry_run:
                        store.write_landing_facts(facts)
                    print("  --- as the first-impression pass reads it ---")
                    print("  " + summarize_landing_facts(facts).replace("\n", "\n  "))
        finally:
            await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
