"""Scroll depth must measure participants, and say how.

The first external reviewer asked "skąd AI wie jaka jest głębokość
przewijania strony". The report answered with a bare percentage, and on the
Hyundai configurator that percentage was wrong twice over:

  * every session read 69.9% — 2090 px of a 3890 px page, recorded at step 0,
    before any participant had acted. The landing position, not behaviour,
    which is why all three sessions agreed to a decimal place;
  * from step 1 the configurator scrolls inside its own panels, so the page is
    exactly as tall as the window and window.scrollY stays 0. Dividing by
    max(page − window, 1) turned "cannot be measured" into "0% — nobody scrolled".

Ordinary pages are unaffected: pepco and dm read the same before and after.
"""
import json

from simulated_web_agent.main.reporter_html import _render_scroll_depth
from simulated_web_agent.main.scroll_depth import run_scroll_analysis
from simulated_web_agent.main.trace_store import TraceStore


def _session(tmp_path, readings):
    (tmp_path / "screenshot").mkdir(parents=True, exist_ok=True)
    for step, (top, page, window) in enumerate(readings):
        (tmp_path / "screenshot" / f"scroll_top_{step}.txt").write_text(
            json.dumps({"scroll_top": top, "page_height": page, "viewport_height": window}))
    return TraceStore(tmp_path)


def test_the_pre_action_reading_is_not_counted(tmp_path):
    """The Hyundai case exactly: 2090 of 3890 at step 0, nothing after."""
    store = _session(tmp_path, [(2090, 3890, 900), (0, 900, 900), (0, 900, 900)])
    summary = run_scroll_analysis(store)
    assert summary["max_depth_pct"] is None, "69.9% was the landing position"


def test_a_page_that_cannot_scroll_is_not_measurable_rather_than_zero(tmp_path):
    store = _session(tmp_path, [(0, 2000, 900), (0, 900, 900), (0, 900, 900)])
    rows = store.read_scroll_depths()
    assert rows[1]["depth_pct"] is None
    summary = run_scroll_analysis(store)
    assert summary["steps_unmeasurable"] == 2
    assert summary["steps_measured"] == 0


def test_a_page_a_few_pixels_taller_than_the_window_is_not_measurable(tmp_path):
    """The configurator's panel routes: 912-922 px in a 900 px window. A 22 px
    nudge read as "100%", resting at the top as "0% — rzadko przewijali"."""
    store = _session(tmp_path, [(0, 3000, 900), (22, 922, 900), (0, 912, 900)])
    summary = run_scroll_analysis(store)
    assert summary["max_depth_pct"] is None
    assert summary["steps_unmeasurable"] == 2


def test_ordinary_scrolling_is_measured_as_before(tmp_path):
    store = _session(tmp_path, [(0, 3000, 900), (1050, 3000, 900), (2100, 3000, 900)])
    summary = run_scroll_analysis(store)
    assert summary["max_depth_pct"] == 100.0
    assert summary["steps_measured"] == 2 and summary["steps_unmeasurable"] == 0


def test_the_section_explains_the_method():
    html = _render_scroll_depth({"avg_max_depth_pct": 42.0, "min_max_depth_pct": 10,
                                 "max_max_depth_pct": 80, "sessions_with_depth_pct": 3,
                                 "steps_measured": 20, "steps_unmeasurable": 10})
    assert "Jak to mierzymy" in html
    assert "nie pochodzą z Google PageSpeed" in html, "it sat under the PageSpeed source line"
    assert "Zmierzono na 20 z 30 kroków" in html
    assert "Avg. maks." not in html


def test_a_site_nothing_could_be_measured_on_says_so():
    html = _render_scroll_depth({"avg_max_depth_pct": None, "steps_measured": 0,
                                 "steps_unmeasurable": 12})
    assert "Nie do zmierzenia" in html


def test_participant_behaviour_is_not_worded_as_a_site_verdict():
    html = _render_scroll_depth({"avg_max_depth_pct": 12.0, "min_max_depth_pct": 5,
                                 "max_max_depth_pct": 20, "sessions_with_depth_pct": 2,
                                 "steps_measured": 10, "steps_unmeasurable": 0})
    assert "below-the-fold mogą być pomijane" not in html
    assert "Uczestnicy badania" in html
