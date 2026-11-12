"""A notice the regulator prescribes is not the thing to restyle.

The first real audit of bnpparibas.pl: priority recommendation #1 was "restyle the UOKiK notice
to be more discreet and less alarming (a flat expandable bar)", and five of six first
impressions asked for the same thing in other words. The notice is the publication of a UOKiK
commitment decision (RKR-3/2026): wording, frame and presence are prescribed, it stays until the
visitor closes it. Only its legibility is the site's to improve. The same wording about an app
banner, an ad or a promotion is a fair recommendation and stays.
"""
import copy

import pytest

from simulated_web_agent.main.mandated_notice import (
    LEFT_OUT_NOTE, LEGIBILITY_ONLY, dilutes_mandated_notice, guard_first_impression, guard_narrative,
    is_mandated_notice, mandated_notice_reason, only_mandated, recommends_dilution, rewrite_sentences,
)

REAL_NOTICE = ("Komunikat o decyzji Prezesa Urzędu Ochrony Konkurencji i Konsumentów Na stronie UOKiK możesz "
               "zobaczyć decyzję zobowiązującą Prezesa UOKiK z dnia 4 września 2026 r. nr RKR-3/2026 "
               "dotyczącą zgłoszeń transakcji nieautoryzowanych.")

# What the aggregate said, verbatim (run 2026-11-10_16-15-42_cfe5).
REAL_RECOMMENDATION = ("Zmienić stylizację komunikatu o decyzji UOKiK na bardziej dyskretną i mniej alarmującą "
                       "(np. płaski pasek do rozwinięcia zamiast okna z ramką na górze ekranu).")
APP_BANNER_RECOMMENDATION = ("Zmniejszyć baner aplikacji GOmobile na górze ekranu mobilnego, bo zasłania "
                             "menu i przesuwa treść poniżej pierwszego ekranu.")


# --- what counts as a mandated notice -------------------------------------------------

@pytest.mark.parametrize("text", [
    REAL_NOTICE,
    "Decyzja Prezesa UOKiK nr RKR-3/2026 z dnia 4 września 2026 r.",
    "Zgodnie z decyzją Prezesa Urzędu Ochrony Konkurencji i Konsumentów bank informuje o zobowiązaniach.",
    "Komunikat o decyzji Komisji Nadzoru Finansowego w sprawie banku.",
    "Na podstawie decyzji Prezesa Urzędu Ochrony Konkurencji i Konsumentów publikujemy komunikat.",
    "Wykonujemy obowiązek publikacyjny nałożony na bank.",
    "Publikujemy to ogłoszenie w ramach obowiązku publikacyjnego.",
    "Decyzja Prezesa UODO z dnia 3 maja.",
    "Informacja o przetwarzaniu danych osobowych zgodnie z RODO.",
    "Administrator danych działa zgodnie z RODO.",
    "We process your data in line with the GDPR; see the privacy notice.",
    "Notice published under the decision of the President of the Office of Competition and Consumer Protection.",
    "Sprawa DOZIK-12/2025 zakończyła się zobowiązaniem banku.",
    "Bank zakończył sprawę RKR-3/2026 zobowiązaniem.",
    "Zgodnie z ustawą o prawach konsumenta informujemy o zmianie regulaminu.",
    "Zgodnie z art. 24 ustawy bank informuje o obowiązku publikacji warunków rachunku.",
])
def test_regulator_prescribed_notices_are_mandated(text):
    assert is_mandated_notice(text) is True
    assert mandated_notice_reason(text)


