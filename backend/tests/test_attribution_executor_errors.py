"""The executor writes `observation["error"]` whenever it cannot carry out an
action. That is the strongest harness-vs-site signal a run produces, and until
now nothing read it: a consent overlay that blocked a click became a first-person
UX complaint (feedback.txt tells the persona to narrate the failure), which
became a Reflection, which auditor.py feeds verbatim to the heuristic LLM. The
first external review came back dominated by a cookie "defect" the reviewer said
she had never once hit as a real user.

These tests pin both directions, because over-suppression is as damaging as
under-suppression: a rule that swallows real defects would quietly gut the
report instead of visibly breaking it.
"""
import json

import pytest

from simulated_web_agent.main import attribution
from simulated_web_agent.main.trace_store import TraceStore


def _trace(tmp_path, actions, observations, obs_texts=None):
    store = TraceStore(tmp_path / "persona")
    store.write_action_trace([json.dumps(a) for a in actions])
    for obs in observations:
        store.append_observation(obs)
    for i, text in enumerate(obs_texts or []):
        store.write_observation_step_text(i, text)
    return store


def _ok(url="https://x.pl/"):
    return {"tabs": [{"url": url, "is_active": True}], "error": None}


def _failed(err, url="https://x.pl/"):
    return {"tabs": [{"url": url, "is_active": True}], "error": err}


# --- the fix: executor failures are harness-caused -------------------------

@pytest.mark.parametrize("err", [
    "Error executing action: Locator.click: Timeout 10000ms exceeded",
    "Error executing action: Element 'zaakceptuj' not found on page",
    "Invalid JSON action format: Expecting value",
    "Missing required parameter in action: 'target'",
])
def test_executor_failure_marks_the_failing_step(tmp_path, err):
    """observations[i] is the state *before* action i, so its error belongs to
    action i-1 — the one that actually failed."""
    store = _trace(
        tmp_path,
        actions=[{"action": "click", "target": "a"}, {"action": "click", "target": "b"}],
        observations=[_ok(), _failed(err)],
    )
    suspect = attribution.harness_suspect_steps(store)
    assert 0 in suspect, "the action that failed must be blamed"
    assert 1 in suspect, "the step that perceives the unchanged page anchors the finding"


def test_a_finding_on_a_failed_step_is_excluded_from_the_report(tmp_path):
    """End to end: the flag must reach severity.excluded_from_report, which is
    what every counter, the prioritization matrix and the dashboard consult."""
    from simulated_web_agent.main.severity import excluded_from_report, severity_score

    store = _trace(
        tmp_path,
        actions=[{"action": "click", "target": "consent_ok"}, {"action": "click", "target": "menu"}],
        observations=[_ok(), _failed("Error executing action: Locator.click: Timeout 10000ms exceeded")],
    )
    findings = [{
        "heuristic_id": 1, "severity": "krytyczny", "step": 0,
        "evidence": "Kliknięcie nie wywołało żadnej reakcji strony.",
        "recommendation": "Naprawić przycisk.",
    }]
    attribution.flag_harness_suspects(store, findings)

    assert findings[0]["harness_suspect"] is True
    assert findings[0]["requires_human_check"] is True
    assert excluded_from_report(findings[0]) is True
    assert severity_score(findings[0]) == 0, "must not carry weight in headline counts"


# --- the guard: real defects must survive ----------------------------------

def test_a_clean_session_flags_nothing(tmp_path):
    store = _trace(
        tmp_path,
        actions=[{"action": "click", "target": "a"}, {"action": "type", "target": "b"}],
        observations=[_ok(), _ok(), _ok()],
    )
    assert attribution.harness_suspect_steps(store) == set()


@pytest.mark.parametrize("advisory", [
    "Repeated identical action 3 times with no page change: {\"action\":\"click\"}. "
    "Choose a different element or strategy.",
    "You have clicked 'dodaj_do_koszyka' 4 times this session.",
])
def test_harness_advisories_do_not_condemn_a_finding(tmp_path, advisory):
    """experiment.py injects these into the same field, but they mean "the agent
    retried" — which is equally consistent with the *site* being unresponsive.
    Treating them as proof of a harness fault would suppress exactly the real
    defect the tool exists to find. Consecutive repeats are still caught, more
    conservatively, by _REPEAT_THRESHOLD."""
    store = _trace(
        tmp_path,
        actions=[{"action": "click", "target": "buy"}, {"action": "click", "target": "other"}],
        observations=[_ok(), _failed(advisory)],
    )
    assert attribution.harness_suspect_steps(store) == set()


