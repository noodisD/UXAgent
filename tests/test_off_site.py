"""Findings about pages outside the site under test must not reach the report.

Ground truth that forced this gate: in the diabetyk24.pl run the site team
verified (runs/2026-07-20_14-50-35_2277), persona Marek wandered off to doz.pl —
a competitor pharmacy — and 6 of his 9 findings, several feeding the report's
critical issues, describe doz.pl pages. Run 0e1f likewise audited gemini.pl.
Nothing compared a finding's URL to the target domain, so a diabetyk24 report
shipped criticals about other websites.

The URL compared is the trace-resolved one (evidence.py overwrites the LLM's
claimed url with the trace's), so the gate rides on ground truth, not on what
the model wrote.
"""
from src.simulated_web_agent.main.site_scope import (
    flag_off_site,
    registrable_domain,
    same_site,
)


# --- domain parsing ------------------------------------------------------

def test_registrable_domain_strips_www_and_subdomains():
    assert registrable_domain("https://www.diabetyk24.pl/products/x") == "diabetyk24.pl"
    assert registrable_domain("https://signin.dm.pl/auth/web-login") == "dm.pl"
    assert registrable_domain("https://doz.pl/koszyk") == "doz.pl"


def test_registrable_domain_handles_junk():
    assert registrable_domain("") == ""
    assert registrable_domain("not a url") == ""
    assert registrable_domain(None) == ""


# --- same_site -----------------------------------------------------------

def test_same_site_accepts_www_and_subdomain_variants():
    # dm's own login lives on signin.dm.pl — it is emphatically ON-site;
    # excluding it would delete the (team-relevant) forced-login findings.
    assert same_site("https://signin.dm.pl/auth", "https://www.dm.pl/") is True
    assert same_site("https://www.dm.pl/koszyk", "https://dm.pl/") is True


def test_same_site_rejects_other_domains():
    assert same_site("https://www.doz.pl/apteki", "https://diabetyk24.pl/") is False
    assert same_site("https://gemini.pl/x", "https://diabetyk24.pl/") is False


def test_same_site_fails_open_on_missing_url():
    # No URL (unverified citation) → cannot prove it's off-site; the
    # unverified path already caps such findings. Never exclude on missing data.
    assert same_site("", "https://diabetyk24.pl/") is True
    assert same_site(None, "https://diabetyk24.pl/") is True


# --- flag_off_site -------------------------------------------------------

def _f(url, **kw):
    return {"heuristic_id": 1, "severity": "poważny", "url": url,
            "evidence": "e", **kw}


def test_flags_competitor_findings():
    findings = [
        _f("https://www.doz.pl/koszyk/dostawa"),   # Marek's actual doz.pl claim
        _f("https://diabetyk24.pl/products/libre"),
    ]
    flag_off_site(findings, "https://diabetyk24.pl/")
    assert findings[0]["off_site"] is True
    assert findings[1]["off_site"] is False


def test_reflagging_clears_a_stale_flag():
    """Same idempotency contract as attribution: flags persist on disk, so a
    rule change must be able to clear them on re-run."""
    findings = [_f("https://diabetyk24.pl/x", off_site=True)]
    flag_off_site(findings, "https://diabetyk24.pl/")
    assert findings[0]["off_site"] is False


def test_non_dicts_pass_through():
    assert flag_off_site(["junk", None], "https://diabetyk24.pl/") == ["junk", None]


def test_missing_target_never_flags():
    findings = [_f("https://www.doz.pl/x")]
    flag_off_site(findings, "")
    assert findings[0]["off_site"] is False
