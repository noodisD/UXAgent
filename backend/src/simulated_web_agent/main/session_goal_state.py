"""What the session actually did — evidence-grounded task-completion outcome.

METHODOLOGY_PLAN §4.2. Task completion (success / partial / failed) was, until
this module, `objective_reflection.outcome` — a gemini-2.5-pro verdict on its own
run (iso_metrics.py docstring: "NOT measured… an LLM's verdict"). Nothing checked
it against the trace. This module derives the outcome from what the trace SHOWS.

Two judgments hide in "task completion", and only the LLM was ever making both:
  1. what counts as success for THIS task? — a property of the authored intent,
     derived once (deterministic keyword mapping over the intent, or an explicit
     `success_condition`), transparent and overridable. `infer_target_rung`.
  2. did this run achieve it? — a property of the trace, pure evidence.
     `compute_goal_state`.
The verdict (#2) is always evidence; the LLM may only ever help with #1.

The shopping goal ladder, ordered:
  none < searched < product_viewed < cart_reached < cart_has_items <
  order_summary_reached
Each rung is decided from URL markers first (robust — the same reason
session_capabilities uses URLs, not fuzzy text: "Do koszyka"/"w koszyku"/"Do
kasy" appear on product pages and mini-carts) and confirmed with observation
text only where the URL cannot carry it (items in cart, the summary block). Every
rung is same-site scoped: a competitor's `doz.pl/koszyk` must not count (the
cross-site contamination site_scope.py exists to stop).

The ladder tops out at *order summary / checkout initiation*, never a placed
order — the tasks say "bez finalizacji płatności", so requiring a completed
purchase would be wrong. And checkout itself is login-gated: on dm, clicking
"Do kasy" leads to signin.dm.pl and the agent cannot register. So the reachable
ceiling — and therefore success — is reaching the order summary OR proceeding to
checkout (the "Do kasy" click / the login gate) with items in the cart. Penalising
a persona for a login wall it could never pass would repeat the "marked down for
a capability the site never offered" mistake the reflection prompt already warns
against.

Fail-toward-declared: when the target is undecidable (non-shopping / oblique
intent) or the trace has no on-site evidence, `observed_outcome` returns
`unknown` and the caller (iso_metrics) keeps the declared outcome, labelled.
The same when the site has no shop: an inferred target is only measured if some
session of the run reached a product, cart or checkout page (`ladder_applies`).

A task can presuppose what the site does not show (premise_check). A cart the run
verified absent caps an inferred target at product_viewed ("capped:cart"): the
rung cannot be reached, so not reaching it is no failure. The other presupposed
features (pickup, stock in a store) have no rung; they are listed in
`premise_gaps` and the outcome speaks for the part of the task that could be done.
"""
from __future__ import annotations

import re

from .site_scope import same_site, target_url_for
from .trace_store import TraceStore, parse_action as _parse_action

# --- the ladder ------------------------------------------------------------
RUNGS = (
    "none",
    "searched",
    "product_viewed",
    "cart_reached",
    "cart_has_items",
    "order_summary_reached",
)
_RUNG_INDEX = {name: i for i, name in enumerate(RUNGS)}

# The failed->partial boundary (user calibration): "partial" requires that an
# item was actually ADDED TO CART — merely viewing/comparing products or landing
# on an empty cart page is "failed". So a checkout task only earns "partial" once
# cart_has_items is reached. (For a shallow task whose target is at/below this
# rung, there is no partial band — it is binary success/failed.)
_PARTIAL_FLOOR = "cart_has_items"

# --- URL markers (primary evidence; substring match on the lowered URL) -----
#
_PRODUCT_URL_MARKERS = ("/p/", "/produkt", "/product/")
# The Shopify-style product page (pepco.pl: /products/poduszka-324777?Colour=..).
# It needs a handle after the segment: the bare /products/ index is a listing, and
# '/collections/x/products/y' is still a product.
_PRODUCT_HANDLE_RE = re.compile(r"/products/[^/?#]+")
_CART_URL_MARKERS = ("koszyk", "/cart", "checkout", "/kasa")
_SEARCH_URL_MARKERS = ("?query", "&query", "/search", "/szukaj", "szukaj=", "q=")
_CHECKOUT_URL_MARKERS = ("checkout", "/kasa")
# The checkout ceiling is login-gated: on dm, clicking "Do kasy" leads to
# signin.dm.pl/authentication, and the agent cannot register. Reaching that gate
# (with items in the cart) IS the furthest a synthetic user can go — the site's
# real ceiling — so it counts as success, not as a shortfall. Verified: Ola's
# step 27 "Do kasy" click landed on signin.dm.pl/.../web-login at step 28.
_CHECKOUT_GATE_URL_MARKERS = ("signin", "/login", "authentication", "logowanie")
# A checkout-initiation action ("proceed to checkout") in the action trace.
# Specific checkout-initiation phrasings only. Looser ones ("order summary",
# "podsumowani") backfired: an agent clicking Menu "to find ... the cart or order
# summary" matched, before it had even added to cart. Proceeding-to-summary that
# isn't phrased this way is still caught by the login/checkout gate URL below.
_CHECKOUT_ACTION_MARKERS = (
    "do kasy", "przejdź do kasy", "przejdz do kasy", "checkout",
    "sfinalizuj", "złóż zam", "zloz zam", "finalizuj zam",
)
_CHECKOUT_ACTION_TYPES = ("click", "navigate", "goto")

