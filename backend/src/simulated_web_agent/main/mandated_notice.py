"""A notice the site is obliged to show, and what the report may say about it.

A regulator's decision (UOKiK, KNF, UODO, NBP) or a statutory duty can prescribe a
notice's wording, its frame and that it stays until the visitor closes it. The first
real audit of bnpparibas.pl made "restyle the UOKiK notice to be more discreet and less
alarming (a flat expandable bar)" its priority recommendation #1 - a change the bank is
not free to make. For such a notice only legibility is a fair thing to recommend
(contrast, text size, line length, a reachable close control, a plain-language summary
beside the legal text), never hiding, shrinking, softening, collapsing or removing it.

`is_mandated_notice` decides from the notice's own text and errs towards "no": an
ordinary promotion must never be shielded from criticism. `dilutes_mandated_notice` and
the two guards apply the rule in code, because the prompts asking for it are not always
obeyed. The guards are idempotent and never touch a sentence about an app banner, an
ad or a promotion.
"""
import re
from typing import Callable

_ORGAN = (r"UOKiK|KNF|UODO|GIODO|NBP|Urz[ęe]d\w*\s+Ochrony\s+(?:Konkurencji|Danych)"
          r"|Komisj\w*\s+Nadzoru\s+Finansowego|Narodow\w*\s+Bank\w*\s+Polsk\w*"
          r"|Office\s+of\s+Competition\s+and\s+Consumer\s+Protection|Financial\s+Supervision")

# A notice that is mandated whatever else it says.
_STRONG = (
    ("decyzja organu (UOKiK, KNF, UODO lub NBP)", re.compile(
        rf"decyzj\w*(?:\s+\S+){{0,3}}?\s+(?:{_ORGAN})|decision\s+of\s+(?:the\s+)?(?:President\s+of\s+(?:the\s+)?)?(?:{_ORGAN})",
        re.IGNORECASE)),
    ("komunikat o decyzji organu", re.compile(r"komunikat\w*\s+o\s+decyzj", re.IGNORECASE)),
    ("obowiązek publikacyjny", re.compile(
        r"obowi[ąa]z\w*\s+(?:publikacyjn|publikacji|opublikowania|informacyjn)\w*"
        r"|publication\s+(?:obligation|duty)", re.IGNORECASE)),
    ("numer decyzji organu", re.compile(
        r"\b(?:RKR|DOZIK|DKK|RWR|RGD|RPZ|DDK|DOK|RBG|RLU|RŁO)[-/]\s?\d+", re.IGNORECASE)),
    ("na podstawie decyzji organu", re.compile(
        rf"na\s+podstawie\s+decyzji(?:\s+\S+){{0,3}}?\s+(?:prezesa|urz[ęe]du|organu|komisji|nadzor\w*|sądu|ministra|{_ORGAN})",
        re.IGNORECASE)),
    ("RODO / GDPR", re.compile(
        r"\bRODO\b|\bGDPR\b|klauzul\w*\s+informacyjn|przetwarzani\w*\s+danych\s+osobowych", re.IGNORECASE)),
)

# A legal basis alone is not enough ("zgodnie z art. 5" sits on promotions too): it counts
# only in a text that is framed as a notice and has no promotional vocabulary.
_LEGAL_BASIS = re.compile(
    r"zgodnie\s+z\s+(?:ustaw\w*|rozporz[ąa]dzeni\w*|dyrektyw\w*|art\.)"
    r"|na\s+mocy\s+(?:ustaw\w*|rozporz[ąa]dzeni\w*|dyrektyw\w*|art\.)"
    r"|pursuant\s+to\s+(?:the\s+)?(?:Act|Regulation|Directive|Article)"
    r"|in\s+accordance\s+with\s+(?:the\s+)?(?:Act|Regulation|Directive|Article)", re.IGNORECASE)
_NOTICE_FRAME = re.compile(
    r"komunikat|informuj|zawiadamia|powiadamia|obowi[ąa]z|publikuj|ogłasz|"
    r"notice|we\s+inform|obliged", re.IGNORECASE)
