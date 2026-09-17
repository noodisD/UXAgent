"""Every session used to run at one desktop-sized window, which quietly made the
whole heuristic layer a desktop report — the only mobile evidence was a
technical scan of page layout, not a journey. The reviewer asked whether the
analysis could be split by device.

"both" alternates personas rather than running each one twice. Doubling the
sessions would double an already long audit for the same number of
participants; alternating splits the panel instead, so the comparison is free.
"""
import pytest

from simulated_web_agent.config import load_cfg
from simulated_web_agent.main.experiment import (
    DEVICE_CHOICES,
    apply_device_profile,
    devices_for_run,
)


# --- who browses on what ---------------------------------------------------

@pytest.mark.parametrize("choice", ["desktop", "mobile"])
def test_a_single_device_applies_to_everyone(choice):
    assert devices_for_run(choice, 3) == [choice] * 3


def test_both_alternates_instead_of_doubling():
    """The whole point: 4 personas stay 4 sessions, split two and two."""
    assigned = devices_for_run("both", 4)
    assert len(assigned) == 4, "must not add sessions"
    assert assigned.count("desktop") == 2 and assigned.count("mobile") == 2


def test_an_odd_panel_gives_the_extra_session_to_desktop():
    """Desktop is what the rest of the pipeline is anchored to."""
    assigned = devices_for_run("both", 3)
    assert assigned.count("desktop") == 2 and assigned.count("mobile") == 1


@pytest.mark.parametrize("bad", ["", None, "nonsense", "DESKTOP ", "tablet"])
def test_an_unrecognised_choice_falls_back_to_desktop(bad):
    """This arrives from a request payload; it must never raise or silently
    produce an empty assignment that drops the run."""
    out = devices_for_run(bad, 2)
    assert out == ["desktop", "desktop"] or out == ["desktop"] * 2


def test_no_personas_is_not_an_error():
    assert devices_for_run("both", 0) == []


def test_the_choices_are_the_ones_the_wizard_offers():
    assert set(DEVICE_CHOICES) == {"desktop", "mobile", "both"}


# --- what each profile does to the browser --------------------------------

def test_mobile_gets_a_phone_viewport_and_a_phone_identity():
    """Layout alone is not enough: sites serve different markup, different
    consent flows and sometimes an app interstitial to a phone UA."""
    cfg = apply_device_profile(load_cfg(), "mobile")
    ctx = cfg.environment.browser.context_options
    assert ctx.viewport.width < 500
    assert "iPhone" in str(ctx.user_agent)
    assert ctx.is_mobile is True and ctx.has_touch is True


def test_desktop_gets_a_desktop_viewport_and_no_touch():
    cfg = apply_device_profile(load_cfg(), "desktop")
    ctx = cfg.environment.browser.context_options
    assert ctx.viewport.width >= 1024
    assert not ctx.get("is_mobile", False)


def test_the_profile_is_applied_to_a_copy():
    """Personas run concurrently off one loaded config — mutating it in place
    would have them racing to set each other's viewport."""
    base = load_cfg()
    before = base.environment.browser.context_options.viewport.width
    apply_device_profile(base, "mobile")
    assert base.environment.browser.context_options.viewport.width == before


def test_two_profiles_do_not_leak_into_each_other():
    base = load_cfg()
    mobile = apply_device_profile(base, "mobile")
    desktop = apply_device_profile(base, "desktop")
    assert mobile.environment.browser.context_options.viewport.width < \
        desktop.environment.browser.context_options.viewport.width


def test_an_unknown_profile_is_treated_as_desktop():
    cfg = apply_device_profile(load_cfg(), "smartwatch")
    assert cfg.environment.browser.context_options.viewport.width >= 1024
