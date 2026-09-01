"""goto_url must tolerate model-emitted relative or scheme-less URLs —
Playwright rejects them with "Cannot navigate to invalid URL" (observed in
run 2026-07-09_11-29-52: goto_url('/collections/dom/swieczniki-i-lampiony'))."""

from simulated_web_agent.executor.env import resolve_url

BASE = "https://example-shop.pl/collections/dom/dekoracje-do-domu"


def test_absolute_url_unchanged():
    assert resolve_url("https://example.com/x", BASE) == "https://example.com/x"


def test_site_relative_path_resolved_against_base():
    assert (
        resolve_url("/collections/dom/swieczniki-i-lampiony", BASE)
        == "https://example-shop.pl/collections/dom/swieczniki-i-lampiony"
    )


def test_relative_path_resolved_against_base():
    assert (
        resolve_url("swieczniki-i-lampiony", BASE)
        == "https://example-shop.pl/collections/dom/swieczniki-i-lampiony"
    )


def test_schemeless_host_gets_https():
    assert resolve_url("example-shop.pl/collections/dom", BASE) == "https://example-shop.pl/collections/dom"


def test_empty_url_passthrough():
    assert resolve_url("", BASE) == ""
    assert resolve_url("   ", BASE) == ""
