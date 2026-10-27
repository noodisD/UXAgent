"""The dashboard must agree with the report on two things it used to ignore.

1. Survey answers that are not assessable. A persona who never reached payment
   answers "nie dotyczy", or a placeholder 3 with "nie mogę ocenić" as the
   reason. The report keeps those out of every average and distribution and
   counts them (reporter._not_assessable); the dashboard averaged them in. On
   the archived runs 19 of 183 answers are placeholders — e.g. run
   2026-09-18_11-29-22_e5d6, where the payment and delivery questions (e6, e7)
   have no real answer from anyone, yet the survey tab showed "śr. 3,00".

2. The computed score. scoring.compute_ux_score is stored as
   statistics.score_breakdown, and every report renderer shows its score
   (reporter.resolve_narrative). The dashboard read narrative.overall_ux_score,
   which is forced to the computed score only when the aggregate LLM call
   succeeded, and printed 0.0 when it was missing.
"""
import json

import pytest

from simulated_web_agent.main import results_api
from simulated_web_agent.main.reporter import _build_per_agent_json, aggregate_statistics
from simulated_web_agent.main.reporter_html import _render_score_breakdown
from simulated_web_agent.main.scoring import compute_ux_score
from simulated_web_agent.main.trace_store import TraceStore

QUESTIONS = [
    {"id": "q1", "prompt": "Łatwo było znaleźć produkt", "options": ["1", "2", "3", "4", "5"]},
    {"id": "q2", "prompt": "Czułem kontrolę przy płatności", "options": ["1", "2", "3", "4", "5"]},
    {"id": "q3", "prompt": "Warunki dostawy były jasne", "options": ["1", "2", "3", "4", "5"]},
    {"id": "q4", "prompt": "Co było najtrudniejsze?"},
]

# Per persona: (question_id, value, reason). B and C never reached payment.
ANSWERS = {
    "pA": [("q1", "4", "Szybko."), ("q2", "4", "Płatność była jasna."),
           ("q3", "3", "Czas dostawy podany, koszt nie."), ("q4", "Filtry", "Za dużo opcji.")],
    "pB": [("q1", "5", "Od razu."), ("q2", "3", "Nie mogę ocenić — sesja nie dotarła do płatności."),
           ("q3", "3", "I cannot assess the delivery terms."), ("q4", "Menu", "Ukryte.")],
    "pC": [("q1", "2", "Długo szukałem."), ("q2", "N/A", "No payment step."),
           ("q3", "nie dotyczy", ""), ("q4", "N/A", "Not applicable to my session.")],
}


def _persona(run_dir, name, answers, steps=12):
    store = TraceStore(run_dir / name)
    store.write_basic_info({"persona": f"Persona: {name}", "intent": "kup buty"})
    store.write_persona_card({"name": name, "objective": "kup buty"})
    store.write_session_result({"steps_taken": steps, "ended_reason": "terminated"})
    store.write_survey_result({"answers": [
        {"question_id": q, "value": v, "reason": r} for q, v, r in answers]})
    store.write_iso_metrics({
        "effectiveness": {"outcome_source": "declared"},
        # what iso_metrics stores: every 1-5 answer averaged, placeholders in
        "satisfaction": {"synthetic_score_1_5": 3.0, "synthetic_seq_1_7": 5.0,
                         "frustration_signals": 0},
    })
    # The per-agent report exactly as the pipeline writes it — the report's
    # aggregation reads survey answers from here.
    store.write_ux_report(_build_per_agent_json(store))
    return store


def _run(tmp_path, run_id="2026-09-20_10-00-00_test", report=None, ux_reports=True,
         site="shop.example", started="2026-09-20T10:00:00"):
    run_dir = tmp_path / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run_manifest.json").write_text(json.dumps({
        "run_id": run_id, "site": site, "status": "done", "started_at": started,
        "questionnaire": {"questions": QUESTIONS},
    }), encoding="utf-8")
    stores = [_persona(run_dir, name, ans) for name, ans in ANSWERS.items()]
    if not ux_reports:
        for s in stores:
            (s.trace_dir / "ux_report.json").unlink()
    if report is not None:
        (run_dir / "aggregated_report.json").write_text(json.dumps(report), encoding="utf-8")
    return run_dir, stores


