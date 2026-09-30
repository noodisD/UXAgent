"""An issue no confirmed finding supports is named, not counted.

aggregate_synthesize tells the model such issues "will be discarded as
unverifiable", and the methodology note tells the client unconfirmed
conclusions were excluded. They were kept and counted: in the 2026-09-17
hyundai run the header said 1 critical / 1 serious over confirmed 0/0/2, and
the one critical was an H1 issue whose findings were all held for a human.
"""
from simulated_web_agent.main.reporter import resolve_narrative
from simulated_web_agent.main.reporter_html import _issue_counts, generate_html_report


def _agg(issues, by_heuristic=(), a11y=(), deductions=()):
    return {"website": "x.pl", "narrative": {"executive_summary": "S.", "overall_ux_score": 3,
                                             "critical_issues": issues, "serious_issues": [],
                                             "minor_issues": [], "priority_recommendations": []},
            "statistics": {"participant_count": 2,
                           "heuristic": {"by_heuristic": list(by_heuristic)},
                           "accessibility": {"findings": list(a11y)},
                           "score_breakdown": {"deductions": list(deductions)}}}


H1 = {"heuristic_id": 1, "heuristic_name": "Widoczność statusu systemu", "impact": "Klik bez efektu."}


def test_a_nielsen_issue_without_a_confirmed_finding_is_held_out():
    n = resolve_narrative(_agg([dict(H1)]))
    assert n["critical_issues"] == [] and _issue_counts(n)["total"] == 0
    assert [i["heuristic_id"] for i in n["unconfirmed_issues"]] == [1]


def test_a_confirmed_one_stays():
    confirmed = {"heuristic_id": 1, "verified": True, "examples": [{"step": 3}]}
    n = resolve_narrative(_agg([dict(H1)], by_heuristic=[confirmed]))
    assert len(n["critical_issues"]) == 1 and n["critical_issues"][0]["verified"] is True


def test_the_report_names_what_it_left_out():
    html = generate_html_report(_agg([dict(H1)]))
    assert "Pominięto też 1 problem z podsumowania, dla którego" in html and "H1: Widoczność statusu systemu" in html


def test_an_accessibility_issue_with_an_invented_id_finds_its_evidence():
    """The model wrote "A11Y-1"; only null was recognised as WCAG."""
    scan = {"topic": "control_name", "verified": True, "examples": [{"step": 15}]}
    issue = {"heuristic_id": "A11Y-1", "heuristic_name": "Dostępność: bariera w formularzu",
             "impact": "Kontrolki bez etykiet."}
    n = resolve_narrative(_agg([issue], a11y=[scan]))
    kept = n["critical_issues"][0]
    assert kept["verified"] is True and kept["examples"] == [{"step": 15}]


def test_a_pagespeed_issue_is_proven_by_its_measurement():
    perf = {"heuristic_id": None, "heuristic_name": "Wydajność techniczna",
            "impact": "Wynik PageSpeed 34/100."}
    line = {"kind": "performance", "basis": "PageSpeed mobile 34/100 (poniżej 50)"}
    n = resolve_narrative(_agg([perf], deductions=[line]))
    kept = n["critical_issues"][0]
    assert kept["verified"] is True and kept["measured"]["basis"].startswith("PageSpeed mobile 34")
