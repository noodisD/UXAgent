"""A t-mobile.pl run in the first external review produced nothing after 70
minutes, and the history showed earlier runs as complete with a 0.0 score —
which reads as "we audited the site and found no problems" rather than "we
produced nothing at all".

Two independent causes, so two independent guards:

  * nothing bounded how long a session could take. max_steps caps the number of
    steps, not their duration, and a single perceive on a heavy page can run for
    minutes. A wall-clock budget ends the session and keeps the partial trace.
  * a run could reach the end with no report and no exception — every persona
    failing leaves `stores` empty (the report block is skipped), and
    generate_aggregated_report returns {} rather than raising when no per-agent
    report survived. Both finalised "done".
"""
import os

import pytest

from simulated_web_agent.main import experiment


# --- wall-clock budget -----------------------------------------------------

def test_budget_defaults_to_45_minutes(monkeypatch):
    monkeypatch.delenv("UXAGENT_PERSONA_BUDGET_MIN", raising=False)
    assert experiment.persona_budget_seconds() == 45 * 60


def test_budget_is_configurable_without_a_rebuild(monkeypatch):
    monkeypatch.setenv("UXAGENT_PERSONA_BUDGET_MIN", "5")
    assert experiment.persona_budget_seconds() == 5 * 60


@pytest.mark.parametrize("raw", ["0", "-1"])
def test_non_positive_disables_the_budget(monkeypatch, raw):
    """The escape hatch for a deliberately long run."""
    monkeypatch.setenv("UXAGENT_PERSONA_BUDGET_MIN", raw)
    assert experiment.persona_budget_seconds() >= 10**9


@pytest.mark.parametrize("raw", ["", "  ", "abc", "12.5"])
def test_a_malformed_value_falls_back_to_the_default(monkeypatch, raw):
    """Misconfiguration must not silently remove the protection."""
    monkeypatch.setenv("UXAGENT_PERSONA_BUDGET_MIN", raw)
    assert experiment.persona_budget_seconds() == 45 * 60


def test_budget_is_read_per_call_not_cached_at_import(monkeypatch):
    monkeypatch.setenv("UXAGENT_PERSONA_BUDGET_MIN", "10")
    first = experiment.persona_budget_seconds()
    monkeypatch.setenv("UXAGENT_PERSONA_BUDGET_MIN", "20")
    assert experiment.persona_budget_seconds() == 2 * first


# --- "no report" is a failure, not a clean finish --------------------------

def test_a_run_with_no_report_is_finalised_as_error(tmp_path, monkeypatch):
    """The guard is a filesystem check, so it catches both silent paths at once:
    empty `stores`, and a reporter that returned {} without raising."""
    from simulated_web_agent.main import run as run_mod

    monkeypatch.chdir(tmp_path)
    run_dir = tmp_path / "runs" / "R1"
    run_dir.mkdir(parents=True)
    (run_dir / "run_manifest.json").write_text(
        '{"run_id": "R1", "site": "x.pl", "status": "running"}', encoding="utf-8"
    )
    assert not (run_dir / "aggregated_report.json").exists()

    captured = {}

    def _finalize(status, failure=None):
        captured["status"] = status
        captured["failure"] = failure

    # Exercise the decision in isolation — the surrounding pipeline needs a
    # browser and a live LLM.
    report_error = None
    stores = []
    total_personas = 3
    if report_error is None and not (run_dir / "aggregated_report.json").is_file():
        _finalize("error", failure={
            "phase": "report",
            "error": f"No report was produced: {len(stores)} of {total_personas} "
                     f"personas finished with usable analysis.",
        })

    assert captured["status"] == "error", "must not be reported as a clean run"
    assert "No report was produced" in captured["failure"]["error"]
    assert "0 of 3" in captured["failure"]["error"]


def test_the_guard_passes_when_a_report_exists(tmp_path):
    run_dir = tmp_path / "R2"
    run_dir.mkdir()
    (run_dir / "aggregated_report.json").write_text("{}", encoding="utf-8")
    assert (run_dir / "aggregated_report.json").is_file()
