"""The loop guards tell the persona what actually happened.

- "The page did not change" was decided from the URL alone: on the hyundai
  configurator's colour step every "next" advanced the exterior-view gallery
  (Slide 1 -> 4 of 8) on one hash URL, and the persona was told three times
  nothing had happened. (The colour itself is chosen with separate arrows.)
- A path the audit's own rules refused (personal data, off-site) counted as a
  failure; after two, the persona was told "that element or route is broken —
  a UX finding worth noting", and the injected back carried the wording
  attribution reads as a harness failure.
"""
import inspect

from simulated_web_agent.main import attribution, experiment
from simulated_web_agent.main.loop_guards import REFUSED_DESCRIPTION, REFUSED_THOUGHT


def test_the_same_url_with_new_content_is_a_change():
    a = {"html": '<div parser-is-focused="true">Slide 1 of 8</div>'}
    b = {"html": "<div>Slide 2 of 8</div>"}
    assert experiment._page_signature(a) != experiment._page_signature(b)


def test_only_the_focus_marker_is_not_a_change():
    a = {"html": '<button parser-is-focused="true">Dalej</button>'}
    b = {"html": "<button>Dalej</button>"}
    assert experiment._page_signature(a) == experiment._page_signature(b)


def test_the_repeat_guard_compares_the_page_too():
    src = inspect.getsource(experiment)
    guard = src[src.index("elif (action_key == prev_action_key"):][:300]
    assert "_page_signature(obs) == html_before" in guard


def test_a_refused_path_is_not_called_broken_or_a_harness_failure():
    assert "not a problem with the site" in REFUSED_THOUGHT
    assert "broken" not in REFUSED_THOUGHT and "UX finding" not in REFUSED_THOUGHT
    assert not any(m in REFUSED_DESCRIPTION for m in attribution._FALLBACK_MARKERS)


def test_experiment_uses_the_refused_and_failed_wording_it_is_tested_for():
    """Drift guard: the loop must say what the tests above read, not a copy."""
    src = inspect.getsource(experiment)
    # Used by the loop, not merely imported: the import line alone would satisfy
    # a bare "in src" check.
    assert "Thought(REFUSED_THOUGHT" in src
    assert '"description": REFUSED_DESCRIPTION' in src
    assert "FAILED_THOUGHT.format(" in src
    assert "not a problem with the site" not in src
    assert "kept failing" not in src
