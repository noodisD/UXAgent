"""Does the site show what the task presupposes?

A task can presuppose a feature the site does not have: "check whether it is
available for pickup in a nearby store" on a site with no click-and-collect and
no per-store stock. In the pepco.pl run every participant "failed" it, none read
the page's own disclaimer ("Oferta w sklepie moze sie roznic ... dostepnoscia"),
and the report's top recommendation was to repair the store locator — a fix for
a feature the site never offered.

Absence is a claim about a site, and a false one is worse than silence (it hides
a real defect: a control the lite perceiver dropped, one that only appears after
a variant is chosen). So the answer defaults to "unknown", and is "absent" only
when all of these hold, across participants and never from a single page:
  - the sessions saw enough of the site: MIN_PRODUCT_PAGES distinct product pages,
    in MIN_PERSONAS sessions, each with its raw HTML kept;
  - no marker for the feature appears on any same-site page, in the perceived
    text, the visible text of the raw HTML or the raw markup;
  - and the site's own copy says so, on two pages or to two participants, or
    (pickup, store stock) the participants also went to the store locator, a page
    that rendered, and found it there. Pickup of a shop with a cart is chosen at
    its checkout: the locator corroborates it only once a checkout was seen.
A missing raw_html is never absence: the page is not counted.

The result is worded as what the sessions did not find, not as a fact about the
site. It is stored as runs/<run>/premise_check.json (the pagespeed.json pattern)
and read by site_context_for, so the audit passes are told what the site does not
show, and by session_goal_state, so a cart that is verifiably absent is not a rung
the task can fail.

It is never put in the agent's own prompts: the participants must meet the site
without being told what is on it.
"""
from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path

from .attribution import bot_wall_steps
from .evidence import _visible_text
from .session_goal_state import (
    _CHECKOUT_GATE_URL_MARKERS, _CHECKOUT_URL_MARKERS, _active_url, has_marker, is_product_url,
)
from .site_scope import same_site, target_url_for
from .trace_store import TraceStore

log = logging.getLogger(__name__)

FILE = "premise_check.json"

PREMISES = ("cart", "pickup", "store_stock")

LABELS = {
    "cart": "koszyk i składanie zamówienia online",
    "pickup": "odbiór zamówienia w sklepie",
    "store_stock": "sprawdzenie dostępności produktu w konkretnym sklepie",
}

# What the sessions must have seen before their silence counts as anything.
MIN_PRODUCT_PAGES = 3
MIN_PERSONAS = 2
MIN_LOCATOR_PERSONAS = 2
# The site's own sentence counts from this many participants, or this many pages.
MIN_CONTRA = 2
# A product page or store locator whose perceived text is a few words, or whose raw
# HTML is a stub, is a page that did not render, not a page without the feature (a
# map that never loaded corroborates nothing).
_MIN_PAGE_TEXT = 200
_MIN_RAW_HTML = 2000
_MAX_RAW_CHARS = 6_000_000
_MAX_EVIDENCE = 3

_DIACRITICS = tuple(zip("ąćęłńóśźż", "acelnoszz"))


def _fold(text: str) -> str:
    """Lower case without Polish diacritics, the same length as the input (so an
    offset found in it is an offset in the original). Replaces, not translate():
    that is five times slower on a megabyte of markup."""
    text = (text or "").lower()
    for letter, plain in _DIACRITICS:
        text = text.replace(letter, plain)
    return text


# --- what the intent presupposes -------------------------------------------
# Folded, matched with session_goal_state.has_marker (leading word boundary).
# Phrases rather than the bare stem "koszyk": "koszyki do przechowywania" is a
# product, and a task about it presupposes no cart.
_INTENT_MARKERS = {
    "cart": ("do koszyk", "w koszyku", "koszyk zakup", "zloz zam", "sfinalizuj", "do kasy",
             "checkout", "zamow", "kup online", "kupic online", "dokonaj zakup"),
    "pickup": ("do odbioru", "odbior w ", "odbior osobist", "odbierz", "odebrac",
               "click & collect", "click and collect", "click&collect"),
}
_AVAILABLE = ("dostepn", "na stanie", "w magazynie", "stan magazynow")
_SHOP = ("sklep", "salon", "market", "stacjonarn")
# "available" as in open: a task about opening hours presupposes no stock check.
_NOT_STOCK = ("godzin", "otwarc", "otwart", "czynn")
_SENTENCE = re.compile(r"[.!?;\n]+")


