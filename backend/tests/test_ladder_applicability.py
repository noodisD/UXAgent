"""The shopping ladder only decides an outcome where the site has a shop.

bnpparibas.pl, six personas: the report said "goal not achieved 6/6, measured from
the session record" while every persona's own judgement was "goal achieved", and
that one measurement took 1.50 of the 2.40 deducted points (5.0 became 2.6).

The goal said "znajdź ... sprawdź ... ofertę", and generic verbs like those infer
the target "product_viewed". Reaching it needs a same-site product URL. A bank has
none, so nobody could ever succeed, and the measured outcome overrode the declared
one. Avoiding those words in the goal is no fix: any goal a user writes with them
hits the same artifact.

The ladder now applies only when some session of the run reached a product, cart
or checkout page. Then a persona that never got there really did fail.
"""
import json
from unittest.mock import AsyncMock, patch

import pytest

from simulated_web_agent.main.iso_metrics import compute_iso_metrics, effective_outcome
from simulated_web_agent.main.reporter import _format_outcome_distribution, generate_aggregated_report
from simulated_web_agent.main.scoring import compute_ux_score
from simulated_web_agent.main.session_goal_state import (
    compute_goal_state,
    infer_target_rung,
    observed_outcome,
)
from simulated_web_agent.main.trace_store import TraceStore
from tests.conftest import make_agent_report, make_finding

BNP_URL = "https://www.bnpparibas.pl"
BNP_GOAL = (
    "Rozważasz pierwszą pożyczkę w banku. Znajdź na stronie ofertę pożyczki "
    "gotówkowej dla nowych klientów, sprawdź jej warunki i dowiedz się, w którym "
    "oddziale możesz ją załatwić."
)
PEPCO_URL = "https://www.pepco.pl"
PEPCO_GOAL = "Znajdź poduszkę i sprawdź jej cenę."


def _run(tmp_path, start_url, manifest=True):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    if manifest:
        (run_dir / "run_manifest.json").write_text(
            json.dumps({"start_url": start_url}), encoding="utf-8")
    return run_dir


def _persona(run_dir, name, urls, intent, declared="success", texts=None):
    store = TraceStore(run_dir / name)
    store.write_basic_info({"intent": intent})
    store.write_action_trace(
        [json.dumps({"action": "click", "description": "c"})] * len(urls))
    for i, url in enumerate(urls):
        store.append_observation({"tabs": [{"url": url, "is_active": True}]})
        store.write_observation_step_text(i, texts[i] if texts else "strona")
    store.write_objective_reflection({"outcome": declared})
    store.write_session_result({"steps_taken": len(urls), "ended_reason": "terminated"})
    return store


def _bnp_run(tmp_path, declared=("success", "success", "partial")):
    run_dir = _run(tmp_path, BNP_URL)
    paths = (
        ["/", "/klienci-indywidualni/kredyty/pierwsza-pozyczka"],
        ["/", "/kontakt/oddzialy-z-obsluga-detaliczna-i-biznesowa"],
        ["/", "/klienci-indywidualni/kredyty/pierwsza-pozyczka",
         "/kontakt/oddzialy-z-obsluga-detaliczna-i-biznesowa"],
    )
    return [
        _persona(run_dir, f"p{i}", [BNP_URL + p for p in urls], BNP_GOAL, declared[i])
        for i, urls in enumerate(paths)
    ]


# --- a site without a shop --------------------------------------------------

def test_the_bank_goal_infers_a_product_target():
    """The input that caused it: ordinary words, not a shopping task."""
    assert infer_target_rung(BNP_GOAL) == "product_viewed"


def test_a_run_that_never_saw_a_product_page_is_not_measured_against_one(tmp_path):
    for store in _bnp_run(tmp_path):
        oo = observed_outcome(store, BNP_URL)
        assert oo["outcome"] == "unknown" and oo["decidable"] is False
        assert oo["ladder_applies"] is False
        assert oo["target_rung"] == "product_viewed"


def test_the_declared_outcome_stands_for_every_persona(tmp_path):
    stores = _bnp_run(tmp_path, declared=("success", "success", "partial"))
    assert [effective_outcome(s) for s in stores] == [
        ("success", "declared"), ("success", "declared"), ("partial", "declared")]


