"""Two ways a demoted finding could still reach a reader as if it were real.

The gates decide *whether* a finding counts; these tests cover what a human
actually sees. A harness failure rendered as "[KRYTYCZNY] H3: …" in the
per-persona file is the same false defect the gates exist to stop shipping,
even though the headline counts already exclude it.

And a run orphaned by a restart: the worker is a daemon thread and the registry
is in memory, so a redeploy leaves the manifest saying "running" for ever, with
no way to clear it — the cancel endpoint only knows runs in this process.
"""
import pytest

from simulated_web_agent.main import app as app_module
from simulated_web_agent.main.reporter import _gate_reason


# --- demoted findings are visibly demoted ---------------------------------

@pytest.mark.parametrize("flag,expected", [
    ({"harness_suspect": True}, "narzędzia"),
    ({"off_site": True}, "domeny"),
    ({"claim_verdict": "unsupported"}, "nie potwierdza"),
    ({"requires_human_check": True}, "człowieka"),
])
def test_every_gate_explains_itself(flag, expected):
    """A reader needs *why*, not just that a count is lower than the tally."""
    reason = _gate_reason({"severity": "krytyczny", **flag})
    assert reason and expected in reason


def test_a_confirmed_finding_carries_no_caveat():
    assert _gate_reason({"severity": "krytyczny", "harness_suspect": False}) == ""


def test_harness_suspicion_outranks_the_other_reasons():
    """When several apply, name the one that most explains the demotion: that we
    caused it, rather than that someone should look at it."""
    assert "narzędzia" in _gate_reason(
        {"harness_suspect": True, "off_site": True, "requires_human_check": True}
    )


@pytest.mark.parametrize("bad", [None, "x", 42, []])
def test_malformed_findings_do_not_crash_the_report(bad):
    assert _gate_reason(bad) == ""


# --- orphaned runs are reaped ---------------------------------------------

def _run(root, name, status):
    d = root / name
    d.mkdir(parents=True)
    (d / "run_manifest.json").write_text(
        '{"run_id": "%s", "site": "x.pl", "status": "%s"}' % (name, status),
        encoding="utf-8",
    )
    return d


def test_a_run_left_running_by_a_dead_process_is_marked_interrupted(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runs = tmp_path / "runs"
    _run(runs, "ORPHAN", "running")

    assert app_module.reap_orphaned_runs(runs) == ["ORPHAN"]

    from simulated_web_agent.main.run import read_manifest
    m = read_manifest(runs / "ORPHAN")
    assert m["status"] == "error"
    assert "restarted" in m["failure"]["error"]
    assert m["failure"]["suggestion"], "must tell the user what to do next"


@pytest.mark.parametrize("status", ["done", "error", "cancelled"])
def test_finished_runs_are_left_alone(tmp_path, monkeypatch, status):
    monkeypatch.chdir(tmp_path)
    runs = tmp_path / "runs"
    _run(runs, "FINISHED", status)

    assert app_module.reap_orphaned_runs(runs) == []

    from simulated_web_agent.main.run import read_manifest
    assert read_manifest(runs / "FINISHED")["status"] == status


def test_reaping_is_safe_with_no_runs_directory(tmp_path):
    assert app_module.reap_orphaned_runs(tmp_path / "nope") == []


def test_stray_files_and_junk_dirs_are_skipped(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    runs = tmp_path / "runs"
    runs.mkdir()
    (runs / "notes.txt").write_text("x", encoding="utf-8")
    (runs / "half_written").mkdir()          # no manifest
    assert app_module.reap_orphaned_runs(runs) == []
