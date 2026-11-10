"""A task can presuppose what the site does not show — and the report must not blame a UI.

The pepco.pl run: "assemble decorations within a budget and check whether they are
available for pickup in a nearby store" on a site with no cart, no click-and-collect
and no per-store stock (its product pages say the offer differs per store). All
three personas "failed", none read the disclaimer, and the report's top
recommendation was to repair the store locator.

The probe says "feature absent" only from positive evidence gathered across
participants, and is silent otherwise; a false "the site has no X" is worse than
silence. These tests pin the false-positive cases as hard as the real one.
"""
import json

import pytest

from simulated_web_agent.main import premise_check as pc
from simulated_web_agent.main.attribution import unperceived_steps
from simulated_web_agent.main.evidence import _visible_text
from simulated_web_agent.main.iso_metrics import compute_iso_metrics, effective_outcome
from simulated_web_agent.main.reporter import _format_outcome_distribution
from simulated_web_agent.main.results_api import build_run
from simulated_web_agent.main.scoring import compute_ux_score
from simulated_web_agent.main.session_goal_state import observed_outcome
from simulated_web_agent.main.site_scope import UNDECLARED_SITE_CONTEXT, site_context_for
from simulated_web_agent.main.trace_store import TraceStore

PEPCO = "https://www.pepco.pl"
INTENT = ("Skompletuj 2–3 dekoracje do salonu (np. poduszki, lampka, tekstylia) w łącznym "
          "budżecie ok. 150 zł i sprawdź, czy są dostępne do odbioru w pobliskim sklepie")

# The shop's own words, verbatim from a pepco.pl product page.
FOOTER = ("Pokazane produkty są zapowiedzią nadchodzącej oferty i stopniowo staną się dostępne "
          "w sklepach. Oferta w sklepie może się różnić ceną, ilością i dostępnością produktów "
          "(niektóre produkty będą dostępne tylko w wybranych lokalizacjach). Zdjęcia "
          "przedstawiają przykładowe wzory.")
# Words that look like the features and are not them.
LOOKALIKES = ("Pepco koszyki łazienkowe z plecioną teksturą. Zestaw koszyków do przechowywania, "
              "3 sztuki, 15 zł. Pliki cookie związane z reklamami i ich odbiorcami.")


def _text(n, footer=FOOTER, extra=""):
    return (f"Poduszka dekoracyjna numer {n}. Poduszka z motywem dodaje wnętrzu przytulnego "
            f"charakteru, ma haftowane detale i dobrze prezentuje się na kanapie. Kolory: "
            f"beżowy. Rozmiary: ONE. {LOOKALIKES} {extra} {footer}")


def _raw(text, extra=""):
    # More than the stub threshold: a page that rendered.
    return f"<html><body><script>{'x' * 1800}</script><p>{text}</p>{extra}</body></html>"


def _product(n, footer=FOOTER, extra_text="", extra_html=""):
    text = _text(n, footer, extra_text)
    return (f"{PEPCO}/products/poduszka-{n}?Colour=Cream&Size=ONE", text, _raw(text, extra_html))


def _locator(extra=""):
    # Long enough to be a page that rendered (the probe ignores a stub of a locator).
    text = ("Znajdź sklep Pepco. Wpisz miasto lub kod pocztowy, aby zobaczyć sklepy w okolicy, "
            "ich adresy i godziny otwarcia. Lista sklepów pojawi się poniżej mapy, a po wybraniu "
            "sklepu zobaczysz jego szczegóły: adres, telefon, dojazd i godziny pracy w każdy "
            "dzień tygodnia. " + extra)
    return (f"{PEPCO}/store-locator", text, _raw(text))


def _home():
    text = "Pepco. Gazetka, Kolekcje, Dom, Dziecko. " + LOOKALIKES + " " * 200
    return (f"{PEPCO}/", text, _raw(text))


def _checkout(extra=""):
    # A delivery step of the order: rendered, and (without `extra`) no pickup on it.
    text = ("Dostawa i płatność. Wybierz sposób dostawy: kurier 12 zł, paczkomat 9 zł. Podaj adres, "
            "pod który mamy wysłać zamówienie, i numer telefonu dla kuriera. Wybierz metodę płatności: "
            "szybki przelew, karta albo BLIK. Sprawdź podsumowanie zamówienia i zatwierdź je. " + extra)
    return (f"{PEPCO}/checkout/delivery", text, _raw(text))


