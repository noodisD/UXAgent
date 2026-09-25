"""Task outcomes: no invented verdicts, no measured ones dropped by wording.

- A failed or off-list reflection became "partial": the score charged it as the
  model's verdict and the report printed "Cel osiągnięty częściowo" for a
  verdict nobody gave. The dashboard did the same for a missing reflection,
  counting hebe 2e58 — no session, no reflection — as partially successful.
- The rule that sets aside a verdict resting on a held-back finding also set
  aside outcomes measured from the trace, so the lidl report's score line read
  "nieosiągnięty: 1/2 … zmierzone z zapisu sesji" while the rest said 2/2.
"""
from unittest.mock import AsyncMock, patch

import pytest

from simulated_web_agent.main.reporter import _coerce_reflection, generate_objective_reflection
from simulated_web_agent.main.results_api import _effective_outcome
from simulated_web_agent.main.scoring import compute_ux_score
from simulated_web_agent.main.trace_store import TraceStore


def test_an_off_list_verdict_is_unknown():
    assert _coerce_reflection({"outcome": "mostly"}, "X")["outcome"] == "unknown"
    assert _coerce_reflection(None, "X")["outcome"] == "unknown"
    assert _coerce_reflection({"outcome": "Failed"}, "X")["outcome"] == "failed"


@pytest.mark.asyncio
async def test_a_failed_reflection_call_is_unknown(tmp_path):
    store = TraceStore(tmp_path)
    with patch("simulated_web_agent.main.reporter.async_chat",
               new=AsyncMock(side_effect=RuntimeError("quota"))):
        r = await generate_objective_reflection(store)
    assert r["outcome"] == "unknown"


def test_an_unknown_outcome_costs_nothing():
    res = compute_ux_score({"heuristic": {}}, outcomes={"a": "unknown", "b": "success"},
                           outcome_sources={"a": "declared", "b": "declared"})
    assert not [d for d in res["deductions"] if d["kind"] == "task"]


def test_no_reflection_is_no_verdict_on_the_dashboard(tmp_path):
    assert _effective_outcome(TraceStore(tmp_path)) == "unknown"


def _stats(held_dirs):
    return {"heuristic": {}, "objective_reflections": [
        {"persona_dir": d, "self_reported_only": True} for d in held_dirs]}


def test_a_measured_failure_counts_whatever_its_reflection_says():
    res = compute_ux_score(_stats(["a"]), outcomes={"a": "failed", "b": "failed"},
                           outcome_sources={"a": "observed", "b": "observed"})
    task = [d for d in res["deductions"] if d["kind"] == "task"][0]
    assert "2/2" in task["basis"]
    assert not [n for n in res["not_counted"] if n["label"].startswith("Wynik zadania")]


def test_a_declared_verdict_on_a_held_claim_is_still_set_aside():
    res = compute_ux_score(_stats(["a"]), outcomes={"a": "failed", "b": "success"},
                           outcome_sources={"a": "declared", "b": "declared"})
    assert not [d for d in res["deductions"] if d["kind"] == "task"]
    assert [n for n in res["not_counted"] if n["label"].startswith("Wynik zadania")]