def test_site_errors_shown_as_page_content_are_untouched(tmp_path):
    """A 404 or a validation message is page content, never observation["error"],
    so a genuine site failure keeps its full severity."""
    store = _trace(
        tmp_path,
        actions=[{"action": "click", "target": "submit"}],
        observations=[_ok(), _ok()],
        obs_texts=["I see a page reading 'Błąd 404 — strona nie istnieje'."],
    )
    findings = [{"heuristic_id": 9, "severity": "krytyczny", "step": 0,
                 "evidence": "Strona zwróciła błąd 404.", "recommendation": "Naprawić link."}]
    attribution.flag_harness_suspects(store, findings)
    assert findings[0]["harness_suspect"] is False


def test_flags_are_rewritten_not_latched(tmp_path):
    """Re-running after a rule change must clear stale flags."""
    store = _trace(
        tmp_path,
        actions=[{"action": "click", "target": "a"}],
        observations=[_ok(), _ok()],
    )
    findings = [{"heuristic_id": 1, "severity": "drobny", "step": 0,
                 "harness_suspect": True, "requires_human_check": True}]
    attribution.flag_harness_suspects(store, findings)
    assert findings[0]["harness_suspect"] is False


# --- consent overlays ------------------------------------------------------
#
# The review's headline complaint. A notice that will not dismiss intercepts
# every later click, and the resulting "the button does nothing" findings are
# ours, not the site's.

def test_unresolved_consent_condemns_every_later_step(tmp_path):
    store = _trace(
        tmp_path,
        actions=[{"action": "click", "target": f"t{i}"} for i in range(4)],
        observations=[_ok()] * 5,
    )
    store.write_consent_states({0: {"attempted": True, "dismissed": False, "selector": None}})
    assert attribution.harness_suspect_steps(store) == {0, 1, 2, 3}


def test_consent_dismissed_cleanly_condemns_nothing(tmp_path):
    """The overwhelmingly common case: a banner appeared and was accepted."""
    store = _trace(
        tmp_path,
        actions=[{"action": "click", "target": "a"}, {"action": "click", "target": "b"}],
        observations=[_ok(), _ok(), _ok()],
    )
    store.write_consent_states(
        {0: {"attempted": True, "dismissed": True, "selector": "#onetrust-accept-btn-handler"}}
    )
    assert attribution.harness_suspect_steps(store) == set()


def test_no_banner_at_all_condemns_nothing(tmp_path):
    """attempted=False is not a failure — most pages simply have no notice."""
    store = _trace(
        tmp_path,
        actions=[{"action": "click", "target": "a"}],
        observations=[_ok(), _ok()],
    )
    store.write_consent_states({0: {"attempted": False, "dismissed": False, "selector": None}})
    assert attribution.harness_suspect_steps(store) == set()


def test_missing_consent_file_is_not_an_error(tmp_path):
    """Runs recorded before consent tracking existed must still audit."""
    store = _trace(
        tmp_path,
        actions=[{"action": "click", "target": "a"}],
        observations=[_ok(), _ok()],
    )
    assert store.read_consent_states() == {}
    assert attribution.harness_suspect_steps(store) == set()


# --- why the short-target floor stays --------------------------------------

def test_the_target_floor_is_measured_not_assumed(tmp_path):
    """A numeric target must not condemn a finding by naming.

    The review plan proposed dropping _MIN_TARGET_LEN so short consent ids
    ("ok", "zgoda") could be matched by name. Measured over the 28 archived
    sessions, removing it flagged 2 extra findings out of 135 — both matched
    through the ids "1" and "2" hitting digits in Polish prose, neither about
    consent. Consent is caught per step from the recorded consent state, which
    does not depend on prose at all, so the floor buys accuracy for nothing.
    """
    store = _trace(
        tmp_path,
        actions=[{"action": "click", "target": "2"}, {"action": "click", "target": "x"}],
        observations=[_ok(), _failed("Error executing action: Locator.click: Timeout")],
    )
    findings = [{
        "heuristic_id": 1, "severity": "krytyczny", "step": 7,
        "evidence": "Ikona koszyka wciąż pokazuje 'Koszyk, 2 artykuły'.",
        "recommendation": "Zsynchronizować licznik koszyka.",
    }]
    attribution.flag_harness_suspects(store, findings)
    assert findings[0]["harness_suspect"] is False, \
        "a digit in prose must not demote an unrelated finding"


