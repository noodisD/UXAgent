"""
Tests for aggregate_statistics() in reporter.py.

Behavior under test:
  1. First-impression average score is computed correctly across agents
  2. Severity counts are summed correctly across agents
  3. Heuristic violations are grouped by heuristic_id and sorted by frequency
"""
from tests.conftest import make_agent_report, make_finding
from src.simulated_web_agent.main.reporter import aggregate_statistics


# ---------------------------------------------------------------------------
# Slice 1: first-impression average
# ---------------------------------------------------------------------------

def test_avg_first_impression_score_is_mean_of_agent_scores():
    reports = [
        make_agent_report(fi_score=3.0),
        make_agent_report(fi_score=5.0),
    ]
    stats = aggregate_statistics(reports)
    assert stats["first_impression"]["avg_score"] == 4.0


# ---------------------------------------------------------------------------
# Slice 2: severity counts summed across agents
# ---------------------------------------------------------------------------

def test_severity_counts_are_summed_across_agents():
    reports = [
        make_agent_report(findings=[
            make_finding(severity="krytyczny"),
            make_finding(severity="poważny"),
        ]),
        make_agent_report(findings=[
            make_finding(severity="krytyczny"),
            make_finding(severity="drobny"),
            make_finding(severity="drobny"),
        ]),
    ]
    stats = aggregate_statistics(reports)
    counts = stats["heuristic"]["severity_counts"]
    assert counts["krytyczny"] == 2
    assert counts["poważny"] == 1
    assert counts["drobny"] == 2


# ---------------------------------------------------------------------------
# Slice 2b: heuristic violations grouped and sorted by frequency
# ---------------------------------------------------------------------------

def test_heuristic_violations_grouped_by_id_and_sorted_by_frequency():
    # H1 appears 3 times, H3 appears 1 time → H1 should be first
    reports = [
        make_agent_report(findings=[
            make_finding(heuristic_id=1),
            make_finding(heuristic_id=3),
        ]),
        make_agent_report(findings=[
            make_finding(heuristic_id=1),
        ]),
        make_agent_report(findings=[
            make_finding(heuristic_id=1),
        ]),
    ]
    stats = aggregate_statistics(reports)
    by_heuristic = stats["heuristic"]["by_heuristic"]

    ids_in_order = [h["heuristic_id"] for h in by_heuristic]
    assert ids_in_order[0] == 1, "H1 (3 occurrences) should rank first"
    assert ids_in_order[-1] == 3, "H3 (1 occurrence) should rank last"

    h1 = next(h for h in by_heuristic if h["heuristic_id"] == 1)
    assert h1["frequency"] == 3
