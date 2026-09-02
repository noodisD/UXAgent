"""Regressions for the defects found in the consistency review.

Each test here corresponds to something that was silently wrong and that the
existing suite could not see. Grouped by the module that owned the defect.
"""

import json

import pytest

from simulated_web_agent.main import cancellation
from simulated_web_agent.main.results_api import _trend
from simulated_web_agent.main.reporter import _top_issues_text
from simulated_web_agent.main.session_goal_state import (
    compute_goal_state,
    infer_target_rung,
)
from simulated_web_agent.main.severity import (
    SEVERITY_EN,
    heuristic_id,
    severity_rank,
)
from simulated_web_agent.main.trace_store import TraceStore, parse_action


# ---------------------------------------------------------------------------
# severity.heuristic_id — crashed aggregate_statistics, which was then finalised
# as a successful run with score 0.0
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (3, 3), ("3", 3),           # the LLM emits both
    (None, 0), ("", 0), ("x", 0),  # explicit null / junk
    (0, 0), (11, 0), (-1, 0),   # out of the 1..10 range
])
def test_heuristic_id_always_int(raw, expected):
    assert heuristic_id({"heuristic_id": raw}) == expected


def test_heuristic_id_makes_mixed_keys_sortable():
    """`.get("heuristic_id", 0)` let None and "3" through as dict keys, and
    sorted() over the mixed set raised TypeError."""
    findings = [{"heuristic_id": v} for v in (3, "3", None, "x", 7)]
    keys = {heuristic_id(f) for f in findings}
    assert sorted(keys) == [0, 3, 7]


# ---------------------------------------------------------------------------
# severity.severity_rank — three incompatible scales collapsed into one
# ---------------------------------------------------------------------------

def test_severity_rank_is_worst_highest():
    assert severity_rank("krytyczny") > severity_rank("poważny") > severity_rank("drobny")


def test_severity_rank_accepts_finding_or_label():
    assert severity_rank({"severity": "krytyczny"}) == severity_rank("krytyczny")


def test_worst_of_group_is_max_by_rank():
    group = [{"severity": "drobny"}, {"severity": "krytyczny"}, {"severity": "poważny"}]
    assert max(group, key=severity_rank)["severity"] == "krytyczny"


def test_severity_en_covers_every_label():
    assert set(SEVERITY_EN) == {"krytyczny", "poważny", "drobny"}


# ---------------------------------------------------------------------------
# trace_store.parse_action — six copies, two different failure shapes
# ---------------------------------------------------------------------------

def test_parse_action_keeps_raw_text_on_bad_json():
    """Five of the six copies did this; the sixth returned {}, so the same
    malformed trace produced a description at five call sites and nothing at
    the last."""
    assert parse_action("not json") == {"description": "not json"}


def test_parse_action_unwraps_single_element_list():
    assert parse_action(json.dumps([{"action": "click"}]))["action"] == "click"


@pytest.mark.parametrize("entry", [None, 42, [], {}])
def test_parse_action_always_returns_dict(entry):
    assert isinstance(parse_action(entry), dict)


# ---------------------------------------------------------------------------
# session_goal_state — a checkout URL used to grant the top rung with an empty
# cart, and "cen" matched centrum/centralny
# ---------------------------------------------------------------------------

def _write_session(store: TraceStore, urls: list[str], obs_text: str = ""):
    for i, u in enumerate(urls):
        store.append_observation({"tabs": [{"url": u, "is_active": True}]})
        store.write_observation_step_text(i, obs_text)
    store.write_action_trace([])


def test_checkout_url_alone_does_not_reach_order_summary(tmp_path):
    """_CART_URL_MARKERS and _CHECKOUT_URL_MARKERS both contain "/kasa", so one
    URL granted cart_reached AND order_summary_reached while cart_has_items —
    the rung between them — stayed false, scoring success with nothing added."""
    store = TraceStore(tmp_path)
    _write_session(store, ["https://shop.example.pl/kasa"])
    state = compute_goal_state(store, "https://shop.example.pl/")
    ladder = state["ladder"]
    assert ladder["cart_reached"]["reached"] is True
    assert ladder["cart_has_items"]["reached"] is False
    assert ladder["order_summary_reached"]["reached"] is False, (
        "reaching a checkout URL with an empty cart must not grant the top rung"
    )


@pytest.mark.parametrize("intent", [
    "przejdź do centrum pomocy",
    "otwórz centralny panel klienta",
])
def test_generic_intent_does_not_infer_a_shopping_rung(intent):
    """"cen" was short enough to prefix centrum/centralny, so a help-centre task
    was classified as product browsing and scored against a ladder that does not
    apply to it. Abstaining lets the caller keep the declared outcome."""
    assert infer_target_rung(intent) is None


