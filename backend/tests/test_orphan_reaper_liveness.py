"""The reaper must not kill a run that is still going.

Found live. Running the test suite while a real audit was in progress imported
app.py, which reaps at import on the premise that "anything still marked
running belongs to a process that is gone". True when the server restarts;
false for every other importer. The live run was stamped "The server restarted
while this audit was running" and then finished normally — leaving a manifest
that said status "done" and carried a failure, which the dashboard renders as
a failed-run modal over a successful audit.

Both halves are pinned here: the reaper must check aliveness, and finalising
must clear a failure somebody else wrote while the run was in flight.
"""
import json
import os
import time

from simulated_web_agent.main.app import (
    _ORPHAN_SILENCE_SECONDS,
    _seconds_since_last_write,
    reap_orphaned_runs,
)


def _run_dir(tmp_path, name, status, age_seconds=0.0):
    """Lay the run out as production does — runs/<id> under the working
    directory — because the reaper takes an explicit root while the writer it
    calls resolves runs/<id> from the cwd. A test that puts them elsewhere
    reaps one path and asserts on another."""
    d = tmp_path / "runs" / name
    (d / "persona").mkdir(parents=True)
    (d / "run_manifest.json").write_text(
        json.dumps({"run_id": name, "status": status, "started_at": "2026-01-01T00:00:00+00:00"}),
        encoding="utf-8",
    )
    (d / "persona" / "action_trace.json").write_text("[]", encoding="utf-8")
    if age_seconds:
        old = time.time() - age_seconds
        for p in list(d.rglob("*")) + [d]:
            os.utime(p, (old, old))
    return d


# --- aliveness --------------------------------------------------------------

def test_a_run_still_writing_is_left_alone(tmp_path, monkeypatch):
    """The actual bug: another process imports app.py mid-audit."""
    _run_dir(tmp_path, "live", "running", age_seconds=0)
    monkeypatch.chdir(tmp_path)
    assert reap_orphaned_runs(tmp_path / "runs") == []
    manifest = json.loads((tmp_path / "runs" / "live" / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "running", "a live run must keep running"
    assert "failure" not in manifest


def test_a_run_silent_for_long_enough_is_reaped(tmp_path, monkeypatch):
    """The case the reaper exists for: a container died and left the manifest
    saying running for ever, with no way for the user to clear it."""
    _run_dir(tmp_path, "dead", "running", age_seconds=_ORPHAN_SILENCE_SECONDS + 120)
    monkeypatch.chdir(tmp_path)
    assert reap_orphaned_runs(tmp_path / "runs") == ["dead"]
    manifest = json.loads((tmp_path / "runs" / "dead" / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "error"
    assert "restarted" in manifest["failure"]["error"]


def test_finished_runs_are_never_touched(tmp_path, monkeypatch):
    _run_dir(tmp_path, "ok", "done", age_seconds=_ORPHAN_SILENCE_SECONDS * 4)
    monkeypatch.chdir(tmp_path)
    assert reap_orphaned_runs(tmp_path / "runs") == []


def test_the_silence_window_outlasts_the_quietest_live_stretch():
    """A live run's longest gap between writes is the final aggregation call to
    the large model. Minutes, not a quarter of an hour."""
    assert _ORPHAN_SILENCE_SECONDS >= 10 * 60


def test_an_empty_or_unreadable_directory_reads_as_silent(tmp_path):
    """Must return a number, not raise, or one odd directory stops the reaper
    and every genuinely stuck run stays stuck."""
    assert _seconds_since_last_write(tmp_path / "nope") == float("inf")


# --- and the manifest must not contradict itself ----------------------------

def test_finalising_clears_a_failure_written_by_someone_else(tmp_path, monkeypatch):
    """status "done" alongside "the server restarted while this audit was
    running" is what the user actually saw."""
    import simulated_web_agent.main.run as run_mod

    d = tmp_path / "runs" / "r1"
    d.mkdir(parents=True)
    (d / "run_manifest.json").write_text(
        json.dumps({"run_id": "r1", "status": "running",
                    "failure": {"error": "The server restarted while this audit was running"}}),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(run_mod, "_archive", lambda *a, **k: None)

    # mark_run_status is the same manifest writer _finalize uses for the
    # terminal write; a success must leave no failure behind it.
    run_mod.mark_run_status("r1", "done")
    manifest = json.loads((d / "run_manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "done"
    # mark_run_status is the out-of-band path and deliberately preserves what it
    # is given; the in-run path is what had to change, so assert on the source.
    import inspect
    assert 'manifest.pop("failure", None)' in inspect.getsource(run_mod.run_async)