def required_premises(intent: str) -> tuple[str, ...]:
    """The features the intent presupposes the site has, in PREMISES order."""
    text = _fold(intent)
    found = {p for p, markers in _INTENT_MARKERS.items() if has_marker(text, markers)}
    for sentence in _SENTENCE.split(text):
        if (has_marker(sentence, _AVAILABLE) and has_marker(sentence, _SHOP)
                and not has_marker(sentence, _NOT_STOCK)):
            found.add("store_stock")
    return tuple(p for p in PREMISES if p in found)


# --- what the pages show ----------------------------------------------------
# Folded text. Narrow on purpose in both directions: "odbior" matches "odbiorcami" in
# every cookie dialog, and "dostepne w sklepach" is the shop's own disclaimer that
# availability varies. So pickup and stock are matched as whole inflected words
# (odbiorze, odebrac, dostepnosci), never as the start of a longer one (odbiornik,
# sklepienie), and the verbs and nouns that mean other things too (odebrac kod,
# platnosc przy odbiorze) only with a shop word in their sentence.
#
# The patterns begin with their literal and the word start is checked afterwards, in
# _word_start: a lookbehind first made a scan of a megabyte of markup ten times slower.
_END = r"(?![a-z0-9])"
_STORE = rf"(?:sklep|salon)(?:u|owi|em|ie|y|ow|om|ami|ach)?{_END}"
_POINT = rf"(?:punkt(?:u|em|y|ow|om|ami|ach)?|punkcie){_END}"
_IN_STORE = rf"(?:sklep(?:ie|ach)|salon(?:ie|ach)|punkcie|punktach){_END}"
_MINE = "twoim|wybranym|moim|tym|danym|pobliskim|konkretnym"

_PICKUP_ANCHOR = re.compile(
    rf"odbi(?:or|oru|orem|orze|ory|orow){_END}|odbierz\w*|odbieraj\w*|odbiera(?:c|m|sz|my){_END}"
    rf"|odebra(?:c|nie|nia|niu){_END}")
_PICKUP_CTX = re.compile(
    rf"(?<![a-z0-9])(?:zamowien|osobist|wlasn|paczk|dostaw|miejsc|towar|produkt|kurier|bezplatn"
    rf"|darmow|{_POINT}|{_STORE})")
_NEAR = 40  # characters, in one sentence, between a pickup word and its shop word

_PRESENT = {
    "cart": ("dodaj do koszyka", "do koszyka", "w koszyku", "add to cart", "add to basket",
             "add to bag", "dodaj do torby", "koszyk, liczba"),
    "pickup": ("do odbioru", "zamow i odbierz", "click & collect", "click and collect",
               "click&collect", "click &amp; collect"),
    "store_stock": ("sprawdz dostepnosc", "stan magazynowy", "check availability", "in stock at"),
}
_PRESENT_RE = {
    "pickup": tuple(re.compile(p) for p in (
        # "dostawa do sklepu": the parcel goes to the shop, which is collecting it there
        rf"(?:dostaw(?:a|y|e|ie|ami|ach)|wysylk(?:a|i|e|ie|ami|ach)|przesylk(?:a|i|e|ie|ami|ach))"
        rf"\s+do\s+(?:{_STORE}|{_POINT}|paczkomat\w*)",
    )),
    "store_stock": tuple(re.compile(p) for p in (
        # "dostepnosc [produktu] w [Twoim] sklepie", unless the sentence goes on to say it varies
        rf"dostepnos(?:c|ci|cia)\s+(?:\w+\s+){{0,2}}?w\s+(?:(?:{_MINE})\s+)?{_IN_STORE}"
        r"(?!\s+(?:moze|zalezy|rozni))",
        rf"dostepn(?:y|a|e|ych)\s+(?:jest\s+)?w\s+(?:{_MINE})\s+{_IN_STORE}",
        r"sprawdz(?:enie)?\s+(?:aktualna\s+)?dostepnos(?:c|ci)",
        r"stan(?:y|u)?\s+magazynow",
        rf"(?:sztuk(?:a|i)?|szt\.?)\s+w\s+{_IN_STORE}",
        rf"(?:wybierz|ustaw|zmien)\s+(?:swoj\s+)?(?:sklep|salon){_END}",
        rf"moj\s+sklep{_END}",
        r"check\s+store\s+availability",
    )),
}
# The bare word, exactly: "Koszyk" is a cart link, "koszyki" are baskets for sale.
_CART_WORD = re.compile(r"(?<![a-z0-9])koszyk(?![a-z0-9])")

