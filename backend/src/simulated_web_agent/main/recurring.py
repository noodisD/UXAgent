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

from .attribution import injected_back_steps, tool_failed_actions, unperceived_steps
from .loop_phrases import KIND_REFUSED
from .redaction import clean_narrative
from .textutil import clip_words
from .trace_store import parse_action

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


_QUOTED_NAME = re.compile(r"['‘\"“]([^'’\"”]{3,60})['’\"”]")
_STEM_LEN = 4
# Words that open or pad a control's description and say nothing about its subject.
_ROUTE_STOP = frozenset("""jest nie się oraz dla przy przez które który która został kliknięcie
próba proba formularz strona stronie strony""".split())


def _actions_with_failures(store) -> list[tuple[dict, bool]]:
    try:
        actions = store.read_action_trace() or []
        observations = store.read_observations() or []
    except Exception:
        return []
    out = []
    for k, raw in enumerate(actions):
        try:
            a = json.loads(raw) if isinstance(raw, str) else dict(raw)
        except (TypeError, ValueError):
            continue
        after = observations[k + 1] if k + 1 < len(observations) else {}
        failed = str((after or {}).get("error") or "").startswith("Error executing action")
        out.append((a, failed))
    return out


def route_summary(store, last: int = 14) -> list[str]:
    """The participant's last steps, each marked when the tool failed it, when the harness
    went back in their place, and when its result was never seen (for the aggregate prompt)."""
    try:
        raw_actions = store.read_action_trace() or []
        failed = tool_failed_actions(store)
        injected = injected_back_steps(store)
        unseen = set(unperceived_steps(store))
    except Exception:
        return []
    out = []
    for k, raw in enumerate(raw_actions):
        a = parse_action(raw)
        if k in injected:
            # Not their choice, and its own sentence ("ta metoda nie działała") reads as theirs.
            what = ("[cofnięcie wynikające z reguł badania]" if injected[k] == KIND_REFUSED
                    else "[cofnięcie wymuszone przez narzędzie]")
        else:
            what = str(a.get("description") or f"{a.get('action', '')} {a.get('target', '')}").strip()[:90]
        marks = [m for m, on in ((" (nieudane)", k in failed), (" (wynik nieznany)", k + 1 in unseen)) if on]
        out.append(f"{k}: {what}{''.join(marks)}")
    return out[-last:]


def failed_click_names(store) -> list[str]:
    """Names of the controls whose click failed in this session, as the persona called them."""
    names = []
    for a, failed in _actions_with_failures(store):
        if not failed or a.get("action") != "click":
            continue
        m = _QUOTED_NAME.search(str(a.get("description") or ""))
        name = m.group(1).strip() if m else str(a.get("target") or "").replace("_", " ").strip()
        if name and name not in names:
            names.append(name)
    return names


def _stems(text: str) -> set:
    return {w[:_STEM_LEN] for w in re.findall(r"[a-ząćęłńóśźż]{4,}", str(text or "").lower())
            if w not in _ROUTE_STOP}


def annotate_routes(themes: list, reflections: list) -> list:
    """Say where a theme's participants first tried another way and failed.

    The 2026-10-13 run: all six participants "agreed" the test-drive form did not
    carry the configured model, yet every one had reached it through the menu,
    two of them after the summary-screen button the task named timed out under an
    overlay. The agreement was about a route nobody was asked to test. A model
    asked to notice this did not; the failed clicks are in the trace, so the
    note is built from them: for each participant in a theme, the controls whose
    click failed AND whose name shares at least two word stems with the theme.
    """
    by_name = {str(r.get("persona")): r for r in reflections or [] if isinstance(r, dict)}
    out = []
    for theme in themes or []:
        subject = _stems(theme.get("theme", "") + " " + " ".join((theme.get("phrases") or {}).values()))
        hit_people, controls = [], []
        for who in theme.get("participants") or []:
            mine = [n for n in (by_name.get(who) or {}).get("failed_clicks") or []
                    if len(_stems(n) & subject) >= 2]
            if mine:
                hit_people.append(who)
                controls += [n for n in mine if n not in controls]
        note = ""
        if hit_people:
            cited = ", ".join(f"„{c}”" for c in controls[:2])
            note = (f"{len(hit_people)} z {len(theme['participants'])} tych uczestników najpierw "
                    f"bezskutecznie próbowało {cited} i weszło tam inną drogą (np. z menu), więc "
                    "zgodność nie dotyczy drogi, którą zakładało zadanie — sprawdź ją osobno.")
        out.append({**theme, "route_note": note})
    return out


def _own_text(reflection: dict) -> str:
    parts = [reflection.get("summary"), reflection.get("priorityFix") or reflection.get("priority_fix")]
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
                     "najwazniejsza_poprawka": str(r.get("priorityFix") or r.get("priority_fix") or "")})
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
    note = str(theme.get("route_note") or "").strip()
    return (f"{theme['theme']} — {len(theme['participants'])} z {theme['of']} uczestników. {who}"
            + (f" Uwaga o drodze: {note}" if note else ""))
