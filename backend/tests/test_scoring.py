"""The score must be arithmetic a reader can redo, not a number to take on trust.

It used to be the aggregate model's judgement — "<float 1.0-5.0 — derive it from
the findings below>" — with a prose rationale written afterwards, so there was
nothing to itemise. On the user's hyundai run the model said 1.5, anchored on a
"3/3 failed" that came from scoring a configurator against a checkout ladder.
Computed from the verified evidence the same run is 3.0, and every tenth of a
point of the difference is on a line of its own.
"""
import pytest

from simulated_web_agent.main.scoring import RULES, breakdown_text, compute_ux_score


def _stats(prioritization=(), a11y=(), share=None, gated=0, participants=3):
    return {
        "prioritization": list(prioritization),
        "heuristic": {"severity_counts": {"needs_verification": gated}},
        "accessibility": {
            "findings": list(a11y),
            "measured": {"images": {"total": 1287, "unnamed": 133, "share_unnamed": share}},
        },
        "outcomes": {"participant_count": participants},
    }


def _issue(h_id, sev, affected, n=3, conf="high", name="Heurystyka"):
    return {"heuristic_id": h_id, "heuristic_name": name, "severity": sev,
            "personas_affected": affected, "participant_count": n, "confidence": conf}


# --- the user's run, exactly ------------------------------------------------

def test_the_hyundai_run_scores_three_and_says_why():
    stats = _stats(
        prioritization=[_issue(1, "krytyczny", 1), _issue(9, "poważny", 1)],
        a11y=[{"category": "Brakujące etykiety kontrolek", "severity": "krytyczny",
               "topic": "control_name", "verified": True},
              {"category": "Brak tekstu alternatywnego", "severity": "poważny",
               "topic": "alt_text", "verified": True}],
        share=0.103, gated=5,
    )
    outcomes = {"a": "failed", "b": "success", "c": "partial"}
    res = compute_ux_score(stats, outcomes=outcomes,
                           outcome_sources={k: "declared" for k in outcomes},
                           pagespeed={"mobile": {"error": "timed out"}})
    amounts = [d["amount"] for d in res["deductions"]]
    assert amounts == [0.67, 0.33, 0.38, 0.5, 0.1]
    assert res["total_deduction"] == 1.98
    assert res["score"] == 3.0


# --- the arithmetic always adds up ----------------------------------------

@pytest.mark.parametrize("issues", [
    [],
    [_issue(1, "krytyczny", 3)],
    [_issue(i, "drobny", 1) for i in range(1, 11)],
    [_issue(1, "krytyczny", 3), _issue(2, "poważny", 2), _issue(3, "drobny", 1)],
])
def test_the_lines_sum_to_the_total_and_the_total_gives_the_score(issues):
    """The one property the whole feature rests on: the reader can redo it."""
    res = compute_ux_score(_stats(prioritization=issues), outcomes={"a": "failed"},
                           outcome_sources={"a": "observed"})
    assert round(sum(d["amount"] for d in res["deductions"]), 2) == res["total_deduction"]
    assert res["score"] == round(max(res["floor"], res["start"] - res["total_deduction"]), 1)


def test_every_line_says_what_sized_it():
    res = compute_ux_score(_stats(prioritization=[_issue(1, "krytyczny", 2)]))
    assert all(d.get("basis") and d.get("label") for d in res["deductions"])


# --- the rules --------------------------------------------------------------

def test_an_issue_everyone_hit_costs_more_than_one_somebody_hit():
    wide = compute_ux_score(_stats(prioritization=[_issue(1, "krytyczny", 3)]))
    narrow = compute_ux_score(_stats(prioritization=[_issue(1, "krytyczny", 1)]))
    assert wide["total_deduction"] > narrow["total_deduction"]
    assert wide["total_deduction"] == RULES["issue"]["krytyczny"]


def test_a_self_assessed_failure_costs_half_a_measured_one():
    measured = compute_ux_score(_stats(), outcomes={"a": "failed"}, outcome_sources={"a": "observed"})
    declared = compute_ux_score(_stats(), outcomes={"a": "failed"}, outcome_sources={"a": "declared"})
    assert declared["total_deduction"] == measured["total_deduction"] * RULES["declared_factor"]
    assert "ocena deklarowana" in declared["deductions"][0]["basis"]
    # the model's verdict on a run, not the participant's own rating
    assert "samoocena" not in declared["deductions"][0]["basis"]


def test_cosmetic_issues_cannot_sink_a_site_and_the_cap_is_shown():
    res = compute_ux_score(_stats(prioritization=[_issue(i, "drobny", 3) for i in range(1, 11)]))
    cap = [d for d in res["deductions"] if d["kind"] == "adjustment"]
    assert len(cap) == 1 and cap[0]["amount"] < 0, "the cap must be a visible line, not a silent clamp"
    assert res["total_deduction"] == RULES["minor_cap"]