def test_a_distinctive_id_still_condemns_by_name(tmp_path):
    """The name path has to keep working — findings often anchor on a later
    step but name the stuck element in their text."""
    store = _trace(
        tmp_path,
        actions=[{"action": "type", "target": "wyszukaj_lokalizacje"},
                 {"action": "click", "target": "x"}],
        observations=[_ok(), _failed("Error executing action: Locator.click: Timeout")],
    )
    findings = [{
        "heuristic_id": 1, "severity": "poważny", "step": 28,
        "evidence": "Wpisanie tekstu w pole 'wyszukaj_lokalizacje' nie dało efektu.",
        "recommendation": "Naprawić pole.",
    }]
    attribution.flag_harness_suspects(store, findings)
    assert findings[0]["harness_suspect"] is True


# --- bot-detection challenge pages ------------------------------------------
#
# The review's hebe.pl report: six critical "site" defects generated entirely
# from a Cloudflare challenge screen, one of them advising hebe to put a
# timeout on its own bot protection. No earlier signature could see it — a
# challenge page loads with a normal status and renders text, so there is no
# executor error, no empty observation and no consent state.

@pytest.mark.parametrize("wall", [
    "www.hebe.pl Performing verification This website uses a security service to "
    "protect against malicious bots.",
    "Checking your browser before accessing the site. DDoS protection by Cloudflare.",
    "Verify you are human by completing the action below.",
    "Enable JavaScript and cookies to continue",
])
def test_a_challenge_page_condemns_its_step(tmp_path, wall):
    store = _trace(
        tmp_path,
        actions=[{"action": "click", "target": "a"}, {"action": "click", "target": "b"}],
        observations=[_ok(), _ok(), _ok()],
        obs_texts=[wall, "Strona główna sklepu"],
    )
    assert 0 in attribution.harness_suspect_steps(store)


def test_a_finding_from_the_challenge_screen_is_excluded(tmp_path):
    """End to end, the hebe case exactly."""
    from simulated_web_agent.main.severity import excluded_from_report

    store = _trace(
        tmp_path,
        actions=[{"action": "click", "target": "verify"}],
        observations=[_ok(), _ok()],
        obs_texts=["www.hebe.pl Performing verification This website uses a security "
                   "service to protect against malicious bots."],
    )
    findings = [{
        "heuristic_id": 9, "severity": "krytyczny", "step": 0,
        "evidence": "System na stałe zablokował użytkownika na ekranie weryfikacji.",
        "recommendation": "Zaimplementować mechanizm timeout dla weryfikacji.",
    }]
    attribution.flag_harness_suspects(store, findings)
    assert findings[0]["harness_suspect"] is True
    assert excluded_from_report(findings[0]) is True


def test_ordinary_pages_mentioning_bots_or_cookies_are_untouched(tmp_path):
    """Matched as whole phrases, never on a bare "bot" or "captcha" — a shop
    selling robot vacuums, or any page explaining its own bot policy, would
    otherwise condemn its own session."""
    store = _trace(
        tmp_path,
        actions=[{"action": "click", "target": "a"}, {"action": "click", "target": "b"}],
        observations=[_ok(), _ok(), _ok()],
        obs_texts=[
            "Robot sprzątający iRobot Roomba — najlepsze oferty. Polityka cookies.",
            "Nasz bot obsługi klienta odpowie na Twoje pytania. Captcha chroni formularz.",
        ],
    )
    assert attribution.harness_suspect_steps(store) == set()


def test_a_session_is_only_blocked_when_every_step_was_the_challenge(tmp_path):
    """One challenge step is noise to gate; a whole session behind the wall
    means nothing about the site was seen, which run.py treats as no evidence.
    Getting through on any step must not count as blocked."""
    walled = _trace(
        tmp_path / "a",
        actions=[{"action": "click", "target": "x"}, {"action": "click", "target": "y"}],
        observations=[_ok(), _ok(), _ok()],
        obs_texts=["Performing verification", "Checking your browser"],
    )
    assert attribution.session_blocked_by_bot_wall(walled) is True

    got_through = _trace(
        tmp_path / "b",
        actions=[{"action": "click", "target": "x"}, {"action": "click", "target": "y"}],
        observations=[_ok(), _ok(), _ok()],
        obs_texts=["Performing verification", "Drogeria Hebe — kosmetyki i pielęgnacja"],
    )
    assert attribution.session_blocked_by_bot_wall(got_through) is False


def test_a_session_with_no_steps_is_not_called_bot_blocked(tmp_path):
    """It failed for some other reason; mislabelling it would send the reader
    chasing bot protection that was never involved."""
    empty = _trace(tmp_path, actions=[], observations=[])
    assert attribution.session_blocked_by_bot_wall(empty) is False