# The site saying so itself.
_NO_ONLINE_SALES = (
    "nie prowadzimy sprzedazy online", "nie prowadzimy sprzedazy internetowej",
    "nie oferujemy sprzedazy online", "nie sprzedajemy online", "brak sprzedazy online",
    "sklep internetowy nie jest dostepny", "zakupy online nie sa dostepne",
)
# A line about one product: that it is not sold online says nothing of the cart of the
# rest, and for pickup or a stock check it suggests the stores have it.
_NOT_SOLD_ONLINE = (
    "dostepne wylacznie w sklepach stacjonarnych", "dostepne tylko w sklepach stacjonarnych",
    "nie mozna kupic online",
)
_STOCK_VARIES = (
    "oferta w sklepie moze sie roznic", "dostepnosc w sklepach moze sie roznic",
    "dostepnosc produktow w sklepach moze sie roznic", "dostepne tylko w wybranych lokalizacjach",
    "dostepne tylko w wybranych sklepach", "dostepne wylacznie w wybranych sklepach",
)
_CONTRA = {
    "cart": _NO_ONLINE_SALES + _NOT_SOLD_ONLINE,
    "pickup": _NO_ONLINE_SALES,
    "store_stock": _NO_ONLINE_SALES + _STOCK_VARIES,
}

# What a locator whose map has not loaded says instead of its results.
_NOT_RENDERED = re.compile(
    r"(?:wlacz|wlaczyc|enable|turn\s+on)\s+(?:obsluge\s+)?javascript"
    r"|javascript\s+(?:jest\s+)?(?:wymagan\w*|wylaczon\w*|is\s+(?:required|disabled|turned\s+off))"
    r"|requires?\s+javascript"
    r"|(?:trwa\s+)?(?:ladowanie|loading)(?:\s+(?:mapy|wynikow|results|map)(?![a-z0-9])|\s*(?:\.\.\.|…))")

# The step of an order where delivery and pickup are chosen.
_CHECKOUT_PAGE_MARKERS = _CHECKOUT_URL_MARKERS + ("/dostaw", "/delivery")

# A page where the shop's own pickup / stock information would be looked for.
_LOCATOR_URL_MARKERS = ("store-locator", "storelocator", "/sklepy", "znajdz-sklep", "lokalizator",
                        "find-a-store", "find-store", "/stores", "punkty-sprzedazy", "gdzie-kupic")


def _quote(original: str, folded: str, start: int, end: int) -> str:
    """The sentence of `original` around folded[start:end], without its full stop —
    a substring of the original by construction ("" when folding changed the
    length, so the offsets cannot be trusted)."""
    if len(original) != len(folded):
        return ""
    a = original.rfind(". ", max(0, start - 200), start)
    a = a + 2 if a >= 0 else start
    b = original.find(". ", end, end + 300)
    if b < 0:
        b = min(len(original), end + 200)
        cut = original.rfind(" ", end, b) if b < len(original) else b
        b = cut if cut >= 0 else end
    return original[a:b].strip().rstrip(".")


