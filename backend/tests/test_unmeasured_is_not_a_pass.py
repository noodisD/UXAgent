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


# --- a pass that failed: "nie oceniono", never a score of nothing ---------------

from simulated_web_agent.main.reporter_html import (  # noqa: E402
    _fi_header, _render_accessibility, _render_first_impression, score_coverage_text,
    stats_table_rows,
)

FAILED_ROW = {"persona": "Ala", "persona_dir": "p0", "passes": ["analiza dostępności"],
              "pass_ids": ["accessibility"]}


def _ledger_stats(scored, failed, section="accessibility", of=3, avg=None):
    stats = {**MEASURED, "participant_count": of,
             "audit_failures": [{**FAILED_ROW, "pass_ids": [section]}] * failed}
    stats[section] = {"avg_score": avg, "scored": scored, "of": of}
    return stats


def test_accessibility_nobody_scored_says_not_assessed_and_never_a_score():
    stats = _ledger_stats(scored=0, failed=3)
    html = _render_accessibility(stats, {})
    assert "nie oceniono" in html and "/5" not in html
    assert "analiza nie powiodła się dla 3 z 3 uczestników" in html
    assert "usterka narzędzia" in html


def test_accessibility_partial_prints_how_many_it_rests_on():
    stats = _ledger_stats(scored=2, failed=1, avg=3.5)
    html = _render_accessibility(stats, {})
    assert "3,50" in html and "Ocena z 2 z 3 uczestników" in html
    assert "analiza nie powiodła się dla 1 z 3" in html


def test_accessibility_scored_by_everyone_prints_no_qualifier():
    html = _render_accessibility(_ledger_stats(scored=3, failed=0, avg=4.0), {})
    assert "4,00" in html and "Ocena z" not in html and "Nie oceniono" not in html


def test_a_legacy_report_without_the_counts_renders_as_it_did():
    """NOTHING has an average of 5 and neither `scored` nor `of`."""
    assert "5,00" in _render_accessibility(NOTHING, {})
    assert score_coverage_text(NOTHING, "accessibility", "accessibility", "x") == ""


def test_no_unfailed_zero_is_a_failure_either():
    """No evidence (nothing scanned) leaves nobody scored with no failure on record:
    still not a score, and no tool fault is claimed."""
    stats = _ledger_stats(scored=0, failed=0)
    html = _render_accessibility(stats, {})
    assert "nie oceniono" in html and "usterka" not in html


def test_the_first_impression_header_and_section_follow_the_same_rule():
    none = _ledger_stats(0, 3, section="first_impression")
    assert "nie oceniono" in _fi_header(none) and "/5" not in _fi_header(none)
    section = _render_first_impression(none, {})
    assert "Nie oceniono — brak wyniku dla żadnego z 3 uczestników" in section
    assert "test nie powiódł się dla 3 z 3" in section
    part = _ledger_stats(2, 1, section="first_impression", avg=3.4)
    assert "3,40/5" in _fi_header(part) and "(2 z 3)" in _fi_header(part)
    assert "Ocena z 2 z 3 uczestników" in _render_first_impression(part, {})
    assert _fi_header(_ledger_stats(3, 0, section="first_impression", avg=3.4)) == "3,40/5"


def test_the_statistics_table_agrees_with_the_headlines():
    rows = dict(stats_table_rows(_ledger_stats(scored=0, failed=3)))
    assert rows["Dostępność (śr.)"] == "nie oceniono"
    rows = dict(stats_table_rows(_ledger_stats(scored=2, failed=1, avg=3.5)))
    assert rows["Dostępność (śr.)"] == "3,50/5 (2 z 3 uczestników)"
    assert dict(stats_table_rows(NOTHING))["Dostępność (śr.)"] == "5,00/5", "legacy"


def test_the_whole_report_never_prints_a_failed_pass_as_a_score():
    stats = _ledger_stats(scored=0, failed=3)
    stats["first_impression"] = {"avg_score": None, "scored": 0, "of": 3}
    html = generate_html_report({"website": "x.pl", "narrative": {}, "statistics": stats})
    assert "None/5" not in html and "—/5" not in html
    assert html.count("nie oceniono") >= 2
