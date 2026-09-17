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


# --- the scan must cover the width the journey used ------------------------

def test_the_scan_measures_the_desktop_width_too():
    """The heuristic findings come from the session viewport. Measuring only
    mobile and tablet meant the one width the findings came from was the one
    width never measured, and the two sections silently described different
    screens."""
    from simulated_web_agent.main.viewport_scan import VIEWPORTS

    labels = {v["label"] for v in VIEWPORTS}
    assert labels == {"mobile", "tablet", "desktop"}


def test_the_scanned_desktop_width_matches_the_session_profile():
    """If these drift apart the scan stops describing the screen the agent saw,
    which is the whole reason desktop was added."""
    from simulated_web_agent.config import load_cfg
    from simulated_web_agent.main.experiment import apply_device_profile
    from simulated_web_agent.main.viewport_scan import VIEWPORTS

    scanned = next(v for v in VIEWPORTS if v["label"] == "desktop")
    session = apply_device_profile(load_cfg(), "desktop").environment.browser.context_options
    assert scanned["width"] == session.viewport.width


# --- the report must not claim a device the run did not use ----------------

def test_the_caveat_names_the_device_the_sessions_actually_used():
    from simulated_web_agent.main.reporter_html import _session_device_phrase

    assert "telefon" in _session_device_phrase(
        {"viewport": {"session_devices": {"mobile": 3}}})
    assert "desktop" in _session_device_phrase(
        {"viewport": {"session_devices": {"desktop": 3}}})


def test_a_mixed_run_is_described_as_mixed():
    """"both" is the case an unchecked sentence gets wrong: half the findings
    come from a phone."""
    from simulated_web_agent.main.reporter_html import _session_device_phrase

    phrase = _session_device_phrase(
        {"viewport": {"session_devices": {"desktop": 2, "mobile": 2}}})
    assert "desktop" in phrase and "telefon" in phrase


def test_an_older_run_says_nothing_rather_than_guessing():
    """Runs made before the device was recorded cannot be reconstructed, and a
    confident wrong sentence about method is worse than a vague true one."""
    from simulated_web_agent.main.reporter_html import _session_device_phrase

    for stats in ({}, {"viewport": {}}, {"viewport": {"session_devices": {}}}):
        phrase = _session_device_phrase(stats)
        assert "desktop" not in phrase and "telefon" not in phrase
        assert phrase.strip()


def test_session_devices_is_counted_from_the_personas(tmp_path):
    from simulated_web_agent.main.reporter import aggregate_statistics
    from simulated_web_agent.main.trace_store import TraceStore

    stores = []
    for i, device in enumerate(["desktop", "mobile", "desktop"]):
        store = TraceStore(tmp_path / f"persona_{i}")
        store.write_persona_card({"name": f"P{i}", "device": device})
        stores.append(store)
    reports = [{"trace_dir": str(s.trace_dir)} for s in stores]

    stats = aggregate_statistics(reports, stores=stores)
    assert stats["viewport"]["session_devices"] == {"desktop": 2, "mobile": 1}
