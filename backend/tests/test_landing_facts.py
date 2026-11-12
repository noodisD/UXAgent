"""The landing screen is measured, not described by a model looking at a screenshot.

The first real audit of bnpparibas.pl: all six first impressions called an in-flow, white,
2 px-framed, 18 px UOKiK notice a pop-up that "covers the screen" in "small grey print", and
a chat circle clipping the edge of one button an "almost completely covered" button. The
script in main/landing_facts.py measures it in the live page; these tests run it in real
Chromium on small local pages (skipped where Chromium is not installed).
"""
import json
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import async_playwright

from simulated_web_agent.main.landing_facts import (
    LANDING_FACTS_JS, NO_FACTS, capture_landing_facts, finalize_facts, landing_notice_rollup,
    summarize_landing_facts,
)
from simulated_web_agent.main.trace_store import TraceStore

NAV = ('<header><nav><a href="#k">Konta</a> <a href="#r">Kredyty</a> <a href="#c">Karty</a></nav></header>')
CTA = ('<a class="btn" href="#" style="display:block;position:absolute;left:200px;top:500px;width:200px;'
       'height:50px;background:#0a0;color:#fff;text-align:center;line-height:50px">OTWÓRZ KONTO</a>')
FILLER = '<div style="height:3000px"></div>'

NOTICE = ('<div class="uokik-info" style="border:2px solid #00834f;background:#fff;padding:8px;'
          'position:relative;font-size:18px"><button style="position:absolute;right:4px;top:4px">'
          'Zamknij<span>×</span></button><p><b>Komunikat o decyzji Prezesa Urzędu Ochrony Konkurencji '
          'i Konsumentów</b></p><p>Na stronie UOKiK możesz zobaczyć decyzję zobowiązującą Prezesa UOKiK '
          'z dnia 4 września 2026 r. nr RKR-3/2026 określającą obowiązki Banku.</p></div>')

VIEWPORT = {"width": 1000, "height": 700}


def _page(body: str) -> str:
    return f'<!doctype html><html><body style="margin:0;font:16px Arial">{body}</body></html>'


async def _measure(body: str, viewport=VIEWPORT) -> dict:
    pw = await async_playwright().start()
    try:
        try:
            browser = await pw.chromium.launch(headless=True)
        except Exception as exc:  # noqa: BLE001 - no browser installed
            pytest.skip(f"chromium is not available: {exc}")
        try:
            page = await browser.new_page(viewport=viewport)
            await page.set_content(_page(body))
            raw = await page.evaluate(LANDING_FACTS_JS)
        finally:
            await browser.close()
    finally:
        await pw.stop()
    facts = finalize_facts(raw)
    assert facts is not None, raw
    return facts


async def test_an_in_flow_notice_is_measured_as_in_flow_and_mandated():
    facts = await _measure(NOTICE + NAV + CTA + FILLER)
    (n,) = facts["notices"]
    assert n["kind"] == "in_flow" and n["is_overlay"] is False
    assert n["covers_navigation"] is False and n["covers_ctas"] is False and n["covers_content"] is False
    assert n["font_size_px"] == 18
    assert n["background_color"] == "#ffffff"
    assert (n["border_px"], n["border_color"]) == (2, "#00834f")
    assert n["has_close_control"] is True
    assert n["mandated"] is True and n["mandated_reason"]
    assert n["text"].startswith("Komunikat o decyzji Prezesa"), "the close label is not part of the text"
    assert n["viewport_height_share"] == pytest.approx(n["rect"]["h"] / VIEWPORT["height"], abs=0.005)
    assert n["viewport_share"] == pytest.approx(n["rect"]["h"] / VIEWPORT["height"], abs=0.005), \
        "full width, so area and height shares agree"
    assert facts["viewport"]["width"] == 1000 and facts["viewport"]["height"] == 700


async def test_a_fixed_overlay_that_sits_on_the_menu_is_an_overlay_that_covers_navigation():
    overlay = ('<div role="dialog" style="position:fixed;top:0;left:0;width:100%;height:300px;z-index:1000;'
               'background:#fff;border:1px solid #000"><p>Zaakceptuj regulamin serwisu, aby kontynuować '
               'korzystanie z naszej strony internetowej.</p><button>Zamknij</button></div>')
    facts = await _measure(NAV + CTA + overlay + FILLER)
    (n,) = facts["notices"]
    assert n["kind"] == "overlay" and n["is_overlay"] is True
    assert n["covers_navigation"] is True and "Konta" in n["covered"]
    assert n["viewport_share"] == pytest.approx(300 / 700, abs=0.01)
    assert n["mandated"] is False, "a house rule is not a regulator's notice"


