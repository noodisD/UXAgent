"""Live-DOM accessibility heuristics (METHODOLOGY_PLAN §3.4, viewport half).

Tap-target size, text contrast and drag-only interaction cannot be read off the
aria tree — they need computed styles and layout, so they live here rather than
in a11y_checks.py.

Thresholds are normative where one exists, and labelled as advisory where not:
  • contrast 4.5:1 normal text / 3:1 large text — WCAG 2.2 SC 1.4.3 (AA)
  • tap target 24x24 — WCAG 2.2 SC 2.5.8 (AA). 48x48 is Material/HIG guidance,
    reported separately as advisory: calling a 30px button a WCAG failure would
    be false.

Each test serves real HTML to a real browser, so a passing test means the check
actually works — not that a regex matched.
"""
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from src.simulated_web_agent.main.trace_store import TraceStore
from src.simulated_web_agent.main.viewport_scan import run_viewport_scan


def _make_server(html: bytes) -> tuple[HTTPServer, str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/"


async def _scan(store: TraceStore, html: bytes) -> list[dict]:
    server, url = _make_server(html)
    try:
        store.append_observation({"tabs": [{"url": url}]})
        return await run_viewport_scan(store, headless=True)
    finally:
        server.shutdown()


def _mobile(findings: list[dict]) -> dict:
    return next(f for f in findings if f.get("viewport") == "mobile")


_PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{{margin:0;background:#fff;font-family:sans-serif}}</style>
</head><body>{}</body></html>"""


def _html(body: str) -> bytes:
    return _PAGE.format(body).encode("utf-8")


# --- tap targets ---------------------------------------------------------

@pytest.mark.asyncio
async def test_lone_tiny_target_conforms_but_is_still_advised(store: TraceStore):
    """A 10px button alone on the page passes SC 2.5.8 via the spacing
    exception — so it is not a WCAG failure. It is still hard to hit, so it must
    not disappear: it belongs in the 48px advisory."""
    findings = await _scan(store, _html(
        '<button style="width:10px;height:10px;padding:0;border:0">x</button>'))
    tt = _mobile(findings)["tap_targets"]
    assert tt["below_24px"] == 0
    assert tt["below_48px_advisory"] == 1


@pytest.mark.asyncio
async def test_target_meeting_wcag_but_under_48_is_advisory_only(store: TraceStore):
    """30px passes WCAG 2.2 SC 2.5.8. Reporting it as a failure would be false —
    it is only below the 48px Material/HIG recommendation."""
    findings = await _scan(store, _html(
        '<button style="width:30px;height:30px;padding:0;border:0">x</button>'))
    tt = _mobile(findings)["tap_targets"]
    assert tt["below_24px"] == 0
    assert tt["below_48px_advisory"] == 1


@pytest.mark.asyncio
async def test_large_tap_target_is_not_flagged(store: TraceStore):
    findings = await _scan(store, _html(
        '<button style="width:56px;height:56px;padding:0;border:0">ok</button>'))
    tt = _mobile(findings)["tap_targets"]
    assert tt["below_24px"] == 0
    assert tt["below_48px_advisory"] == 0


@pytest.mark.asyncio
async def test_isolated_undersized_target_passes_via_the_spacing_exception(store: TraceStore):
    """SC 2.5.8 passes an undersized target whose 24px circle touches no other
    target's. A well-spaced 20px link conforms — calling it a WCAG failure is a
    false claim, and on example-shop.pl it is what flagged the footer links."""
    findings = await _scan(store, _html(
        '<a href="#" style="display:block;width:120px;height:20px">Polityka prywatnosci</a>'
        '<div style="height:200px"></div>'
        '<a href="#" style="display:block;width:120px;height:20px">Regulamin</a>'))
    assert _mobile(findings)["tap_targets"]["below_24px"] == 0


@pytest.mark.asyncio
async def test_crowded_undersized_targets_still_fail(store: TraceStore):
    """Carousel dots sit shoulder to shoulder — the spacing exception cannot
    save them, and example-shop.pl really does ship 12x12 ones."""
    dots = "".join(
        '<button style="width:12px;height:12px;padding:0;margin:0;border:0">.</button>'
        for _ in range(4))
    findings = await _scan(store, _html(f'<div style="display:flex;gap:2px">{dots}</div>'))
    tt = _mobile(findings)["tap_targets"]
    assert tt["below_24px"] == 4
    assert tt["examples"], "a failing target must be identifiable"


@pytest.mark.asyncio
async def test_inline_text_link_is_exempt(store: TraceStore):
    """SC 2.5.8 exempts targets in a sentence — flagging them would bury the
    real failures under every link in the body copy."""
    findings = await _scan(store, _html(
        '<p style="font-size:14px">Zobacz <a href="#">nasz sklep</a> dzisiaj.</p>'))
    assert _mobile(findings)["tap_targets"]["below_24px"] == 0


# --- contrast ------------------------------------------------------------

@pytest.mark.asyncio
async def test_low_contrast_text_is_flagged(store: TraceStore):
    # #999 on #fff is 2.85:1 — below the 4.5:1 required for normal text.
    findings = await _scan(store, _html(
        '<p style="color:#999999;background:#ffffff;font-size:14px">Tekst</p>'))
    c = _mobile(findings)["contrast"]
    assert c["below_threshold"] >= 1
    assert c["examples"][0]["ratio"] < 4.5


@pytest.mark.asyncio
@pytest.mark.parametrize("hex_colour, expected", [
    ("#000000", 21.0),   # black on white — the maximum possible ratio
    ("#999999", 2.85),   # the canonical "too light grey"
    ("#767676", 4.54),   # the lightest grey that still passes AA on white
    ("#ffffff", 1.0),    # white on white — invisible
])
async def test_contrast_ratio_matches_the_wcag_formula(store: TraceStore,
                                                       hex_colour, expected):
    """Pin the luminance math against published values. A plausible-looking but
    wrong formula would ship confident, fabricated contrast findings — the ratio
    itself is quoted to the client."""
    findings = await _scan(store, _html(
        f'<p style="color:{hex_colour};background:#ffffff;font-size:14px">Tekst</p>'))
    c = _mobile(findings)["contrast"]
    if expected >= 4.5:
        assert c["below_threshold"] == 0
        return
    assert c["examples"][0]["ratio"] == pytest.approx(expected, abs=0.02)


@pytest.mark.asyncio
async def test_good_contrast_is_not_flagged(store: TraceStore):
    findings = await _scan(store, _html(
        '<p style="color:#000000;background:#ffffff;font-size:14px">Tekst</p>'))
    assert _mobile(findings)["contrast"]["below_threshold"] == 0


@pytest.mark.asyncio
async def test_large_text_uses_the_3_to_1_threshold(store: TraceStore):
    """#767676 on white is 4.54:1 — passes as normal text and as large text.
    #949494 is 3.03:1: fails normal, passes large. Using 4.5 everywhere would
    report a compliant heading as a defect."""
    findings = await _scan(store, _html(
        '<p style="color:#949494;background:#fff;font-size:32px">Naglowek</p>'))
    assert _mobile(findings)["contrast"]["below_threshold"] == 0


@pytest.mark.asyncio
async def test_text_over_a_background_image_is_not_judged(store: TraceStore):
    """We cannot compute the contrast of text over an image, so it must be
    counted as undetermined — guessing a ratio would be a fabricated finding."""
    findings = await _scan(store, _html(
        '<div style="background-image:url(data:image/gif;base64,R0lGODlhAQABAAAAACH5BAEKAAEALAAAAAABAAEAAAICTAEAOw==)">'
        '<p style="color:#999;font-size:14px">Tekst na obrazku</p></div>'))
    c = _mobile(findings)["contrast"]
    assert c["undetermined"] >= 1
    assert c["below_threshold"] == 0


# --- drag-only interaction ----------------------------------------------

@pytest.mark.asyncio
async def test_draggable_element_is_reported_for_human_check(store: TraceStore):
    """SC 2.5.7 requires a single-pointer alternative. We can see the drag
    handle but not prove an alternative is missing, so this is a candidate for a
    human to check — never an asserted failure."""
    findings = await _scan(store, _html('<div draggable="true">Przeciagnij</div>'))
    d = _mobile(findings)["drag_interactions"]
    assert d["count"] == 1
    assert d["requires_human_check"] is True


@pytest.mark.asyncio
async def test_page_without_drag_reports_nothing(store: TraceStore):
    findings = await _scan(store, _html("<p>Zwykly tekst</p>"))
    assert _mobile(findings)["drag_interactions"]["count"] == 0


# --- degradation ---------------------------------------------------------

def test_dashboard_rows_separate_wcag_failures_from_advice():
    from src.simulated_web_agent.main.results_api import _viewport_rows

    rows = _viewport_rows([{
        "viewport": "mobile",
        "tap_targets": {"below_24px": 2, "below_48px_advisory": 5,
                        "examples": [{"el": "button", "w": 10, "h": 10}]},
        "contrast": {"below_threshold": 3, "undetermined": 1,
                     "examples": [{"ratio": 2.85, "required": 4.5}]},
        "drag_interactions": {"count": 1, "requires_human_check": True},
    }])
    by_sev = {r["severity"] for r in rows}
    assert "serious" in by_sev  # the 24px failures and the contrast failures
    advisory = [r for r in rows if "recommended 48" in r["issue"]]
    assert advisory and advisory[0]["severity"] == "minor"
    assert any("2.85:1" in r["issue"] for r in rows)
    assert any("human check" in r["issue"] for r in rows)


def test_dashboard_says_nothing_when_the_page_never_loaded():
    """None means unmeasured. Emitting no row is right; emitting a clean row
    would claim we checked."""
    from src.simulated_web_agent.main.results_api import _viewport_rows

    rows = _viewport_rows([{
        "viewport": "mobile", "error": "timeout", "horizontal_overflow": None,
        "tap_targets": None, "contrast": None, "drag_interactions": None,
    }])
    assert rows == []


@pytest.mark.asyncio
async def test_failed_page_load_leaves_checks_absent_not_zero(store: TraceStore):
    """A page that never loaded has no measurements. Emitting zeros would read
    as 'we checked and found nothing wrong'."""
    store.append_observation({"tabs": [{"url": "http://127.0.0.1:9/"}]})
    findings = await run_viewport_scan(store, headless=True)
    f = _mobile(findings)
    assert f["error"] is not None
    assert f["tap_targets"] is None
    assert f["contrast"] is None
