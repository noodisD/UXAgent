"""The gate protected the findings list and nothing else.

An adversarial read of the re-run reports found the same shape at every site:
gating works on the heuristic pipeline, and the suppressed claim reaches the
client anyway by a route with no gate on it.

lidl.pl was the worst case. Three findings carried the entire report — "the
page says 10 products and shows none", two of them critical — and all three
were ours. raw_html at the cited steps holds 18 and 22 price strings; the
simplified observation the agent saw holds none. The product grid never
reached it.

The claim verifier then confirmed all three. It re-reads the cited step's
observation, which is the same simplified text that produced the claim, so a
perception failure certifies itself: claim_verdict "supported", high
confidence, no gate, straight into critical_issues.

Measured over all 43 archived sessions: 3 findings out of 157 newly flagged —
exactly those three.
"""
import json

import pytest

from simulated_web_agent.main import attribution
from simulated_web_agent.main.trace_store import TraceStore

PRICES = " ".join(f"{n},99 zł" for n in range(10, 20))


def _store(tmp_path, *, raw, seen, actions=1):
    store = TraceStore(tmp_path)
    store.write_action_trace([json.dumps({"action": "click", "target": f"t{i}"})
                              for i in range(actions)])
    for i in range(actions):
        store.write_raw_html(i, raw)
        store.write_observation_step_text(i, seen)
    return store


# --- the signal -------------------------------------------------------------

def test_a_step_whose_prices_vanished_is_perception_loss(tmp_path):
    store = _store(tmp_path, raw=f"<html><body>{PRICES}</body></html>",
                   seen="Widzę nagłówki, filtry i karuzele kategorii.")
    assert attribution.perception_loss_steps(store) == {0}


def test_a_step_that_kept_its_prices_is_not(tmp_path):
    """The agent saw what the browser had; nothing to excuse."""
    store = _store(tmp_path, raw=f"<html><body>{PRICES}</body></html>",
                   seen="Lista produktów: Chleb 4,99 zł, Mleko 3,49 zł")
    assert attribution.perception_loss_steps(store) == set()


def test_a_page_that_never_had_prices_is_not(tmp_path):
    """A contact page is not a dropped product grid — this must not become a
    blanket excuse for every page without money on it."""
    store = _store(tmp_path, raw="<html><body>Kontakt i godziny otwarcia</body></html>",
                   seen="Strona kontaktowa")
    assert attribution.perception_loss_steps(store) == set()


def test_a_missing_raw_capture_is_not_treated_as_loss(tmp_path):
    store = TraceStore(tmp_path)
    store.write_action_trace([json.dumps({"action": "click", "target": "a"})])
    store.write_observation_step_text(0, "cokolwiek")
    assert attribution.perception_loss_steps(store) == set()


# --- what it does to findings ----------------------------------------------

def test_the_lidl_finding_is_gated(tmp_path):
    """The exact claim, verbatim from the run, with the verdict it arrived
    with: supported, high confidence, ungated."""
    from simulated_web_agent.main.severity import excluded_from_report

    store = _store(tmp_path, raw=f"<html><body>{PRICES}</body></html>",
                   seen="Nagłówki i filtry.", actions=2)
    findings = [{
        "heuristic_id": 1, "severity": "krytyczny", "step": 0,
        "claim_verdict": "supported", "confidence": "high",
        "evidence": 'Strona informuje, że znaleziono "10 produktów", jednak lista '
                    "produktów nie jest widoczna.",
        "recommendation": "Naprawić renderowanie listy produktów.",
    }]
    attribution.flag_harness_suspects(store, findings)
    assert findings[0]["harness_suspect"] is True
    assert excluded_from_report(findings[0]) is True


def test_an_unrelated_finding_on_the_same_step_survives(tmp_path):
    """A step that lost its product grid can still host a good finding about
    something else. Condemning the whole step would be over-reach."""
    store = _store(tmp_path, raw=f"<html><body>{PRICES}</body></html>",
                   seen="Nagłówki i filtry.", actions=2)
    findings = [{
        "heuristic_id": 4, "severity": "drobny", "step": 0,
        "evidence": "Etykieta filtra używa żargonu 'SKU', niezrozumiałego dla klienta.",
        "recommendation": "Zmienić nazwę filtra.",
    }]
    attribution.flag_harness_suspects(store, findings)
    assert findings[0]["harness_suspect"] is False


# --- gated claims restated in free text ------------------------------------

def test_a_reflection_repeating_a_gated_finding_is_caught():
    """hyundai: the finding was gated "unsupported" and the persona's own
    blocker asserted it anyway, rendering under "Co przeszkadzało"."""
    gated = [{
        "heuristic_id": 9, "severity": "poważny", "claim_verdict": "unsupported",
        "evidence": "Opis akcji wskazuje, że przycisk 'Następny krok' był niedostępny.",
    }]
    assert attribution.restates_a_gated_finding(
        ["Przycisk 'Następny krok' był nieaktywny bez komunikatu."], gated) is True