@pytest.mark.parametrize("text", [
    "",
    None,
    "Otwórz konto w aplikacji GOmobile i odbierz 200 zł.",
    "Promocja: konto bez opłat przez rok. Zgodnie z regulaminem promocji, oferta ważna do końca miesiąca.",
    "Zgodnie z art. 5 regulaminu promocji możesz wygrać nagrodę.",
    "Informujemy: zgodnie z ustawą każdy klient otrzyma darmowe konto w ramach promocji.",
    "Pobierz aplikację i zyskaj rabat na pierwszy przelew.",
    "Korzystamy z plików cookie, aby strona działała lepiej.",
    "Szlachetna Paczka w banku BNP - zostań darczyńcą.",
    "Kredyt hipoteczny z oprocentowaniem od 5,9%. Sprawdź ofertę.",
    "Win a prize: open an account today and get a free card.",
])
def test_an_ordinary_promotion_or_banner_is_not_mandated(text):
    assert is_mandated_notice(text) is False
    assert mandated_notice_reason(text) is None


def test_a_legal_basis_alone_does_not_shield_a_promotion():
    assert not is_mandated_notice("Zgodnie z art. 5 oferty specjalnej odbierz bonus na start.")
    assert not is_mandated_notice("Zgodnie z ustawą każdy klient otrzyma darmowe konto - promocja trwa do piątku.")


# --- which sentences recommend diluting one -------------------------------------------

def test_the_real_recommendation_dilutes_a_mandated_notice():
    assert dilutes_mandated_notice(REAL_RECOMMENDATION) is True


@pytest.mark.parametrize("sentence", [
    "Zwinąć komunikat UOKiK do formy wąskiego paska z przyciskiem 'Rozwiń'.",
    "Zmniejszyć obszar zajmowany przez komunikat o decyzji Prezesa UOKiK.",
    "Usunąć zielone obramowanie okna UOKiK i zmniejszyć marginesy.",
    "Przeredagować lub wizualnie stonować nagłówek komunikatu UOKiK.",
    "Ukryć komunikat prawny po pierwszym wejściu.",
    "Zmniejszyć inwazyjność okna z komunikatem regulatora.",
    "Odroczyć wyświetlenie komunikatu UOKiK do momentu przewinięcia strony.",
    "Uczynić komunikat UOKiK dyskretnym.",
    "Make the UOKiK notice less prominent.",
    "Collapse the legal notice into a discreet bar.",
])
def test_every_way_of_asking_to_dilute_it_is_caught(sentence):
    assert dilutes_mandated_notice(sentence) is True


@pytest.mark.parametrize("sentence", [
    APP_BANNER_RECOMMENDATION,
    "Zwinąć baner reklamowy kampanii kredytowej do wąskiego paska.",
    "Usunąć okno promocji z konkursem sprzed menu.",
    "Ukryć czat do czasu, aż odwiedzający go otworzy.",
    "Zmniejszyć obraz bohatera, aby pokazać kafelki produktów.",
    "Hide the app banner on mobile.",
])
def test_the_same_words_about_a_banner_an_ad_or_a_promotion_are_untouched(sentence):
    assert dilutes_mandated_notice(sentence) is False


@pytest.mark.parametrize("sentence", [
    LEGIBILITY_ONLY,
    "Zwiększyć kontrast tekstu komunikatu UOKiK i skrócić długość linii.",
    "Dodać krótkie streszczenie prostym językiem obok tekstu prawnego komunikatu UOKiK.",
    "Umieścić przycisk „Zamknij” komunikatu UOKiK w łatwo dostępnym miejscu.",
    "Powiększyć czcionkę komunikatu UOKiK na urządzeniach mobilnych.",
    "Mały, gęsty tekst komunikatu UOKiK budzi podejrzenie, że bank chce coś ukryć.",
])
def test_legibility_advice_is_never_taken_back(sentence):
    assert recommends_dilution(sentence) is False
    assert dilutes_mandated_notice(sentence) is False


def test_a_complaint_about_what_the_notice_does_is_not_advice_about_it():
    assert dilutes_mandated_notice("Komunikat UOKiK ukrywa ofertę i zasłania menu.") is False