async def test_a_sticky_bar_that_covers_nothing_is_not_an_overlay():
    bar = ('<div style="position:sticky;top:0;z-index:5;background:#eef;padding:10px">Nowy regulamin '
           'obowiązuje od 1 stycznia, sprawdź zmiany w zasadach korzystania. <button>OK</button></div>')
    facts = await _measure(bar + NAV + CTA + FILLER)
    (n,) = facts["notices"]
    assert n["kind"] == "fixed_bar" and n["is_overlay"] is False
    assert n["covers_navigation"] is False and n["covers_ctas"] is False


async def test_a_promotion_with_a_close_button_is_not_mandated():
    promo = ('<div style="background:#fe0;padding:10px">Promocja: otwórz konto i odbierz 200 zł. Oferta ważna '
             'do końca miesiąca, szczegóły w regulaminie promocji. <button>Zamknij</button></div>')
    facts = await _measure(promo + NAV + CTA + FILLER)
    (n,) = facts["notices"]
    assert n["mandated"] is False and n["mandated_reason"] == ""


async def test_a_page_without_a_notice_has_none():
    facts = await _measure(NAV + CTA + FILLER)
    assert facts["notices"] == []
    assert facts["nav_labels"] == ["Konta", "Kredyty", "Karty"]
    assert [c["text"] for c in facts["ctas"]] == ["OTWÓRZ KONTO"]


def _disc_share(cx, cy, r, rect):
    """Share of `rect` (x0, y0, x1, y1) inside the disc, by a fine grid."""
    x0, y0, x1, y1 = rect
    n = hit = 0
    for i in range(200):
        for j in range(100):
            x, y = x0 + (x1 - x0) * (i + .5) / 200, y0 + (y1 - y0) * (j + .5) / 100
            n += 1
            hit += (x - cx) ** 2 + (y - cy) ** 2 <= r * r
    return hit / n


async def test_a_floating_circle_over_a_button_edge_reports_the_measured_share():
    # The button spans x 200-400, y 500-550; a round 100 px bubble centred at (425, 525).
    bubble = ('<div id="chat" style="position:fixed;left:375px;top:475px;width:100px;height:100px;'
              'border-radius:50%;background:#075;z-index:99"></div>')
    facts = await _measure(NAV + CTA + bubble + FILLER)
    (w,) = facts["floating_widgets"]
    assert w["id"] == "chat" and w["shape"] == "round" and w["position"] == "fixed"
    (o,) = w["overlaps"]
    assert o["cta"] == "OTWÓRZ KONTO"
    assert o["overlap_share"] == pytest.approx(_disc_share(425, 525, 50, (200, 500, 400, 550)), abs=0.03)
    assert 0.02 < o["overlap_share"] < 0.2, "a clipped edge, not a covered button"
    assert o["label_overlapped"] is False


async def test_a_widget_over_the_label_says_so():
    bubble = ('<div style="position:fixed;left:250px;top:475px;width:100px;height:100px;border-radius:50%;'
              'background:#075;z-index:99"></div>')
    facts = await _measure(NAV + CTA + bubble + FILLER)
    (o,) = facts["floating_widgets"][0]["overlaps"]
    assert o["label_overlapped"] is True and o["label_overlap_share"] > 0.3
    assert o["overlap_share"] > 0.4


async def test_a_widget_that_overlaps_nothing_is_listed_without_overlaps():
    bubble = ('<div style="position:fixed;right:10px;bottom:10px;width:60px;height:60px;border-radius:50%;'
              'background:#075;z-index:99"></div>')
    facts = await _measure(NAV + CTA + bubble + FILLER)
    (w,) = facts["floating_widgets"]
    assert w["overlaps"] == []


async def test_a_bubble_inside_a_shadow_root_is_found_through_its_host():
    shadow = ('<div id="host"></div><script>const r = document.getElementById("host").attachShadow({mode:"open"});'
              'r.innerHTML = \'<div style="position:fixed;left:360px;top:475px;width:100px;height:100px;'
              'border-radius:50%;background:#075;z-index:99"></div>\';</script>')
    facts = await _measure(NAV + CTA + shadow + FILLER)
    (w,) = facts["floating_widgets"]
    (o,) = w["overlaps"]
    assert o["cta"] == "OTWÓRZ KONTO"
    assert o["overlap_share"] == pytest.approx(_disc_share(410, 525, 50, (200, 500, 400, 550)), abs=0.03)


