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
  - and the site's own copy says so, or (pickup, store stock) the participants
    also went to the store locator and found it there.
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
from .session_goal_state import _active_url, has_marker, is_product_url
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
# A product page whose perceived text is a few words, or whose raw HTML is a stub,
# is a page that did not render, not a page without the feature.
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
# Folded phrases, substring-matched. Narrow on purpose in both directions: a stem
# like "odbior" matches "odbiorcami" in every cookie dialog, and "dostepne w
# sklepach" is the shop's own disclaimer that availability varies.
_PRESENT = {
    "cart": ("dodaj do koszyka", "do koszyka", "w koszyku", "add to cart", "add to basket",
             "add to bag", "dodaj do torby", "koszyk, liczba"),
    "pickup": ("odbior w sklepie", "odbior w punkcie", "odbior w salonie", "odbior w paczkomac",
               "odbior osobisty", "odbior wlasny", "odbierz w ", "do odbioru", "odbierz zamowienie",
               "zamow i odbierz", "click & collect", "click and collect", "click&collect"),
    "store_stock": ("dostepnosc w sklepie", "dostepnosc w sklepach", "dostepnosc w twoim sklepie",
                    "dostepnosc w wybranym sklepie", "dostepnosc produktu w sklepie",
                    "sprawdz dostepnosc", "stan magazynowy", "sztuk w sklepie",
                    "dostepne w twoim sklepie", "dostepne w wybranym sklepie",
                    "check availability", "in stock at"),
}
# The bare word, exactly: "Koszyk" is a cart link, "koszyki" are baskets for sale.
_CART_WORD = re.compile(r"(?<![a-z0-9])koszyk(?![a-z0-9])")

# The site saying so itself.
_NO_ONLINE_SALES = (
    "nie prowadzimy sprzedazy online", "nie prowadzimy sprzedazy internetowej",
    "nie oferujemy sprzedazy online", "nie sprzedajemy online", "brak sprzedazy online",
    "dostepne wylacznie w sklepach stacjonarnych", "dostepne tylko w sklepach stacjonarnych",
    "nie mozna kupic online", "sklep internetowy nie jest dostepny",
    "zakupy online nie sa dostepne",
)
_STOCK_VARIES = (
    "oferta w sklepie moze sie roznic", "dostepnosc w sklepach moze sie roznic",
    "dostepnosc produktow w sklepach moze sie roznic", "dostepne tylko w wybranych lokalizacjach",
    "dostepne tylko w wybranych sklepach", "dostepne wylacznie w wybranych sklepach",
)
_CONTRA = {
    "cart": _NO_ONLINE_SALES,
    "pickup": _NO_ONLINE_SALES,
    "store_stock": _NO_ONLINE_SALES + _STOCK_VARIES,
}

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


def _page_key(url: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", url.split("#")[0].split("?")[0].lower()).rstrip("/")


class _Scan:
    """One pass over every same-site step of the run."""

    def __init__(self, premises) -> None:
        self.hits: dict[str, list] = {p: [] for p in premises}
        self.contra: dict[str, list] = {p: [] for p in premises}
        self.pages: set[str] = set()
        self.page_personas: set[str] = set()
        self.locator_personas: set[str] = set()


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
            if url:
                if (is_product_url(url) and len(text.strip()) >= _MIN_PAGE_TEXT
                        and len(raw) >= _MIN_RAW_HTML):
                    scan.pages.add(_page_key(url))
                    scan.page_personas.add(persona)
                if any(m in url.lower() for m in _LOCATOR_URL_MARKERS):
                    scan.locator_personas.add(persona)
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
            span = _first_hit(_PRESENT[premise], folded)
            if span is None and premise == "cart":
                m = _CART_WORD.search(folded)
                span = m.span() if m else None
            if span is not None:
                scan.hits[premise].append(_evidence(source, original, folded, span))
                break
    if _room(scan.contra[premise]):
        # Readable text only: a quote from markup would not be the site's words.
        for source, original, folded in hay[:2]:
            span = _first_hit(_CONTRA[premise], folded)
            if span is not None:
                ev = _evidence(source, original, folded, span)
                if ev["quote"]:
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
    if scan.hits[premise]:
        out.update(status="present", evidence=scan.hits[premise])
    elif pages < MIN_PRODUCT_PAGES:
        out["reason"] = "few_pages"
    elif personas < MIN_PERSONAS:
        out["reason"] = "few_personas"
    elif not (scan.contra[premise] or (
            premise != "cart" and len(scan.locator_personas) >= MIN_LOCATOR_PERSONAS)):
        out["reason"] = "no_corroboration"
    else:
        out.update(status="absent", evidence=scan.contra[premise],
                   text=_sentence(premise, personas, pages, scan.contra[premise],
                                  len(scan.locator_personas)))
    return out


def check_stores(stores: list[TraceStore], extra_intents=()) -> dict:
    """Probe every premise some participant's intent presupposes."""
    intents = [str((s.read_basic_info() or {}).get("intent") or "") for s in stores]
    intents += [str(i or "") for i in extra_intents]
    wanted = [p for p in PREMISES if any(p in required_premises(i) for i in intents)]
    result: dict = {"version": 1, "premises": {}}
    if wanted:
        scan = _scan(stores, wanted)
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


def run_gaps(run_dir: Path) -> list[dict]:
    """Every verified gap of the run: {premise, label, text}, for the report."""
    absent = _absent(read_premise_check(run_dir))
    return [{"premise": p, "label": LABELS[p], "text": str(absent[p].get("text") or "")}
            for p in PREMISES if p in absent]


def gap_context_lines(store: TraceStore, run_level: bool = False) -> list[str]:
    """What the audit passes are told the site did not show — one line per gap, for
    this participant's intent, or for every participant's when `run_level`."""
    absent = _absent(read_premise_check(store.trace_dir.parent))
    wanted = [p for p in PREMISES if p in absent] if run_level else premise_gaps(store)
    return [
        f"Zadanie zakłada funkcję „{LABELS[p]}”. {absent[p].get('text') or ''} Nie traktuj "
        "jej braku jako wady interfejsu ani nie zalecaj jej „naprawy”: opisz to jako granicę "
        "zakresu serwisu."
        for p in wanted
    ]