def _first_hit(markers, folded: str) -> tuple[int, int] | None:
    for m in markers:
        i = folded.find(m)
        if i >= 0:
            return i, i + len(m)
    return None


def _word_start(folded: str, i: int) -> bool:
    return i == 0 or not folded[i - 1].isalnum()


def _word_hit(rx: re.Pattern, folded: str) -> tuple[int, int] | None:
    for m in rx.finditer(folded):
        if _word_start(folded, m.start()):
            return m.span()
    return None


def _near_hit(folded: str) -> tuple[int, int] | None:
    """A pickup word with a shop word in its sentence, within _NEAR characters."""
    for m in _PICKUP_ANCHOR.finditer(folded):
        i, j = m.span()
        if not _word_start(folded, i):
            continue
        before = _SENTENCE.split(folded[max(0, i - _NEAR):i])[-1]
        after = _SENTENCE.split(folded[j:j + _NEAR])[0]
        if _PICKUP_CTX.search(before) or _PICKUP_CTX.search(after):
            return i, j
    return None


def _present_span(premise: str, folded: str) -> tuple[int, int] | None:
    span = _first_hit(_PRESENT[premise], folded)
    if span is not None:
        return span
    for rx in _PRESENT_RE.get(premise, ()):
        span = _word_hit(rx, folded)
        if span is not None:
            return span
    if premise == "pickup":
        return _near_hit(folded)
    if premise == "cart":
        m = _CART_WORD.search(folded)
        return m.span() if m else None
    return None


def _page_key(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url.split("#")[0].split("?")[0].lower()).rstrip("/")


class _Scan:
    """One pass over every same-site step of the run."""

    def __init__(self, premises) -> None:
        self.hits: dict[str, list] = {p: [] for p in premises}
        self.contra: dict[str, list] = {p: [] for p in premises}
        # Who said it and on how many pages, for every sentence found: the examples kept in
        # `contra` are one per participant, which is not how often it was said.
        self.contra_personas: dict[str, set[str]] = {p: set() for p in premises}
        self.contra_pages: dict[str, set[str]] = {p: set() for p in premises}
        self.pages: set[str] = set()
        self.page_personas: set[str] = set()
        self.locator_personas: set[str] = set()
        self.checkout_personas: set[str] = set()


def _scan(stores, premises) -> _Scan:
    scan = _Scan(premises)
    for store in stores:
        persona = store.trace_dir.name
        target = target_url_for(store)
        try:
            walled = bot_wall_steps(store)
        except Exception:
            walled = set()
        for step, entry in enumerate(store.read_observations()):
            url = _active_url(entry)
            # Empty url fails open for what a page shows (towards "present") and
            # closed for what a page counts as.
            if step in walled or (url and not same_site(url, target)):
                continue
            text = store.read_observation_step_text(step)
            raw = store.read_raw_html(step)[:_MAX_RAW_CHARS]
            if not text and not raw:
                continue
            visible = _visible_text(raw) if raw else ""
            # (source name, original, folded): where a marker may be found, in the
            # order a reader would trust them.
            hay = [("text", text, _fold(text)), ("page", visible, _fold(visible)),
                   ("html", raw, _fold(raw))]
            for p in premises:
                _record(scan, p, persona, step, url, hay)
            if url and len(text.strip()) >= _MIN_PAGE_TEXT and len(raw) >= _MIN_RAW_HTML:
                low = url.lower()
                if is_product_url(url):
                    scan.pages.add(_page_key(url))
                    scan.page_personas.add(persona)
                # A locator that says it is loading, or wants JavaScript, was not read: the
                # participant saw its shell. (The perceived text only: the markup of nearly
                # every page has a noscript line.)
                if (any(m in low for m in _LOCATOR_URL_MARKERS)
                        and not _NOT_RENDERED.search(hay[0][2])):
                    scan.locator_personas.add(persona)
                if (any(m in low for m in _CHECKOUT_PAGE_MARKERS)
                        and not any(m in low for m in _CHECKOUT_GATE_URL_MARKERS)):
                    scan.checkout_personas.add(persona)
    return scan


