"""Difficulties several participants describe independently, validated against what they wrote.

In the 2026-10-01 hyundai run five of six participants wrote, in their own words,
that the share/overlay window blocked or jammed the configurator. The report kept
each account in that person's own card and nowhere said they agreed: the findings
list is gated, the reflections are free text, and no code compared them.

Grouping accounts by meaning is what a model does well and a keyword list does
badly ("nakładka udostępniania", "zacinające się okno", "modal blokujący
kliknięcie" share no stem). So a model proposes the groups — and the code accepts
only what it can check: every participant it names must be a real participant and
must come with a phrase that is in THAT participant's own text. A theme survives
with at least MIN_PARTICIPANTS such participants. The theme's wording is the
model's; who said it, and that they said it, is not.
"""
from __future__ import annotations

import json
import logging
import re

from .redaction import clean_narrative
from .textutil import clip_words

log = logging.getLogger(__name__)

# Three independent accounts are a pattern; two are a coincidence.
MIN_PARTICIPANTS = 3
MAX_THEMES = 4
# Short enough to be a phrase, long enough to be a quotation and not a word.
MIN_PHRASE_CHARS = 8

SYSTEM_PROMPT = (
    "Jesteś badaczem UX. Dostajesz trudności, które uczestnicy badania opisali własnymi słowami "
    "(\"trudnosci\" i \"najwazniejsza_poprawka\"). Znajdź trudności opisane NIEZALEŻNIE przez co "
    "najmniej 3 różnych uczestników — to samo zjawisko (to samo okno blokujące klikanie, ta sama "
    "brakująca funkcja), nie tylko ten sam temat. Nie łącz różnych zjawisk: okno powitalne na "
    "stronie głównej i okno udostępniania w konfiguratorze to dwa różne zjawiska. Dla każdego "
    "uczestnika podaj DOSŁOWNY fragment jego tekstu (do 12 słów), skopiowany bez zmian. Nie oceniaj, "
    "czy to wada strony, i nie pisz, że coś zablokowało lub uniemożliwiło zadanie — opisz tylko, co "
    "uczestnicy napisali. Opis zjawiska (\"theme\") ma być krótki, neutralny, po polsku. "
    "Odpowiedz WYŁĄCZNIE w JSON: "
    '{"themes": [{"theme": "<opis>", "reports": [{"persona": "<imię>", "phrase": "<fragment>"}]}]}. '
    'Jeśli nic się nie powtarza: {"themes": []}.')


def _squeeze(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def _own_text(reflection: dict) -> str:
    parts = [reflection.get("summary"), reflection.get("priorityFix")]
    parts.extend(reflection.get("blockers") or [])
    return " ".join(str(p) for p in parts if p)


def prompt_payload(reflections: list) -> list[dict]:
    """What each participant wrote about difficulty — nothing about wins or the outcome."""
    rows = []
    for r in reflections or []:
        if not isinstance(r, dict) or not r.get("persona"):
            continue
        blockers = [str(b) for b in (r.get("blockers") or []) if b]
        if not blockers:
            continue
        rows.append({"persona": str(r["persona"]), "trudnosci": blockers,
                     "najwazniejsza_poprawka": str(r.get("priorityFix") or "")})
    return rows


def validate_themes(raw, reflections: list, participants: int) -> list[dict]:
    """The model's themes, cut down to what the participants' own texts bear out.

    [{"theme", "participants": [names], "of", "phrases": {name: phrase}}]
    """
    texts = {str(r["persona"]): _squeeze(_own_text(r))
             for r in reflections or [] if isinstance(r, dict) and r.get("persona")}
    themes = (raw or {}).get("themes") if isinstance(raw, dict) else None
    out: list[dict] = []
    for theme in themes if isinstance(themes, list) else []:
        if not isinstance(theme, dict):
            continue
        label = str(theme.get("theme") or "").strip()
        confirmed: dict[str, str] = {}
        for rep in theme.get("reports") if isinstance(theme.get("reports"), list) else []:
            if not isinstance(rep, dict):
                continue
            who, phrase = str(rep.get("persona") or "").strip(), str(rep.get("phrase") or "").strip()
            if (who in texts and who not in confirmed and len(phrase) >= MIN_PHRASE_CHARS
                    and _squeeze(phrase) in texts[who]):
                confirmed[who] = phrase
        if label and len(confirmed) >= MIN_PARTICIPANTS:
            out.append({"theme": clean_narrative(clip_words(label, 160)),
                        "participants": list(confirmed), "of": int(participants or len(texts)),
                        "phrases": {k: clean_narrative(v) for k, v in confirmed.items()}})
    return out[:MAX_THEMES]


async def group_recurring_reports(reflections: list, participants: int, chat) -> list[dict]:
    """Ask the model for themes, keep what validates. [] on any failure — never fatal."""
    payload = prompt_payload(reflections)
    if len(payload) < MIN_PARTICIPANTS:
        return []
    raw = await chat(
        [{"role": "system", "content": SYSTEM_PROMPT},
         {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
        json_mode=True, log=False)
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("[recurring] the model's reply was not JSON")
        return []
    return validate_themes(parsed, reflections, participants)


INTRO = (
    "Te trudności opisało niezależnie kilku uczestników badania własnymi słowami. To relacje, "
    "nie pomiar: zgodność nie dowodzi, że to wada strony (symulowani uczestnicy mogą powtarzać tę "
    "samą usterkę narzędzia), ale jest powodem, by sprawdzić to w zwykłej przeglądarce.")


def theme_text(theme: dict) -> str:
    """One theme as a sentence: what, how many, and each participant's own words."""
    who = "; ".join(f"{name}: „{phrase}”" for name, phrase in (theme.get("phrases") or {}).items())
    return (f"{theme['theme']} — {len(theme['participants'])} z {theme['of']} uczestników. {who}")