_PROMOTIONAL = re.compile(
    r"promocj|rabat|zniżk|bonus|nagrod|konkurs|taniej|gratis|darmow|wygraj|zyskaj|"
    r"oprocentowan|\bofert|\bsale\b|discount|\bwin\b|\bfree\b", re.IGNORECASE)


def mandated_notice_reason(text: str) -> str | None:
    """Why `text` is a notice the site is obliged to show; None when it is not (or not
    surely: an ordinary promotion, an app banner, a consent text without a legal anchor)."""
    text = " ".join(str(text or "").split())
    if not text:
        return None
    for reason, pattern in _STRONG:
        if pattern.search(text):
            return reason
    if _LEGAL_BASIS.search(text) and _NOTICE_FRAME.search(text) and not _PROMOTIONAL.search(text):
        return "podstawa prawna: ustawa, rozporządzenie lub dyrektywa"
    return None


def is_mandated_notice(text: str) -> bool:
    return mandated_notice_reason(text) is not None


# --- what the report may recommend about one ------------------------------------

# Forms that RECOMMEND a change (imperative, infinitive, verbal noun), not the verbs that
# complain ("komunikat ukrywa ofertę" is a claim about the page, not advice about it).
_DILUTION = re.compile(
    r"dyskretn\w*"
    r"|mniej\s+(?:alarmując|agresywn|rzucając|widoczn|inwazyjn|natarczyw|nachaln|przytłaczając|dominując)\w*"
    r"|bardziej\s+(?:neutraln|stonowan|subteln)\w*"
    r"|neutraln\w*|stonuj|stonować|stonowan\w*|złagodz\w*|złagodź|wyciszy\w*|przytłum\w*"
    r"|zmniejsz(?:yć|aj|enie|ać)?\b|pomniejsz\w*|zredukuj|zredukować|zminimalizuj\w*"
    r"|ogranicz\w*\s+(?:rozmiar|wielkość|widoczność|wagę)"
    r"|ukry(?:ć|j|cie|cia)\b|schow(?:aj|ać|anie)\b|usu(?:ń|nąć|nięci\w*)|usuwa(?:ć|nie)\b|wyrzuć|zlikwiduj|zlikwidow\w*"
    r"|zwi(?:ń|nąć|nięci\w*)|rozwijan\w*|zwijan\w*|do\s+rozwini\w*|płask\w*\s+pasek"
    r"|wyłącz(?:yć|ać|enie|\b)|zrezygn\w*"
    r"|odroczy\w*|odłóż|odłożyć|opóźni\w*|(?:pokaz|wyświetl)\w*\s+(?:dopiero|później)"
    r"|skr[óo][ćc]\w*\s+(?:\S+\s+){0,3}?(?:tekst|treść|komunikat)\w*"
    r"|bez\s+(?:\S+\s+)?(?:ramk|tła)\w*|pozbaw\w*"
    r"|\b(?:hide|conceal|shrink|soften|collapse|remove|downplay|minimi[sz]e|defer|discreet|neutral)\b"
    r"|tone\s+down|less\s+alarming|less\s+prominent", re.IGNORECASE)

# Words that name the notice the report is talking about, whatever the measurements say.
_NAMES_REGULATOR = re.compile(
    r"UOKiK|\bKNF\b|\bUODO\b|\bGIODO\b|\bNBP\b|regulator\w*|\bregulatory\b"
    r"|komunikat\w*\s+(?:prawn|o\s+decyzj|urz[ęe]d|regulac|nadzor)|informacj\w*\s+prawn|nota\s+prawn"
    r"|(?:tekst|okn\w+|pasek|ramk\w+)\s+prawn|decyzj\w*\s+(?:prezes|zobowiązując)"
    r"|obowi[ąa]z\w*\s+publikac|\bRODO\b|\bGDPR\b|legal\s+notice|\bprawn\w+\s+komunikat",
    re.IGNORECASE)