def _run(tmp_path, sessions, intent=INTENT, manifest=True):
    """sessions: {name: [(url, observation_text, raw_html | None), ...]}. Returns the stores."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    if manifest:
        (tmp_path / "run_manifest.json").write_text(
            json.dumps({"start_url": PEPCO + "/", "intent": intent}), encoding="utf-8")
    stores = []
    for name, steps in sessions.items():
        store = TraceStore(tmp_path / name)
        store.write_basic_info({"intent": intent, "persona": "Julia woli sklepy stacjonarne."})
        store.write_action_trace([json.dumps({"action": "click", "description": "c"})] * len(steps))
        for i, (url, text, raw) in enumerate(steps):
            store.append_observation({"tabs": [{"url": url, "is_active": True}]})
            store.write_observation_step_text(i, text)
            if raw is not None:
                store.write_raw_html(i, raw)
        stores.append(store)
    return stores


def _pepco_like(tmp_path, **kw):
    return _run(tmp_path, {
        "a": [_home(), _product(1), _product(2), _product(3), _locator()],
        "b": [_home(), _product(4), _product(5), _locator()],
        "c": [_home(), _product(6), _locator()],
    }, **kw)


def _status(stores, premise):
    return pc.check_stores(stores)["premises"][premise]


# --- what the intent presupposes ---------------------------------------------

def test_the_real_pepco_intent_presupposes_pickup_and_a_stock_check():
    assert pc.required_premises(INTENT) == ("pickup", "store_stock")


def test_an_intent_with_a_cart_presupposes_one():
    assert pc.required_premises("Dodaj lampkę do koszyka i przejdź do kasy") == ("cart",)


def test_intents_that_presuppose_nothing_presuppose_nothing():
    for intent in ("Znajdź kontakt do doradcy kredytowego",
                   # a product that is a basket is not a cart
                   "Znajdź koszyki do przechowywania i porównaj ich ceny",
                   # "available" as in open
                   "Sprawdź, czy sklep jest dostępny w niedzielę i podaj godziny otwarcia", ""):
        assert pc.required_premises(intent) == (), intent


# --- absent, and only from positive evidence ---------------------------------

def test_absent_when_enough_pages_across_participants_show_no_trace_of_it(tmp_path):
    stores = _pepco_like(tmp_path)
    result = pc.check_stores(stores)["premises"]
    assert set(result) == {"pickup", "store_stock"}
    stock = result["store_stock"]
    assert stock["status"] == "absent"
    assert (stock["product_pages"], stock["personas"]) == (6, 3)
    # the site's own words are quoted, and were read off the page they name
    assert stock["evidence"] and {e["persona_dir"] for e in stock["evidence"]} == {"a", "b", "c"}
    for e in stock["evidence"]:
        assert e["quote"].startswith("Oferta w sklepie może się różnić")
        assert e["quote"] in _source_text(stores, e)
    assert "Oferta w sklepie może się różnić" in stock["text"]
    # pickup: no disclaimer about it, but every participant also went to the locator
    assert result["pickup"]["status"] == "absent"
    assert result["pickup"]["locator_personas"] == 3
    assert "wyszukiwarki sklepów" in result["pickup"]["text"]


def _source_text(stores, evidence):
    """The text an evidence quote claims to be a substring of."""
    store = next(s for s in stores if s.trace_dir.name == evidence["persona_dir"])
    if evidence["source"] == "text":
        return store.read_observation_step_text(evidence["step"])
    raw = store.read_raw_html(evidence["step"])
    return raw if evidence["source"] == "html" else _visible_text(raw)


def test_the_result_says_what_the_sessions_did_not_find_never_what_the_site_is(tmp_path):
    result = pc.check_stores(_pepco_like(tmp_path))["premises"]
    for entry in result.values():
        assert "nie zaobserwowano" in entry["text"]
        for word in ("nie istnieje", "nie oferuje", "serwis nie ma", "brak "):
            assert word not in entry["text"].lower()
    lines = pc.gap_context_lines(TraceStore(tmp_path / "a"), run_level=True)
    assert lines == []  # nothing is read until the result is written
    pc.write_premise_check(tmp_path, pc.check_stores(_pepco_like(tmp_path)))
    for line in pc.gap_context_lines(TraceStore(tmp_path / "a")):
        assert "granic" in line and "wady interfejsu" in line


def test_a_gap_the_site_does_not_state_is_worded_as_not_found_and_may_still_be_reported(tmp_path):
    # pickup is absent on the locator alone; the stock check is what the site's own copy
    # says varies by store. The first must not be told to the passes as a fact about the
    # site, nor forbid reporting a locator that is broken.
    stores = _pepco_like(tmp_path)
    pc.write_premise_check(tmp_path, pc.check_stores(stores))
    assert [(g["premise"], g["basis"]) for g in pc.run_gaps(tmp_path)] == [
        ("pickup", "locator"), ("store_stock", "site")]
    lines = pc.gap_context_lines(TraceStore(tmp_path / "a"))
    pickup, stock = (next(line for line in lines if f"„{pc.LABELS[p]}”" in line)
                     for p in ("pickup", "store_stock"))
    assert "Nie traktuj jej braku jako wady interfejsu" in stock
    assert "opisz to jako granicę zakresu serwisu" in stock
    assert "Nie traktuj" not in pickup and "nie zalecaj" not in pickup
    assert "serwis sam tego nie potwierdza" in pickup and "zgłoś jak każdą inną" in pickup
    assert "może to być granica zakresu serwisu" in pickup


def test_lookalikes_are_not_a_cart_a_pickup_or_a_stock_check(tmp_path):
    # "koszyki do przechowywania" are products; "odbiorcami" is a cookie dialog. If either
    # counted as the feature, the premise would read "present" and the probe would be blind.
    stores = _pepco_like(tmp_path, intent=INTENT + " Dodaj do koszyka.")
    result = pc.check_stores(stores)["premises"]
    assert result["pickup"]["status"] == "absent"
    assert result["cart"]["status"] != "present"


# --- a site that has it is never called absent --------------------------------

def test_a_cart_the_perceiver_dropped_but_the_raw_page_has_is_present(tmp_path):
    cart = '<a aria-label="Koszyk, liczba produktów: 0" href="/cart"></a>'
    stores = _run(tmp_path, {
        "a": [_home(), _product(1), _product(2, extra_html=cart), _product(3)],
        "b": [_home(), _product(4), _product(5)],
    }, intent="Dodaj poduszkę do koszyka")
    entry = _status(stores, "cart")
    assert entry["status"] == "present"
    assert entry["evidence"][0]["source"] == "html"
    assert entry["evidence"][0]["quote"] in _source_text(stores, entry["evidence"][0])


def test_a_positive_hit_beats_a_disclaimer(tmp_path):
    shop = _product(7, extra_text="Nie prowadzimy sprzedaży online.")
    stores = _run(tmp_path, {
        "a": [_home(), _product(1), _product(2), shop],
        "b": [_home(), _product(4), _product(5, extra_text="Dodaj do koszyka")],
    }, intent="Dodaj poduszkę do koszyka")
    assert _status(stores, "cart")["status"] == "present"


def test_pickup_offered_on_the_locator_page_is_present(tmp_path):
    stores = _run(tmp_path, {
        "a": [_home(), _product(1), _product(2), _locator("Odbiór w sklepie w 2 godziny.")],
        "b": [_home(), _product(4), _product(5), _locator()],
    })
    assert _status(stores, "pickup")["status"] == "present"
    assert _status(stores, "store_stock")["status"] == "absent"


def test_a_stock_check_in_the_page_text_is_present(tmp_path):
    stores = _run(tmp_path, {
        "a": [_home(), _product(1), _product(2, extra_text="Sprawdź dostępność w sklepie."),
              _product(3)],
        "b": [_home(), _product(4), _product(5)],
    })
    assert _status(stores, "store_stock")["status"] == "present"


# --- unknown is the default ---------------------------------------------------

def test_two_product_pages_are_not_enough(tmp_path):
    stores = _run(tmp_path, {"a": [_home(), _product(1), _locator()],
                             "b": [_home(), _product(2), _locator()]})
    entry = _status(stores, "store_stock")
    assert (entry["status"], entry["reason"]) == ("unknown", "few_pages")


def test_one_participant_is_not_enough(tmp_path):
    # Five product pages, all seen by one participant; the others never got there.
    stores = _run(tmp_path, {
        "a": [_home()] + [_product(n) for n in range(1, 6)] + [_locator()],
        "b": [_home(), _locator()],
        "c": [_home(), _locator()],
    })
    entry = _status(stores, "store_stock")
    assert (entry["status"], entry["reason"]) == ("unknown", "few_personas")


def test_a_participant_who_never_reached_a_product_page_adds_nothing(tmp_path):
    stores = _run(tmp_path, {
        "a": [_home(), _product(1), _product(2), _product(3)],
        "b": [_home()],
    })
    assert _status(stores, "store_stock")["status"] == "unknown"


def test_a_session_that_crashed_before_the_relevant_page_adds_nothing(tmp_path):
    stores = _run(tmp_path, {
        "a": [_home(), _product(1), _product(2), _product(3)],
        "b": [],                      # never took a step
        "c": [_home()],               # died on the home page
    })
    stores[1].write_session_result({"steps_taken": 0, "ended_reason": "error"})
    entry = _status(stores, "store_stock")
    assert (entry["status"], entry["reason"]) == ("unknown", "few_personas")


def test_a_page_without_its_raw_html_is_never_counted(tmp_path):
    def bare(n):
        url, text, _ = _product(n)
        return url, text, None
    stores = _run(tmp_path, {
        "a": [_home(), bare(1), bare(2), bare(3)],
        "b": [_home(), bare(4), bare(5)],
    })
    entry = _status(stores, "store_stock")
    assert (entry["status"], entry["product_pages"]) == ("unknown", 0)


def test_a_stub_of_a_page_is_not_a_product_page(tmp_path):
    def stub(n):
        return (f"{PEPCO}/products/x-{n}", "Ładowanie…", "<html></html>")

    def unperceived(n):  # a large page the perceiver returned next to nothing for
        return (f"{PEPCO}/products/y-{n}", "Ładowanie…", _raw("x" * 3000))
    for name, make in (("stub", stub), ("unperceived", unperceived)):
        stores = _run(tmp_path / name, {"a": [_home(), make(1), make(2), make(3)],
                                        "b": [_home(), make(4), make(5)]})
        assert _status(stores, "store_stock")["product_pages"] == 0, name


def test_a_bot_challenge_is_not_a_product_page(tmp_path):
    def walled(n):
        text = "Performing verification. " + "Verify you are human by completing the action. " * 8
        return (f"{PEPCO}/products/poduszka-{n}", text, _raw(text))
    stores = _run(tmp_path, {"a": [walled(1), walled(2), walled(3)],
                             "b": [walled(4), walled(5)]})
    assert _status(stores, "store_stock")["product_pages"] == 0


def test_without_corroboration_a_missing_control_is_only_unknown(tmp_path):
    # Pages with no stock control, but no disclaimer and nobody opened the locator.
    stores = _run(tmp_path, {
        "a": [_home(), _product(1, footer=""), _product(2, footer=""), _product(3, footer="")],
        "b": [_home(), _product(4, footer=""), _product(5, footer="")],
    })
    entry = _status(stores, "store_stock")
    assert (entry["status"], entry["reason"]) == ("unknown", "no_corroboration")


def test_one_locator_visitor_does_not_corroborate(tmp_path):
    stores = _run(tmp_path, {
        "a": [_home(), _product(1, footer=""), _product(2, footer=""), _locator()],
        "b": [_home(), _product(4, footer=""), _product(5, footer="")],
    })
    assert _status(stores, "pickup")["status"] == "unknown"


def test_a_locator_that_did_not_render_corroborates_nothing(tmp_path):
    # A map that never loaded: the participants "went to the locator" and saw a stub, so
    # they cannot say the page holds no pickup. The page text is a few words / the markup
    # a shell; both visits would otherwise read as two corroborating sessions.
    def blank(url_text, raw):
        return (f"{PEPCO}/store-locator", url_text, raw)
    for name, locator in (("stub", blank("Ładowanie…", "<html></html>")),
                          ("unperceived", blank("Ładowanie…", _raw("x" * 3000)))):
        stores = _run(tmp_path / name, {
            "a": [_home(), _product(1, footer=""), _product(2, footer=""), locator],
            "b": [_home(), _product(4, footer=""), _product(5, footer=""),
                  _product(6, footer=""), locator],
        })
        entry = _status(stores, "pickup")
        assert (entry["status"], entry["reason"], entry["locator_personas"]) == (
            "unknown", "no_corroboration", 0), name


def _pickup_run(tmp_path, locator=None, a_extra=(), **product_kw):
    """Two sessions that read five product pages (the first with `product_kw`) and went
    to the locator: the shape in which pickup is read as absent."""
    locator = locator or _locator()
    return _run(tmp_path, {
        "a": [_home(), _product(1, footer="", **product_kw), _product(2, footer=""),
              _product(3, footer=""), *a_extra, locator],
        "b": [_home(), _product(4, footer=""), _product(5, footer=""), locator],
    })


@pytest.mark.parametrize("wording", [
    "Ładowanie mapy…",
    "Włącz JavaScript, aby zobaczyć wyniki wyszukiwania.",
    "This page requires JavaScript to show the stores.",
    "JavaScript jest wyłączony w Twojej przeglądarce.",
])
def test_a_locator_that_only_shows_its_loading_shell_corroborates_nothing(tmp_path, wording):
    # Long enough to pass for a page, but what it says is that the map has not loaded: a
    # broken locator is a defect to report, not a reason to say the shop has no pickup.
    entry = _status(_pickup_run(tmp_path, _locator(wording)), "pickup")
    assert (entry["status"], entry["reason"], entry["locator_personas"]) == (
        "unknown", "no_corroboration", 0)


def test_a_locator_that_was_captured_but_never_read_corroborates_nothing(tmp_path):
    # The session was cut right after the click that opened the locator: the page is in the
    # trace, nobody read it, so nobody can say what it did not show.
    stores = _pickup_run(tmp_path)
    for store in stores:
        last = len(store.read_observations()) - 1
        store.write_action_trace(store.read_action_trace()[:-1])
        store.write_observation_step_text(last, "")
        assert unperceived_steps(store) == [last]
    entry = _status(stores, "pickup")
    assert (entry["status"], entry["reason"], entry["locator_personas"]) == (
        "unknown", "no_corroboration", 0)


# --- a shop with a cart offers pickup at its checkout --------------------------

def test_pickup_is_not_absent_on_the_locator_alone_when_the_shop_has_a_cart_nobody_got_through(tmp_path):
    # Pickup is normally chosen at the checkout, which nobody reached: the locator proves nothing.
    stores = _pickup_run(tmp_path / "text", extra_text="Dodaj do koszyka")
    entry = _status(stores, "pickup")
    assert (entry["status"], entry["reason"], entry["locator_personas"]) == (
        "unknown", "checkout_not_seen", 2)
    # the cart link the perceiver dropped counts too
    cart = '<a aria-label="Koszyk, liczba produktów: 0" href="/cart"></a>'
    entry = _status(_pickup_run(tmp_path / "html", extra_html=cart), "pickup")
    assert (entry["status"], entry["reason"]) == ("unknown", "checkout_not_seen")
    # ... while a stock check is on the product page, which they did read
    assert _status(stores, "store_stock")["status"] == "absent"


def test_a_checkout_that_was_seen_without_pickup_lets_the_locator_corroborate(tmp_path):
    stores = _pickup_run(tmp_path / "seen", extra_text="Dodaj do koszyka", a_extra=(_checkout(),))
    assert _status(stores, "pickup")["status"] == "absent"
    # a login wall on the way to the checkout is not a checkout
    wall = (f"{PEPCO}/checkout/login", _checkout()[1], _checkout()[2])
    stores = _pickup_run(tmp_path / "wall", extra_text="Dodaj do koszyka", a_extra=(wall,))
    assert _status(stores, "pickup")["reason"] == "checkout_not_seen"
    # and what pickup the checkout offers makes it present
    stores = _pickup_run(tmp_path / "offers", extra_text="Dodaj do koszyka",
                         a_extra=(_checkout("Odbiór osobisty w sklepie: 0 zł."),))
    assert _status(stores, "pickup")["status"] == "present"


def test_a_site_without_a_cart_is_still_corroborated_by_the_locator(tmp_path):
    assert _status(_pickup_run(tmp_path), "pickup")["status"] == "absent"


# --- the ordinary Polish wording of the feature is the feature ------------------

PICKUP_WORDING = (
    "Odbiór zamówienia w sklepie: bezpłatny.",
    "Możliwość odbioru osobistego w salonie.",
    "Zamów z odbiorem w sklepie Pepco.",
    "Wybierz punkt odbioru.",
    "Darmowa dostawa do sklepu.",
    "Zaloguj się, aby odebrać zamówienie w sklepie.",
    "Opcje odbioru i dostawy",
    "Możliwy odbiór osobisty",
    "Zamów i odbierz w ciągu 2 godzin.",
    "Dostępne do odbioru jutro.",
    "Odbierz w sklepie",
    "Click & Collect",
)


@pytest.mark.parametrize("sentence", PICKUP_WORDING)
def test_inflected_pickup_wording_is_present_in_the_text_and_in_the_markup(tmp_path, sentence):
    assert _status(_pickup_run(tmp_path / "base"), "pickup")["status"] == "absent"
    for where, kw in (("text", {"extra_text": sentence}),
                      ("html", {"extra_html": f"<p>{sentence}</p>"})):
        stores = _pickup_run(tmp_path / where, **kw)
        entry = _status(stores, "pickup")
        assert entry["status"] == "present", (sentence, where)
        assert entry["evidence"][0]["quote"] in _source_text(stores, entry["evidence"][0])


@pytest.mark.parametrize("sentence", [
    "Odbiornik DVB-T z odbiorem sygnału cyfrowego.",
    "Sklepienie katedry ma dwadzieścia metrów.",
    "Odbiornik radiowy stoi pod sklepieniem.",
    "Odbierz swój kod rabatowy w aplikacji.",
    "Płatność przy odbiorze.",
    "Dostawca do sklepów hurtowych.",
    "Dostawa do domu: 12 zł.",
])
def test_words_that_only_look_like_pickup_are_not_pickup(tmp_path, sentence):
    for where, kw in (("text", {"extra_text": sentence}),
                      ("html", {"extra_html": f"<p>{sentence}</p>"})):
        entry = _status(_pickup_run(tmp_path / where, **kw), "pickup")
        assert entry["status"] == "absent", (sentence, where)


STOCK_WORDING = (
    "Sprawdź dostępność w sklepie.",
    "Dostępność produktu w sklepie: 3 szt.",
    "Sprawdzenie dostępności w sklepie",
    "Dostępny w moim sklepie",
    "Dostępne w Twoim sklepie od ręki.",
    "Wybierz sklep",
    "Mój sklep: Warszawa",
    "Stan magazynowy: 12",
    "Dostępność w sklepach: sprawdź online.",
)


@pytest.mark.parametrize("sentence", STOCK_WORDING)
def test_the_wording_of_a_per_store_stock_check_is_present(tmp_path, sentence):
    def stock(path, **kw):
        stores = _run(path, {"a": [_home(), _product(1, **kw), _product(2), _product(3)],
                             "b": [_home(), _product(4), _product(5)]})
        return _status(stores, "store_stock")["status"]
    assert stock(tmp_path / "base") == "absent"
    assert stock(tmp_path / "text", extra_text=sentence) == "present"
    assert stock(tmp_path / "html", extra_html=f"<p>{sentence}</p>") == "present"


def test_a_shops_own_availability_disclaimer_is_not_a_stock_check(tmp_path):
    # "Dostępność w sklepach może się różnić" names the thing it says is not guaranteed.
    said = "Dostępność w sklepach może się różnić."
    stores = _run(tmp_path, {"a": [_home(), _product(1, footer=said), _product(2, footer=said),
                                   _product(3, footer=said)],
                             "b": [_home(), _product(4, footer=said), _product(5, footer=said)]})
    entry = _status(stores, "store_stock")
    assert entry["status"] == "absent"
    assert entry["evidence"][0]["quote"] == "Dostępność w sklepach może się różnić"


# --- what the site says about itself must be said by more than one page ----------

PER_PRODUCT = "Ten model: dostępne wyłącznie w sklepach stacjonarnych."


def _one_remark(tmp_path, remark, pages=1, intent=INTENT, locators=False, footer=""):
    """`remark` on `pages` product pages of the first participant (one page by default)."""
    mine = [_product(n, footer=footer, extra_text=remark) for n in range(1, pages + 1)]
    extra = [_locator()] if locators else []
    return _run(tmp_path, {
        "a": [_home(), *mine, *[_product(n, footer=footer) for n in range(10, 13)], *extra],
        "b": [_home(), _product(4, footer=footer), _product(5, footer=footer), *extra],
    }, intent=intent)


def test_one_products_line_about_stores_says_nothing_about_pickup_or_a_stock_check(tmp_path):
    # It is about one product, and suggests the stores have it: not "the site has no pickup".
    for pages in (1, 3):
        result = pc.check_stores(_one_remark(tmp_path / f"p{pages}", PER_PRODUCT, pages))["premises"]
        for premise in ("pickup", "store_stock"):
            assert (result[premise]["status"], result[premise]["reason"]) == (
                "unknown", "no_corroboration"), (premise, pages)


def test_a_per_product_line_counts_for_the_cart_only_when_it_repeats(tmp_path):
    cart = "Dodaj poduszkę do koszyka"
    one = _status(_one_remark(tmp_path / "one", PER_PRODUCT, intent=cart), "cart")
    assert one["status"] == "unknown"
    two = _status(_one_remark(tmp_path / "two", PER_PRODUCT, pages=2, intent=cart), "cart")
    assert (two["status"], two["evidence"][0]["quote"]) == (
        "absent", "Ten model: dostępne wyłącznie w sklepach stacjonarnych")


def test_a_sitewide_statement_on_one_page_is_not_enough_but_two_pages_or_two_people_are(tmp_path):
    said = "Nie prowadzimy sprzedaży online."
    cart = "Dodaj poduszkę do koszyka"
    assert _status(_one_remark(tmp_path / "1", said, intent=cart), "cart")["status"] == "unknown"
    assert _status(_one_remark(tmp_path / "2", said, pages=2, intent=cart), "cart")["status"] == "absent"
    # one page each, two participants
    stores = _run(tmp_path / "people", {
        "a": [_home(), _product(1, extra_text=said), _product(2), _product(3)],
        "b": [_home(), _product(4, extra_text=said), _product(5)]}, intent=cart)
    assert _status(stores, "cart")["status"] == "absent"
    # the same product in two variants is one page
    def variant(colour):
        url, text, raw = _product(1, extra_text=said)
        return url.replace("Colour=Cream", f"Colour={colour}"), text, raw
    stores = _run(tmp_path / "variants", {
        "a": [_home(), variant("Cream"), variant("Beige"), _product(2), _product(3)],
        "b": [_home(), _product(4), _product(5)]}, intent=cart)
    assert _status(stores, "cart")["status"] == "unknown"


def test_a_single_quote_is_not_presented_as_what_the_site_says_when_the_locator_decides(tmp_path):
    # Pickup is absent here on the locator (two sessions), with a sitewide sentence on a single page:
    # the sentence is not quoted as the site's statement, the locator's evidence is what is given.
    stores = _one_remark(tmp_path, "Nie prowadzimy sprzedaży online.", locators=True)
    entry = _status(stores, "pickup")
    assert entry["status"] == "absent" and entry["evidence"] == []
    assert "Serwis sam zastrzega" not in entry["text"] and "wyszukiwarki sklepów" in entry["text"]


def test_a_cart_is_absent_only_when_the_site_says_so_itself(tmp_path):
    shop = {"a": [_home(), _product(1), _product(2), _product(3)],
            "b": [_home(), _product(4), _product(5)]}
    stores = _run(tmp_path, shop, intent="Dodaj poduszkę do koszyka")
    assert _status(stores, "cart")["status"] == "unknown"

    said = "Nie prowadzimy sprzedaży online."
    stores = _run(tmp_path / "said", {
        "a": [_home(), _product(1, extra_text=said), _product(2, extra_text=said), _product(3)],
        "b": [_home(), _product(4), _product(5)]}, intent="Dodaj poduszkę do koszyka")
    entry = _status(stores, "cart")
    assert entry["status"] == "absent"
    assert entry["evidence"][0]["quote"] == "Nie prowadzimy sprzedaży online"


def test_a_competitors_pages_neither_count_nor_decide(tmp_path):
    rival = ("https://www.hebe.pl/products/krem-1", "Dodaj do koszyka. Odbiór w sklepie. " * 12,
             _raw("Dodaj do koszyka. Odbiór w sklepie. " * 12))
    stores = _run(tmp_path, {
        "a": [_home(), _product(1), _product(2), _product(3), rival],
        "b": [_home(), _product(4), _product(5), rival],
    })
    result = pc.check_stores(stores)["premises"]
    assert result["pickup"]["status"] != "present"
    assert result["store_stock"]["product_pages"] == 5  # the rival's page is not ours


def test_the_same_product_in_two_variants_is_one_page(tmp_path):
    def variant(n, colour):
        url, text, raw = _product(n)
        return url.replace("Colour=Cream", f"Colour={colour}"), text, raw
    stores = _run(tmp_path, {
        "a": [_home(), variant(1, "Cream"), variant(1, "Beige"), variant(1, "Pink")],
        "b": [_home(), variant(1, "Green"), variant(2, "Cream")],
    })
    assert _status(stores, "store_stock")["product_pages"] == 2


def test_a_run_whose_intents_presuppose_nothing_probes_nothing(tmp_path):
    stores = _pepco_like(tmp_path, intent="Znajdź kontakt do sklepu")
    assert pc.check_stores(stores) == {"version": 1, "premises": {}}


# --- the file -----------------------------------------------------------------

def test_the_result_is_stored_and_read_back(tmp_path):
    stores = _pepco_like(tmp_path)
    result = pc.check_stores(stores)
    pc.write_premise_check(tmp_path, result)
    assert pc.read_premise_check(tmp_path) == result
    assert [g["premise"] for g in pc.run_gaps(tmp_path)] == ["pickup", "store_stock"]
    assert all(g["text"] for g in pc.run_gaps(tmp_path))


def test_a_missing_or_broken_file_reads_as_nothing_verified(tmp_path):
    assert pc.read_premise_check(tmp_path) == {}
    (tmp_path / pc.FILE).write_text("{not json", encoding="utf-8")
    assert pc.read_premise_check(tmp_path) == {}
    (tmp_path / pc.FILE).write_text(json.dumps({"premises": []}), encoding="utf-8")
    assert pc.read_premise_check(tmp_path) == {}
    assert pc.run_gaps(tmp_path) == []


def test_a_finished_run_gets_its_result_computed_once(tmp_path):
    _pepco_like(tmp_path)
    first = pc.ensure_premise_check(tmp_path)
    assert first["premises"]["store_stock"]["status"] == "absent"
    stored = json.loads((tmp_path / pc.FILE).read_text(encoding="utf-8"))
    assert stored == first
    # a stored result is not recomputed
    (tmp_path / pc.FILE).write_text(json.dumps({"version": 1, "premises": {
        "pickup": {"status": "unknown"}}}), encoding="utf-8")
    assert pc.ensure_premise_check(tmp_path)["premises"] == {"pickup": {"status": "unknown"}}


def test_a_folder_that_is_not_a_run_is_not_scanned(tmp_path):
    _pepco_like(tmp_path, manifest=False)
    assert pc.ensure_premise_check(tmp_path) == {}
    assert not (tmp_path / pc.FILE).exists()


# --- what the audit passes are told -------------------------------------------

def test_site_context_is_the_declared_text_when_nothing_is_absent(tmp_path):
    store = _run(tmp_path, {"a": [_home()]})[0]
    store.write_basic_info({"intent": INTENT, "site_context": "Katalog bez zakupów online."})
    assert site_context_for(store) == "Katalog bez zakupów online."


def test_an_undeclared_context_gets_the_one_default(tmp_path):
    store = _run(tmp_path, {"a": [_home()]})[0]
    assert site_context_for(store) == UNDECLARED_SITE_CONTEXT
    assert "nie zakładaj, że to sklep internetowy" in UNDECLARED_SITE_CONTEXT
    assert site_context_for(store, default="") == ""
    assert site_context_for(store, declared="Bank.") == "Bank."


def test_verified_gaps_are_added_to_the_context_for_the_participant_who_asked(tmp_path):
    stores = _pepco_like(tmp_path)
    pc.write_premise_check(tmp_path, pc.check_stores(stores))
    stores[1].write_basic_info({"intent": "Znajdź poduszkę i sprawdź jej cenę",
                                "site_context": "Sklep z dekoracjami."})
    asked = site_context_for(stores[0])
    assert asked.startswith(UNDECLARED_SITE_CONTEXT)
    assert "odbiór zamówienia w sklepie" in asked
    assert "sprawdzenie dostępności produktu w konkretnym sklepie" in asked
    assert "Oferta w sklepie może się różnić" in asked
    # a participant whose task did not need them is not told about them ...
    other = site_context_for(stores[1])
    assert other == "Sklep z dekoracjami."
    # ... but the aggregate speaks for everyone
    assert "odbiór zamówienia w sklepie" in site_context_for(stores[1], run_level=True)


# --- the goal ladder ----------------------------------------------------------

def _gap_file(tmp_path, premise, status):
    pc.write_premise_check(tmp_path, {"version": 1, "premises": {
        premise: {"status": status, "text": "W 3 sesjach na 6 stronach produktów nie zaobserwowano: "
                  "koszyk i składanie zamówienia online."}}})


def _goal_store(tmp_path, intent, steps=1, success_condition=None):
    store = TraceStore(tmp_path / "persona")
    info = {"intent": intent}
    if success_condition:
        info["success_condition"] = success_condition
    store.write_basic_info(info)
    store.write_action_trace([json.dumps({"action": "click", "description": "c"})] * steps)
    for i in range(steps):
        store.append_observation({"tabs": [{"url": f"{PEPCO}/products/poduszka-{i}", "is_active": True}]})
        store.write_observation_step_text(i, "Poduszka. Cena 20 zł.")
    return store


def test_a_cart_verified_absent_caps_the_target_instead_of_failing_the_persona(tmp_path):
    store = _goal_store(tmp_path, "Dodaj poduszkę do koszyka")
    assert observed_outcome(store, PEPCO)["outcome"] == "failed"  # nothing known: as before
    _gap_file(tmp_path, "cart", "absent")
    out = observed_outcome(store, PEPCO)
    assert (out["target_rung"], out["target_source"]) == ("product_viewed", "capped:cart")
    assert out["outcome"] == "success"
    assert out["premise_gaps"] == ["cart"]


def test_an_unverified_cart_changes_nothing(tmp_path):
    store = _goal_store(tmp_path, "Dodaj poduszkę do koszyka")
    for status in ("unknown", "present"):
        _gap_file(tmp_path, "cart", status)
        out = observed_outcome(store, PEPCO)
        assert (out["target_rung"], out["target_source"], out["outcome"]) == (
            "cart_has_items", "inferred", "failed")
        assert out["premise_gaps"] == []


def test_an_explicit_success_condition_is_not_capped(tmp_path):
    store = _goal_store(tmp_path, "Dodaj poduszkę do koszyka", success_condition="cart_has_items")
    _gap_file(tmp_path, "cart", "absent")
    out = observed_outcome(store, PEPCO)
    assert (out["target_source"], out["outcome"]) == ("explicit", "failed")


def test_pickup_and_stock_have_no_rung_but_are_listed(tmp_path):
    store = _goal_store(tmp_path, INTENT, steps=3)
    pc.write_premise_check(tmp_path, {"version": 1, "premises": {
        "pickup": {"status": "absent", "text": "t"}, "store_stock": {"status": "absent", "text": "t"}}})
    out = observed_outcome(store, PEPCO)
    assert out["premise_gaps"] == ["pickup", "store_stock"]
    assert (out["target_rung"], out["target_source"], out["outcome"]) == (
        "product_viewed", "inferred", "success")


def test_a_capped_target_is_still_checked_against_the_site_having_a_shop_at_all(tmp_path):
    # Nobody reached a product page: the ladder cannot tell failure from absence, whatever
    # the target was capped at.
    (tmp_path / "run_manifest.json").write_text(json.dumps({"start_url": PEPCO}), encoding="utf-8")
    store = TraceStore(tmp_path / "persona")
    store.write_basic_info({"intent": "Dodaj poduszkę do koszyka"})
    store.write_action_trace([json.dumps({"action": "click", "description": "c"})])
    store.append_observation({"tabs": [{"url": f"{PEPCO}/", "is_active": True}]})
    store.write_observation_step_text(0, "Pepco.")
    _gap_file(tmp_path, "cart", "absent")
    out = observed_outcome(store, PEPCO)
    assert (out["target_source"], out["ladder_applies"], out["outcome"]) == (
        "capped:cart", False, "unknown")


# --- what the rest of the report does with it ---------------------------------

def test_a_persona_whose_only_shortfall_is_the_absent_cart_is_not_charged(tmp_path):
    store = _goal_store(tmp_path, "Dodaj poduszkę do koszyka")

    def deductions():
        outcome, source = effective_outcome(store)
        res = compute_ux_score({"heuristic": {}}, outcomes={"a": outcome},
                               outcome_sources={"a": source})
        return outcome, [d for d in res["deductions"] if d["kind"] == "task"]

    outcome, charged = deductions()
    assert outcome == "failed" and charged, "without the finding the persona is charged"
    _gap_file(tmp_path, "cart", "absent")
    assert deductions() == ("success", [])
    eff = compute_iso_metrics(store)["effectiveness"]
    assert (eff["outcome_source"], eff["target_source"], eff["premise_gaps"]) == (
        "observed", "capped:cart", ["cart"])


def test_the_outcome_distribution_says_which_part_of_the_task_it_speaks_for():
    gaps = {"a": ["pickup", "store_stock"], "b": ["pickup"], "c": []}
    outcomes = {"a": "success", "b": "success", "c": "success"}
    observed = _format_outcome_distribution(outcomes, [], 3, {k: "observed" for k in outcomes},
                                            premise_gaps=gaps)
    declared = _format_outcome_distribution(outcomes, [], 3, {k: "declared" for k in outcomes},
                                            premise_gaps=gaps)
    for text in (observed, declared):
        assert ("2/3 zadań zakładało funkcję, której sesje nie zaobserwowały w serwisie "
                "(odbiór zamówienia w sklepie; sprawdzenie dostępności produktu w konkretnym "
                "sklepie)") in text
        assert "nie jest to wada interfejsu" in text
    # what only the locator backs is not called "not an interface defect": a locator that
    # shows nothing may be the defect
    for basis in ({"pickup": "locator", "store_stock": "site"},
                  {"pickup": "locator", "store_stock": "locator"}):
        text = _format_outcome_distribution(outcomes, [], 3, {k: "observed" for k in outcomes},
                                            premise_gaps=gaps, premise_basis=basis)
        assert "nie przesądzaj, czy to wada interfejsu" in text
        assert "nie jest to wada interfejsu" not in text
    text = _format_outcome_distribution(outcomes, [], 3, {k: "observed" for k in outcomes},
                                        premise_gaps=gaps,
                                        premise_basis={"pickup": "site", "store_stock": "site"})
    assert "nie jest to wada interfejsu" in text
    for text in (_format_outcome_distribution(outcomes, [], 3, {k: "observed" for k in outcomes}),
                 _format_outcome_distribution(outcomes, [], 3, {k: "observed" for k in outcomes},
                                              premise_gaps={"a": [], "b": []})):
        assert "zakładało funkcję" not in text


def test_the_dashboard_payload_lists_what_the_sessions_did_not_find(tmp_path):
    runs = tmp_path / "runs"
    run_dir = runs / "2026-10-20_10-00-00_test"
    run_dir.mkdir(parents=True)
    (run_dir / "run_manifest.json").write_text(json.dumps(
        {"run_id": run_dir.name, "site": "pepco.pl", "status": "done"}), encoding="utf-8")
    assert build_run(run_dir.name, runs)["meta"]["premiseGaps"] == []
    pc.write_premise_check(run_dir, {"version": 1, "premises": {
        "store_stock": {"status": "absent", "text": "Tekst luki."},
        "pickup": {"status": "unknown", "reason": "few_pages"}}})
    assert build_run(run_dir.name, runs)["meta"]["premiseGaps"] == [
        {"premise": "store_stock", "label": pc.LABELS["store_stock"], "text": "Tekst luki.",
         "basis": "locator"}]
