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


# ---------------------------------------------------------------------------
# Slice 3: cross-heuristic root-cause dedup (root_cause.py)
#
# Ground truth: the 20-persona dm.pl run filed "search doesn't refresh
# results for a new query" as 6 separate krytyczny findings across 6 DIFFERENT
# heuristic_ids — inflating severity_counts.krytyczny 6x for one bug.
# ---------------------------------------------------------------------------

_SEARCH_STALE_EVIDENCE = (
    "Po wpisaniu nowego zapytania w pole wyszukiwania system nie zaktualizował "
    "wyników. Strona nadal wyświetlała produkty dla poprzedniego zapytania."
)


def _report_with(persona_dir, hid, severity="krytyczny", evidence=_SEARCH_STALE_EVIDENCE):
    return {
        "trace_dir": persona_dir,
        "persona": f"Persona: {persona_dir}\nTło: test.",
        "first_impression": {"overall_score": 4.0, "scores": {}},
        "heuristic_findings": [{
            "heuristic_id": hid, "heuristic_name": f"Heuristic {hid}",
            "severity": severity, "url": "https://example.com", "step": 1,
            "evidence": evidence, "recommendation": "",
        }],
        "accessibility": {"overall_score": 4},
        "viewport_findings": [],
        "survey_result": {},
    }


def test_same_bug_across_heuristics_counts_once_in_severity_totals():
    reports = [_report_with(f"p{h}", h) for h in (1, 2, 3, 4, 5, 9)]
    stats = aggregate_statistics(reports)
    # One shared root cause, all krytyczny -> counted ONCE, not 6x.
    assert stats["heuristic"]["severity_counts"]["krytyczny"] == 1


def test_root_causes_summary_lists_the_cluster_with_full_reach():
    reports = [_report_with(f"p{h}", h) for h in (1, 2, 3, 4, 5, 9)]
    stats = aggregate_statistics(reports)
    root_causes = stats["heuristic"]["root_causes"]
    assert len(root_causes) == 1
    rc = root_causes[0]
    assert rc["root_cause_id"] == "search_stale_results"
    assert rc["heuristics_involved"] == [1, 2, 3, 4, 5, 9]
    assert rc["personas_affected"] == 6
    assert rc["worst_severity"] == "krytyczny"


def test_unrelated_findings_are_not_clustered_and_by_heuristic_is_untouched():
    reports = [
        _report_with("p1", 1, evidence=_SEARCH_STALE_EVIDENCE),
        _report_with("p2", 4, evidence="Duplikat przycisku menu w nagłówku."),
    ]
    stats = aggregate_statistics(reports)
    assert stats["heuristic"]["severity_counts"]["krytyczny"] == 2  # not clustered
    assert len(stats["heuristic"]["root_causes"]) == 1  # only the search bug
    ids_in_by_heuristic = {h["heuristic_id"] for h in stats["heuristic"]["by_heuristic"]}
    assert ids_in_by_heuristic == {1, 4}  # per-heuristic detail unchanged
