"""A claim about the result of an action nobody saw the result of is not a finding.

2026-07-09 pepco.pl run, Filip: the session crashed on an API rate limit right after
he clicked 'Wyszukaj sklep'. The store-locator page was captured (28 observations
for 27 actions) but never perceived: only 27 observation texts were written. Yet a
heuristic finding said the site gave "no feedback after clicking Wyszukaj sklep",
his reflection listed the same as his blocker, and his survey said the function
failed. Nothing in the trace shows what that page said to a person.
"""
import json

import pytest

from simulated_web_agent.main import attribution
from simulated_web_agent.main.reporter import _gate_reason, _human_check, aggregate_statistics
from simulated_web_agent.main.severity import excluded_from_report, severity_score
from simulated_web_agent.main.trace_store import TraceStore

WYSZUKAJ = ("Click on the 'Wyszukaj sklep' button on the product detail page to check "
            "in-store availability.")

# Verbatim from the archived run's heuristic_findings.json.
FILIP_EVIDENCE = (
    "Użytkownik kliknął przycisk 'Wyszukaj sklep' na stronie produktu (krok 26) w celu "
    "sprawdzenia dostępności w sklepie stacjonarnym. Brak jest jednak w obserwacji kroku 27 "
    "bezpośredniej informacji zwrotnej o dostępności tego konkretnego produktu w sklepach, co "
    "wskazuje na to, że system nie informuje jasno o wyniku tej kluczowej dla użytkownika akcji.")
FILIP_FIX = ("Wdrożyć mechanizm wyświetlania statusu dostępności produktu w pobliskich sklepach "
             "stacjonarnych bezpośrednio na stronie produktu lub w wynikach lokalizatora sklepów.")

FILIP_BLOCKERS = [
    "Brak informacji zwrotnej o dostępności produktu w sklepie stacjonarnym po kliknięciu 'Wyszukaj sklep'.",
    "Brak możliwości oceny fizycznych cech produktów (faktura, kolor, design) online.",
    "Nieefektywny proces wyszukiwania i porównywania produktów, wymagający ciągłego przełączania się między stronami.",
]


def _filip(tmp_path, n=27, name="filip", errors=None):
    """The shape of the archived run: n actions, n+1 observations, texts 0..n-1.

    The last action is the click on 'Wyszukaj sklep'; observation n is its result,
    recorded and never perceived. errors: {observation index: executor error}."""
    store = TraceStore(tmp_path / name)
    actions = [{"action": "click", "target": f"produkt_{i}", "description": f"Clicking on product {i}"}
               for i in range(n - 1)]
    actions.append({"action": "click", "target": "wyszukaj_sklep", "description": WYSZUKAJ})
    store.write_action_trace([json.dumps(a) for a in actions])
    for i in range(n + 1):
        store.append_observation({"tabs": [{"url": f"https://pepco.pl/p/{i}", "is_active": True}],
                                  "error": (errors or {}).get(i)})
    for i in range(n):
        store.write_observation_step_text(i, f"Strona produktu {i}. Cena 20 zł. Opis i skład.")
    store.write_session_result({"steps_taken": n, "max_steps": 30, "terminated": False,
                                "ended_reason": "error",
                                "error": "RateLimitError: RESOURCE_EXHAUSTED"})
    return store


def _finding(step, evidence, **extra):
    return {"heuristic_id": 1, "severity": "poważny", "step": step, "evidence": evidence,
            "recommendation": "Dodać wskaźnik.", **extra}


# --- which steps were never perceived ---------------------------------------------------

def test_the_filip_shape_has_one_unperceived_step(tmp_path):
    assert attribution.unperceived_steps(_filip(tmp_path)) == [27]


def test_a_clean_session_has_no_unperceived_steps(tmp_path):
    store = TraceStore(tmp_path / "clean")
    store.write_action_trace([json.dumps({"action": "click", "target": "a"})] * 2)
    for i in range(2):
        store.append_observation({"tabs": [], "error": None})
        store.write_observation_step_text(i, f"Strona {i}")
    assert attribution.unperceived_steps(store) == []


def test_a_blank_but_written_observation_counts_as_unperceived(tmp_path):
    store = _filip(tmp_path, n=4)
    store.write_observation_step_text(4, "  \n ")
    assert attribution.unperceived_steps(store) == [4]


def test_a_blank_page_in_the_middle_is_not_a_cut_session(tmp_path):
    """The participant acted on it: that is the perceiver's gap, which has its own signature."""
    store = _filip(tmp_path, n=4)
    store.write_observation_step_text(2, "")
    assert attribution.unperceived_steps(store) == [4]
    ok = TraceStore(tmp_path / "ok")
    ok.write_action_trace([json.dumps({"action": "click", "target": "a"})] * 3)
    for i in range(3):
        ok.append_observation({"tabs": [], "error": None})
        ok.write_observation_step_text(i, "" if i == 1 else "Strona")
    assert attribution.unperceived_steps(ok) == []


