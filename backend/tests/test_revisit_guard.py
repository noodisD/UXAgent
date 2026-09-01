"""The consecutive-repeat guard misses A-B-A-B loops (open product, go back,
open the same product again). _revisit_warning provides advisory memory
thoughts on the 2nd click of a target and error-channel escalation from the
3rd, without hard-blocking legitimate repeats (navigation, "load more")."""

import json

from simulated_web_agent.main.experiment import _revisit_warning
from simulated_web_agent.main.trace_store import parse_action


def test_first_click_no_warning():
    thought, error = _revisit_warning(0, "poduszka_dekoracyjna")
    assert thought is None
    assert error is None


def test_second_click_gets_thought_only():
    thought, error = _revisit_warning(1, "poduszka_dekoracyjna")
    assert thought is not None
    assert "poduszka_dekoracyjna" in thought
    assert error is None


def test_third_click_escalates_to_error():
    thought, error = _revisit_warning(2, "poduszka_dekoracyjna")
    assert thought is not None
    assert error is not None
    assert "poduszka_dekoracyjna" in error


def test_parse_action_json_string():
    a = parse_action(json.dumps({"action": "click", "target": "x"}))
    assert a == {"action": "click", "target": "x"}


def test_parse_action_list_wrapped():
    a = parse_action(json.dumps([{"action": "click", "target": "x"}]))
    assert a["target"] == "x"


def test_parse_action_garbage():
    assert parse_action("not json") == {"description": "not json"}
