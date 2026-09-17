"""Findings are gated; free text was not.

Re-running the first external review's hyundai.com.pl case, the gate worked
exactly as designed — the "'Next step' button did not work reliably" finding
was flagged harness_suspect and dropped from every count. The aggregate pass
then wrote it back as the report's number-one CRITICAL issue, because the same
complaint reached it by a route with no gate on it: the persona's own survey
answer and objective reflection.

One of those answers quoted this tool's internal field name:

    "…co skutkowało wielokrotnymi błędami 'last_action_error' podczas klikania
    'Następny krok'."

Two separate failures, tested separately below: shipping our internals to a
client, and promoting unverified self-report to a headline defect.
"""
import pytest

from simulated_web_agent.main.redaction import (
    clean,
    mentions_harness_fault,
    redact_internals,
)


# --- our vocabulary must not reach the reader ------------------------------

@pytest.mark.parametrize("raw", [
    "wielokrotnymi błędami 'last_action_error' podczas klikania",
    'wielokrotnymi błędami "last_action_error" podczas klikania',
    "wielokrotnymi błędami last_action_error podczas klikania",
])
def test_the_internal_field_name_never_survives(raw):
    out = redact_internals(raw)
    assert "last_action_error" not in out
    assert "błąd interakcji" in out


def test_executor_prefixes_are_stripped_but_the_complaint_survives():
    """Redaction removes the machine string, never the substance — deleting the
    sentence would be suppression, which is the opposite failure."""
    out = redact_internals(
        "Error executing action: przycisk 'Dalej' nie zareagował na kliknięcie."
    )
    assert "Error executing action" not in out
    assert "przycisk 'Dalej' nie zareagował" in out


def test_playwright_internals_become_plain_language():
    out = redact_internals("Locator.click: Timeout 10000ms exceeded")
    assert "Locator" not in out and "10000ms" not in out
    assert "przekroczono czas oczekiwania" in out


def test_a_network_error_code_is_named_in_words():
    assert "net::" not in redact_internals("Strona nie wczytała się: net::ERR_CERT_AUTHORITY_INVALID")


def test_ordinary_polish_prose_is_left_alone():
    """The redactor runs over every survey answer in every report; it must not
    quietly rewrite normal sentences."""
    text = ("Największą trudnością było to, że nie mogłam znaleźć ceny dostawy "
            "przed dodaniem produktu do koszyka.")
    assert redact_internals(text) == text


def test_clean_walks_nested_structures_without_changing_their_shape():
    node = {"summary": "błąd last_action_error", "blockers": ["Locator.click: nic się nie stało"],
            "steps": 4, "ok": True, "nested": {"wins": ["Wyszukiwarka działała"]}}
    out = clean(node)
    assert set(out) == set(node)
    assert out["steps"] == 4 and out["ok"] is True
    assert "last_action_error" not in out["summary"]
    assert "Locator" not in out["blockers"][0]
    assert out["nested"]["wins"] == ["Wyszukiwarka działała"]


def test_empty_and_non_string_values_pass_through():
    assert redact_internals("") == ""
    assert clean(None) is None
    assert clean(7) == 7


# --- and it must be flagged as unconfirmed, not just cleaned ---------------

def test_a_complaint_about_our_own_failure_is_flagged():
    assert mentions_harness_fault(
        "system zwracał last_action_error przy każdej próbie") is True
    assert mentions_harness_fault(
        "'IONIQ 5' not being a clickable element") is True


def test_a_real_usability_complaint_is_not_flagged():
    """Over-flagging here would demote genuine findings to "self-reported", so
    this direction matters as much as the other."""
    assert mentions_harness_fault(
        "Cena dostawy pojawia się dopiero na ostatnim kroku, co zaskakuje.") is False
    assert mentions_harness_fault("") is False
    assert mentions_harness_fault(None) is False
