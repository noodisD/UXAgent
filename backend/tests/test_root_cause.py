"""Cross-heuristic dedup: the same underlying defect filed under different
Nielsen heuristics must collapse to one root_cause_id — but ONLY when the
evidence is a confident, specific match. Ground truth: the 20-persona example.pl
run filed "search doesn't refresh results for a new query" as 6 separate
krytyczny findings across H1/H2/H3/H4/H5/H9. Precision matters more than
recall here — a false cluster silently merges two different real bugs.
"""
from simulated_web_agent.main.root_cause import assign_root_causes

# Paraphrased from the real run's kept findings (evidence field), one per
# heuristic it was actually filed under.
_REAL_EVIDENCE = {
    1: "Pole wyszukiwania pokazuje aktualne zapytanie użytkownika, ale adres "
       "URL strony i wyświetlane wyniki dotyczą poprzedniego, innego "
       "zapytania. System nie aktualizuje wyników, aby pasowały do zapytania.",
    2: "Po wpisaniu w pole wyszukiwania frazy i kliknięciu przycisku 'Szukaj', "
       "system nie wyświetlił listy wyników, lecz pozostawił użytkownika na "
       "stronie produktu.",
    3: "Na stronie wyników wyszukiwania użytkownik wpisał nowe zapytanie i "
       "użył przycisku wyszukiwania. System nie zareagował: strona nie "
       "odświeżyła się i wciąż wyświetlała wyniki dla poprzedniego zapytania.",
    4: "Będąc na stronie produktu, użytkownik wpisał nowe hasło w globalną "
       "wyszukiwarkę i kliknął przycisk szukania. Strona nie przeniosła go "
       "do wyników wyszukiwania.",
    5: "Po wpisaniu nowego zapytania w pole wyszukiwania i kliknięciu "
       "przycisku 'Szukaj', system nie zaktualizował wyników. Strona nadal "
       "wyświetlała produkty dla poprzedniego zapytania.",
    9: "Będąc na stronie wyników wyszukiwania, użytkownik wpisał nowe "
       "zapytanie i próbował je wysłać. Strona nie zaktualizowała się i "
       "wciąż wyświetlała poprzednie wyniki.",
}


def _f(hid, evidence, persona="p1", severity="krytyczny"):
    return {"heuristic_id": hid, "severity": severity, "evidence": evidence,
            "recommendation": "", "_persona_dir": persona}


def test_same_bug_across_six_heuristics_gets_one_root_cause_id():
    findings = [_f(hid, ev, persona=f"p{hid}") for hid, ev in _REAL_EVIDENCE.items()]
    assign_root_causes(findings)
    ids = {f.get("root_cause_id") for f in findings}
    assert ids == {"search_stale_results"}, ids
    names = {f.get("root_cause_name") for f in findings}
    assert len(names) == 1 and next(iter(names))


def test_idempotent_reassignment_clears_stale_tags():
    findings = [_f(1, _REAL_EVIDENCE[1])]
    assign_root_causes(findings)
    assert findings[0]["root_cause_id"] == "search_stale_results"
    findings[0]["evidence"] = "Duplikat przycisku menu w nagłówku."
    assign_root_causes(findings)
    assert findings[0].get("root_cause_id") is None


# --- precision: must NOT cluster near-miss / unrelated text -----------------

def test_general_search_broken_claim_does_not_match():
    # "search doesn't work" (no re-query, no stale-results phrasing) is a
    # DIFFERENT claim shape — clustering it would risk merging distinct bugs.
    findings = [_f(3, "Wyszukiwarka na stronie wyników nie działa, blokując "
                      "dalsze poszukiwania.")]
    assign_root_causes(findings)
    assert findings[0].get("root_cause_id") is None


def test_legitimate_zero_results_does_not_match():
    findings = [_f(2, "Wyszukiwarka poprawnie przetworzyła zapytanie i "
                      "zwróciła zero wyników dla nieistniejącego produktu.")]
    assign_root_causes(findings)
    assert findings[0].get("root_cause_id") is None


def test_unrelated_consistency_finding_does_not_match():
    findings = [_f(4, "W nagłówku strony znajdują się dwa osobne przyciski, "
                      "które wizualnie sugerują tę samą funkcję menu.")]
    assign_root_causes(findings)
    assert findings[0].get("root_cause_id") is None


def test_empty_and_non_dict_inputs_are_safe():
    assert assign_root_causes([]) == []
    assert assign_root_causes(None) is None
    findings = ["not a dict", _f(1, _REAL_EVIDENCE[1])]
    assign_root_causes(findings)  # must not raise
    assert findings[1]["root_cause_id"] == "search_stale_results"
