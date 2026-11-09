"""A PageSpeed failure is a failed measurement, said so — never a site defect.

The pepco run: both strategies answered "The read operation timed out", the
narrator put "przekroczenie czasu ładowania … w testach PageSpeed" at #2 of the
recommendations. Behind that, an answer with no Lighthouse result was stored as
a measurement (score None, printed "Performance None/100") and named nowhere as
unmeasured, and a single timeout or 429 cost the whole measurement.
"""
import json
import urllib.error

import pytest

from simulated_web_agent.main import pagespeed
from simulated_web_agent.main.pagespeed import (
    _extract_metrics, _fetch_strategy, format_pagespeed_summary, read_pagespeed,
    run_pagespeed_check,
)
from simulated_web_agent.main.reporter_html import pagespeed_error_text

GOOD = json.dumps({"lighthouseResult": {
    "categories": {"performance": {"score": 0.42}},
    "audits": {"speed-index": {"numericValue": 5000, "displayValue": "5,0 s"}}}}).encode()
NO_SCORE = json.dumps({"lighthouseResult": {"categories": {"performance": {}}, "audits": {}}}).encode()
TIMEOUT = TimeoutError("The read operation timed out")


class _Resp:
    def __init__(self, body):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._body


def _http_error(code):
    return urllib.error.HTTPError("https://example.test/", code, "x", {}, None)


@pytest.fixture
def api(monkeypatch):
    """urlopen answering from a script: each item is a body or an exception to
    raise; the last repeats. `api.calls` is how often the API was asked."""
    monkeypatch.setattr(pagespeed, "_RETRY_DELAY_S", 0)
    monkeypatch.setenv("GOOGLE_PAGESPEED_API_KEY", "test-key")

    class Api:
        def __init__(self):
            self.script, self.calls, self.by_strategy = [GOOD], 0, {}
            self.seen = {}

        def says(self, *script):
            self.script, self.calls, self.by_strategy, self.seen = list(script), 0, {}, {}

        def says_for(self, **by_strategy):
            self.by_strategy, self.calls, self.seen = {k: list(v) for k, v in by_strategy.items()}, 0, {}

    a = Api()

    def fake_urlopen(req, timeout=None):
        strategy = "mobile" if "strategy=mobile" in req.full_url else "desktop"
        script = a.by_strategy.get(strategy) or a.script
        n = a.seen[strategy] = a.seen.get(strategy, -1) + 1
        a.calls += 1
        item = script[min(n, len(script) - 1)]
        if isinstance(item, BaseException):
            raise item
        return _Resp(item)

    monkeypatch.setattr(pagespeed.urllib.request, "urlopen", fake_urlopen)
    return a


# --- an empty answer is an error ---------------------------------------------------

def test_an_answer_with_no_lighthouse_result_is_an_error_not_a_measurement():
    for body in ({}, {"lighthouseResult": {}}, [], {"lighthouseResult": None}):
        with pytest.raises(pagespeed.EmptyResult):
            _extract_metrics(body, "mobile")


def test_a_lighthouse_result_without_a_performance_score_is_an_error():
    with pytest.raises(pagespeed.EmptyResult):
        _extract_metrics(json.loads(NO_SCORE), "mobile")


@pytest.mark.asyncio
async def test_an_empty_body_is_stored_as_an_error_and_not_retried(api, tmp_path):
    api.says(b"{}")
    found = await run_pagespeed_check("https://x.pl/", tmp_path)
    for strategy in ("mobile", "desktop"):
        assert "performance_score" not in found[strategy]
        assert found[strategy]["error"] and found[strategy]["error_kind"] == "empty"
    assert api.calls == 2, "one call per strategy: an empty answer is not a transient fault"
    assert read_pagespeed(tmp_path)["mobile"]["error_kind"] == "empty"


# --- one retry, only for faults that are not ours --------------------------------

@pytest.mark.asyncio
async def test_a_timeout_then_success_succeeds_after_exactly_one_retry(api):
    api.says(TIMEOUT, GOOD)
    result = await _fetch_strategy("https://x.pl/", "mobile", "k")
    assert result["performance_score"] == 42
    assert api.calls == 2