# --- findings ----------------------------------------------------------------------------

def test_a_finding_on_the_unperceived_step_is_held(tmp_path):
    store = _filip(tmp_path)
    findings = [_finding(27, "Strona lokalizatora sklepów jest pusta i niczego nie pokazuje.")]
    attribution.flag_harness_suspects(store, findings)
    f = findings[0]
    assert f["harness_suspect"] and f["requires_human_check"] and excluded_from_report(f)
    assert f["harness_cause"] == {"code": "outcome_unseen", "detail": "Wyszukaj sklep",
                                  "step": 27, "action": "click"}


def test_the_filip_finding_about_wyszukaj_sklep_is_held_as_outcome_unseen(tmp_path):
    store = _filip(tmp_path)
    findings = [_finding(26, FILIP_EVIDENCE, recommendation=FILIP_FIX)]
    attribution.flag_harness_suspects(store, findings)
    f = findings[0]
    assert f["harness_suspect"] is True
    assert f["harness_cause"]["code"] == "outcome_unseen" and f["harness_cause"]["step"] == 27
    assert severity_score(f) == 0
    reason = _gate_reason(f)
    assert "sesja urwała się" in reason and "po akcji w kroku 26" in reason
    assert "usterka" not in reason and "awari" not in reason  # no claim that the SITE or the tool broke
    check = _human_check(f)
    assert "Kliknij „Wyszukaj sklep”" in check and "uczestnik tego nie zobaczył" in check


@pytest.mark.parametrize("claim", [
    "Po kliknięciu 'Wyszukaj sklep' strona nie pokazuje żadnej reakcji.",
    "Brak informacji zwrotnej o wyniku wyszukiwania sklepu.",
    "Po wpisaniu miasta nie pojawia się lista sklepów (krok 27).",
    # no verb and no absence phrase: only the citation of the page nobody read
    "W obserwacji kroku 27 lokalizator sklepów nie zawiera listy placówek.",
])
def test_a_claim_about_what_the_last_action_did_is_held(tmp_path, claim):
    store = _filip(tmp_path)
    findings = [_finding(26, claim)]
    attribution.flag_harness_suspects(store, findings)
    assert findings[0]["harness_cause"]["code"] == "outcome_unseen"


def test_the_same_remark_without_the_citation_is_about_the_page_that_was_read(tmp_path):
    store = _filip(tmp_path)
    findings = [_finding(26, "Strona produktu nie zawiera listy placówek.")]
    attribution.flag_harness_suspects(store, findings)
    assert not findings[0]["harness_suspect"]


def test_a_page_copy_finding_on_the_last_perceived_step_survives(tmp_path):
    """Per claim, not per step: the participant did read the page of step 26."""
    store = _filip(tmp_path)
    findings = [_finding(26, "Strona produktu nie podaje składu materiału ani wymiarów lampki.")]
    attribution.flag_harness_suspects(store, findings)
    f = findings[0]
    assert not f["harness_suspect"] and not excluded_from_report(f) and f["harness_cause"] is None


def test_a_clean_session_holds_nothing_for_a_claim_about_the_last_action(tmp_path):
    store = TraceStore(tmp_path / "clean")
    store.write_action_trace([json.dumps({"action": "click", "target": "a", "description": "x"})] * 2)
    for i in range(2):
        store.append_observation({"tabs": [], "error": None})
        store.write_observation_step_text(i, f"Strona {i}")
    findings = [_finding(1, "Po kliknięciu przycisk nie reaguje i brak informacji zwrotnej.")]
    attribution.flag_harness_suspects(store, findings)
    assert not findings[0]["harness_suspect"]


def test_an_outcome_finding_is_not_also_blamed_on_repeated_clicks(tmp_path):
    """Repeated-with-progress would otherwise name the wrong reason in the report."""
    store = _filip(tmp_path)
    store.write_action_trace([json.dumps({"action": "click", "target": "wyszukaj_sklep",
                                          "description": WYSZUKAJ})] * 27)
    findings = [_finding(26, FILIP_EVIDENCE)]
    attribution.flag_harness_suspects(store, findings)
    assert findings[0]["harness_cause"]["code"] == "outcome_unseen"
    assert not findings[0]["repeated_with_progress"]


