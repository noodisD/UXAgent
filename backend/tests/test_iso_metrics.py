"""ISO 9241-11 signal separation (§1.1): effectiveness / efficiency /
satisfaction must be distinct, log-derived where possible, and satisfaction
must be labelled synthetic."""
import json

from simulated_web_agent.main.iso_metrics import compute_iso_metrics
from simulated_web_agent.main.trace_store import TraceStore


def _store(tmp_path, *, actions=None, session=None, reflection=None, survey=None):
    s = TraceStore(tmp_path / "p")
    if actions is not None:
        s.write_action_trace([json.dumps(a) for a in actions])
    if session is not None:
        s.write_session_result(session)
    if reflection is not None:
        s.write_objective_reflection(reflection)
    if survey is not None:
        s.write_survey_result(survey)
    return s


def test_effectiveness_from_outcome(tmp_path):
    s = _store(tmp_path, reflection={"outcome": "success"},
               session={"steps_taken": 10, "ended_reason": "terminated"})
    m = compute_iso_metrics(s)
    assert m["effectiveness"]["task_success"] == "success"
    assert m["effectiveness"]["goal_state_reached"] is True
    assert m["effectiveness"]["critical_error"] is False


def test_error_session_marks_critical_error(tmp_path):
    s = _store(tmp_path, reflection={"outcome": "failed"},
               session={"steps_taken": 27, "ended_reason": "error"})
    m = compute_iso_metrics(s)
    assert m["effectiveness"]["task_success"] == "failed"
    assert m["effectiveness"]["critical_error"] is True


def test_efficiency_counts_backtracks_and_loops(tmp_path):
    actions = [
        {"action": "type", "target": "loc", "description": "x"},
        {"action": "type", "target": "loc", "description": "x"},
        {"action": "type", "target": "loc", "description": "x"},  # 3x loop
        {"action": "back", "target": None, "description": "go back"},
    ]
    s = _store(tmp_path, actions=actions,
               session={"steps_taken": 4, "ended_reason": "max_steps"})
    m = compute_iso_metrics(s)
    assert m["efficiency"]["step_count"] == 4
    assert m["efficiency"]["backtracks"] == 1
    assert m["efficiency"]["loop_detected"] is True
    assert m["efficiency"]["reached_step_limit"] is True


def test_satisfaction_is_labelled_synthetic(tmp_path):
    survey = {"answers": [{"question_id": "q1", "value": "4"},
                          {"question_id": "q2", "value": "2"},
                          {"question_id": "q5", "value": "free text"}]}
    s = _store(tmp_path, survey=survey, reflection={"outcome": "partial"})
    m = compute_iso_metrics(s)
    assert m["satisfaction"]["source"] == "synthetic_self_report"
    assert m["satisfaction"]["synthetic_score_1_5"] == 3.0  # avg of 4 and 2
    assert m["satisfaction"]["requires_real_user_validation"] is True


# --- §4.2: effectiveness now OBSERVED where the target is decidable ---------

_DM = "https://www.dm.pl"
_SHOP = "dodaj do koszyka i przejdź do podsumowania zamówienia"


def _store_obs(tmp_path, steps, *, intent, outcome, session=None):
    s = TraceStore(tmp_path / "p")
    s.write_action_trace([json.dumps({"action": "click", "description": "c"})] * len(steps))
    for i, (url, obs) in enumerate(steps):
        s.append_observation({"tabs": [{"url": url, "is_active": True}]})
        s.write_observation_step_text(i, obs)
    s.write_basic_info({"intent": intent})
    s.write_objective_reflection({"outcome": outcome})
    s.write_session_result(session or {"steps_taken": len(steps), "ended_reason": "terminated"})
    return s


def test_observed_outcome_overrides_declared_and_flags_disagreement(tmp_path):
    # Trace reaches the order summary; the LLM declared "failed". Observed wins.
    s = _store_obs(tmp_path,
                   [(f"{_DM}/koszyk", "Podsumowanie. Produkty łącznie 20 zł. Przejdź do kasy.")],
                   intent=_SHOP, outcome="failed")
    eff = compute_iso_metrics(s)["effectiveness"]
    assert eff["task_success"] == "success"
    assert eff["goal_state_reached"] is True
    assert eff["outcome_source"] == "observed"
    assert eff["declared_outcome"] == "failed"
    assert eff["disagreement"] is True
    assert eff["target_rung"] == "order_summary_reached"


def test_declared_fallback_when_target_undecidable(tmp_path):
    # Non-shopping intent → checker abstains → declared kept, no disagreement.
    s = _store_obs(tmp_path, [(f"{_DM}/artykul", "artykuł o zdrowiu")],
                   intent="Przeczytaj artykuł", outcome="success")
    eff = compute_iso_metrics(s)["effectiveness"]
    assert eff["outcome_source"] == "declared"
    assert eff["task_success"] == "success"
    assert eff["observed_outcome"] == "unknown"
    assert eff["disagreement"] is False


