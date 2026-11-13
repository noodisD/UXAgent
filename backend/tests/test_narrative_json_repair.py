"""The aggregate narrator's reply is repaired, not dropped, when it is slightly invalid JSON."""
import pytest

from simulated_web_agent.main.reporter import parse_narrative_json


def test_valid_json_is_parsed_as_is():
    assert parse_narrative_json('text {"a": 1, "b": [2]} tail') == {"a": 1, "b": [2]}


def test_a_single_quoted_key_is_repaired():
    # The shape that broke the 2026-11-13 run: "Expecting property name enclosed in double quotes".
    data = parse_narrative_json("{\n  \"executive_summary\": \"ok\",\n  'score_rationale': \"x\"\n}")
    assert data["executive_summary"] == "ok" and data["score_rationale"] == "x"


def test_a_trailing_comma_is_repaired():
    assert parse_narrative_json('{"a": 1, "b": 2,}') == {"a": 1, "b": 2}


@pytest.mark.parametrize("raw", ["", "no json here", "{}"])
def test_a_reply_without_a_usable_object_raises_so_the_caller_can_retry(raw):
    with pytest.raises(ValueError):
        parse_narrative_json(raw)