def test_when_the_only_notice_is_mandated_the_plain_word_names_it():
    sentence = "Zwinąć komunikat do wąskiego paska, aby odsłonić ofertę."
    assert dilutes_mandated_notice(sentence) is False, "nothing in the sentence says which notice"
    assert dilutes_mandated_notice(sentence, only_mandated=True) is True
    other = "Zwinąć komunikat o aplikacji GOmobile do wąskiego paska."
    assert dilutes_mandated_notice(other, only_mandated=True) is False, "an app banner stays fair game"


def test_only_mandated_needs_a_notice_and_all_of_them_mandated():
    assert only_mandated({"notices": [{"mandated": True}]}) is True
    assert only_mandated({"notices": [{"mandated": True}, {"mandated": False}]}) is False
    assert only_mandated({"notices": []}) is False
    assert only_mandated(None) is False


# --- sentences ------------------------------------------------------------------------

def test_a_full_stop_after_np_does_not_split_the_recommendation():
    """The model writes "(np. płaski pasek ...)": the rewrite must replace the whole advice, not
    leave "płaski pasek do rozwinięcia zamiast okna ..." dangling after a replaced half."""
    text = REAL_RECOMMENDATION + " Poprawi to pierwsze wrażenie."
    out = rewrite_sentences(text, lambda s: dilutes_mandated_notice(s), "ZASTĘPNIK.")
    assert out == "ZASTĘPNIK. Poprawi to pierwsze wrażenie."


def test_rewrite_leaves_a_text_without_a_claim_alone_and_replaces_once():
    assert rewrite_sentences("Nic tu nie ma.", lambda s: False, "X") is None
    two = "Ukryć komunikat UOKiK. Zwinąć komunikat UOKiK. Reszta zostaje."
    assert rewrite_sentences(two, dilutes_mandated_notice, "ZASTĘPNIK.") == "ZASTĘPNIK. Reszta zostaje."
    assert rewrite_sentences(two, dilutes_mandated_notice) == "Reszta zostaje."


# --- one persona's first impression ---------------------------------------------------

def _fi(**over):
    base = {"overall_score": 2.4, "suggested_changes": {
        "purpose_clarity": "Zwinąć komunikat UOKiK do wąskiego paska z przyciskiem 'Rozwiń'.",
        "navigation_visibility": "Zwiększyć odstęp między pozycjami menu 'Konta' i 'Kredyty'.",
        "call_to_action": "Zmniejszyć baner aplikacji GOmobile nad przyciskiem 'OTWÓRZ KONTO'.",
        "visual_hierarchy": "", "trustworthiness": ""},
        "weaknesses": ["Komunikat UOKiK jest długi.", "Należy zmniejszyć okno komunikatu UOKiK, bo przytłacza.",
                       "Brak numeru infolinii na pierwszym ekranie."]}
    base.update(over)
    return base


def test_a_suggested_change_to_hide_the_notice_becomes_the_legibility_only_sentence():
    fi = _fi()
    assert guard_first_impression(fi) is True
    changes = fi["suggested_changes"]
    assert changes["purpose_clarity"] == LEGIBILITY_ONLY
    assert changes["navigation_visibility"] == "Zwiększyć odstęp między pozycjami menu 'Konta' i 'Kredyty'."
    assert changes["call_to_action"].startswith("Zmniejszyć baner aplikacji GOmobile"), "an app banner is untouched"
    assert changes["visual_hierarchy"] == "" and changes["trustworthiness"] == ""
    assert fi["mandated_notice_left_out"] is True


def test_a_weakness_that_asks_to_shrink_the_notice_is_dropped_the_others_stay():
    fi = _fi()
    guard_first_impression(fi)
    assert fi["weaknesses"] == ["Komunikat UOKiK jest długi.", "Brak numeru infolinii na pierwszym ekranie."]


def test_the_first_impression_guard_runs_on_its_own_output_unchanged():
    fi = _fi()
    guard_first_impression(fi)
    again = copy.deepcopy(fi)
    assert guard_first_impression(again) is False
    assert again == fi


