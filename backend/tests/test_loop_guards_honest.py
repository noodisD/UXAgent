"""The loop guards tell the persona what actually happened.

- "The page did not change" was decided from the URL alone: on the hyundai
  configurator every "next" advanced the colour slider (Slide 1 -> 4 of 8) on
  one hash URL, and the persona was told three times nothing had happened.
- A path the audit's own rules refused (personal data, off-site) counted as a
  failure; after two, the persona was told "that element or route is broken —
  a UX finding worth noting", and the injected back carried the wording
  attribution reads as a harness failure.
"""
import inspect

from simulated_web_agent.main import attribution, experiment


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
    src = inspect.getsource(experiment)
    block = src[src.index("intended_key in refused_keys"):src.index("elif fail_counts[intended_key] >= 2:")]
    thought = block[block.index("Thought("):block.index("policy.agent.memory,")]
    assert "not a problem with the site" in thought
    assert "broken" not in thought and "UX finding" not in thought
    desc = "Ta ścieżka jest zamknięta regułami badania — wracam i kontynuuję zadanie."
    assert desc in block
    assert not any(m in desc for m in attribution._FALLBACK_MARKERS)