@pytest.mark.parametrize("intent", [
    "sprawdź cenę produktu",
    "porównaj ceny dwóch modeli",
    "zobacz cennik usług",
])
def test_real_price_intents_still_infer_product_viewed(intent):
    assert infer_target_rung(intent) == "product_viewed"


# ---------------------------------------------------------------------------
# reporter — "≥50%" was 40% at n=5 (banker's rounding)
# ---------------------------------------------------------------------------

def _stats(n: int, personas_affected: int) -> dict:
    return {
        "participant_count": n,
        "heuristic": {"by_heuristic": [{
            "heuristic_id": 1, "heuristic_name": "H1",
            "personas_affected": personas_affected, "frequency": personas_affected,
            "sessions_affected": f"{personas_affected} z {n}",
            "top_evidence": "e", "top_recommendation": "r",
        }]},
    }


def test_half_threshold_is_a_ceiling_not_bankers_rounding():
    """round(5 * 0.5) is 2, so the cutoff was 2 of 5 = 40% under a literal that
    promises 50%."""
    assert "H1" not in _top_issues_text(_stats(5, 2))   # 40% must not qualify
    assert "H1" in _top_issues_text(_stats(5, 3))       # 60% must


# ---------------------------------------------------------------------------
# results_api._trend — five values from two vocabularies, and raw counts
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("now,prev,higher_better,expected", [
    (5, 3, True, "better"),
    (3, 5, True, "worse"),
    (4, 4, True, "flat"),
    (2, 5, False, "better"),   # fewer critical findings is an improvement
    (5, 2, False, "worse"),
    (None, 3, True, "flat"),
])
def test_trend_uses_one_vocabulary(now, prev, higher_better, expected):
    assert _trend(now, prev, higher_is_better=higher_better) == expected


def test_trend_never_emits_up_or_down():
    """The old implementation returned up/down for higher-is-better metrics and
    better/worse for the rest, so one field carried two dialects."""
    values = {
        _trend(a, b, higher_is_better=h)
        for a, b in [(1, 2), (2, 1), (1, 1)] for h in (True, False)
    }
    assert values <= {"better", "worse", "flat"}


# ---------------------------------------------------------------------------
# cancellation — backs the cancel button; had no test at all
# ---------------------------------------------------------------------------

def test_cancellation_flag_roundtrip():
    run_id = "test-run-cancel-1"
    cancellation.clear(run_id)
    assert cancellation.is_cancelled(run_id) is False
    cancellation.cancel(run_id)
    assert cancellation.is_cancelled(run_id) is True
    cancellation.clear(run_id)
    assert cancellation.is_cancelled(run_id) is False


def test_cancellation_is_per_run():
    cancellation.cancel("run-a")
    try:
        assert cancellation.is_cancelled("run-b") is False
    finally:
        cancellation.clear("run-a")


@pytest.mark.parametrize("run_id", [None, ""])
def test_cancellation_falsy_run_id_is_never_cancelled(run_id):
    """run_id is None for a CLI run; treating that as cancelled would stop it."""
    assert cancellation.is_cancelled(run_id) is False


# ---------------------------------------------------------------------------
# results_api.list_runs — the history row counted findings the gates rejected
# ---------------------------------------------------------------------------

def test_history_critical_count_excludes_gated_findings():
    """The history list counted raw `severity == "krytyczny"` while the report
    counted only confirmed ones, so a run whose report correctly said 0 critical
    was painted red with "4" in the history. Same predicate, same answer."""
    from simulated_web_agent.main.severity import excluded_from_report

    findings = [
        {"severity": "krytyczny"},                                # real
        {"severity": "krytyczny", "claim_verdict": "unsupported"},  # rejected
        {"severity": "krytyczny", "harness_suspect": True},         # our fault
        {"severity": "poważny"},                                  # not critical
    ]
    counted = sum(
        1 for f in findings
        if f.get("severity") == "krytyczny" and not excluded_from_report(f)
    )
    assert counted == 1


# ---------------------------------------------------------------------------
# reporter_html._esc — a count of 0 rendered as a blank cell
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (0, "0"),        # the bug: `str(s or "")` made every falsy value empty
    (0.0, "0.0"),
    (1, "1"),
    (2.5, "2.5"),
    (None, ""),      # genuinely absent stays empty
    ("", ""),
])
def test_esc_renders_zero_rather_than_blank(value, expected):
    """An empty "Problemy krytyczne" cell reads as "not measured" when what the
    pipeline found was zero critical problems — the opposite claim, and the more
    reassuring one."""
    from simulated_web_agent.main.reporter_html import _esc

    assert _esc(value) == expected


def test_esc_still_escapes_markup():
    from simulated_web_agent.main.reporter_html import _esc

    assert _esc("<b>&") == "&lt;b&gt;&amp;"
