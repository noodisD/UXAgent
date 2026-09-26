"""loop_detected means a loop, and backtracks are the persona's own.

loop_detected was "any harness-suspect step", so one "Element 'IONIQ 5' not
found" marked an ordinary configurator session as looping; backtracks counted
the backs the harness injects itself. On the archive: 14 → 12 of 21 sessions
flagged, clearing the two with no repeat of any kind.
"""
from simulated_web_agent.main.iso_metrics import _has_stuck_run, _injected_back


def test_three_identical_actions_are_a_loop():
    scroll = {"action": "scroll", "direction": "down", "amount": 3}
    assert _has_stuck_run([scroll, scroll, scroll])
    assert not _has_stuck_run([scroll, scroll, {"action": "click", "target": "a"}, scroll])


def test_the_same_scroll_in_two_directions_is_not_a_loop():
    down = {"action": "scroll", "direction": "down", "amount": 3}
    up = {"action": "scroll", "direction": "up", "amount": 3}
    assert not _has_stuck_run([down, up, down])


def test_our_own_backs_are_not_the_personas():
    assert _injected_back({"action": "back", "description": "Ten element nie działał mimo kilku prób — wracam i wybieram inną drogę."})
    assert _injected_back({"action": "back", "description": "Ta ścieżka jest zamknięta regułami badania — wracam i kontynuuję zadanie."})
    assert not _injected_back({"action": "back", "description": "Going back to the model list."})