def _q(payload, qid):
    return next(q for q in payload["survey"]["questions"] if q["id"] == qid)


# --- 1. not-assessable survey answers ------------------------------------------

def test_persona_rows_flag_placeholders(tmp_path):
    run_dir, _ = _run(tmp_path)
    rows = {p["id"]: p["survey"] for p in results_api.build_run(run_dir.name, tmp_path)["personas"]}
    assert [r["notAssessable"] for r in rows["pA"]] == [False, False, False, False]
    assert [r["notAssessable"] for r in rows["pB"]] == [False, True, True, False]
    assert [r["notAssessable"] for r in rows["pC"]] == [False, True, True, True]
    # the value is kept (the card shows it in a tooltip), only flagged
    assert rows["pB"][1]["answer"] == "3"


def test_run_survey_excludes_placeholders_from_average_and_distribution(tmp_path):
    run_dir, _ = _run(tmp_path)
    payload = results_api.build_run(run_dir.name, tmp_path)
    survey = payload["survey"]
    assert survey["source"] == "traces"  # no report yet: recomputed

    q2 = _q(payload, "q2")  # only pA really answered
    assert q2["kind"] == "scale"
    assert (q2["avg"], q2["n"], q2["not_assessable"]) == (4.0, 1, 2)
    dist = {d["value"]: d["count"] for d in q2["distribution"]}
    assert dist[3] == 0, "pB's placeholder 3 must not be in the distribution"
    assert dist[4] == 1

    q3 = _q(payload, "q3")  # pA's 3 is a rating with a reason; pB/pC placeholders
    assert (q3["avg"], q3["n"], q3["not_assessable"]) == (3.0, 1, 2)

    q4 = _q(payload, "q4")  # open question: "N/A" is not an open answer
    assert q4["kind"] == "open" and "N/A" not in q4["answers"]

    assert survey["notAssessable"] == {"q2": 2, "q3": 2, "q4": 1}
    assert survey["notAssessableTotal"] == 5


def test_a_question_nobody_could_answer_is_marked_not_assessable(tmp_path):
    run_dir = tmp_path / "r"
    run_dir.mkdir()
    (run_dir / "run_manifest.json").write_text(json.dumps({
        "run_id": "r", "questionnaire": {"questions": QUESTIONS}}), encoding="utf-8")
    _persona(run_dir, "pB", ANSWERS["pB"])
    _persona(run_dir, "pC", ANSWERS["pC"])
    q2 = _q(results_api.build_run("r", tmp_path), "q2")
    assert q2["kind"] == "not_assessable" and q2["not_assessable"] == 2
    assert "avg" not in q2 and "distribution" not in q2


def test_run_survey_is_the_reports_own_aggregation(tmp_path):
    """Pinned to reporter.aggregate_statistics so the two cannot drift."""
    run_dir, stores = _run(tmp_path)
    payload = results_api.build_run(run_dir.name, tmp_path)
    reports = [s.read_ux_report() for s in stores]
    expected = aggregate_statistics(reports, q_meta=QUESTIONS, stores=stores)["survey"]
    assert payload["survey"]["questions"] == expected["questions"]
    assert payload["survey"]["notAssessable"] == expected["not_assessable"]


def test_a_current_report_is_used_as_stored(tmp_path):
    stored_q = [{"id": "q1", "prompt": "P", "kind": "scale", "min": 1, "max": 5,
                 "avg": 4.5, "n": 2, "not_assessable": 1,
                 "distribution": [{"value": v, "count": int(v in (4, 5))} for v in range(1, 6)]}]
    report = {"narrative": {}, "statistics": {"survey": {
        "questions": stored_q, "not_assessable": {"q1": 1},
        "surveys_included": 3, "excluded_surveys": []}}}
    run_dir, _ = _run(tmp_path, report=report)
    survey = results_api.build_run(run_dir.name, tmp_path)["survey"]
    assert survey["source"] == "report"
    assert survey["questions"] == stored_q
    assert survey["notAssessableTotal"] == 1