def test_flags_are_rewritten_when_the_session_is_not_cut(tmp_path):
    """Idempotent like the rest of the gate: no stale outcome_unseen once the text exists."""
    store = _filip(tmp_path)
    findings = [_finding(26, FILIP_EVIDENCE)]
    attribution.flag_harness_suspects(store, findings)
    assert findings[0]["harness_suspect"]
    store.write_observation_step_text(27, "Lokalizator sklepów. Wpisz miasto.")
    attribution.flag_harness_suspects(store, findings)
    assert not findings[0]["harness_suspect"] and findings[0]["harness_cause"] is None


# --- the report ---------------------------------------------------------------------------

def _report_for(store, findings):
    return {"trace_dir": str(store.trace_dir), "persona": "Persona: Filip", "intent": "Sprawdź sklep.",
            "first_impression": {}, "heuristic_findings": findings,
            "accessibility": {"findings": []}, "survey_result": {"answers": []},
            "session_result": store.read_session_result()}


def test_the_held_finding_is_left_out_of_the_counts_and_listed_for_verification(tmp_path):
    store = _filip(tmp_path)
    findings = [_finding(26, FILIP_EVIDENCE, recommendation=FILIP_FIX),
                _finding(5, "Strona produktu nie podaje składu materiału.", heuristic_id=2)]
    attribution.flag_harness_suspects(store, findings)
    heuristic = aggregate_statistics([_report_for(store, findings)], stores=[store])["heuristic"]
    counts = heuristic["severity_counts"]
    assert counts["poważny"] == 1 and counts["needs_verification"] == 1
    (item,) = heuristic["needs_verification_items"]
    assert "sesja urwała się" in item["reason"]
    assert "Kliknij „Wyszukaj sklep”" in item["check"]


# --- the participant's own account on a crashed session ------------------------------------------

def _with_reflection(store, blockers=None, fix=FILIP_FIX):
    store.write_objective_reflection({
        "outcome": "failed", "summary": "Filip nie osiągnął celu.",
        "blockers": blockers if blockers is not None else FILIP_BLOCKERS,
        "wins": ["Szybka nawigacja po kategoriach."], "priorityFix": fix})
    return store


def test_crashed_session_blanks_priority_fix_and_blockers_naming_the_last_control(tmp_path):
    store = _with_reflection(_filip(tmp_path))
    (row,) = aggregate_statistics([_report_for(store, [])], stores=[store])["objective_reflections"]
    assert row["session_error"] == {"steps": 27}
    assert row["priority_fix"] == "" and row["summary"] == ""
    # the blocker about 'Wyszukaj sklep' goes; what he saw before the crash stands
    assert row["blockers"] == FILIP_BLOCKERS[1:]
    assert row["wins"] == ["Szybka nawigacja po kategoriach."]


def test_a_crash_leaves_a_fix_that_names_nothing_blanked_too(tmp_path):
    store = _with_reflection(_filip(tmp_path), blockers=["Brak opisu materiału."],
                             fix="Dodać skład materiału na karcie produktu.")
    (row,) = aggregate_statistics([_report_for(store, [])], stores=[store])["objective_reflections"]
    assert row["priority_fix"] == "" and row["blockers"] == ["Brak opisu materiału."]


def test_a_session_that_ended_normally_keeps_its_blocker_about_the_last_control(tmp_path):
    store = _with_reflection(_filip(tmp_path))
    store.write_session_result({"steps_taken": 27, "max_steps": 30, "terminated": True,
                                "ended_reason": "terminated"})
    (row,) = aggregate_statistics([_report_for(store, [])], stores=[store])["objective_reflections"]
    assert "session_error" not in row
    assert row["blockers"] == FILIP_BLOCKERS and row["priority_fix"] == FILIP_FIX


@pytest.mark.parametrize("text, expected", [
    ("Brak informacji zwrotnej po kliknięciu 'Wyszukaj sklep'.", True),    # the label
    ("Wyszukiwanie produktów trwało zbyt długo.", False),                   # one stem only
    ("Funkcja wyszukiwania sklepu nie pokazała wyniku.", True),             # two stems of the id
    ("Brak możliwości oceny fizycznych cech produktów online.", False),
])
def test_what_counts_as_naming_the_last_control(tmp_path, text, expected):
    assert attribution.names_last_executed_control(_filip(tmp_path), text) is expected


def test_a_harness_back_names_no_control_of_the_participants(tmp_path):
    from simulated_web_agent.main.loop_phrases import KIND_LOOP_BREAKER, injected_action
    store = _filip(tmp_path)
    actions = store.read_action_trace()
    actions[-1] = injected_action(KIND_LOOP_BREAKER, json.loads(actions[-1]))
    store.write_action_trace(actions)
    assert attribution.last_executed_control(store) == {}
    assert not attribution.names_last_executed_control(store, "po kliknięciu 'Wyszukaj sklep'")
