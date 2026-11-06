"""Evidence-grounded task-completion outcome (METHODOLOGY_PLAN §4.2).

The verdict (did the run reach its goal?) comes from the trace, never the LLM.
These tests pin the goal ladder, same-site scoping, the deterministic target
inference, and the fail-toward-declared abstain path.
"""
import json

from simulated_web_agent.main.session_goal_state import (
    compute_goal_state,
    infer_target_rung,
    observed_outcome,
    resolve_target_rung,
    session_capabilities,
)
from simulated_web_agent.main.trace_store import TraceStore

DM = "https://www.dm.pl"


def _store(tmp_path, steps, intent="", success_condition=None):
    """steps = list of (url, observation_text). Writes a real on-disk trace plus
    basic_info (intent / optional success_condition)."""
    store = TraceStore(tmp_path / "persona")
    store.write_action_trace(
        [json.dumps({"action": "click", "target": "x", "description": "c"})] * len(steps)
    )
    for i, (url, obs) in enumerate(steps):
        store.append_observation({"tabs": [{"url": url, "is_active": True}]})
        store.write_observation_step_text(i, obs)
    info = {"intent": intent}
    if success_condition is not None:
        info["success_condition"] = success_condition
    store.write_basic_info(info)
    return store


# --- #2: what the trace shows (the ladder) ---------------------------------

def test_ladder_climbs_to_order_summary(tmp_path):
    store = _store(tmp_path, [
        (f"{DM}/search?query=krem", "wyniki wyszukiwania dla krem"),
        (f"{DM}/p/d/111/alverde", "strona produktu alverde krem"),
        (f"{DM}/koszyk", "Twój koszyk. Produkty łącznie 76,50 zł. Usuń."),
        (f"{DM}/koszyk", "Podsumowanie. Produkty łącznie 76,50 zł. Przejdź do kasy."),
    ])
    gs = compute_goal_state(store, DM)
    assert gs["reached_rung"] == "order_summary_reached"
    for rung in ("searched", "product_viewed", "cart_reached",
                 "cart_has_items", "order_summary_reached"):
        assert gs["ladder"][rung]["reached"], rung
    assert gs["ladder"]["cart_has_items"]["step"] == 2  # first cart-with-items step
    assert gs["same_site_steps"] == 4


def test_add_to_cart_toast_counts_without_visiting_cart_page(tmp_path):
    # dm reality: the item is added via a "Dodano do koszyka" toast on the
    # product page; the agent never opens /koszyk. That must still be cart_has_items.
    store = _store(tmp_path, [
        (f"{DM}/p/d/2957695/loreal-krem", "Strona produktu. Dodano do koszyka! 1 Koszyk."),
    ], intent=_SHOP)
    gs = compute_goal_state(store, DM)
    assert gs["ladder"]["cart_has_items"]["reached"] is True
    assert observed_outcome(store, DM)["outcome"] == "partial"  # added, not checked out


def test_checkout_click_after_add_is_success_despite_login_wall(tmp_path):
    # The reachable ceiling: items added, then "Do kasy" clicked → login wall
    # (signin.dm.pl). Success, because checkout is login-gated and the agent
    # cannot register. (Verified against Ola's real trace.)
    store = TraceStore(tmp_path / "persona")
    store.write_action_trace([
        json.dumps({"action": "click", "description": "open product"}),
        json.dumps({"action": "click", "description": "Dodaj do koszyka"}),
        json.dumps({"action": "click",
                    "description": "Viewing the order summary by clicking 'Do kasy'."}),
    ])
    steps = [
        (f"{DM}/p/d/1/a", "Strona produktu."),
        (f"{DM}/p/d/1/a", "Dodano do koszyka! 1 Koszyk."),
        ("https://signin.dm.pl/dm-pl/authentication/web-login",
         "Witaj w dm. Twój adres e-mail. Zaloguj się. Utwórz konto dm."),
    ]
    for i, (u, o) in enumerate(steps):
        store.append_observation({"tabs": [{"url": u, "is_active": True}]})
        store.write_observation_step_text(i, o)
    store.write_basic_info({"intent": _SHOP})
    oo = observed_outcome(store, DM)
    assert oo["reached_rung"] == "order_summary_reached"
    assert oo["outcome"] == "success"


def test_cart_has_items_needs_line_item_not_just_the_url(tmp_path):
    store = _store(tmp_path, [(f"{DM}/koszyk", "Twój koszyk jest pusty.")])
    gs = compute_goal_state(store, DM)
    assert gs["ladder"]["cart_reached"]["reached"] is True
    assert gs["ladder"]["cart_has_items"]["reached"] is False


def test_order_summary_not_triggered_by_stray_mini_cart_button(tmp_path):
    # "Do kasy" is a persistent mini-cart button on many pages; it is not the
    # order-summary block ("Podsumowanie" + "Przejdź do kasy").
    store = _store(tmp_path, [
        (f"{DM}/gospodarstwo-domowe", "Lista produktów. Przycisk mini-koszyka: Do kasy."),
    ])
    gs = compute_goal_state(store, DM)
    assert gs["ladder"]["order_summary_reached"]["reached"] is False