def test_a_report_built_before_the_rule_is_recomputed(tmp_path):
    """Every archived report predates the rule and averaged placeholders in."""
    stale_q = [{"id": "q2", "prompt": "P", "kind": "scale", "avg": 3.5, "n": 2,
                "distribution": [{"value": 3, "count": 1}, {"value": 4, "count": 1}]}]
    report = {"narrative": {}, "statistics": {"survey": {"questions": stale_q,
                                                         "avg_scores": {"q2": 3.5}}}}
    run_dir, _ = _run(tmp_path, report=report)
    payload = results_api.build_run(run_dir.name, tmp_path)
    assert payload["survey"]["source"] == "traces"
    assert _q(payload, "q2")["avg"] == 4.0
    # the stored report itself is passed through untouched
    assert payload["report"]["statistics"]["survey"]["questions"] == stale_q


def test_short_sessions_stay_out_as_in_the_report(tmp_path):
    run_dir, _ = _run(tmp_path)
    _persona(run_dir, "pD", [("q1", "1", "Nic nie zobaczyłem.")], steps=2)
    payload = results_api.build_run(run_dir.name, tmp_path)
    q1 = _q(payload, "q1")
    assert q1["n"] == 3 and q1["avg"] == pytest.approx(3.67)
    assert [e["persona_dir"] for e in payload["survey"]["excludedSurveys"]] == ["pD"]


def test_legacy_run_without_per_agent_reports_still_renders(tmp_path):
    """Nothing to aggregate: no invented figures, and the per-persona rows (which
    the survey tab then falls back to) still carry the flag."""
    run_dir, _ = _run(tmp_path, ux_reports=False)
    payload = results_api.build_run(run_dir.name, tmp_path)
    assert payload["survey"] is None
    pb = next(p for p in payload["personas"] if p["id"] == "pB")
    assert pb["survey"][1]["notAssessable"] is True


def test_persona_satisfaction_average_excludes_placeholders(tmp_path):
    run_dir, _ = _run(tmp_path)
    personas = {p["id"]: p for p in results_api.build_run(run_dir.name, tmp_path)["personas"]}
    # pB: q1=5 is its only rating; iso_metrics had averaged 5, 3, 3 → 3.67
    sat_b = personas["pB"]["iso"]["satisfaction"]
    assert sat_b["synthetic_score_1_5"] == 5.0 and sat_b["not_assessable"] == 2
    # pA answered everything, so nothing is taken out and the SEQ passes through. The
    # score is recomputed whether or not a placeholder was dropped: the stored 3.0 is
    # the fixture's own, and the ratings 4, 4, 3 average 3.67.
    assert personas["pA"]["iso"]["satisfaction"] == {
        "synthetic_score_1_5": 3.67, "synthetic_seq_1_7": 5.0, "frustration_signals": 0}


def test_placeholder_seq_is_not_a_seq():
    iso = {"satisfaction": {"synthetic_score_1_5": 4.0, "synthetic_seq_1_7": 4.0,
                            "synthetic_confidence_1_7": 4.0}}
    survey = {"answers": [
        {"question_id": "q1", "value": "4", "reason": "ok"},
        {"question_id": "synthetic_seq", "value": "4", "reason": "I cannot assess the task."},
        {"question_id": "synthetic_confidence", "value": "4", "reason": "sure"}]}
    sat = results_api._dashboard_iso(iso, survey)["satisfaction"]
    assert sat["synthetic_seq_1_7"] is None
    assert sat["synthetic_confidence_1_7"] == 4.0
    assert sat["synthetic_score_1_5"] == 4.0


@pytest.mark.parametrize("iso", [None, {}, {"effectiveness": {}}, {"satisfaction": "bad"}])
def test_dashboard_iso_leaves_odd_shapes_alone(iso):
    assert results_api._dashboard_iso(iso, {"answers": [
        {"question_id": "q", "value": "N/A"}]}) == iso


# --- 2. the computed score and its breakdown -----------------------------------

def _stats_for_score():
    return {
        "participant_count": 3,
        "prioritization": [
            {"heuristic_id": 1, "heuristic_name": "Widoczność statusu systemu",
             "severity": "krytyczny", "personas_affected": 1, "participant_count": 3,
             "confidence": "high"},
            {"heuristic_id": 9, "heuristic_name": "Pomoc w naprawianiu błędów",
             "severity": "poważny", "personas_affected": 3, "participant_count": 3,
             "confidence": "medium"},
        ],
        "heuristic": {"severity_counts": {"needs_verification": 2}},
        "accessibility": {"findings": [
            {"verified": True, "severity": "krytyczny", "category": "Brak etykiet"}]},
    }