def test_a_first_impression_with_nothing_to_take_back_is_not_touched_or_stamped():
    fi = {"suggested_changes": {"purpose_clarity": "Dodać numer infolinii obok przycisku 'Kontakt'."},
          "weaknesses": ["Brak numeru infolinii."]}
    before = copy.deepcopy(fi)
    assert guard_first_impression(fi) is False
    assert fi == before and "mandated_notice_left_out" not in fi


def test_the_facts_decide_when_the_sentence_does_not_name_the_notice():
    fi = {"suggested_changes": {"visual_hierarchy": "Zwinąć komunikat u góry strony do jednego wiersza."}}
    assert guard_first_impression(copy.deepcopy(fi), None) is False
    only = {"notices": [{"mandated": True}]}
    assert guard_first_impression(copy.deepcopy(fi), only) is True
    with_banner = {"notices": [{"mandated": True}, {"mandated": False}]}
    assert guard_first_impression(copy.deepcopy(fi), with_banner) is False, "another notice may be the one meant"


@pytest.mark.parametrize("bad", [None, "text", [], {}, {"suggested_changes": None, "weaknesses": "x"},
                                 {"suggested_changes": {"a": 5}, "weaknesses": [None, 3]}])
def test_a_malformed_first_impression_does_not_break_the_guard(bad):
    assert guard_first_impression(bad) is False


# --- the aggregate narrative ----------------------------------------------------------

def _narr(**over):
    base = {
        "executive_summary": "Serwis dobrze komunikuje ofertę. Ogromny komunikat UOKiK przytłacza ekran. "
                             "Brakuje kalkulatora na stronie kredytu.",
        "first_impression_assessment": "Pierwsze wrażenie jest słabe (2,4/5). Warto ukryć komunikat UOKiK.",
        "methodology_note": "Badanie jakościowe.",
        "priority_recommendations": [
            {"priority": 1, "area": "Pierwsze wrażenie / UI", "action": REAL_RECOMMENDATION,
             "expected_impact": "Mniejszy niepokój nowych użytkowników."},
            {"priority": 2, "area": "Nawigacja", "action": "Dodać link „Sprawdź ratę” do kalkulatora.",
             "expected_impact": "Krótsza ścieżka."},
            {"priority": 3, "area": "Baner aplikacji", "action": APP_BANNER_RECOMMENDATION,
             "expected_impact": "Więcej miejsca na pierwszym ekranie."},
        ]}
    base.update(over)
    return base


def test_priority_one_is_replaced_by_the_legibility_only_sentence_and_keeps_its_place():
    narr = guard_narrative(_narr())
    first = narr["priority_recommendations"][0]
    assert first["priority"] == 1 and first["action"] == LEGIBILITY_ONLY
    assert first["area"] == "Czytelność komunikatu prawnego"
    assert first["expected_impact"] == "", "the stated benefit belonged to the removed advice"
    assert "dyskretn" not in str(narr) and "płaski pasek" not in str(narr)


def test_an_app_banner_recommendation_is_kept_verbatim():
    narr = guard_narrative(_narr())
    assert [r["action"] for r in narr["priority_recommendations"]][2] == APP_BANNER_RECOMMENDATION
    assert narr["priority_recommendations"][2]["expected_impact"] == "Więcej miejsca na pierwszym ekranie."
    assert narr["priority_recommendations"][1]["action"].startswith("Dodać link")


def test_the_summary_and_the_assessment_lose_only_the_sentence_that_recommends_it():
    narr = guard_narrative(_narr(executive_summary="Serwis jest dobry. Należy zwinąć komunikat UOKiK. Koniec.",
                                 first_impression_assessment="Słabe (2,4/5). Warto ukryć komunikat UOKiK."))
    assert narr["executive_summary"] == "Serwis jest dobry. Koniec."
    assert narr["first_impression_assessment"] == "Słabe (2,4/5)."
    only = guard_narrative(_narr(first_impression_assessment="Zwinąć komunikat UOKiK."))
    assert only["first_impression_assessment"] == LEGIBILITY_ONLY, "an emptied field is never left blank"