# --- observation-text markers (confirmation only; word-boundary matched) ----
_SEARCH_OBS_MARKERS = ("wyniki wyszukiwania",)
# A line item is really in the cart (not a product-tile "w koszyku" toast).
_CART_ITEMS_OBS_MARKERS = ("produkty łącznie", "produkty lacznie")
_CART_REMOVE_OBS_MARKERS = ("usuń", "usun")
# The add-to-cart CONFIRMATION toast — proves an item was added even when the
# agent never opened the cart page (the dm reality: adds happen on the product
# or search page via a "Dodano do koszyka" toast, and most sessions never visit
# /koszyk). Without this, a session that added to cart reads as "failed".
_ADDED_TO_CART_OBS_MARKERS = (
    "dodano do koszyk", "dodano produkt", "produkt dodano", "dodano 1 produkt",
    "added to cart", "added to basket",
)
# The order-summary block; require the summary heading AND the checkout CTA to
# co-occur — "Do kasy" alone is a persistent mini-cart button on many pages.
_SUMMARY_HEADING_MARKERS = ("podsumowanie",)
_SUMMARY_CTA_MARKERS = ("przejdź do kasy", "przejdz do kasy")

_PRICE_RE = re.compile(r"\d+(?:[.,]\d{1,2})?\s*zł")


def has_marker(text: str, markers) -> bool:
    """Whole-word-ish marker match: a word boundary before the marker so
    "nie przechodz" does not match "wielokrotNIE PRZECHODZić" (a real false
    match caught on a legit compare-friction finding). For Polish observation
    text only — URL markers use plain substring (a leading "/" has no boundary).

    Leading boundary only, and deliberately so: markers are truncated stems that
    must still match Polish inflection ("porówn" -> porównaj / porównanie /
    porównać). A trailing boundary would reject all of those. The cost is that a
    stem short enough to prefix an unrelated word matches it too, so the length
    of each marker is load-bearing — see the note on _TARGET_KEYWORDS.
    """
    return any(re.search(r"(?<!\w)" + re.escape(m), text) for m in markers)


def _url_has(url: str, markers) -> bool:
    return any(m in url for m in markers)


def is_product_url(url: str) -> bool:
    """Is this URL a product page? One definition for the ladder, the claim
    verifier and the premise probe."""
    u = (url or "").lower()
    return _url_has(u, _PRODUCT_URL_MARKERS) or bool(_PRODUCT_HANDLE_RE.search(u))


def _checkout_initiated_step(store: TraceStore, min_step: int = 0) -> tuple[int, str] | None:
    """(step, url) of the agent clicking 'Do kasy' / proceeding to checkout at or
    after `min_step`, or None. The action trace is where this lives — the
    resulting page is a login wall, so the observation cannot show it. `min_step`
    enforces order: you cannot proceed to checkout before adding to the cart."""
    for i, raw in enumerate(store.read_action_trace() or []):
        if i < min_step:
            continue
        a = _parse_action(raw)
        if str(a.get("action", "")).lower() not in _CHECKOUT_ACTION_TYPES:
            continue
        blob = f"{a.get('description', '')} {a.get('target', '')}".lower()
        if has_marker(blob, _CHECKOUT_ACTION_MARKERS):
            return i, store.url_at_step(i)
    return None


def _active_url(obs_entry: dict) -> str:
    """The active-tab http URL of one observation entry ('' if none)."""
    tabs = obs_entry.get("tabs") or []
    for tab in tabs:
        if tab.get("is_active") and str(tab.get("url", "")).startswith("http"):
            return tab["url"]
    for tab in tabs:
        if str(tab.get("url", "")).startswith("http"):
            return tab["url"]
    return ""