def test_off_site_step_grants_no_rung(tmp_path):
    store = _store(tmp_path, [
        (f"{DM}/", "strona główna dm"),
        ("https://www.doz.pl/koszyk", "Twój koszyk. Produkty łącznie 20 zł. Usuń."),
    ])
    gs = compute_goal_state(store, DM)
    assert gs["ladder"]["cart_reached"]["reached"] is False  # doz.pl is a competitor
    assert gs["same_site_steps"] == 1


# --- #1: what counts as success (deterministic target inference) ------------

def test_infer_target_rung_from_intents():
    assert infer_target_rung(
        "...dodaj wybrane produkty do koszyka i przejdź do podsumowania "
        "zamówienia — bez finalizacji płatności."
    ) == "order_summary_reached"
    assert infer_target_rung(
        "Znajdź produkt i porównaj dwie opcje pod względem ceny."
    ) == "product_viewed"
    assert infer_target_rung("Przeczytaj artykuł o zdrowiu.") is None


def test_explicit_success_condition_overrides_inference(tmp_path):
    store = _store(tmp_path, [(f"{DM}/p/d/1/a", "produkt")],
                   intent="Przeczytaj artykuł", success_condition="product_viewed")
    assert resolve_target_rung(store) == ("product_viewed", "explicit")


# --- the fusion: outcome from reached-vs-target -----------------------------

_SHOP = "dodaj do koszyka i przejdź do podsumowania zamówienia"


def test_observed_success_when_reached_meets_target(tmp_path):
    store = _store(tmp_path, [(f"{DM}/koszyk", "Podsumowanie. Przejdź do kasy.")],
                   intent=_SHOP)
    oo = observed_outcome(store, DM)
    assert oo["decidable"] and oo["outcome"] == "success"
    assert oo["target_rung"] == "order_summary_reached"
    assert oo["target_source"] == "inferred"


def test_observed_partial_requires_item_in_cart(tmp_path):
    # Added to cart (item present) but no order summary → partial.
    store = _store(tmp_path, [
        (f"{DM}/p/d/1/a", "produkt"),
        (f"{DM}/koszyk", "Twój koszyk. Produkty łącznie 20,00 zł. Usuń."),
    ], intent=_SHOP)
    assert observed_outcome(store, DM)["outcome"] == "partial"


def test_viewing_without_add_to_cart_is_failed(tmp_path):
    # User calibration: viewing/comparing products without adding to cart is not
    # "partial" — it is failed.
    store = _store(tmp_path, [(f"{DM}/p/d/1/a", "produkt")], intent=_SHOP)
    assert observed_outcome(store, DM)["outcome"] == "failed"


def test_empty_cart_page_is_failed(tmp_path):
    store = _store(tmp_path, [(f"{DM}/koszyk", "Twój koszyk jest pusty.")], intent=_SHOP)
    assert observed_outcome(store, DM)["outcome"] == "failed"


def test_observed_failed_when_no_progress(tmp_path):
    store = _store(tmp_path, [(f"{DM}/", "strona główna")], intent=_SHOP)
    assert observed_outcome(store, DM)["outcome"] == "failed"


def test_abstains_to_unknown_on_non_shopping_intent(tmp_path):
    store = _store(tmp_path, [(f"{DM}/p/d/1/a", "produkt")], intent="Przeczytaj artykuł")
    oo = observed_outcome(store, DM)
    assert oo["outcome"] == "unknown" and oo["decidable"] is False


def test_abstains_to_unknown_on_empty_trace(tmp_path):
    store = _store(tmp_path, [], intent=_SHOP)
    assert observed_outcome(store, DM)["outcome"] == "unknown"


# --- session_capabilities compat (now same-site scoped) ---------------------

def test_session_capabilities_excludes_off_site_urls(tmp_path):
    store = _store(tmp_path, [
        (f"{DM}/p/d/1/a", "x"),
        ("https://www.doz.pl/p/d/2/b", "x"),   # competitor product — must not count
        (f"{DM}/koszyk", "x"),
    ])
    caps = session_capabilities(store)
    assert caps["product_pages_opened"] == 1
    assert caps["reached_cart"] is True


def test_a_shopify_style_product_page_is_a_product_page(tmp_path):
    # pepco.pl: /products/<handle>?Colour=..&Size=.. — neither /product/ nor /produkt.
    pepco = "https://www.pepco.pl"
    store = _store(tmp_path, [
        (f"{pepco}/products/poduszka-324777?Colour=Cream&Size=ONE", "x"),
    ], intent="Znajdź poduszkę i sprawdź jej cenę.")
    assert observed_outcome(store, pepco)["outcome"] == "success"
    assert session_capabilities(store)["product_pages_opened"] == 1
