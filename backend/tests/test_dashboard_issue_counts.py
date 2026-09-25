"""The dashboard's headline counts are the report's, not a recount.

It counted every per-persona finding, the held-back ones included: "Analiza
wykazała 0 krytycznych, 4 poważnych i 4 drobnych" above a report listing 0/0/2
(2026-09-24 hyundai), and 8 of 9 archived runs disagreed. The comparison summed
severity_counts, which also holds needs_verification: 2 confirmed issues
against 2 read "8 vs 7 → worse".
"""
from simulated_web_agent.main.results_api import _report_issue_counts

REPORT = {
    "narrative": {"executive_summary": "S.", "overall_ux_score": 3,
                  "critical_issues": [], "serious_issues": [],
                  "minor_issues": [{"heuristic_id": 8, "heuristic_name": "Design", "examples": []},
                                   {"heuristic_id": 4, "heuristic_name": "Spójność", "examples": []}]},
    "statistics": {"heuristic": {"severity_counts": {"drobny": 2, "needs_verification": 6}}},
}


def test_the_report_counts_and_the_held_ones_apart():
    c = _report_issue_counts(REPORT)
    assert (c["critical"], c["serious"], c["minor"], c["total"]) == (0, 0, 2, 2)
    assert c["needs_verification"] == 6


def test_the_stored_report_is_not_rewritten():
    before = repr(REPORT)
    _report_issue_counts(REPORT)
    assert repr(REPORT) == before


def test_no_report_no_counts():
    assert _report_issue_counts(None) is None
    assert _report_issue_counts({}) is None