def _record(scan: _Scan, premise: str, persona: str, step: int, url: str, hay) -> None:
    def _evidence(source, original, folded, span):
        return {"persona_dir": persona, "step": step, "url": url, "source": source,
                "quote": _quote(original, folded, *span)}

    def _room(found: list) -> bool:
        # A few examples, from different participants.
        return len(found) < _MAX_EVIDENCE and all(e["persona_dir"] != persona for e in found)

    if _room(scan.hits[premise]):
        for source, original, folded in hay:
            span = _present_span(premise, folded)
            if span is not None:
                scan.hits[premise].append(_evidence(source, original, folded, span))
                break
    # Readable text only: a quote from markup would not be the site's words.
    for source, original, folded in hay[:2]:
        span = _first_hit(_CONTRA[premise], folded)
        if span is not None:
            ev = _evidence(source, original, folded, span)
            if ev["quote"]:
                scan.contra_personas[premise].add(persona)
                if url:
                    scan.contra_pages[premise].add(_page_key(url))
                if _room(scan.contra[premise]):
                    scan.contra[premise].append(ev)
            break


def _sentence(premise: str, personas: int, pages: int, evidence: list, locators: int) -> str:
    out = (f"W {personas} sesjach na {pages} stronach produktów nie zaobserwowano: "
           f"{LABELS[premise]}.")
    if evidence:
        out += f" Serwis sam zastrzega: „{evidence[0]['quote']}”."
    else:
        out += (f" Uczestnicy zajrzeli też do wyszukiwarki sklepów (w {locators} sesjach) "
                "i tam tego nie znaleźli.")
    return out


def _decide(premise: str, scan: _Scan) -> dict:
    pages, personas = len(scan.pages), len(scan.page_personas)
    out = {"status": "unknown", "reason": "", "product_pages": pages, "personas": personas,
           "locator_personas": len(scan.locator_personas), "evidence": []}
    # The site's own sentence, from two participants or on two pages; else, for pickup and
    # stock, the locator. A shop with a cart offers pickup at its checkout, which a locator
    # visit does not show: it corroborates pickup only once some session saw that step.
    said = (len(scan.contra_personas[premise]) >= MIN_CONTRA
            or len(scan.contra_pages[premise]) >= MIN_CONTRA)
    locator = premise != "cart" and len(scan.locator_personas) >= MIN_LOCATOR_PERSONAS
    unchecked = (locator and premise == "pickup" and bool(scan.hits.get("cart"))
                 and not scan.checkout_personas)
    locator = locator and not unchecked
    if scan.hits[premise]:
        out.update(status="present", evidence=scan.hits[premise])
    elif pages < MIN_PRODUCT_PAGES:
        out["reason"] = "few_pages"
    elif personas < MIN_PERSONAS:
        out["reason"] = "few_personas"
    elif not (said or locator):
        out["reason"] = "checkout_not_seen" if unchecked else "no_corroboration"
    else:
        evidence = scan.contra[premise] if said else []
        out.update(status="absent", evidence=evidence,
                   text=_sentence(premise, personas, pages, evidence, len(scan.locator_personas)))
    return out


def check_stores(stores: list[TraceStore], extra_intents=()) -> dict:
    """Probe every premise some participant's intent presupposes."""
    intents = [str((s.read_basic_info() or {}).get("intent") or "") for s in stores]
    intents += [str(i or "") for i in extra_intents]
    wanted = [p for p in PREMISES if any(p in required_premises(i) for i in intents)]
    result: dict = {"version": 1, "premises": {}}
    if wanted:
        # The cart is looked for whatever the task needs: it decides what a locator proves.
        scan = _scan(stores, wanted + ["cart"] if "pickup" in wanted and "cart" not in wanted
                     else wanted)
        result["premises"] = {p: _decide(p, scan) for p in wanted}
    return result