async def test_a_site_header_that_sticks_is_not_a_notice():
    header = ('<div style="position:sticky;top:0;z-index:10;background:#fff"><nav>'
              + "".join(f'<a href="#{i}" style="margin-right:20px">Pozycja {i}</a>' for i in range(8))
              + '</nav><p>Bank zmieniającego się świata, sprawdź naszą ofertę dla ciebie.</p></div>')
    facts = await _measure(header + CTA + FILLER)
    assert facts["notices"] == []


# --- capture: never fails or holds up the session -----------------------------------

async def test_capture_stores_the_facts_beside_the_screenshot(tmp_path):
    store = TraceStore(tmp_path / "p")
    raw = {"viewport": {"width": 390, "height": 844, "dpr": 1}, "notices": [{
        "text": "Zamknij× Komunikat o decyzji Prezesa UOKiK", "text_full": "Zamknij× Komunikat o decyzji Prezesa UOKiK",
        "close_label": "Zamknij×", "kind": "in_flow"}], "floating_widgets": [], "ctas": [], "nav_labels": []}
    page = type("P", (), {"evaluate": AsyncMock(return_value=raw)})()
    facts = await capture_landing_facts(page, store)
    assert store.read_landing_facts() == facts
    assert json.loads((store.trace_dir / "landing_facts.json").read_text(encoding="utf-8"))["version"] == 1
    assert facts["notices"][0]["mandated"] is True and "text_full" not in facts["notices"][0]
    assert facts["notices"][0]["text"] == "Komunikat o decyzji Prezesa UOKiK"


async def test_capture_survives_a_page_that_throws(tmp_path):
    store = TraceStore(tmp_path / "p")
    page = type("P", (), {"evaluate": AsyncMock(side_effect=RuntimeError("Execution context was destroyed"))})()
    assert await capture_landing_facts(page, store) is None
    assert store.read_landing_facts() is None


async def test_capture_gives_up_on_a_page_that_hangs(tmp_path):
    import asyncio

    async def hang(_js):
        await asyncio.sleep(30)

    store = TraceStore(tmp_path / "p")
    page = type("P", (), {"evaluate": staticmethod(hang)})()
    assert await asyncio.wait_for(capture_landing_facts(page, store, timeout=0.2), 5) is None
    assert store.read_landing_facts() is None


def test_a_script_that_failed_halfway_is_not_a_measurement():
    assert finalize_facts({"viewport": {"width": 1}, "notices": [], "error": "TypeError"}) is None
    assert finalize_facts("nope") is None and finalize_facts(None) is None


def test_the_stored_facts_are_bounded():
    raw = {"viewport": {"width": 1, "height": 1, "dpr": 1}, "nav_labels": ["x" * 900] * 200,
           "notices": [{"text": "t" * 5000, "text_full": "t" * 5000}] * 20}
    facts = finalize_facts(raw)
    assert len(facts["notices"]) <= 6 and len(facts["nav_labels"]) <= 40
    assert len(facts["nav_labels"][0]) <= 300 and len(facts["notices"][0]["text"]) <= 240


def test_an_older_run_has_no_landing_facts(tmp_path):
    assert TraceStore(tmp_path / "old").read_landing_facts() is None


# --- the Polish block the first-impression pass reads ----------------------------------

def _facts(**notice):
    base = {"text": "Komunikat o decyzji Prezesa Urzędu Ochrony Konkurencji i Konsumentów Na stronie UOKiK…",
            "kind": "in_flow", "position": "relative", "viewport_share": 0.317, "viewport_height_share": 0.343,
            "font_size_px": 12, "color": "#373a3c", "background_color": "#ffffff", "border_px": 2,
            "border_color": "#00834f", "covers_content": False, "covers_navigation": False, "covers_ctas": False,
            "covered": [], "has_close_control": True, "close_label": "Zamknij", "mandated": True,
            "mandated_reason": "decyzja organu"}
    base.update(notice)
    return {"viewport": {"width": 390, "height": 844, "dpr": 1}, "notices": [base], "floating_widgets": [],
            "nav_labels": ["Klienci indywidualni"], "ctas": [{"text": "OTWÓRZ KONTO"}], "top_controls": ["zaloguj", "menu"]}


