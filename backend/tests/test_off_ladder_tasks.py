"""A task the shopping ladder cannot score must not be scored as failed.

The user's hyundai run: three personas configured a car and reached the
configurator's summary page, one of them by its own account successfully. The
report opened with "0% skuteczności" and "3/3 failed", and two sections later
tallied 1 failed · 1 partial · 1 success — the document contradicting itself on
its headline.

The goal ladder is shopping-shaped: searched → product_viewed → cart →
cart_has_items → order_summary_reached, every rung decided from URL markers a
configurator never produces. The objective said "dojdź do strony
podsumowania", and "podsumowani" alone mapped it to the checkout rung. Removing
that keyword would not have helped: "porównaj", "sprawdź" and "cenę" map it to
product_viewed, which needs /p/ or /produkt URLs — also absent — so it would
still have scored 3/3 failed.

Then the aggregator was told, unconditionally, that the completion figures were
"zmierzone z zapisu sesji … nie samoocena agenta", even when every one of them
was the persona's own verdict. The prompt calls measured completion its
strongest evidence, so that sentence set the score.
"""
import pytest

from simulated_web_agent.main.reporter import _format_outcome_distribution
from simulated_web_agent.main.session_goal_state import infer_target_rung

HYUNDAI = (
    "Chcesz kupić nowego Hyundaia. Skonfiguruj jeden konkretny model: wybierz "
    "wersję wyposażenia, silnik, kolor nadwozia i wnętrze. Budżet: do 150 000 zł. "
    "Po drodze porównaj co najmniej dwie wersje wyposażenia i sprawdź, czym się "
    "różnią. Następnie dojdź do strony podsumowania, sprawdź cenę całkowitą i "
    "dostępne promocje."
)
DM = (
    "Uzupełniasz swoje stałe zakupy drogeryjne. Znajdź produkty, dodaj wybrane "
    "produkty do koszyka i przejdź do podsumowania zamówienia — bez finalizacji "
    "płatności."
)


# --- the ladder -------------------------------------------------------------

def test_a_configurator_task_abstains():
    """Abstaining hands the outcome back to the declared verdict, labelled."""
    assert infer_target_rung(HYUNDAI) is None


@pytest.mark.parametrize("intent", [
    "Przejdź przez cały proces konfiguracji modelu i wypisz zalety.",
    "Otwórz konfigurator i zbuduj swój samochód.",
])
def test_every_configuration_phrasing_abstains(intent):
    assert infer_target_rung(intent) is None


def test_the_shopping_task_the_ladder_was_built_for_is_unchanged():
    """The dm.pl intent is what the ladder exists to score. It must keep its
    checkout target — it names the cart and the order, not just a summary."""
    assert infer_target_rung(DM) == "order_summary_reached"


def test_summary_alone_is_not_a_checkout():
    """A configuration summary, an order summary and "podsumuj wrażenia" all
    contain the stem; only a shopping sentence makes it a checkout."""
    assert infer_target_rung("Przeczytaj podsumowanie oferty na stronie głównej.") != \
        "order_summary_reached"


def test_summary_with_a_cart_is_a_checkout():
    assert infer_target_rung(
        "Dodaj produkt do koszyka i dojdź do strony podsumowania.") == "order_summary_reached"


# --- and the aggregator is told what it is getting ------------------------

OUTCOMES = {"a": "failed", "b": "success", "c": "partial"}


def test_self_assessed_outcomes_are_not_called_measured():
    text = _format_outcome_distribution(OUTCOMES, [], 3, {k: "declared" for k in OUTCOMES})
    assert "DEKLAROWANE" in text
    assert "nie samoocena agenta" not in text, "must not claim the opposite of what it is"


def test_measured_outcomes_still_say_so():
    text = _format_outcome_distribution(OUTCOMES, ["none"], 3, {k: "observed" for k in OUTCOMES})
    assert "zmierzone z zapisu sesji" in text
    assert "DEKLAROWANE" not in text


def test_a_mixed_run_says_how_many_of_each():
    text = _format_outcome_distribution(
        OUTCOMES, ["none"], 3, {"a": "observed", "b": "declared", "c": "declared"})
    assert "1/3 wyników zmierzono" in text and "2/3 to samoocena" in text


def test_callers_without_sources_keep_the_old_wording():
    assert "zmierzone z zapisu sesji" in _format_outcome_distribution(OUTCOMES, [], 3)
