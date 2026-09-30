"""The run comparison sees the accessibility barriers the report shows.

It read only the narrative's critical and serious lists. In the 2026-09-27 run
the test-drive combobox sat in the accessibility section alone, so the dashboard
listed it as "resolved" against the run before while the report still carried
it. (Measured on the two real runs: resolved -> persisting, 11 shared stems.)"""
from simulated_web_agent.main.results_api import _comparable_issues, _match_issues

DETAIL = ("Na stronie rezerwacji jazdy próbnej pole wyboru (combobox) nie ma "
          "dostępnej nazwy, więc czytnik ekranu nie zapowie, czego dotyczy pole")
RECO = "Dodać dostępną nazwę pola wyboru przez element label z atrybutem for lub aria-label"


def _report(narrative_issues=(), findings=()):
    return {
        "narrative": {"executive_summary": "S.", "overall_ux_score": 3,
                      "critical_issues": list(narrative_issues), "serious_issues": [],
                      "minor_issues": []},
        "statistics": {"heuristic": {}, "accessibility": {
            "findings": list(findings), "measured": {"images": {}}}},
    }


def _wcag_issue():
    return {"heuristic_id": None, "heuristic_name": "Dostępność (WCAG)",
            "impact": DETAIL, "recommendation": RECO, "verified": True}


def _finding(**kw):
    return {"category": "Brak nazwy pola wyboru", "severity": "krytyczny", "verified": True,
            "topic": "control_name", "detail": DETAIL, "recommendation": RECO, **kw}


def test_a_barrier_only_in_the_accessibility_section_persists_not_resolved():
    # The earlier run had both: a WCAG issue with no verified finding behind it is
    # demoted as unproven, and would not have been compared at all.
    prev = _report(narrative_issues=[_wcag_issue()], findings=[_finding()])
    now = _report(findings=[_finding()])
    fresh, persisting, gone = _match_issues(_comparable_issues(now), _comparable_issues(prev))
    assert len(persisting) == 1 and not fresh and not gone


def test_a_barrier_the_narrative_already_carries_is_not_counted_twice():
    now = _report(narrative_issues=[_wcag_issue()], findings=[_finding()])
    kinds = [i["kind"] for i in _comparable_issues(now)]
    assert kinds == ["wcag:control_name"]


def test_only_what_the_report_shows_as_critical_or_serious_counts():
    now = _report(findings=[
        _finding(verified=False),                      # unproven: not shown as a barrier
        _finding(severity="drobny", topic="contrast"),  # minor
        _finding(topic="alt_text", severity="poważny"),  # demoted by the measured share
    ])
    now["statistics"]["accessibility"]["measured"]["images"] = {
        "total": 500, "unnamed": 90, "share_unnamed": 0.18, "share_unnamed_content": 0.004}
    assert _comparable_issues(now) == []


def test_a_barrier_fixed_since_the_last_run_is_still_resolved():
    prev = _report(findings=[_finding()])
    now = _report()
    fresh, persisting, gone = _match_issues(_comparable_issues(now), _comparable_issues(prev))
    assert len(gone) == 1 and not persisting and not fresh
