"""LLM JSON responses must be repaired, never fatal.

`_parse_json_response` sits on the agent's perceive/plan/act path via
async_chat(json_mode=True). A raise there killed the whole persona session — the
recurring "błąd sesji". Grounded in example.pl run 2026-07-20_16-43-51: flash-lite
transcribed a page and emitted JSON with an unescaped quote inside a value,
`json.loads` raised "Expecting ',' delimiter", and the session died at step 12.
These pin that such responses are salvaged into valid JSON.
"""
import json

from simulated_web_agent.agent.gpt import _parse_json_response


def _roundtrips(s: str) -> dict:
    """The helper's contract: its return value is always json.loads-able."""
    return json.loads(_parse_json_response(s))


def test_clean_json_passes_through():
    assert _roundtrips('{"observations": ["the page shows a search box"]}') == {
        "observations": ["the page shows a search box"]
    }


def test_json_with_prose_around_it_is_extracted():
    raw = 'Here is the result:\n{"a": 1, "b": 2}\nHope that helps.'
    assert _roundtrips(raw) == {"a": 1, "b": 2}


def test_unescaped_inner_quote_is_repaired():
    """The actual example.pl failure: a transcribed value contains a bare quote, so
    the string ends early and the parser wants a comma at that char."""
    broken = '{"observations": ["product named "FreeStyle Libre 2" sensor"]}'
    out = _roundtrips(broken)
    assert "observations" in out and isinstance(out["observations"], list)


def test_trailing_comma_is_repaired():
    assert _roundtrips('{"a": 1, "b": 2,}') == {"a": 1, "b": 2}


def test_unterminated_json_is_repaired():
    """Truncated output (a long transcription cut mid-object) still yields a
    usable object rather than a crash."""
    out = _roundtrips('{"observations": ["a long description that got cut off')
    assert isinstance(out, dict)


def test_result_is_always_a_string():
    # async_chat returns the JSON *string*; callers json.loads() it.
    assert isinstance(_parse_json_response('{"x": 1}'), str)