def test_the_summary_calls_an_in_flow_block_what_it_is_with_its_numbers():
    text = summarize_landing_facts(_facts())
    assert "blok w przepływie strony (NIE jest oknem, pop-upem ani nakładką" in text
    assert "32% powierzchni pierwszego ekranu (34% jego wysokości)" in text
    assert "tekst 12 px w kolorze #373a3c, tło #ffffff" in text and "ramka 2 px #00834f" in text
    assert "nie zasłania nawigacji ani przycisków" in text and "„Zamknij”" in text
    assert "OBOWIĄZKOWY" in text and "nie wolno zalecać" in text
    assert "Klienci indywidualni" in text and "OTWÓRZ KONTO" in text and "zaloguj" in text
    assert text.startswith("Pomiar w przeglądarce, ekran 390 px×844 px")


def test_the_summary_names_what_an_overlay_covers_and_drops_the_mandate_for_a_promotion():
    text = summarize_landing_facts(_facts(kind="overlay", covers_navigation=True, covered=["Konta", "Karty"],
                                          mandated=False, mandated_reason="", text="Zapisz się do newslettera"))
    assert "nakładka na treść strony" in text and "ZASŁANIA: „Konta”, „Karty”" in text
    assert "OBOWIĄZKOWY" not in text


def test_the_summary_quotes_the_overlap_percentage_and_calls_a_few_percent_minor():
    facts = _facts()
    facts["floating_widgets"] = [{"tag": "div", "id": "chat", "class": "", "label": "", "shape": "round",
                                  "rect": {"w": 88, "h": 88}, "viewport_share": 0.024,
                                  "overlaps": [{"cta": "OTWÓRZ KONTO", "overlap_share": 0.066,
                                                "label_overlapped": False, "label_overlap_share": 0}]}]
    text = summarize_landing_facts(facts)
    assert "nachodzi na przycisk „OTWÓRZ KONTO” w 7% jego powierzchni" in text
    assert "napis przycisku jest w całości widoczny" in text and "Kilka procent to drobne nałożenie" in text


def test_no_facts_means_no_geometry_claims():
    for missing in (None, {}, {"viewport": "x"}):
        assert summarize_landing_facts(missing) == NO_FACTS
    assert "nie opisuj geometrii, kolorów ani rozmiaru czcionki" in NO_FACTS


def test_a_page_with_no_notice_says_so():
    facts = _facts()
    facts["notices"] = []
    assert "Nie wykryto żadnego komunikatu" in summarize_landing_facts(facts)


# --- the run-level roll-up the aggregate guard reads ------------------------------------

def _store(tmp_path, name, notices=None, left_out=False):
    store = TraceStore(tmp_path / name)
    if notices is not None:
        store.write_landing_facts({"viewport": {"width": 1}, "notices": notices})
    if left_out:
        store.write_first_impression({"overall_score": 2.0, "mandated_notice_left_out": True})
    return store


def test_rollup_says_the_only_notice_is_mandated(tmp_path):
    uokik = {"text": "Komunikat o decyzji Prezesa UOKiK", "mandated": True, "kind": "in_flow"}
    roll = landing_notice_rollup([_store(tmp_path, "a", [uokik]), _store(tmp_path, "b", [uokik]),
                                  _store(tmp_path, "c", left_out=True)])
    assert roll["measured_sessions"] == 2 and roll["only_mandated"] is True and roll["mandated"] is True
    assert roll["notices"] == [{"title": "Komunikat o decyzji Prezesa UOKiK", "mandated": True,
                                "kind": "in_flow", "sessions": 2}]
    assert roll["left_out"] == 1


def test_rollup_is_not_only_mandated_beside_an_app_banner(tmp_path):
    uokik = {"text": "Komunikat o decyzji Prezesa UOKiK", "mandated": True}
    app = {"text": "Otwórz konto w aplikacji GOmobile", "mandated": False}
    roll = landing_notice_rollup([_store(tmp_path, "a", [app, uokik])])
    assert roll["mandated"] is True and roll["only_mandated"] is False


def test_rollup_is_empty_for_a_run_without_measurements(tmp_path):
    assert landing_notice_rollup([_store(tmp_path, "a"), _store(tmp_path, "b")]) == {}