# --- #2: what the trace shows ----------------------------------------------

def compute_goal_state(store: TraceStore, target_url: str) -> dict:
    """Climb the ladder from the trace, same-site scoped. Returns the highest
    rung reached plus the proof (step + url) for each rung."""
    observations = store.read_observations()
    ladder: dict[str, dict] = {r: {"reached": False, "step": None, "url": ""}
                               for r in RUNGS[1:]}
    same_site_steps = 0

    def _mark(rung: str, step: int, url: str) -> None:
        cell = ladder[rung]
        if not cell["reached"]:
            cell.update(reached=True, step=step, url=url)

    checkout_gate: tuple[int, str] | None = None  # login/checkout wall reached
    for step, entry in enumerate(observations):
        url = _active_url(entry)
        # Off-site steps grant no rungs (a competitor's cart is not ours).
        # Empty url fails open (same_site returns True) — it may still be us.
        if url and not same_site(url, target_url):
            continue
        u = url.lower()
        if url and same_site(url, target_url):
            same_site_steps += 1
        o = store.read_observation_step_text(step).lower()

        if _url_has(u, _SEARCH_URL_MARKERS) or has_marker(o, _SEARCH_OBS_MARKERS):
            _mark("searched", step, url)
        if is_product_url(u):
            _mark("product_viewed", step, url)
        if _url_has(u, _CART_URL_MARKERS):
            _mark("cart_reached", step, url)
            # Items present on the cart page itself.
            if (has_marker(o, _CART_ITEMS_OBS_MARKERS)
                    or (has_marker(o, _CART_REMOVE_OBS_MARKERS) and _PRICE_RE.search(o))):
                _mark("cart_has_items", step, url)
        # Or an add-to-cart confirmation on any page (the dm toast) — proof an
        # item was added even without opening the cart page.
        if has_marker(o, _ADDED_TO_CART_OBS_MARKERS):
            _mark("cart_has_items", step, url)
        # Order summary: the summary block in the observation, or a checkout URL
        # — but only once we already know items were in the cart.
        #
        # _CART_URL_MARKERS and _CHECKOUT_URL_MARKERS both contain "checkout"
        # and "/kasa", so a single URL used to grant cart_reached AND
        # order_summary_reached in the same iteration while cart_has_items — the
        # rung between them, and the _PARTIAL_FLOOR — stayed False. The ladder
        # is meant to be monotonic, so that lifted the reached index straight to
        # the top and scored the persona `success` on a task whose target is
        # cart_has_items, with nothing in the trace showing anything was ever
        # added. This is the same "only counts once items were in the cart" rule
        # the checkout-click and login-gate paths below already apply.
        summary_in_observation = (
            has_marker(o, _SUMMARY_HEADING_MARKERS) and has_marker(o, _SUMMARY_CTA_MARKERS)
        )
        if summary_in_observation or (
            _url_has(u, _CHECKOUT_URL_MARKERS) and ladder["cart_has_items"]["reached"]
        ):
            _mark("order_summary_reached", step, url)
        # The login/checkout gate (dm forces sign-in at checkout) — recorded, but
        # only counts once we know items were in the cart (below).
        if checkout_gate is None and _url_has(u, _CHECKOUT_GATE_URL_MARKERS):
            checkout_gate = (step, url)

    # The reachable ceiling: with items in the cart, proceeding to checkout —
    # clicking "Do kasy" or landing on the login/checkout gate — IS success, even
    # though the post-login summary is unreachable (the agent cannot register).
    if ladder["cart_has_items"]["reached"] and not ladder["order_summary_reached"]["reached"]:
        items_step = ladder["cart_has_items"]["step"] or 0
        click = _checkout_initiated_step(store, min_step=items_step)
        if click is not None:
            _mark("order_summary_reached", click[0], click[1])
        elif checkout_gate is not None and checkout_gate[0] >= items_step:
            _mark("order_summary_reached", checkout_gate[0], checkout_gate[1])

    reached_index = 0
    for rung in RUNGS[1:]:
        if ladder[rung]["reached"]:
            reached_index = max(reached_index, _RUNG_INDEX[rung])
    evidence = [
        {"rung": r, "step": ladder[r]["step"], "url": ladder[r]["url"]}
        for r in RUNGS[1:] if ladder[r]["reached"]
    ]
    return {
        "reached_rung": RUNGS[reached_index],
        "reached_index": reached_index,
        "ladder": ladder,
        "evidence": evidence,
        "same_site_steps": same_site_steps,
    }