def test_observed_agrees_with_declared_no_disagreement(tmp_path):
    # Item added to cart (partial floor) but no summary → observed partial.
    s = _store_obs(tmp_path, [
        (f"{_DM}/p/d/1/a", "produkt"),
        (f"{_DM}/koszyk", "Twój koszyk. Produkty łącznie 20,00 zł. Usuń."),
    ], intent=_SHOP, outcome="partial")
    eff = compute_iso_metrics(s)["effectiveness"]
    assert eff["outcome_source"] == "observed"
    assert eff["observed_outcome"] == "partial"
    assert eff["disagreement"] is False


# --- the participant's own route: pages opened, distinct, reopened -----------------------------

def _browsed(tmp_path, steps, landing="https://x.pl/", cut=False):
    """steps: [(action, url the page is at afterwards)].

    A session that ended on its own records one observation per action (the page after
    the last one is never captured) and says so with a final terminate; a cut one has
    the page after its last action, recorded and never read."""
    s = TraceStore(tmp_path / "route")
    actions = [a for a, _ in steps] + ([] if cut else [{"action": "terminate"}])
    s.write_action_trace([json.dumps(a) for a in actions])
    urls = [landing] + [u for _, u in steps]
    for i, url in enumerate(urls):
        s.append_observation({"tabs": [{"url": url, "is_active": True}], "error": None})
        if not cut or i < len(steps):
            s.write_observation_step_text(i, f"Strona {i}")
    return s


def _click(target="x"):
    return {"action": "click", "target": target, "description": f"Clicking {target}"}


# A results list and the same products, opened again and again: the shape of the
# 2026-07-09 pepco.pl session whose H6/H7 findings called it the site's inefficient comparison.
ALTERNATING = [
    ({"action": "type", "target": "q", "text": "poduszki", "enter": True}, "https://x.pl/search?q=poduszki"),
    (_click("a"), "https://x.pl/p/a"), ({"action": "back"}, "https://x.pl/search?q=poduszki"),
    (_click("a"), "https://x.pl/p/a?ref=list"), ({"action": "back"}, "https://x.pl/search?q=poduszki"),
    (_click("b"), "https://x.pl/p/b"), ({"action": "back"}, "https://x.pl/search?q=poduszki"),
    (_click("a"), "https://x.pl/p/a"),
]


def test_efficiency_counts_page_views_distinct_pages_and_revisits(tmp_path):
    eff = compute_iso_metrics(_browsed(tmp_path, ALTERNATING))["efficiency"]
    # the landing page and three openings of /p/a and one of /p/b; the query string is not a page
    assert eff["page_views"] == 5 and eff["distinct_pages"] == 3 and eff["revisits"] == 2
    assert eff["reopened_pages"] == [{"page": "x.pl/p/a", "times": 3}]


def test_a_route_without_repeats_has_none(tmp_path):
    steps = [(_click("a"), "https://x.pl/a"), (_click("b"), "https://x.pl/b"),
             ({"action": "goto_url", "url": "https://x.pl/c"}, "https://x.pl/c")]
    eff = compute_iso_metrics(_browsed(tmp_path, steps))["efficiency"]
    assert (eff["page_views"], eff["distinct_pages"], eff["revisits"]) == (4, 4, 0)
    assert eff["reopened_pages"] == []


def test_a_click_that_leaves_the_page_where_it_was_is_not_a_view(tmp_path):
    steps = [(_click("menu"), "https://x.pl/"), (_click("failed"), "https://x.pl/")]
    assert compute_iso_metrics(_browsed(tmp_path, steps))["efficiency"]["page_views"] == 1


def test_injected_steps_are_not_views(tmp_path):
    from simulated_web_agent.main.loop_phrases import KIND_LOOP_BREAKER, injected_action
    forced = injected_action(KIND_LOOP_BREAKER, _click("a"))
    steps = [(_click("a"), "https://x.pl/a"), (forced, "https://x.pl/"),
             # a click written by the harness: whatever it opened was not the participant's choice
             ({**_click("a"), "harness": KIND_LOOP_BREAKER}, "https://x.pl/a")]
    eff = compute_iso_metrics(_browsed(tmp_path, steps))["efficiency"]
    assert (eff["page_views"], eff["distinct_pages"], eff["revisits"]) == (2, 2, 0)


def test_a_page_captured_when_the_session_was_cut_is_not_a_view(tmp_path):
    steps = [(_click("a"), "https://x.pl/a"), (_click("b"), "https://x.pl/b")]
    # the page after the last click was recorded and never read
    assert compute_iso_metrics(_browsed(tmp_path, steps, cut=True))["efficiency"]["page_views"] == 2