def test_effectiveness_is_labelled_declared_with_no_disagreement(tmp_path):
    eff = compute_iso_metrics(_bnp_run(tmp_path)[0])["effectiveness"]
    assert eff["outcome_source"] == "declared"
    assert eff["task_success"] == "success"
    assert eff["observed_outcome"] == "unknown"
    assert eff["ladder_applies"] is False
    assert eff["disagreement"] is False


def test_the_report_says_it_is_declared_and_charges_it_at_half_weight(tmp_path):
    stores = _bnp_run(tmp_path, declared=("failed", "success", "success"))
    outcomes, sources = {}, {}
    for s in stores:
        outcomes[s.trace_dir.name], sources[s.trace_dir.name] = effective_outcome(s)

    text = _format_outcome_distribution(outcomes, [], len(stores), sources)
    assert "DEKLAROWANE" in text and "nie samoocena agenta" not in text

    task = next(d for d in compute_ux_score(
        {"heuristic": {}}, outcomes=outcomes, outcome_sources=sources)["deductions"]
        if d["kind"] == "task")
    assert task["amount"] == 0.25, "one declared failure of three, at half of 1.5"
    assert "ocena deklarowana (werdykt modelu, niezmierzony)" in task["basis"]


def test_a_single_persona_run_of_a_bank_is_judged_the_same_way(tmp_path):
    run_dir = _run(tmp_path, BNP_URL)
    store = _persona(run_dir, "p0", [BNP_URL + "/"], BNP_GOAL, "success")
    assert effective_outcome(store) == ("success", "declared")


def test_an_explicit_success_condition_is_the_authors_word(tmp_path):
    """Nothing sets one today. When someone does, the site is asserted to have it."""
    run_dir = _run(tmp_path, BNP_URL)
    store = _persona(run_dir, "p0", [BNP_URL + "/"], "Przeczytaj artykuł", "success")
    store.write_basic_info({"intent": "Przeczytaj artykuł", "success_condition": "product_viewed"})
    oo = observed_outcome(store, BNP_URL)
    assert oo["outcome"] == "failed" and oo["ladder_applies"] is None


# --- a site with one ---------------------------------------------------------

def test_one_persona_reaching_a_product_page_makes_the_ladder_apply_to_all(tmp_path):
    run_dir = _run(tmp_path, PEPCO_URL)
    found = _persona(run_dir, "found", [
        PEPCO_URL + "/", PEPCO_URL + "/products/poduszka-324777?Colour=Ivory"], PEPCO_GOAL)
    lost = _persona(run_dir, "lost", [
        PEPCO_URL + "/", PEPCO_URL + "/search?searchString=x"], PEPCO_GOAL)

    assert observed_outcome(found, PEPCO_URL)["outcome"] == "success"
    oo = observed_outcome(lost, PEPCO_URL)
    assert oo["outcome"] == "failed" and oo["ladder_applies"] is True
    assert effective_outcome(lost) == ("failed", "observed")


def test_search_and_listing_pages_do_not_make_a_shop(tmp_path):
    run_dir = _run(tmp_path, PEPCO_URL)
    a = _persona(run_dir, "a", [PEPCO_URL + "/search?searchString=x"], PEPCO_GOAL)
    _persona(run_dir, "b", [PEPCO_URL + "/", PEPCO_URL + "/products"], PEPCO_GOAL)
    assert observed_outcome(a, PEPCO_URL)["outcome"] == "unknown"


def test_a_cart_page_in_a_sibling_session_is_enough(tmp_path):
    run_dir = _run(tmp_path, PEPCO_URL)
    a = _persona(run_dir, "a", [PEPCO_URL + "/"], PEPCO_GOAL)
    _persona(run_dir, "b", [PEPCO_URL + "/koszyk"], PEPCO_GOAL)
    assert observed_outcome(a, PEPCO_URL)["outcome"] == "failed"


def test_a_competitors_product_page_does_not_make_a_shop(tmp_path):
    run_dir = _run(tmp_path, BNP_URL)
    a = _persona(run_dir, "a", [BNP_URL + "/"], BNP_GOAL)
    _persona(run_dir, "b", [BNP_URL + "/", "https://www.allegro.pl/produkt/x"], BNP_GOAL)
    assert observed_outcome(a, BNP_URL)["outcome"] == "unknown"


