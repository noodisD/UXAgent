"""A consent notice gates the steps it blocked, not the rest of the session.

One failed dismissal used to mark every later step harness-suspect, so an
unrelated critical finding at the end of the session ("Cena w koszyku różni się
od ceny na karcie produktu") was held back — and a notice the persona then
accepted itself changed nothing, because no later state recorded it gone.
"""
import inspect

from simulated_web_agent.main import attribution, experiment
from tests.test_attribution_executor_errors import _ok, _trace


def _store(tmp_path, states, n=10):
    store = _trace(tmp_path, actions=[{"action": "click", "target": f"t{i}"} for i in range(n)],
                   observations=[_ok()] * (n + 1))
    store.write_consent_states(states)
    return store


def test_the_range_ends_where_the_notice_was_dismissed(tmp_path):
    store = _store(tmp_path, {3: {"attempted": True, "dismissed": False},
                              4: {"attempted": True, "dismissed": True}})
    assert attribution.harness_suspect_steps(store) == {3, 4}


def test_the_range_ends_where_the_notice_was_gone(tmp_path):
    store = _store(tmp_path, {3: {"attempted": True, "dismissed": False},
                              6: {"attempted": False, "dismissed": False, "present": False}})
    assert attribution.harness_suspect_steps(store) == {3, 4, 5, 6}


def test_with_no_record_of_it_going_the_rest_stays_suspect(tmp_path):
    """consent_present does not see every notice the landing dismissal tries,
    so a missing record is no proof the notice went away."""
    store = _store(tmp_path, {3: {"attempted": True, "dismissed": False}})
    assert attribution.harness_suspect_steps(store) == set(range(3, 10))


def test_a_late_unrelated_finding_is_not_held(tmp_path):
    store = _store(tmp_path, {3: {"attempted": True, "dismissed": False},
                              4: {"attempted": True, "dismissed": True}})
    f = {"heuristic_id": 5, "severity": "krytyczny", "step": 9,
         "evidence": "Cena w koszyku różni się od ceny na karcie produktu."}
    assert attribution.flag_harness_suspects(store, [f])[0]["harness_suspect"] is False


def test_the_hook_records_a_notice_that_went_away():
    assert experiment._consent_unresolved({0: {"attempted": True, "dismissed": False}})
    assert not experiment._consent_unresolved({0: {"attempted": True, "dismissed": True}})
    assert not experiment._consent_unresolved({})


def test_a_crashed_session_still_saves_its_consent_states():
    src = inspect.getsource(experiment)
    start = src.index("\n    except Exception as exc:\n")  # the session-level handler
    crash = src[start:src.index("\n    finally:", start)]
    assert "write_session_result" in crash, "the session's own error path"
    assert "write_consent_states" in crash