def test_a_complaint_in_the_summary_stays_as_the_participants_reading():
    narr = guard_narrative(_narr())
    assert "Ogromny komunikat UOKiK przytłacza ekran." in narr["executive_summary"]


def test_the_methodology_note_says_once_that_a_recommendation_was_left_out():
    narr = guard_narrative(_narr())
    assert narr["methodology_note"].endswith(LEFT_OUT_NOTE) and narr["methodology_note"].count(LEFT_OUT_NOTE) == 1
    assert "regulatora" in LEFT_OUT_NOTE and "Pominięto" in LEFT_OUT_NOTE
    guard_narrative(narr)
    assert narr["methodology_note"].count(LEFT_OUT_NOTE) == 1
    assert narr["methodology_note"].startswith("Badanie jakościowe.")


def test_the_narrative_guard_is_idempotent():
    once = guard_narrative(_narr())
    twice = guard_narrative(copy.deepcopy(once))
    assert twice == once
    landing = {"only_mandated": True, "left_out": 2}
    a = guard_narrative(_narr(), landing)
    assert guard_narrative(copy.deepcopy(a), landing) == a


def test_two_recommendations_that_become_the_same_sentence_collapse_and_priorities_renumber():
    narr = _narr()
    narr["priority_recommendations"].insert(1, {
        "priority": 2, "area": "Pierwsze wrażenie / UI",
        "action": "Zwinąć komunikat UOKiK do paska.", "expected_impact": "x"})
    for i, rec in enumerate(narr["priority_recommendations"], 1):
        rec["priority"] = i
    narr["priority_recommendations"][1]["area"] = narr["priority_recommendations"][0]["area"]
    guard_narrative(narr)
    recs = narr["priority_recommendations"]
    assert [r["priority"] for r in recs] == [1, 2, 3]
    assert [r["action"] for r in recs].count(LEGIBILITY_ONLY) == 1


def test_a_legacy_narrative_without_such_a_recommendation_is_unchanged():
    legacy = {"executive_summary": "Serwis działa. Kalkulator jest trudno dostępny.",
              "first_impression_assessment": "Pierwsze wrażenie jest dobre (3,6/5).",
              "methodology_note": "Badanie jakościowe.",
              "priority_recommendations": [{"priority": 1, "area": "Kalkulator",
                                            "action": "Dodać link do kalkulatora.", "expected_impact": "Szybciej."}]}
    before = copy.deepcopy(legacy)
    assert guard_narrative(legacy) == before
    assert guard_narrative(legacy, {}) == before
    assert guard_narrative(legacy, {"measured_sessions": 2, "only_mandated": True, "left_out": 0}) == before
    assert guard_narrative({}) == {} and guard_narrative(None) is None


def test_the_note_appears_when_the_per_persona_guard_already_took_one_back():
    narr = {"executive_summary": "Serwis działa.", "methodology_note": "Badanie.", "priority_recommendations": []}
    out = guard_narrative(narr, {"left_out": 3})
    assert out["methodology_note"].endswith(LEFT_OUT_NOTE)


def test_the_landing_facts_extend_the_guard_to_a_plain_word():
    narr = _narr(priority_recommendations=[
        {"priority": 1, "area": "UI", "action": "Zwinąć komunikat do paska.", "expected_impact": "x"}])
    kept = guard_narrative(copy.deepcopy(narr), None)
    assert kept["priority_recommendations"][0]["action"] == "Zwinąć komunikat do paska."
    guarded = guard_narrative(copy.deepcopy(narr), {"only_mandated": True})
    assert guarded["priority_recommendations"][0]["action"] == LEGIBILITY_ONLY
    beside_banner = guard_narrative(copy.deepcopy(narr), {"only_mandated": False, "mandated": True})
    assert beside_banner["priority_recommendations"][0]["action"] == "Zwinąć komunikat do paska."
