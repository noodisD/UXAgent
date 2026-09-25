"""What was not measured is shown as not measured, never as a pass.

The tmobile run's two sessions never loaded the site, and its report still
showed "Mobile: Brak overflowu" and "Tablet: Brak overflowu" in green, ten
heuristics "nie stwierdzono naruszeń", and under them a narrative saying the
devices were not covered. Two runs printed "None/5" for the first impression.
"""
from simulated_web_agent.main.reporter_html import (
    _render_heuristic_legend, _render_responsiveness, generate_html_report,
)

NOTHING = {"participant_count": 2, "heuristic": {}, "first_impression": {"avg_score": None},
           "accessibility": {"avg_score": 5}, "viewport": {"mobile_overflow_sessions": 0,
                                                            "tablet_overflow_sessions": 0, "devices": []}}
MEASURED = {**NOTHING, "first_impression": {"avg_score": 3.4},
            "viewport": {"mobile_overflow_sessions": 0, "tablet_overflow_sessions": 0,
                         "devices": [{"viewport": "mobile", "pages_scanned": 4},
                                     {"viewport": "tablet", "pages_scanned": 4}]}}


def test_no_scanned_page_is_not_measured():
    html = _render_responsiveness(NOTHING, {})
    assert html.count("Nie zmierzono") == 2 and "Brak overflowu" not in html


def test_scanned_pages_without_overflow_still_pass():
    assert _render_responsiveness(MEASURED, {}).count("Brak overflowu") == 2


def test_a_legacy_report_without_the_device_list_reads_as_before():
    legacy = {**NOTHING, "viewport": {"mobile_overflow_sessions": 0, "tablet_overflow_sessions": 0}}
    assert _render_responsiveness(legacy, {}).count("Brak overflowu") == 2


def test_heuristics_are_not_certified_when_nothing_was_observed():
    assert "nie oceniono" in _render_heuristic_legend(NOTHING)
    assert "nie stwierdzono naruszeń" in _render_heuristic_legend(MEASURED)


def test_no_none_in_the_header():
    html = generate_html_report({"website": "x.pl", "narrative": {}, "statistics": NOTHING})
    assert "None/5" not in html