@pytest.mark.asyncio
async def test_two_timeouts_are_an_error_of_kind_timeout(api, tmp_path):
    api.says(TIMEOUT)
    found = await run_pagespeed_check("https://x.pl/", tmp_path)
    assert api.calls == 4, "two strategies, each tried twice and no more"
    for strategy in ("mobile", "desktop"):
        assert found[strategy]["error_kind"] == "timeout"
        assert "timed out" in found[strategy]["error"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault,kind", [
    (_http_error(429), "quota"), (_http_error(503), "http_5xx"),
    (urllib.error.URLError(TimeoutError("connect timed out")), "timeout"),
    (urllib.error.URLError(OSError("getaddrinfo failed")), "network"),
    (ConnectionResetError("Connection reset by peer"), "network"),
])
async def test_transient_faults_are_retried_and_classified(api, tmp_path, fault, kind):
    api.says(fault)
    found = await run_pagespeed_check("https://x.pl/", tmp_path)
    assert api.calls == 4
    assert found["mobile"]["error_kind"] == kind


@pytest.mark.asyncio
async def test_a_rejected_request_is_not_retried(api, tmp_path):
    """A bad key answers 400 every time: retrying only spends quota."""
    for code in (400, 403):
        api.says(_http_error(code))
        found = await run_pagespeed_check("https://x.pl/", tmp_path)
        assert api.calls == 2, f"HTTP {code}: one call per strategy"
        assert found["mobile"]["error_kind"] == "other"


@pytest.mark.asyncio
async def test_the_retry_waits_the_module_delay(api, monkeypatch):
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(pagespeed, "_RETRY_DELAY_S", 7)
    monkeypatch.setattr(pagespeed.asyncio, "sleep", fake_sleep)
    api.says(TIMEOUT, GOOD)
    await _fetch_strategy("https://x.pl/", "mobile", "k")
    assert slept == [7]


@pytest.mark.asyncio
async def test_one_strategy_failing_does_not_cost_the_other(api, tmp_path):
    api.says_for(mobile=[TIMEOUT], desktop=[GOOD])
    found = await run_pagespeed_check("https://x.pl/", tmp_path)
    assert found["mobile"]["error_kind"] == "timeout"
    assert found["desktop"]["performance_score"] == 42
    assert api.calls == 3, "mobile twice, desktop once"


# --- what the narrator reads ---------------------------------------------------

@pytest.mark.parametrize("ps", [
    None,
    {"url": "https://x.pl/", "skipped": True, "reason": "no_api_key"},
    {"url": "https://x.pl/", "mobile": {"error": "The read operation timed out"},
     "desktop": {"error": "The read operation timed out"}},
    {"url": "https://x.pl/", "mobile": {"performance_score": None}, "desktop": {}},
])
def test_an_unmeasured_pagespeed_is_worded_the_way_the_prompt_rule_keys_on(ps):
    text = format_pagespeed_summary(ps)
    assert "pomiar niedostępny" in text and "nie wada strony" in text
    assert "Page speed data not available" not in text and "None/100" not in text


def test_a_measured_strategy_is_still_summarised():
    text = format_pagespeed_summary({"url": "x", "mobile": {"performance_score": 20, "performance_label": "poor"},
                                     "desktop": {"error": "x"}})
    assert "Mobile: Performance 20/100 [POOR]" in text
    assert "Desktop: pomiar niedostępny" in text


# --- what the report says --------------------------------------------------------

def test_an_archived_timeout_still_reads_as_a_timeout():
    """pagespeed.json files written before error_kind existed."""
    text = pagespeed_error_text({"error": "The read operation timed out"})
    assert "nie odpowiedziała w czasie" in text and "nie wada strony" in text


def test_the_kind_picks_the_sentence():
    assert "limit zapytań" in pagespeed_error_text({"error": "HTTP Error 429", "error_kind": "quota"})
    assert "błąd serwera" in pagespeed_error_text({"error": "HTTP Error 503", "error_kind": "http_5xx"})
    assert "bez wyniku" in pagespeed_error_text({"error": "x", "error_kind": "empty"})
    assert "bez wyniku" in pagespeed_error_text({}), "no score and no error text: an empty answer"
    assert "zwróciła błąd" in pagespeed_error_text({"error": "HTTP Error 400", "error_kind": "other"})
    # the kind wins over a misleading substring
    assert "limit zapytań" in pagespeed_error_text(
        {"error": "quota timed out", "error_kind": "quota"})
