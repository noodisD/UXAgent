"""Count problems, not repetitions of one problem.

The independent review: "na urządzeniach mobilnych zidentyfikowano 73
przypadki niewystarczającego kontrastu" was the same six footer links —
"Kontakt", "Regulamin", "Polityka prywatności"… — at the same 3.82:1 on every
page, summed across pages and personas. One footer colour. And every
tap-target example was a 1×1 px screen-reader-only element ("Skip to main
content"), which nobody taps.

Re-scanned live on the Hyundai configurator: mobile contrast 6 distinct (was
reported as 73), tap targets below 24 px 1 (was 30).
"""
from simulated_web_agent.main.reporter import aggregate_statistics
from simulated_web_agent.main.viewport_scan import _CONTRAST_JS, _TAP_TARGET_JS


FOOTER = ["Kontakt|3.82", "Regulamin|3.82", "Polityka prywatności|3.82"]


def _page(url, contrast_keys, tap_keys=(), viewport="mobile"):
    return {"url": url, "viewport": viewport, "error": None, "horizontal_overflow": False,
            "contrast": {"below_threshold": len(contrast_keys), "keys": list(contrast_keys)},
            "tap_targets": {"below_24px": len(tap_keys), "keys": list(tap_keys)}}


def _mobile(stats):
    return next(d for d in stats["viewport"]["devices"] if d["viewport"] == "mobile")


def test_the_same_footer_on_every_page_is_one_set_of_failures():
    pages = [_page(f"https://x.pl/{i}", FOOTER) for i in range(5)]
    stats = aggregate_statistics([{"viewport_findings": pages}])
    assert _mobile(stats)["contrast_failures"] == 3, "not 15"


def test_repetition_across_personas_is_not_counted_either():
    reports = [{"viewport_findings": [_page(f"https://x.pl/{i}", FOOTER)]} for i in range(3)]
    assert _mobile(aggregate_statistics(reports))["contrast_failures"] == 3


def test_genuinely_different_failures_all_count():
    pages = [_page("https://x.pl/a", ["A|3.1"]), _page("https://x.pl/b", ["B|2.9", "C|4.0"])]
    assert _mobile(aggregate_statistics([{"viewport_findings": pages}]))["contrast_failures"] == 3


def test_older_scans_without_keys_cannot_overstate():
    """Counted by the worst page rather than summed."""
    old = [{"url": f"https://x.pl/{i}", "viewport": "mobile", "error": None,
            "contrast": {"below_threshold": 6}, "tap_targets": {"below_24px": 2}} for i in range(4)]
    d = _mobile(aggregate_statistics([{"viewport_findings": old}]))
    assert d["contrast_failures"] == 6 and d["tap_targets_below_min"] == 2


def test_working_sets_do_not_leak_into_the_report():
    stats = aggregate_statistics([{"viewport_findings": [_page("https://x.pl/", FOOTER)]}])
    assert not any(k.startswith("_") for k in _mobile(stats))


def test_the_scan_skips_visually_hidden_targets_and_records_keys():
    assert "r.width <= 2 || r.height <= 2" in _TAP_TARGET_JS
    assert "keys" in _TAP_TARGET_JS and "keys" in _CONTRAST_JS