# --- #1: what counts as success for this task ------------------------------

# Highest rung mentioned in the intent wins. Word-boundary matched.
_TARGET_KEYWORDS = (
    # "podsumowani" is NOT here on its own: a configuration summary, an order
    # summary and "podsumuj swoje wrażenia" all contain it. It counts only
    # alongside cart or order vocabulary — see _SUMMARY_NEEDS_CONTEXT below.
    ("order_summary_reached", ("do kasy", "kasa", "checkout",
                               "zamówieni", "zamowieni", "sfinalizuj", "złóż zam",
                               "zloz zam")),
    ("cart_has_items", ("koszyk", "do koszyk", "cart", "dodaj do")),
    # Stems are deliberately truncated to catch Polish inflection ("porówn"
    # matches porównaj/porównanie), so they must still be long enough to be
    # unambiguous. "cen" was not: with only a leading word boundary it matched
    # centrum, centralny and cennik, so "sprawdź godziny otwarcia centrum
    # handlowego" was classified as a product-viewing shopping task and then
    # scored against a ladder that does not apply to it. Spelled out instead of
    # shortened — a tighter matcher cannot fix a stem this generic without
    # breaking the longer stems around it.
    ("product_viewed", ("porówn", "porown", "znajd", "wyszuk", "sprawdź", "sprawdz",
                        "cena", "ceny", "cenę", "cene", "cenie", "cenow", "cennik",
                        "obejrz", "przegląd", "przeglad")),
)


# Tasks this ladder cannot score. A product configurator has no cart, no
# product-detail URLs and no checkout: every rung is decided from URL markers
# that a configurator never produces, so any configurator task scored against it
# reaches "none" and is recorded as failed. The hyundai run did exactly that —
# three personas reached the configuration summary, one of them by its own
# account successfully, and all three were scored "failed" and reported as
# "0% skuteczności". Abstaining hands the outcome back to the declared verdict,
# labelled as declared, which is honest about what the trace can and cannot
# show. (Same class as the "godziny otwarcia centrum handlowego" task below.)
_OFF_LADDER_MARKERS = ("skonfigur", "konfigur")

# "podsumowani" signals the checkout rung only in a shopping sentence.
_SUMMARY_NEEDS_CONTEXT = ("koszyk", "cart", "dodaj do", "zamówieni", "zamowieni",
                          "do kasy", "checkout")

# "Znajdź i kup lampkę" asks for a purchase, not for a product page: the verb
# "znajd" alone set the target at product_viewed, so a persona who only opened the
# product page was measured a success. Whole word and imperative only. As a stem
# "kup" also matches kupon and kupno, and the infinitive is as often the persona's
# motive ("Chcesz kupić prezent. Porównaj ceny…") as the task. "zamów" is left out:
# "zamów rozmowę" is no cart, and an abstaining target keeps the declared outcome.
_BUY_IMPERATIVE_RE = re.compile(r"(?<!\w)kup(?!\w)")


def infer_target_rung(intent: str) -> str | None:
    """Deterministic: the highest ladder rung the intent asks for, or None
    (abstain) when the intent names no shopping rung — or names a task the
    shopping ladder cannot score at all."""
    text = (intent or "").lower()
    if has_marker(text, _OFF_LADDER_MARKERS):
        return None
    for rung, keywords in _TARGET_KEYWORDS:  # ordered highest-rung first
        if has_marker(text, keywords):
            return rung
        if (rung == "order_summary_reached"
                and has_marker(text, ("podsumowani",))
                and has_marker(text, _SUMMARY_NEEDS_CONTEXT)):
            return rung
        if rung == "cart_has_items" and _BUY_IMPERATIVE_RE.search(text):
            return rung
    return None


def resolve_target_rung(store: TraceStore) -> tuple[str | None, str]:
    """(target_rung, source). An explicit `success_condition` wins; else infer
    from the intent; else abstain. An inferred target that needs a cart the run
    verified the site does not show (premise_check) is capped at product_viewed:
    "capped:cart"."""
    from .premise_check import premise_gaps

    info = store.read_basic_info() or {}
    explicit = info.get("success_condition")
    if isinstance(explicit, str) and explicit in _RUNG_INDEX:
        return explicit, "explicit"
    inferred = infer_target_rung(info.get("intent") or "")
    if inferred:
        if (_RUNG_INDEX[inferred] >= _RUNG_INDEX["cart_has_items"]
                and "cart" in premise_gaps(store)):
            return "product_viewed", "capped:cart"
        return inferred, "inferred"
    return None, "abstain"