def test_a_reflection_about_something_else_is_left_alone():
    gated = [{
        "heuristic_id": 9, "claim_verdict": "unsupported",
        "evidence": "przycisk 'Następny krok' był niedostępny",
    }]
    assert attribution.restates_a_gated_finding(
        ["Cena dostawy pojawia się dopiero na ostatnim kroku."], gated) is False


def test_a_reflection_repeating_a_CONFIRMED_finding_is_left_alone():
    """Only suppressed claims are the problem. A reflection echoing a finding
    that passed the gate is corroboration, not leakage."""
    confirmed = [{
        "heuristic_id": 1, "severity": "krytyczny", "claim_verdict": "supported",
        "evidence": "przycisk 'Dodaj do koszyka' nie reaguje",
    }]
    assert attribution.restates_a_gated_finding(
        ["Przycisk 'Dodaj do koszyka' nie reagował."], confirmed) is False


def test_empty_inputs_are_safe():
    assert attribution.restates_a_gated_finding([], []) is False
    assert attribution.restates_a_gated_finding(None, None) is False
    assert attribution.restates_a_gated_finding([""], [{"evidence": ""}]) is False


# --- and the reader has to be told -----------------------------------------

def test_a_flagged_reflection_renders_with_a_caveat():
    """Flagging it in JSON and printing it as plain fact is the same bug."""
    from simulated_web_agent.main.reporter_html import _render_objectives

    html = _render_objectives({"objective_reflections": [{
        "persona": "Natalia", "objective": "Skonfiguruj auto", "outcome": "partial",
        "summary": "Proces był frustrujący.",
        "blockers": ["Przycisk 'Następny krok' był nieaktywny."],
        "wins": [], "priority_fix": "Naprawić przycisk.",
        "self_reported_only": True,
    }]})
    assert "niezweryfikowana" in html
    assert "ograniczeń narzędzia" in html


def test_an_unflagged_reflection_renders_clean():
    from simulated_web_agent.main.reporter_html import _render_objectives

    html = _render_objectives({"objective_reflections": [{
        "persona": "Zofia", "objective": "Kup produkt", "outcome": "success",
        "summary": "Udało się.", "blockers": [], "wins": ["Szybka wyszukiwarka"],
        "priority_fix": "", "self_reported_only": False,
    }]})
    assert "niezweryfikowana" not in html


# --- a link the server refused to US is not a broken link -------------------

def test_statuses_that_mean_we_were_refused_are_not_broken():
    """The hyundai re-run reported the client's own homepage as broken and told
    them to fix it. hyundai.com/pl/pl.html answers 403 to an automated request
    and serves the page to a person. A UX specialist checks a broken-link claim
    by clicking it, so a false one costs more than a missed real one."""
    from simulated_web_agent.main.link_checker import (
        _OK_STATUSES,
        _REFUSED_US_STATUSES,
    )

    for status in (401, 403, 405, 429):
        assert status in _REFUSED_US_STATUSES

    for status in (404, 410, 500, 502, 503):
        assert status not in _REFUSED_US_STATUSES and status not in _OK_STATUSES, \
            "a genuinely dead link must still be reported"


def test_the_two_status_sets_do_not_overlap():
    from simulated_web_agent.main.link_checker import (
        _OK_STATUSES,
        _REFUSED_US_STATUSES,
    )

    assert not (_OK_STATUSES & _REFUSED_US_STATUSES)


# --- pressing "Next" again is the task, not a loop --------------------------

def test_the_revisit_guard_leaves_a_wizard_next_button_alone():
    """The hyundai configurator is a step wizard, so 'Następny krok' is the
    correct action on every step. The guard fired on its third use with "Do not
    open it again", the agent navigated around it, and the report told the
    client the button was inactive and needed a workaround — a claim the
    reviewer would falsify in one click, exactly as she did the cookie banner.

    'nast_pny_krok' is the real semantic id from the run: the parser mangles
    Polish diacritics, so the match has to survive that."""
    from simulated_web_agent.main.experiment import _revisit_warning

    assert _revisit_warning(4, "nast_pny_krok") == (None, None)
    assert _revisit_warning(9, "pokaz_wiecej") == (None, None)
    assert _revisit_warning(4, "continue_button") == (None, None)


def test_the_guard_still_catches_a_real_revisit_loop():
    """Its reason for existing: A-B-A-B product loops wasted ~20% of steps."""
    from simulated_web_agent.main.experiment import _revisit_warning

    thought, error = _revisit_warning(4, "produkt_ioniq_5")
    assert thought and error and "times this session" in error


@pytest.mark.parametrize("target", ["koszyk", "menu", "filtry", "zaloguj", ""])
def test_ordinary_controls_are_not_mistaken_for_progression(target):
    from simulated_web_agent.main.experiment import _is_progression_control

    assert _is_progression_control(target) is False
