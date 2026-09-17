"""A run that observed nothing must not deliver a verdict.

Found live, not in review. Re-running the external reviewer's four cases,
t-mobile.pl turned out to be TLS-intercepted on the way out of this network.
Both sessions ended at step 0 having loaded nothing — and the run still
finished "done" and shipped a report reading "a critical barrier ON THE SITE",
scoring it 1.0/5 and recommending the site fix the fault.

That is the reviewer's own complaint in a worse form: her t-mobile run gave
seventy minutes and nothing, this one gives a confident wrong verdict in three.
The existing guard could not see it — stores existed, a report was written, so
nothing looked absent. Absence of evidence had to be checked directly.
"""
import json

import pytest

from simulated_web_agent.main import run as run_mod


class _Store:
    """Minimal stand-in: run.py only asks a store for its session result here."""

    def __init__(self, tmp_path, name, steps, error=None):
        self.trace_dir = tmp_path / name
        self.trace_dir.mkdir(parents=True, exist_ok=True)
        self._result = {"steps_taken": steps, "ended_reason": "error" if error else "max_steps"}
        if error:
            self._result["error"] = error

    def read_session_result(self):
        return dict(self._result)


def _observed(stores):
    """The predicate run.py applies, isolated so both directions can be pinned
    without standing up a browser, an LLM and a full pipeline."""
    return [s for s in stores
            if int(((s.read_session_result() or {}).get("steps_taken") or 0)) > 0]


def test_a_run_where_nothing_loaded_has_no_observed_sessions(tmp_path):
    stores = [
        _Store(tmp_path, "p0", 0, error='navigating to "https://www.t-mobile.pl/"'),
        _Store(tmp_path, "p1", 0, error='navigating to "https://www.t-mobile.pl/"'),
    ]
    assert _observed(stores) == []


def test_one_persona_reaching_the_site_is_enough_to_report(tmp_path):
    """Partial evidence is still evidence — the report says how many personas
    contributed. Only *no* evidence is disqualifying, or a single flaky session
    would suppress a whole audit."""
    stores = [
        _Store(tmp_path, "p0", 0, error="net::ERR_CERT_AUTHORITY_INVALID"),
        _Store(tmp_path, "p1", 6),
    ]
    assert len(_observed(stores)) == 1


def test_a_missing_or_malformed_session_result_counts_as_unobserved(tmp_path):
    """steps_taken can be absent or null when a session dies early; the guard
    must treat that as "no evidence", never crash on it."""
    class _Broken(_Store):
        def read_session_result(self):
            return None

    class _Null(_Store):
        def read_session_result(self):
            return {"steps_taken": None}

    assert _observed([_Broken(tmp_path, "p0", 0), _Null(tmp_path, "p1", 0)]) == []


def test_the_failure_payload_says_it_is_not_about_the_site(tmp_path, monkeypatch):
    """The whole point of the guard is what the reader is told. A failure that
    still reads as a site defect would be the same bug with a different status.
    """
    import inspect
    src = inspect.getsource(run_mod.run_async)
    start = src.index("No session reached the site")
    payload = src[start:start + 900]
    assert "not a finding about the site" in payload
    assert "TLS" in payload or "proxy" in payload, \
        "the suggestion must point at reachability, which is what actually failed"


def test_the_guard_runs_before_the_report_is_generated():
    """Order matters: producing the 1.0/5 narrative and then flagging it still
    puts the sentence on disk, into the PDF and into the history row."""
    import inspect
    src = inspect.getsource(run_mod.run_async)
    assert src.index("No session reached the site") < src.index("generate_aggregated_report")


# --- the run's own record must name the fault -------------------------------

def test_a_playwright_failure_keeps_the_error_code_not_the_call_log():
    """Playwright's message is multi-line: the code comes first, then a call
    log. Recording the traceback's last line kept 'navigating to "…", waiting
    until "domcontentloaded"' — which says a page load was attempted and hides
    the one word explaining why it failed. That string is what the failure
    payload shows the user, so it has to carry the diagnosis."""
    from simulated_web_agent.main.experiment import _exception_summary

    class PlaywrightError(Exception):
        pass

    exc = PlaywrightError(
        "Page.goto: net::ERR_CERT_AUTHORITY_INVALID at https://www.t-mobile.pl/\n"
        'Call log:\n  - navigating to "https://www.t-mobile.pl/", waiting until "domcontentloaded"'
    )
    summary = _exception_summary(exc)
    assert "ERR_CERT_AUTHORITY_INVALID" in summary
    assert "domcontentloaded" not in summary


def test_an_ordinary_exception_still_reads_normally():
    from simulated_web_agent.main.experiment import _exception_summary

    assert _exception_summary(ValueError("bad config")) == "ValueError: bad config"


def test_an_exception_with_no_message_does_not_render_empty():
    from simulated_web_agent.main.experiment import _exception_summary

    assert _exception_summary(RuntimeError()).strip()
