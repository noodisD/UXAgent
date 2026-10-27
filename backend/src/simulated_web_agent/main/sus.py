"""System Usability Scale (SUS) scoring.

The ten items alternate wording: odd items are positive ("I thought the site was
easy to use"), even items negative ("I found the site unnecessarily complex").
Averaging the raw 1-5 answers across items mixes the two directions — a run's
report printed "2,15/5, bardzo nisko" for two participants who scored 80 and
42.5. The score is per participant: odd items contribute (x - 1), even items
(5 - x), the sum times 2.5, on a 0-100 scale.

Pure functions, no I/O. `polarity` is public: anything that combines answers
across questions has to know which ones read the other way round.
"""
from __future__ import annotations

import json
import math
import re
import statistics
from decimal import ROUND_HALF_UP, Decimal

# The wording WizardScreen.vue's "insert SUS" button writes. English on purpose:
# the questionnaire the personas answer is English, and so is the archived data.
SUS_ITEMS: tuple[str, ...] = (
    "I think I would like to use this site frequently.",
    "I found the site unnecessarily complex.",
    "I thought the site was easy to use.",
    "I think I would need support to use this site.",
    "I found the various functions well integrated.",
    "I thought there was too much inconsistency in this site.",
    "I would imagine most people would learn this very quickly.",
    "I found the site very cumbersome to use.",
    "I felt very confident using the site.",
    "I needed to learn a lot before I could get going.",
)

# The commonly cited average SUS of human respondents. Printed as context only:
# synthetic participants are not a sample of people.
BENCHMARK_HUMAN = 68
BENCHMARK_NOTE = "dotyczy ludzi — kontekst, nie porównanie"


def _normalise(prompt) -> str:
    """Lowercase, collapsed whitespace, no trailing full stop."""
    return " ".join(str(prompt or "").lower().split()).rstrip(".").rstrip()


_CANONICAL = {_normalise(p): no for no, p in enumerate(SUS_ITEMS, start=1)}


def questions_from_manifest(manifest) -> list[dict]:
    """The manifest's questionnaire questions. Also accepts a questionnaire
    stored as a JSON string; anything else yields no questions."""
    qn = manifest.get("questionnaire") if isinstance(manifest, dict) else None
    if isinstance(qn, str):
        try:
            qn = json.loads(qn)
        except ValueError:
            qn = None
    questions = qn.get("questions") if isinstance(qn, dict) else None
    return [q for q in questions if isinstance(q, dict)] if isinstance(questions, list) else []


def _item_no(question: dict) -> int | None:
    """Which SUS item (1-10) a question is: its `sus_item` marker, else its wording."""
    marker = question.get("sus_item")
    if isinstance(marker, int) and not isinstance(marker, bool) and 1 <= marker <= len(SUS_ITEMS):
        return marker
    return _CANONICAL.get(_normalise(question.get("prompt")))


def sus_items(q_meta) -> dict[str, int] | None:
    """{question id: SUS item number 1..10}, or None when the questionnaire is not SUS.

    All ten items must be present exactly once. An edited wording or a missing
    item changes what is measured, so a partial or altered scale is not scored
    as SUS at all. Other questions may sit beside it.
    """
    found: dict[str, int] = {}
    for q in q_meta or []:
        if not isinstance(q, dict) or q.get("id") in (None, ""):
            continue
        no = _item_no(q)
        if no is not None:
            found[str(q["id"])] = no
    if sorted(found.values()) != list(range(1, len(SUS_ITEMS) + 1)):
        return None
    return found


def polarity(q_meta) -> dict[str, bool]:
    """{question id: reverse_scored} for every question that has an id.

    True for a question marked `reverse: true`, and for a SUS item with an even
    number — those are the negatively worded ones. Decided per question: a
    negative SUS item stays negative even in a questionnaire too incomplete to
    score as SUS.
    """
    out: dict[str, bool] = {}
    for q in q_meta or []:
        if not isinstance(q, dict) or q.get("id") in (None, ""):
            continue
        no = _item_no(q)
        out[str(q["id"])] = q.get("reverse") is True or (no is not None and no % 2 == 0)
    return out


def _number(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def sus_score(by_qid: dict, items: dict[str, int]) -> tuple[float | None, str | None]:
    """(score 0-100, None), or (None, why not).

    Needs an answer for all ten items, each numeric and within 1..5. Anything
    short of that is no score — a SUS computed from nine items is not a SUS.
    """
    values = {no: _number(by_qid.get(qid)) for qid, no in items.items()}
    answered = sum(1 for v in values.values() if v is not None)
    if answered < len(SUS_ITEMS):
        return None, f"incomplete: {answered} of {len(SUS_ITEMS)}"
    if any(not 1 <= v <= 5 for v in values.values()):
        return None, "out of range"
    total = sum((v - 1) if no % 2 else (5 - v) for no, v in values.items())
    return round(total * 2.5, 1), None


def _why_not(reason: str) -> str:
    """sus_score's reason as the report prints it."""
    m = re.fullmatch(r"incomplete: (\d+) of (\d+)", reason)
    if m:
        return f"niepełne odpowiedzi SUS: {m.group(1)} z {m.group(2)}"
    return "odpowiedź SUS spoza skali 1–5" if reason == "out of range" else reason


def _round_half_up(x: float) -> float:
    """One decimal, ties away from zero. Participant scores are multiples of 2.5,
    so a two-person mean lands exactly on .x25 / .x75 half the time, and round()
    (half-to-even) would print 61.25 as 61.2 against the 61.3 anyone computes by hand."""
    return float(Decimal(str(x)).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))


def summarize_sus(items: dict[str, int], participants: list[dict]) -> dict:
    """The run's SUS figures from one row per participant.

    A row is {persona_dir, persona, answers: {question id: number}, why_not}.
    `why_not` set means the participant is already out (truncated session,
    failed survey) and is listed with that reason instead of a score.

    The mean needs two scored participants: one person's score is not an
    average. Straight-lined participants (all ten answers equal) are flagged,
    not removed — whether such a run measured anything is the reader's call.
    """
    per_participant, scores, straightlined = [], [], []
    for p in participants:
        score, why = None, p.get("why_not")
        if not why:
            answers = p.get("answers") or {}
            score, reason = sus_score(answers, items)
            why = _why_not(reason) if reason else None
            if score is not None and len({_number(answers.get(q)) for q in items}) == 1:
                straightlined.append(p.get("persona_dir"))
        if score is not None:
            scores.append(score)
        per_participant.append({"persona_dir": p.get("persona_dir"), "persona": p.get("persona"),
                                "score": score, "why_not": why})
    return {
        "scale": "0-100",
        "per_participant": per_participant,
        "n_scored": len(scores),
        "n_participants": len(participants),
        "mean": _round_half_up(statistics.mean(scores)) if len(scores) >= 2 else None,
        "min": min(scores) if scores else None,
        "max": max(scores) if scores else None,
        "straightlined": straightlined,
        "benchmark_human": BENCHMARK_HUMAN,
        "benchmark_note": BENCHMARK_NOTE,
    }
