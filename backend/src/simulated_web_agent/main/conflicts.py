"""Accounts that contradict each other, and the route each participant took.

2026-10-13 Hyundai run 4429: Dominik and Kamil wrote that the test-drive form did not
carry the configured model; Anna, Renata and Krzysztof used the summary-screen button
and their forms showed the configured model. The report's priority #2 asked for the
summary route to be fixed - a route that worked - because nothing set the accounts
against each other.

A model proposes the contradictions - it reads meaning where a keyword match fails
(shared words like "formularz" and "jazda" tied unrelated complaints together) - and
the code keeps only what it can check: each participant named must be real, the
phrase quoted must be in THAT participant's own blockers (or wins), and each control
named must be one that participant actually clicked. Nothing here judges who is right;
it says the accounts differ and, when the two sides came in by different controls,
that the difference may be the route.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re

from .recurring import _QUOTED_NAME, _actions_with_failures, _stems
from .redaction import clean_narrative
from .textutil import clip_words

log = logging.getLogger(__name__)

# Stems a recommendation must share with a topic before it gets the caveat. Generic
# words of the domain ("formularz", "jazda", "testowa") are shared by many unrelated
# recommendations, so the bar is high.
ANNOTATE_STEMS = 4
MAX_CONFLICTS = 3
MIN_PHRASE_CHARS = 8
PHRASE_STEMS = 2

SYSTEM_PROMPT = (
    "Jesteś badaczem UX. Dostajesz relacje uczestników badania: \"trudnosci\" (co im przeszkadzało), "
    "\"zadzialalo\" (co zadziałało dobrze) i \"klikniecia\" (kontrolki, które kliknęli, po kolei). "
    "Znajdź KONKRETNE zjawiska (ta sama funkcja, np. przenoszenie wybranego modelu do formularza), "
    "które jedni uczestnicy opisali jako problem, a inni jako coś, co zadziałało. Nie łącz różnych "
    "zjawisk i nie wystarczy, że chodzi o tę samą stronę. Dla każdego uczestnika podaj DOSŁOWNY "
    "fragment jego tekstu (z \"trudnosci\" dla zgłaszających, z \"zadzialalo\" dla tych, u których "
    "zadziałało; do 15 słów, skopiowany bez zmian) oraz w \"via\" JEDNĄ kontrolkę z JEGO listy "
    "\"klikniecia\" — tę, którą kliknął OSTATNIĄ, wchodząc do miejsca, którego dotyczy zjawisko "
    "(np. przycisk otwierający formularz, nie krok pośredni; puste, jeśli nie wiadomo). "
    "Nie oceniaj, kto ma rację. Opis zjawiska (\"topic\") krótki, neutralny, po polsku. "
    "Odpowiedz WYŁĄCZNIE w JSON: "
    '{"conflicts": [{"topic": "<opis>", "reported": [{"persona": "<imię>", "phrase": "<fragment>", '
    '"via": ["<kontrolka>"]}], "worked": [{"persona": "<imię>", "phrase": "<fragment>", "via": '
    '["<kontrolka>"]}]}]}. Jeśli nic się nie rozjeżdża: {"conflicts": []}.')


_STEM_NOISE = frozenset({"brak", "zamiast", "system", "domy", "ślnie", "uczes", "użytk"})


def clicked_names(store) -> list[str]:
    """Names of the controls this participant clicked successfully, in order."""
    names: list[str] = []
    for a, failed in _actions_with_failures(store):
        if failed or a.get("action") != "click":
            continue
        m = _QUOTED_NAME.search(str(a.get("description") or ""))
        name = m.group(1).strip() if m else str(a.get("target") or "").replace("_", " ").strip()
        if name:
            names.append(name)
    return names[-80:]


def _topic_stems(text: str) -> set:
    return _stems(text) - _STEM_NOISE


def _squeeze(text) -> str:
    return re.sub(r"\s+", "", str(text or "")).lower()


def prompt_payload(reflections: list) -> list[dict]:
    rows = []
    for r in reflections or []:
        if not isinstance(r, dict) or not r.get("persona"):
            continue
        clicks = list(dict.fromkeys(r.get("clicks") or []))[-25:]
        rows.append({"persona": str(r["persona"]),
                     "trudnosci": [str(b) for b in r.get("blockers") or [] if b],
                     "zadzialalo": [str(w) for w in r.get("wins") or [] if w],
                     "klikniecia": clicks})
    return rows


def _accept(items, reflections_by_name: dict, field: str, used: set, topic: set) -> list[dict]:
    """The model's participants, kept only when their own text and clicks bear it out."""
    out = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        who, phrase = str(it.get("persona") or "").strip(), str(it.get("phrase") or "").strip()
        person = reflections_by_name.get(who)
        if person is None or who in used or len(phrase) < MIN_PHRASE_CHARS:
            continue
        own = _squeeze(" ".join(str(t) for t in person.get(field) or []))
        if _squeeze(phrase) not in own:
            continue
        clicks = {c.lower(): c for c in person.get("clicks") or []}
        via = []
        for v in it.get("via") if isinstance(it.get("via"), list) else []:
            real = clicks.get(str(v).strip().lower())
            # A control that has nothing to do with the topic ("div.zamknij" for a conflict
            # about the price bar) would only make the routes look different.
            if real and real not in via and _stems(real) & topic:
                via.append(real)
        used.add(who)
        out.append({"persona": who, "phrase": clean_narrative(phrase), "via": via[:1]})
    return out