def test_a_folder_that_is_not_a_persona_is_not_read(tmp_path):
    run_dir = _run(tmp_path, BNP_URL)
    a = _persona(run_dir, "a", [BNP_URL + "/"], BNP_GOAL)
    stray = TraceStore(run_dir / "stray")  # no basic_info.json
    stray.append_observation({"tabs": [{"url": BNP_URL + "/p/1", "is_active": True}]})
    assert observed_outcome(a, BNP_URL)["outcome"] == "unknown"


# --- a shop is more than a URL ------------------------------------------------

SHOP_URL = "https://shop.example.pl"
SHOP_GOAL = "Znajdź poduszkę i sprawdź jej cenę."


def _toast_shop_run(tmp_path):
    """A shop whose product pages carry none of the URL markers (/poduszka-123.html),
    which is how the ladder can only know one was reached: dm adds to the cart on the
    product page, shows a toast, and most sessions never open /koszyk."""
    run_dir = _run(tmp_path, SHOP_URL)
    adder = _persona(
        run_dir, "adder", [SHOP_URL + "/", SHOP_URL + "/poduszka-kremowa-123.html"], SHOP_GOAL,
        texts=["strona", "Dodano do koszyka!"])
    searcher = _persona(
        run_dir, "searcher", [SHOP_URL + "/", SHOP_URL + "/szukaj?q=poduszka"], SHOP_GOAL, "failed")
    return adder, searcher


def test_an_add_to_cart_toast_is_a_shop_without_a_product_url(tmp_path):
    adder, _ = _toast_shop_run(tmp_path)
    oo = observed_outcome(adder, SHOP_URL)
    assert oo["reached_rung"] == "cart_has_items"
    assert oo["ladder_applies"] is True and oo["outcome"] == "success"
    assert effective_outcome(adder) == ("success", "observed")


def test_a_siblings_add_to_cart_toast_makes_the_ladder_apply_to_the_rest(tmp_path):
    _, searcher = _toast_shop_run(tmp_path)
    oo = observed_outcome(searcher, SHOP_URL)
    assert oo["ladder_applies"] is True and oo["outcome"] == "failed"
    assert effective_outcome(searcher) == ("failed", "observed")


# --- "kup" is a purchase, not a product page ----------------------------------

PEPCO_BUY_GOAL = "Znajdź i kup lampkę stołową do sypialni."


@pytest.mark.parametrize("intent, target", [
    (PEPCO_BUY_GOAL, "cart_has_items"),
    ("Kup lampkę.", "cart_has_items"),
    ("Znajdź kupon rabatowy.", "product_viewed"),
    ("Chcesz kupić prezent. Znajdź i porównaj ceny dwóch produktów.", "product_viewed"),
    ("Zamów rozmowę z doradcą.", None),
])
def test_only_the_imperative_kup_asks_for_a_purchase(intent, target):
    assert infer_target_rung(intent) == target


def test_opening_a_product_page_is_not_buying_it(tmp_path):
    """pepco.pl: 'Znajdź i kup…' inferred product_viewed through 'znajd', so a persona
    who only opened the product page was measured a success over a task it failed."""
    run_dir = _run(tmp_path, PEPCO_URL)
    page = [PEPCO_URL + "/", PEPCO_URL + "/products/lampka-123?Colour=Cream"]
    viewer = _persona(run_dir, "viewer", page, PEPCO_BUY_GOAL, "failed")
    buyer = _persona(run_dir, "buyer", page, PEPCO_BUY_GOAL,
                     texts=["strona", "Dodano do koszyka!"])

    oo = observed_outcome(viewer, PEPCO_URL)
    assert oo["target_rung"] == "cart_has_items" and oo["reached_rung"] == "product_viewed"
    assert oo["outcome"] == "failed"
    assert effective_outcome(viewer) == ("failed", "observed")
    assert effective_outcome(buyer) == ("success", "observed")


# --- and the report says why, when nothing was measured -----------------------

