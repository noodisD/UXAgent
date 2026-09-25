"""One task outcome per participant, counted the same everywhere.

The score used the measured outcome while the objectives section tallied the
model's verdicts, so e5d6 printed "nieosiągnięty: 3/3, zmierzone" beside
"osiągnięty 1 · częściowo 1 · nieosiągnięty 1"; the dashboard ignored an
abandonment; and the "measured" outcome itself came from iso_metrics.json as
written on the day — e5d6's still judged a car configurator against a checkout
ladder, which the current code no longer does.
"""
import json

from simulated_web_agent.main.iso_metrics import effective_outcome
from simulated_web_agent.main.reporter_html import _render_objectives
from simulated_web_agent.main.results_api import _effective_outcome
from simulated_web_agent.main.scoring import compute_ux_score
from simulated_web_agent.main.trace_store import TraceStore


def test_a_stale_stored_measurement_is_not_trusted(tmp_path):
    s = TraceStore(tmp_path)
    s.write_basic_info({"intent": "Skonfiguruj samochód i umów jazdę testową"})
    s.write_objective_reflection({"outcome": "success"})
    s.write_action_trace([json.dumps({"action": "click", "target": "konfigurator"})])
    # What the old checkout ladder wrote for a configurator task.
    s.write_iso_metrics({"effectiveness": {"outcome_source": "observed",
                                           "observed_outcome": "failed",
                                           "target_rung": "order_summary_reached"}})
    assert effective_outcome(s) == ("success", "declared")


def test_without_a_trace_the_stored_block_is_all_there_is(tmp_path):
    s = TraceStore(tmp_path)
    s.write_objective_reflection({"outcome": "failed"})
    s.write_iso_metrics({"effectiveness": {"outcome_source": "observed", "observed_outcome": "success"}})
    assert effective_outcome(s) == ("success", "observed")


def test_an_abandoned_session_is_named_not_charged(tmp_path):
    s = TraceStore(tmp_path)
    s.write_session_result({"ended_reason": "persona_abandoned", "steps_taken": 9})
    s.write_objective_reflection({"outcome": "partial"})
    assert effective_outcome(s) == ("persona_abandoned", "session")
    assert _effective_outcome(s) == "failed", "the dashboard shows it as not completed"
    res = compute_ux_score({"heuristic": {}}, outcomes={"a": "persona_abandoned", "b": "success"},
                           outcome_sources={"a": "session", "b": "declared"})
    assert not [d for d in res["deductions"] if d["kind"] == "task"]
    assert any("przerwana" in n["label"] for n in res["not_counted"])


def test_the_objectives_section_counts_what_the_score_counts():
    rows = [{"persona": p, "outcome": declared, "effective_outcome": "failed",
             "outcome_source": "observed"} for p, declared in
            (("A", "failed"), ("B", "success"), ("C", "partial"))]
    html = _render_objectives({"objective_reflections": rows})
    assert "Cel nieosiągnięty: 3" in html
    assert "model ocenił: cel osiągnięty" in html, "the disagreement is shown, not hidden"