def test_an_overstated_alt_text_finding_is_scored_as_minor():
    res = compute_ux_score(_stats(a11y=[{"category": "Brak tekstu alternatywnego",
                                         "severity": "poważny", "topic": "alt_text",
                                         "verified": True}], share=0.103))
    line = res["deductions"][0]
    assert line["amount"] == RULES["accessibility"]["drobny"]
    assert "133 z 1287" in line["basis"]


def test_unverified_accessibility_findings_cost_nothing():
    res = compute_ux_score(_stats(a11y=[{"category": "X", "severity": "krytyczny",
                                         "topic": "control_name", "verified": False}]))
    assert res["deductions"] == []


def test_performance_counts_only_when_it_was_measured():
    bad = compute_ux_score(_stats(), pagespeed={"mobile": {"performance_score": 20}})
    failed = compute_ux_score(_stats(), pagespeed={"mobile": {"error": "timed out"}})
    assert bad["total_deduction"] == 0.5
    assert failed["total_deduction"] == 0
    assert any("PageSpeed" in n["reason"] for n in failed["not_counted"]), \
        "a failed measurement must be named as left out, not silently skipped"


def test_the_score_never_goes_below_the_floor_and_says_so():
    res = compute_ux_score(_stats(prioritization=[_issue(i, "krytyczny", 3) for i in range(1, 8)]),
                           outcomes={"a": "failed"}, outcome_sources={"a": "observed"})
    assert res["score"] == 1.0 and res["floored"] is True
    assert "minimum" in breakdown_text(res)


def test_gated_findings_are_listed_as_not_counted():
    res = compute_ux_score(_stats(gated=5))
    assert any("5 wniosków" in n["label"] for n in res["not_counted"])


def test_self_reported_opinion_is_always_declared_as_left_out():
    res = compute_ux_score(_stats())
    assert any("ankiecie" in n["label"] for n in res["not_counted"])


# --- the headline can never disagree with the breakdown -------------------

def test_every_render_path_shows_the_computed_score():
    from simulated_web_agent.main.reporter import resolve_narrative

    agg = {"narrative": {"executive_summary": "…", "overall_ux_score": 1.5},
           "statistics": {"score_breakdown": {"score": 3.0}}}
    assert resolve_narrative(agg)["overall_ux_score"] == 3.0


def test_runs_made_before_scoring_keep_the_score_they_were_delivered_with():
    """No breakdown on disk means no retroactive change to a sent report."""
    from simulated_web_agent.main.reporter import resolve_narrative

    agg = {"narrative": {"executive_summary": "…", "overall_ux_score": 2.4}, "statistics": {}}
    assert resolve_narrative(agg)["overall_ux_score"] == 2.4


def test_the_html_section_prints_the_arithmetic():
    from simulated_web_agent.main.reporter_html import _render_score_breakdown

    res = compute_ux_score(_stats(prioritization=[_issue(1, "krytyczny", 1)]),
                           outcomes={"a": "failed", "b": "success", "c": "success"},
                           outcome_sources={"a": "declared", "b": "declared", "c": "declared"})
    html = _render_score_breakdown({"score_breakdown": res})
    assert "Jak obliczono ocenę" in html
    assert "Punkt wyjścia" in html and "Ocena końcowa" in html
    assert f"{res['score']:.1f}" in html
    assert "Nie wliczono do oceny" in html


# --- numbers that agree with each other ------------------------------------

def test_the_reach_floor_is_on_the_page():
    """"zasięg 1/3 (0.67)" read to every reviewer as an arithmetic error: the 0.5
    floor that turns a third into two thirds was not shown."""
    res = compute_ux_score(_stats(prioritization=[_issue(9, "poważny", 1)]))
    assert "0.5 + 0.5 × 0.33 = 0.67" in res["deductions"][0]["basis"]


def test_the_header_counts_the_cards_it_sits_above():
    from simulated_web_agent.main.reporter_html import _issue_counts, _render_header

    narr = {"critical_issues": [{}], "serious_issues": [{}, {}], "minor_issues": [{}],
            "overall_ux_score": 3.7}
    assert _issue_counts(narr) == {"critical": 1, "serious": 2, "minor": 1, "total": 4}
    html = _render_header({"website": "x.pl"}, {"heuristic": {"severity_counts":
                          {"krytyczny": 1, "poważny": 1, "drobny": 0}}}, narr)
    assert ">2<" in html.replace(" ", ""), "serious count must be the 2 cards, not 1 finding"


def test_one_finding_has_one_severity_in_every_section():
    from simulated_web_agent.main.reporter_html import _render_accessibility_detail

    html = _render_accessibility_detail({"accessibility": {
        "findings": [{"category": "Brak tekstu alternatywnego", "severity": "poważny",
                      "topic": "alt_text", "detail": "…"}],
        "measured": {"images": {"total": 1287, "unnamed": 133, "share_unnamed": 0.103}}}})
    assert ">drobny<" in html and ">poważny<" not in html
    assert "133 z 1287" in html
