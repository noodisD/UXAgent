"""Evidence must show the site, not our instrumentation.

The independent review found the first-impression personas praising "the car
in the blue frame" — our click highlight. The before-action hook, which takes
every evidence screenshot, ran inside ElementHighlight after the target had
been scrolled to the centre AND after two cyan outlines were drawn onto the
page. So every evidence frame wore our overlay, and the "5-second test" judged
step 0's frame, which sits 2090 px down the Hyundai configurator — wherever the
first click target was, not the screen a visitor lands on.

Verified on the live configurator: with the hook moved, the overlay is absent at
capture time.
"""
import inspect

from simulated_web_agent.executor.env import ElementHighlight
from simulated_web_agent.main.trace_store import TraceStore


def test_evidence_is_captured_before_the_overlay_is_drawn():
    src = inspect.getsource(ElementHighlight.__aenter__)
    capture = src.index("self._run_before_action_hook()")
    draw = src.index("document.highlightedElements = [outerDiv, innerDiv]")
    assert capture < draw, "the screenshot would carry our cyan outlines"


def test_the_hook_still_runs_for_actions_without_a_target():
    """scroll, back and goto have no element to highlight but still need their
    evidence recorded."""
    src = inspect.getsource(ElementHighlight.__aenter__)
    assert "if not hook_done:" in src


def test_first_impression_judges_the_landing_frame(tmp_path):
    store = TraceStore(tmp_path)
    (tmp_path / "screenshot").mkdir(exist_ok=True)
    store.screenshot_path(0).write_bytes(b"step0")
    assert store.first_impression_screenshot() == store.screenshot_path(0), \
        "older sessions have only step 0"
    store.landing_screenshot_path().write_bytes(b"landing")
    assert store.first_impression_screenshot() == store.landing_screenshot_path()


def test_the_landing_frame_is_taken_before_the_first_observation():
    from simulated_web_agent.main import experiment

    src = inspect.getsource(experiment)
    assert src.index("landing_screenshot_path()") < src.index("obs = await env.observation()")