def _breakdown():
    return compute_ux_score(_stats_for_score(),
                            outcomes={"pA": "success", "pB": "failed", "pC": "partial"},
                            outcome_sources={"pA": "observed", "pB": "observed", "pC": "declared"})


def test_score_comes_from_the_breakdown_when_the_narrative_failed(tmp_path):
    b = _breakdown()
    report = {"narrative": {"error": "LLM timeout"}, "statistics": {"score_breakdown": b}}
    run_dir, _ = _run(tmp_path, report=report)
    payload = results_api.build_run(run_dir.name, tmp_path)
    assert payload["score"] == b["score"]
    # as stored on disk (JSON turns the rules' tuples into lists)
    assert payload["scoreBreakdown"] == json.loads(json.dumps(b))
    assert results_api.build_history_row(run_dir)["score"] == b["score"]


def test_breakdown_wins_over_a_disagreeing_narrative():
    b = _breakdown()
    report = {"narrative": {"overall_ux_score": 4.9}, "statistics": {"score_breakdown": b}}
    assert results_api._report_score(report) == b["score"] != 4.9


def test_legacy_run_keeps_the_score_it_was_delivered_with(tmp_path):
    run_dir, _ = _run(tmp_path, report={"narrative": {"overall_ux_score": 3.2}, "statistics": {}})
    payload = results_api.build_run(run_dir.name, tmp_path)
    assert payload["score"] == 3.2
    assert payload["scoreBreakdown"] is None  # nothing to itemise, nothing invented
    assert results_api.build_history_row(run_dir)["score"] == 3.2


@pytest.mark.parametrize("report", [
    None,                                              # no aggregated report at all
    {"narrative": {"error": "boom"}, "statistics": {}},  # failed LLM, pre-scoring run
    {"narrative": {"overall_ux_score": "n/a"}},
    {"narrative": {"overall_ux_score": 0}},            # what "no score" used to look like
    {"narrative": {"overall_ux_score": "NaN"}},        # would make the payload invalid JSON
    {"narrative": {"overall_ux_score": True}},
])
def test_no_score_is_none_not_zero(tmp_path, report):
    run_dir, _ = _run(tmp_path, report=report)
    payload = results_api.build_run(run_dir.name, tmp_path)
    assert payload["score"] is None
    assert payload["scoreBreakdown"] is None
    assert results_api.build_history_row(run_dir)["score"] is None
    json.dumps(payload, allow_nan=False)  # the browser's JSON.parse takes no NaN


def test_an_incomplete_breakdown_still_sets_the_score_but_is_not_itemised(tmp_path):
    """resolve_narrative takes score_breakdown.score whenever it is there; the
    table needs start, total and raw too, so without them nothing is itemised."""
    report = {"narrative": {"overall_ux_score": 4.5},
              "statistics": {"score_breakdown": {"score": 3.0}}}
    run_dir, _ = _run(tmp_path, report=report)
    payload = results_api.build_run(run_dir.name, tmp_path)
    assert payload["score"] == 3.0
    assert payload["scoreBreakdown"] is None


def test_dashboard_score_is_the_score_every_report_renderer_prints():
    """Pinned to reporter.resolve_narrative (HTML, PDF and Word all go through
    it) for every case where that function prints a real UX score."""
    import copy

    from simulated_web_agent.main.reporter import resolve_narrative

    b = _breakdown()
    cases = [
        {"narrative": {"executive_summary": "x", "overall_ux_score": 4.9},
         "statistics": {"score_breakdown": b}},
        {"narrative": {"error": "LLM timeout"}, "statistics": {"score_breakdown": b}},
        {"narrative": {}, "statistics": {"score_breakdown": b}},
        {"narrative": {"executive_summary": "x", "overall_ux_score": 3.2}, "statistics": {}},
    ]
    for report in cases:
        expected = resolve_narrative(copy.deepcopy(report))["overall_ux_score"]
        assert results_api._report_score(report) == round(expected, 1)