# When the measurements say the only notice on the landing screen is a mandated one, the
# plain words for it name it too - unless the sentence is about something else.
_NAMES_ANY_NOTICE = re.compile(r"komunikat\w*|powiadomien\w*|pop-?up\w*|\bnotice\b", re.IGNORECASE)
_SOMETHING_ELSE = re.compile(
    r"aplikacj|\bapp\b|gomobile|reklam|promocj|kampani|cooki|ciasteczk|zgod\w*\s+na|czat|chat"
    r"|błęd|walidac|formularz|logowan|koszyk|newsletter|subskrypcj", re.IGNORECASE)


# Not advice to dilute: "bank chce coś ukryć" says what a reader suspects, and the length of a
# line is the legibility the site is allowed to improve.
_NOT_ADVICE = re.compile(
    r"(?:chc\w+|próbuj\w+|usiłuj\w+|coś)\s+ukry\w*|(?:długoś|szerokoś)\w+\s+(?:linii|wiersz\w*)", re.IGNORECASE)


def recommends_dilution(text: str) -> bool:
    return bool(_DILUTION.search(_NOT_ADVICE.sub(" ", str(text or ""))))


def names_mandated_notice(text: str, only_mandated: bool = False) -> bool:
    text = str(text or "")
    if _NAMES_REGULATOR.search(text):
        return True
    return bool(only_mandated and _NAMES_ANY_NOTICE.search(text) and not _SOMETHING_ELSE.search(text))


def dilutes_mandated_notice(text: str, only_mandated: bool = False) -> bool:
    """`text` recommends hiding, shrinking, softening, collapsing, removing, deferring or
    restyling a notice the site is obliged to show."""
    return recommends_dilution(text) and names_mandated_notice(text, only_mandated)


LEGIBILITY_ONLY = ("Poprawić wyłącznie czytelność komunikatu prawnego, nie zmieniając jego treści ani "
                   "obecności (narzuca je regulator): kontrast, rozmiar tekstu, długość linii, dostępny "
                   "przycisk „Zamknij” oraz krótkie streszczenie prostym językiem obok tekstu prawnego.")

LEFT_OUT_NOTE = ("Pominięto rekomendację zmiany sposobu prezentacji komunikatu nałożonego przez regulatora "
                 "(decyzja UOKiK): jego treść, forma i widoczność są narzucone, więc oceniano wyłącznie "
                 "jego czytelność.")

_ABBREVIATIONS = ("np", "tzn", "tj", "tzw", "itp", "itd", "ok", "nr", "art", "ust", "pkt", "godz", "zł", "r",
                  "m.in", "tys", "mln", "ul", "al", "prof", "dr", "inż", "wg", "ww", "pt", "por", "zob")


def _sentences(text: str) -> tuple[list[str], list[str]]:
    """`text` as (sentences, the whitespace after each). A full stop does not end a sentence
    after an abbreviation ("np.", "r.", "art.") or inside brackets, nor before a lower-case
    letter - the model's recommendations are full of "(np. płaski pasek ...)"."""
    text = text.strip()
    sentences, gaps, start, depth = [], [], 0, 0
    for m in re.finditer(r"[()]|[.!?]+(?=\s)", text):
        token = m.group(0)
        if token == "(":
            depth += 1
        elif token == ")":
            depth = max(0, depth - 1)
        elif depth == 0:
            before = text[start:m.start()].split()
            word = before[-1].lower().lstrip("„\"(") if before else ""
            after = text[m.end():].lstrip()
            if token == "." and (word.rstrip(".") in _ABBREVIATIONS or (after and after[0].islower())):
                continue
            sentences.append(text[start:m.end()])
            gap = re.match(r"\s+", text[m.end():])
            gaps.append(gap.group(0) if gap else "")
            start = m.end() + len(gaps[-1])
    if start < len(text):
        sentences.append(text[start:])
        gaps.append("")
    return sentences, gaps


def rewrite_sentences(text: str, claim: Callable[[str], bool], replacement: str | None = None) -> str | None:
    """`text` with the sentences that make `claim` replaced by `replacement` (once, and not
    at all if `text` already says it) or dropped when `replacement` is None; None when no
    sentence does. Spacing and paragraph breaks stay."""
    sentences, gaps = _sentences(text)
    if not any(claim(s) for s in sentences):
        return None
    used = replacement is None or replacement in text
    out = []
    for s, g in zip(sentences, gaps):
        if claim(s):
            if not used:
                out.append(replacement + g)
                used = True
            continue
        out.append(s + g)
    return "".join(out).strip()