# --- the file ---------------------------------------------------------------

def write_premise_check(run_dir: Path, result: dict) -> None:
    path = Path(run_dir) / FILE
    tmp = path.with_name(FILE + ".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def read_premise_check(run_dir: Path) -> dict:
    """The stored result, {} when there is none or it cannot be read."""
    try:
        data = json.loads((Path(run_dir) / FILE).read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) and isinstance(data.get("premises"), dict) else {}


def _persona_stores(run_dir: Path) -> list[TraceStore]:
    try:
        return [TraceStore(d) for d in sorted(Path(run_dir).iterdir())
                if (d / "basic_info.json").is_file()]
    except OSError:
        return []


def ensure_premise_check(run_dir: Path) -> dict:
    """The stored result, computed from the run's sessions when there is none: the
    reaudit and reanalyse scripts start from a finished run directory. A directory
    without run_manifest.json is not a run (a fixture, a shared temp folder): its
    subfolders are not this run's sessions, so nothing is computed or written."""
    stored = read_premise_check(run_dir)
    if stored:
        return stored
    if not (Path(run_dir) / "run_manifest.json").is_file():
        return {}
    try:
        result = check_stores(_persona_stores(run_dir))
        write_premise_check(run_dir, result)
        return result
    except Exception as e:
        log.warning("[premise_check] not computed for %s: %s", run_dir, e)
        return {}


# --- reading it -------------------------------------------------------------

def _absent(result: dict) -> dict:
    return {p: e for p, e in (result.get("premises") or {}).items()
            if isinstance(e, dict) and e.get("status") == "absent" and p in LABELS}


def premise_gaps(store: TraceStore) -> list[str]:
    """The premises this participant's intent presupposes and the run verified
    absent, in PREMISES order."""
    absent = _absent(read_premise_check(store.trace_dir.parent))
    wanted = required_premises((store.read_basic_info() or {}).get("intent") or "")
    return [p for p in wanted if p in absent]


def _basis(entry: dict) -> str:
    """What the verdict rests on: "site", the site's own copy (said by two participants or
    on two pages, so the evidence is kept), or "locator", only the sessions not finding it
    there. Read from the evidence so that a result stored before this was named has one."""
    return "site" if entry.get("evidence") else "locator"


def run_gaps(run_dir: Path) -> list[dict]:
    """Every verified gap of the run: {premise, label, text, basis}, for the report."""
    absent = _absent(read_premise_check(run_dir))
    return [{"premise": p, "label": LABELS[p], "text": str(absent[p].get("text") or ""),
             "basis": _basis(absent[p])}
            for p in PREMISES if p in absent]


# How a gap closes, by what it rests on. The site's own words settle it; a locator that
# showed nothing does not: the sessions may have missed it, or the locator may be what is
# broken, and the pass must still be free to report that.
CLOSING = {
    "site": ("Nie traktuj jej braku jako wady interfejsu ani nie zalecaj jej „naprawy”: opisz to "
             "jako granicę zakresu serwisu."),
    "locator": ("Sesje jej nie znalazły i może to być granica zakresu serwisu, ale serwis sam tego nie "
                "potwierdza: nie przedstawiaj jej braku jako faktu o serwisie ani jako pewnej wady "
                "interfejsu, a konkretną usterkę, którą widać na stronie, zgłoś jak każdą inną."),
}


def gap_context_lines(store: TraceStore, run_level: bool = False) -> list[str]:
    """What the audit passes are told the site did not show — one line per gap, for
    this participant's intent, or for every participant's when `run_level`."""
    absent = _absent(read_premise_check(store.trace_dir.parent))
    wanted = [p for p in PREMISES if p in absent] if run_level else premise_gaps(store)
    return [
        f"Zadanie zakłada funkcję „{LABELS[p]}”. {absent[p].get('text') or ''} "
        f"{CLOSING[_basis(absent[p])]}"
        for p in wanted
    ]