def test_breakdown_lines_are_the_ones_the_report_prints_in_its_order(tmp_path):
    b = _breakdown()
    run_dir, _ = _run(tmp_path, report={"narrative": {}, "statistics": {"score_breakdown": b}})
    sb = results_api.build_run(run_dir.name, tmp_path)["scoreBreakdown"]
    html = _render_score_breakdown({"score_breakdown": b})
    positions = [html.index(d["label"]) for d in sb["deductions"]]
    assert positions == sorted(positions), "deduction order must match the report"
    for d in sb["deductions"]:
        assert d["basis"] in html
    assert [n["label"] for n in sb["not_counted"]] == [n["label"] for n in b["not_counted"]]
    assert (sb["start"], sb["floor"], sb["total_deduction"], sb["raw"]) == \
        (b["start"], b["floor"], b["total_deduction"], b["raw"])


def test_comparison_uses_the_same_score(tmp_path):
    b = _breakdown()
    _run(tmp_path, run_id="2026-09-19_10-00-00_prev", started="2026-09-19T10:00:00",
         report={"narrative": {"error": "boom"}, "statistics": {"score_breakdown": b}})
    now_dir, _ = _run(tmp_path, report={"narrative": {"overall_ux_score": 2.0}, "statistics": {}})
    cmp = results_api.build_run(now_dir.name, tmp_path)["comparison"]
    metric = next(m for m in cmp["metrics"] if m["label"] == "Overall score")
    assert (metric["now"], metric["prev"]) == (2.0, b["score"])


# --- 3. surveys the tool failed to produce --------------------------------------

def _failed_survey_persona(run_dir, name):
    """A persona whose survey call was rate-limited: run_survey stored {"error": ...}."""
    store = _persona(run_dir, name, ANSWERS["pA"])
    store.write_survey_result({"error": "429 RESOURCE_EXHAUSTED: quota exceeded"})
    store.write_ux_report(_build_per_agent_json(store))
    return store


def test_the_dashboard_lists_a_failed_survey_and_does_not_count_it(tmp_path):
    run_dir, _ = _run(tmp_path)
    _failed_survey_persona(run_dir, "pE")
    survey = results_api.build_run(run_dir.name, tmp_path)["survey"]
    assert survey["source"] == "traces"
    assert [f["persona_dir"] for f in survey["failedSurveys"]] == ["pE"]
    # Who and why only: the raw error text is not for the client surface.
    assert "error" not in survey["failedSurveys"][0]
    assert "429" not in str(survey["failedSurveys"])
    assert survey["surveysIncluded"] == 3  # pA, pB, pC; pE's survey holds no answer
    assert survey["excludedSurveys"] == []


def test_a_report_stored_before_the_key_existed_has_no_failed_surveys(tmp_path):
    report = {"narrative": {}, "statistics": {"survey": {
        "questions": [], "not_assessable": {}, "surveys_included": 3, "excluded_surveys": []}}}
    run_dir, _ = _run(tmp_path, report=report)
    survey = results_api.build_run(run_dir.name, tmp_path)["survey"]
    assert survey["source"] == "report"
    assert survey["failedSurveys"] == []


def test_a_stored_report_passes_its_failed_surveys_through(tmp_path):
    failed = {"persona_dir": "pE", "persona": "Ewa", "reason": "ankieta nie powiodła się",
              "error": "429"}
    report = {"narrative": {}, "statistics": {"survey": {
        "questions": [], "not_assessable": {}, "surveys_included": 2, "excluded_surveys": [],
        "failed_surveys": [failed, "junk"]}}}
    run_dir, _ = _run(tmp_path, report=report)
    # Who and why only: the stored error text stays in the report, not the payload.
    assert results_api.build_run(run_dir.name, tmp_path)["survey"]["failedSurveys"] == [
        {"persona_dir": "pE", "persona": "Ewa", "reason": "ankieta nie powiodła się"}]


def test_a_run_without_failures_has_an_empty_list(tmp_path):
    run_dir, _ = _run(tmp_path)
    assert results_api.build_run(run_dir.name, tmp_path)["survey"]["failedSurveys"] == []