def only_mandated(facts: dict | None) -> bool:
    """The landing measurements found notices and every one of them is mandated."""
    notices = [n for n in (facts or {}).get("notices") or [] if isinstance(n, dict)]
    return bool(notices) and all(n.get("mandated") is True for n in notices)


def guard_first_impression(fi: dict, facts: dict | None = None) -> bool:
    """Take back, from one persona's first impression, a suggested change or weakness that
    recommends hiding or restyling a mandated notice: a suggested change becomes the
    legibility-only sentence, a weakness is dropped. True when anything changed; stamped on
    the file so the aggregate can say so. Runs on its own output unchanged."""
    if not isinstance(fi, dict):
        return False
    only = only_mandated(facts)

    def claim(s: str) -> bool:
        return dilutes_mandated_notice(s, only)

    changed = False
    changes = fi.get("suggested_changes")
    if isinstance(changes, dict):
        for key, value in list(changes.items()):
            new = rewrite_sentences(value, claim, LEGIBILITY_ONLY) if isinstance(value, str) else None
            if new is not None:
                changes[key] = new
                changed = True
    weaknesses = fi.get("weaknesses")
    if isinstance(weaknesses, list):
        kept, edited = [], False
        for w in weaknesses:
            rest = rewrite_sentences(w, claim) if isinstance(w, str) else None
            if rest is None:
                kept.append(w)
                continue
            edited = True
            if rest:
                kept.append(rest)
        if edited:
            fi["weaknesses"] = kept
            changed = True
    if changed:
        fi["mandated_notice_left_out"] = True
    return changed


def guard_narrative(narr: dict, landing: dict | None = None) -> dict:
    """Take back, from the aggregate narrative, a recommendation to hide, shrink, soften or
    restyle a mandated notice (`landing` is statistics.landing_notices).

    A priority recommendation keeps its place with the legibility-only sentence instead;
    sentences of the executive summary and the first-impression assessment that make it are
    dropped; the methodology note says once, in Polish, that such a recommendation was left
    out. A sentence about an app banner, an ad or a promotion is not touched. Mutates and
    returns `narr`; runs on its own output unchanged.
    """
    if not isinstance(narr, dict):
        return narr
    landing = landing if isinstance(landing, dict) else {}
    only = landing.get("only_mandated") is True

    def claim(s: str) -> bool:
        return dilutes_mandated_notice(s, only)

    changed = False
    recs = narr.get("priority_recommendations")
    if isinstance(recs, list):
        kept, seen = [], set()
        for rec in recs:
            action = rec.get("action") if isinstance(rec, dict) else None
            new = rewrite_sentences(action, claim, LEGIBILITY_ONLY) if isinstance(action, str) else None
            if new is not None:
                changed = True
                rec["action"], rec["expected_impact"] = new, ""
                if new == LEGIBILITY_ONLY:
                    rec["area"] = "Czytelność komunikatu prawnego"
                if (rec.get("area"), new) in seen:
                    continue
                seen.add((rec.get("area"), new))
            kept.append(rec)
        if len(kept) != len(recs):
            recs[:] = kept
            for i, rec in enumerate(recs, 1):
                if isinstance(rec, dict) and "priority" in rec:
                    rec["priority"] = i
    for field in ("executive_summary", "first_impression_assessment"):
        text = narr.get(field)
        rest = rewrite_sentences(text, claim) if isinstance(text, str) else None
        if rest is not None:
            changed = True
            narr[field] = rest or LEGIBILITY_ONLY
    if (changed or landing.get("left_out")) and LEFT_OUT_NOTE not in str(narr.get("methodology_note") or ""):
        narr["methodology_note"] = (str(narr.get("methodology_note") or "") + " " + LEFT_OUT_NOTE).strip()
    return narr
