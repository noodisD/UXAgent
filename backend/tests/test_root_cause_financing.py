"""The same missing financing information, filed under two heuristics, is one defect.

In the 2026-09-30 hyundai run two personas wrote, on the summary page, "brak
prezentacji estymowanej raty miesięcznej" (filed under H2) and "brakuje
kalkulatora rat" (filed under H7). Two minor issues and two score deductions for
one missing thing. The rule is a named signature, like the search one: an
absence word must come right before the financing term ("brakuje … raty"), so it
cannot merge two different defects by similarity. The first version asked only
that a financing word and an absence word both occur in the text, and merged a
broken calculator, a missing Next button and a missing period option into it."""
from simulated_web_agent.main.root_cause import assign_root_causes

NATALIA_H2 = {
    "heuristic_id": 2,
    "evidence": ("W podsumowaniu konfiguracji system podaje jedynie całkowitą kwotę jednorazową. "
                 "Brakuje prezentacji estymowanej raty miesięcznej, choć klienci opierają decyzję "
                 "o miesięczny budżet na finansowanie."),
    "recommendation": "Dodać orientacyjną kwotę raty miesięcznej lub odnośnik do symulatora finansowania.",
}
TOMASZ_H7 = {
    "heuristic_id": 7,
    "evidence": ("Brakuje natychmiastowego dostępu do kalkulatora rat (np. dla leasingu B2B), przez co "
                 "użytkownik musi skontaktować się z doradcą, by poznać koszty miesięczne."),
    "recommendation": ("Dodać w podsumowaniu konfiguratora przejrzysty kalkulator finansowania "
                       "(uwzględniający raty leasingowe i kredytowe)."),
}


def test_both_findings_get_the_same_root_cause():
    a, t = dict(NATALIA_H2), dict(TOMASZ_H7)
    assign_root_causes([a, t])
    assert a["root_cause_id"] == t["root_cause_id"] == "financing_info_missing"
    assert a["root_cause_name"].startswith("Brak raty miesięcznej")


def test_a_calculator_that_does_not_work_is_a_different_defect():
    broken = {"heuristic_id": 4,
              "evidence": "Przycisk „Kalkulator rat” nie reaguje na kliknięcie.",
              "recommendation": "Naprawić przycisk kalkulatora rat."}
    assign_root_causes([broken])
    assert "root_cause_id" not in broken


def test_unrelated_findings_are_left_alone():
    other = {"heuristic_id": 6, "evidence": "Brak przycisku „Wstecz” na ekranie wyboru koloru.",
             "recommendation": "Dodać przycisk powrotu."}
    assign_root_causes([other])
    assert "root_cause_id" not in other


def test_the_search_signature_still_wins_where_it_applies():
    search = {"heuristic_id": 1,
              "evidence": "Po wpisaniu nowego zapytania wyszukiwarka nie zaktualizowała wyników.",
              "recommendation": "Odświeżać listę wyników."}
    assign_root_causes([search])
    assert search["root_cause_id"] == "search_stale_results"


def test_the_two_findings_become_one_row_in_the_prioritisation():
    from simulated_web_agent.main.prioritization import prioritize

    a, t = dict(NATALIA_H2), dict(TOMASZ_H7)
    for f, who in ((a, "Natalia"), (t, "Tomasz")):
        f.update({"severity": "drobny", "verified": True, "_persona_dir": who, "_persona_name": who})
    assign_root_causes([a, t])
    rows = prioritize([a, t], {"Natalia": "partial", "Tomasz": "partial"})
    assert len(rows) == 1 and rows[0]["personas_affected"] == 2


import pytest


@pytest.mark.parametrize("text", [
    "Kalkulator rat nie działa: po zmianie wkładu własnego brak jakiejkolwiek reakcji.",
    "Raty miesięczne są prezentowane poprawnie, ale nie ma przycisku Dalej.",
    "Kalkulator finansowania ma zbyt mało pól; brak opcji wyboru okresu.",
])
def test_a_financing_word_and_an_unrelated_absence_do_not_match(text):
    f = {"heuristic_id": 4, "evidence": text, "recommendation": ""}
    assign_root_causes([f])
    assert "root_cause_id" not in f


@pytest.mark.parametrize("text", [
    "Strona nie podaje raty miesięcznej leasingu na karcie modelu.",
    "Brak informacji o finansowaniu w podsumowaniu.",
    "Bez prezentacji raty użytkownik nie oceni kosztu miesięcznego.",
])
def test_an_absence_that_governs_the_financing_term_matches(text):
    f = {"heuristic_id": 2, "evidence": text, "recommendation": ""}
    assign_root_causes([f])
    assert f["root_cause_id"] == "financing_info_missing"