def test_the_report_does_not_call_a_stopped_shop_an_unassessable_task():
    outcomes = {"a": "failed", "b": "failed"}
    sources = {"a": "declared", "b": "declared"}
    text = _format_outcome_distribution(outcomes, [], 2, sources, no_shop=True)
    assert "Żadna sesja nie dotarła do strony produktu ani koszyka" in text
    assert "tego typu zadania" not in text
    assert "DEKLAROWANE" in text and "porażka: 2/2" in text
    assert "tego typu zadania" in _format_outcome_distribution(outcomes, [], 2, sources)


@pytest.mark.asyncio
async def test_the_narrator_is_told_no_session_reached_a_product_page(tmp_path):
    stores = _bnp_run(tmp_path)
    for s in stores:
        s.write_ux_report(make_agent_report(findings=[make_finding()]))
    chat = AsyncMock(return_value=json.dumps({"executive_summary": "x", "overall_ux_score": 3}))
    with patch("simulated_web_agent.main.reporter.async_chat", new=chat):
        await generate_aggregated_report(stores=stores, task_description="Z", website_url=BNP_URL)
    prompts = [str(c.args[0]) for c in chat.call_args_list]
    assert any("Żadna sesja nie dotarła do strony produktu ani koszyka" in p for p in prompts)
    assert not any("tego typu zadania" in p for p in prompts)


# --- what a product page looks like ------------------------------------------

@pytest.mark.parametrize("path", [
    "/products/poduszka-324777?Colour=Cream&Size=ONE",   # pepco.pl, Shopify-style
    "/product/poduszka",
    "/produkt/poduszka",
    "/p/d/111/alverde",
])
def test_product_pages_are_recognised(tmp_path, path):
    store = TraceStore(tmp_path / "persona")
    store.append_observation({"tabs": [{"url": PEPCO_URL + path, "is_active": True}]})
    store.write_observation_step_text(0, "x")
    store.write_action_trace([])
    assert compute_goal_state(store, PEPCO_URL)["ladder"]["product_viewed"]["reached"]


@pytest.mark.parametrize("path", ["/", "/products", "/search?searchString=x"])
def test_other_pages_are_not_product_pages(tmp_path, path):
    store = TraceStore(tmp_path / "persona")
    store.append_observation({"tabs": [{"url": PEPCO_URL + path, "is_active": True}]})
    store.write_observation_step_text(0, "x")
    store.write_action_trace([])
    assert not compute_goal_state(store, PEPCO_URL)["ladder"]["product_viewed"]["reached"]


# --- where the run cannot be read, nothing changes ---------------------------

def test_without_a_manifest_the_ladder_applies_as_it_always_did(tmp_path):
    """Fixtures, and runs from before run_manifest.json."""
    run_dir = _run(tmp_path, BNP_URL, manifest=False)
    a = _persona(run_dir, "a", [BNP_URL + "/"], BNP_GOAL)
    b = _persona(run_dir, "b", [BNP_URL + "/"], BNP_GOAL)
    for s in (a, b):
        oo = observed_outcome(s, BNP_URL)
        assert oo["outcome"] == "failed" and oo["decidable"] is True


def test_a_lone_fixture_trace_is_measured_as_before(tmp_path):
    store = TraceStore(tmp_path / "persona")
    store.append_observation({"tabs": [{"url": BNP_URL + "/", "is_active": True}]})
    store.write_observation_step_text(0, "x")
    store.write_action_trace([])
    store.write_basic_info({"intent": BNP_GOAL})
    assert observed_outcome(store, BNP_URL)["outcome"] == "failed"


def test_a_sibling_that_cannot_be_read_leaves_the_ladder_in_force(tmp_path):
    run_dir = _run(tmp_path, BNP_URL)
    a = _persona(run_dir, "a", [BNP_URL + "/"], BNP_GOAL)
    broken = _persona(run_dir, "b", [BNP_URL + "/"], BNP_GOAL)
    (broken.trace_dir / "observation_trace.jsonl").unlink()
    (broken.trace_dir / "observation_trace.jsonl").mkdir()  # unreadable as a file
    assert observed_outcome(a, BNP_URL)["outcome"] == "failed"
