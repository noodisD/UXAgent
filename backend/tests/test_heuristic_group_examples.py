"""A heuristic's examples say how severe each one is, and the prompt says not to
lend one defect another's severity.

By-heuristic rollups report the worst severity of the group and every person who
filed anything in it. In the 2026-09-27 hyundai run two unrelated H2 findings (an
untranslated label, a "null kW" price line) shared one row, so each took the
other's severity and reach; the narrator saw only the row."""
import tempfile
from pathlib import Path

from simulated_web_agent.main.reporter import aggregate_statistics
from tests.conftest import make_agent_report, make_finding


# A relative "runs/R/…" made aggregate_statistics create folders beside the real runs.
TRACE_ROOT = Path(tempfile.mkdtemp(prefix="uxagent-test-"))


def _report(name, *findings):
    r = make_agent_report(findings=list(findings))
    r["trace_dir"] = str(TRACE_ROOT / name)
    r["persona"] = f"Persona: {name}"
    return r


def test_each_example_carries_its_own_severity_and_participant():
    minor = {**make_finding(heuristic_id=2, severity="drobny", step=16),
             "evidence": "Etykieta jest po angielsku"}
    serious = {**make_finding(heuristic_id=2, severity="poważny", step=8),
               "evidence": "Cena pokazuje null kW"}
    stats = aggregate_statistics([_report("Magdalena", minor), _report("Dorota", serious)])

    group = next(h for h in stats["heuristic"]["by_heuristic"] if h["heuristic_id"] == 2)
    assert group["worst_severity"] == "poważny", "the group still reports its worst"
    by_who = {e["persona_name"]: e["severity"] for e in group["examples"]}
    assert by_who == {"Magdalena": "drobny", "Dorota": "poważny"}


def test_the_prompt_says_a_group_may_hold_different_defects():
    prompt = (Path(__file__).resolve().parents[1] / "src" / "simulated_web_agent"
              / "audit_prompts" / "aggregate_synthesize.txt").read_text(encoding="utf-8")
    assert "jedna heurystyka Nielsena może obejmować RÓŻNE wady" in prompt
    assert "nie przypisuj drobnej wadzie wagi najgorszego przykładu" in prompt