def validate_conflicts(raw, reflections: list) -> list[dict]:
    """The model's conflicts, cut down to what the participants' own texts and clicks show."""
    by_name = {str(r["persona"]): r for r in reflections or [] if isinstance(r, dict) and r.get("persona")}
    found = (raw or {}).get("conflicts") if isinstance(raw, dict) else None
    out: list[dict] = []
    for c in found if isinstance(found, list) else []:
        if not isinstance(c, dict):
            continue
        topic = str(c.get("topic") or "").strip()
        used: set = set()
        topic_stems = _topic_stems(topic)
        reported = _accept(c.get("reported"), by_name, "blockers", used, topic_stems)
        worked = _accept(c.get("worked"), by_name, "wins", used, topic_stems)
        # The quotations must be about the topic: a phrase that shares nothing with it
        # is a real sentence the model attached to the wrong conflict.
        reported = [r for r in reported if len(_topic_stems(r["phrase"]) & topic_stems) >= PHRASE_STEMS]
        worked = [w for w in worked if len(_topic_stems(w["phrase"]) & topic_stems) >= PHRASE_STEMS]
        if not (topic and reported and worked):
            continue
        rep_via = {v for r in reported for v in r["via"]}
        wor_via = {v for r in worked for v in r["via"]}
        out.append({"topic": clean_narrative(clip_words(topic, 160)), "reported": reported, "worked": worked,
                    "routes_differ": bool(rep_via and wor_via and not (rep_via & wor_via))})
    out.sort(key=lambda c: (not c["routes_differ"], -(len(c["reported"]) + len(c["worked"]))))
    return out[:MAX_CONFLICTS]


async def _ask(payload: list, chat) -> list[dict]:
    try:
        raw = await chat(
            [{"role": "system", "content": SYSTEM_PROMPT},
             {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            json_mode=True, log=False)
        return json.loads(raw)
    except Exception as exc:  # a failed or unparseable reply costs this pass, not the report
        log.warning("[conflicts] a pass failed: %s", exc)
        return {}


# Asked twice and merged: on the Hyundai run one pass in four missed the conflict that
# mattered and returned a spurious one instead.
PASSES = 2


async def find_conflicts(reflections: list, chat) -> list[dict]:
    """Ask the model for contradicting accounts; keep what validates. [] on any failure."""
    payload = prompt_payload(reflections)
    if len(payload) < 2:
        return []
    replies = await asyncio.gather(*[_ask(payload, chat) for _ in range(PASSES)])
    merged: list[dict] = []
    seen: set = set()
    for reply in replies:
        for c in validate_conflicts(reply, reflections):
            key = (frozenset(r["persona"] for r in c["reported"]), frozenset(w["persona"] for w in c["worked"]))
            if key not in seen:
                seen.add(key)
                merged.append(c)
    merged.sort(key=lambda c: (not c["routes_differ"], -(len(c["reported"]) + len(c["worked"]))))
    return merged[:MAX_CONFLICTS]


def _who(items: list[dict]) -> str:
    parts = []
    for it in items:
        via = ", ".join(f"„{v}”" for v in it["via"])
        parts.append(f"{it['persona']}" + (f" (przez {via})" if via else ""))
    return "; ".join(parts)


def conflict_text(c: dict) -> str:
    text = (f"{c['topic']} — zgłosili: {_who(c['reported'])}. Zadziałało u: {_who(c['worked'])}.")
    if c.get("routes_differ"):
        text += (" Obie grupy weszły do tego miejsca innymi kontrolkami, więc różnica może wynikać "
                 "z drogi, a nie ze strony jako całości — sprawdź każdą drogę osobno.")
    else:
        text += " Relacje uczestników się różnią; nie wiadomo, czym — sprawdź to w zwykłej przeglądarce."
    return text


INTRO = ("Ta sama rzecz wypadła u uczestników różnie: jedni opisują ją jako problem, inni jako coś, "
         "co zadziałało. Zestawienie pokazuje, kto i którą kontrolką tam wszedł. To relacje, nie pomiar.")


def prompt_text(conflicts: list[dict]) -> str:
    return "\n".join(f"- {conflict_text(c)}" for c in conflicts) or "(brak)"


def route_caveat(conflict: dict) -> str:
    """The sentence added to a recommendation about a topic the participants disagreed on."""
    worked = [w for w in conflict["worked"] if w["via"]]
    if not worked:
        return ("Uczestnicy opisali to różnie: u części uczestników to zadziałało — rekomendacja "
                "wymaga sprawdzenia w zwykłej przeglądarce.")
    names = ", ".join(w["persona"] for w in worked)
    via = ", ".join(f"„{v}”" for v in dict.fromkeys(v for w in worked for v in w["via"]))
    return (f"Uwaga: u {names} to zadziałało po wejściu przez {via}. Rekomendacja dotyczy tylko "
            "dróg, na których zgłoszono problem — nie tej.")


def annotate_recommendations(narrative: dict, conflicts: list[dict]) -> None:
    """Adds `route_caveat` to every recommendation about a topic with a contradicting account."""
    if not isinstance(narrative, dict) or not conflicts:
        return
    items = [i for i in narrative.get("priority_recommendations") or [] if isinstance(i, dict)]
    for key in ("critical_issues", "serious_issues", "minor_issues"):
        items += [i for i in narrative.get(key) or [] if isinstance(i, dict)]
    for item in items:
        text = " ".join(str(item.get(k) or "") for k in ("area", "action", "recommendation", "impact"))
        stems = _topic_stems(text)
        for c in conflicts:
            if len(stems & _topic_stems(c["topic"])) >= ANNOTATE_STEMS and c["worked"]:
                item["route_caveat"] = route_caveat(c)
                break