# --- does the ladder apply to this site? ------------------------------------

_SHOP_RUNG = _RUNG_INDEX["product_viewed"]


def _shop_seen(store: TraceStore, target_url: str) -> bool:
    """Did this session reach a product page, or any rung above it? Asked of the
    ladder itself: an add-to-cart toast shows a shop as well as a product URL does,
    and a product page whose URL carries no marker still counts once something is
    in the cart (dm adds on the product page and never opens /koszyk)."""
    return compute_goal_state(store, target_url)["reached_index"] >= _SHOP_RUNG


def ladder_applies(store: TraceStore, target_url: str, own_shop_seen: bool) -> bool:
    """Can the shopping ladder tell failure from absence on this site?

    The ladder measures a shop. The target is inferred from generic verbs
    ("znajdź", "sprawdź"), so a goal at a bank infers "product_viewed" — and a bank
    has no product URLs, so every persona scored `failed`, whatever the persona
    achieved (bnpparibas.pl: 6 of 6, over a goal each of them judged achieved).

    It applies when any session of the run reached a product page or beyond (cart,
    checkout): then a persona that never got there really did fail. When none did,
    the site has nothing for the ladder to measure and the caller keeps the declared
    outcome. A run whose siblings cannot be read (a fixture, a run from before
    run_manifest.json) is judged as it always was: the ladder applies.
    """
    if own_shop_seen:
        return True
    run_dir = store.trace_dir.parent
    if not (run_dir / "run_manifest.json").is_file():
        return True
    try:
        for sibling in sorted(run_dir.iterdir()):
            if sibling.name == store.trace_dir.name or not (sibling / "basic_info.json").is_file():
                continue
            if _shop_seen(TraceStore(sibling), target_url):
                return True
    except Exception:
        return True
    return False


# --- the fusion: verdict of #2 against #1 ----------------------------------

def observed_outcome(store: TraceStore, target_url: str) -> dict:
    """Evidence-grounded outcome. `unknown` (→ caller falls back to declared)
    when the target is undecidable, there is no on-site evidence, or the site has
    no shop for an inferred target to be measured on (`ladder_applies`)."""
    from .premise_check import premise_gaps

    gs = compute_goal_state(store, target_url)
    target, source = resolve_target_rung(store)
    # Only an inferred target is checked: an explicit success_condition is the
    # author saying the site has what it names. None = not evaluated.
    applies = None
    if source in ("inferred", "capped:cart") and gs["same_site_steps"] > 0:
        applies = ladder_applies(store, target_url, gs["reached_index"] >= _SHOP_RUNG)
    decidable = target is not None and gs["same_site_steps"] > 0 and applies is not False

    if not decidable:
        outcome = "unknown"
    else:
        ti = _RUNG_INDEX[target]
        ri = gs["reached_index"]
        floor = _RUNG_INDEX[_PARTIAL_FLOOR]
        if ri >= ti:
            outcome = "success"
        elif floor < ti and ri >= floor:
            # Real progress short of the target: added to cart but did not reach
            # the target rung. No partial band when the target is at/below floor.
            outcome = "partial"
        else:
            outcome = "failed"

    return {
        "outcome": outcome,
        "decidable": decidable,
        "ladder_applies": applies,
        "reached_rung": gs["reached_rung"],
        "reached_index": gs["reached_index"],
        "target_rung": target,
        "target_source": source,
        # What the task presupposed and the run verified the site does not show
        # (premise_check). No rung is invented for it: the outcome speaks for the
        # part of the task that could be done.
        "premise_gaps": premise_gaps(store),
        "evidence": gs["evidence"],
        "same_site_steps": gs["same_site_steps"],
    }


# --- back-compat: the claim verifier's negative-claim substrate -------------
# session_capabilities lived in claim_verifier; it moves here so there is one
# "what the session did" module. Now same-site scoped (it was not before — a
# competitor's cart URL could wrongly satisfy `reached_cart`).

def session_capabilities(store: TraceStore) -> dict:
    """Ground-truth facts for refuting negative claims ("links don't work",
    "no add-to-cart feedback"), derived from same-site visited URLs."""
    target = target_url_for(store)
    urls = [(u or "").lower() for u in (store.visited_urls() or [])
            if same_site(u, target)]
    return {
        "product_pages_opened": sum(1 for u in urls if is_product_url(u)),
        "reached_cart": any(_url_has(u, _CART_URL_MARKERS) for u in urls),
    }
