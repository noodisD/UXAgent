"""The overall UX score, computed from evidence — and itemised.

Until this module the score was the aggregate model's own judgement: the prompt
asked for "<float 1.0-5.0 — derive it from the findings below>" and a prose
score_rationale written after the fact. There was no formula behind the number,
so there was nothing to itemise. Asking the model to list deductions would have
produced figures invented to sum to a number it had already chosen — precise-
looking and unfalsifiable, which is exactly what a specialist picks apart first
("why is a missing label worth 0.5 and not 0.3?").

So the score is arithmetic now, and the model only explains it. Every deduction
names what it is for, the rule that sized it, and the evidence behind it; the
reader can disagree with a weight, but never with hidden arithmetic.

What counts
-----------
  * verified usability issues — one line per heuristic, sized by severity, by
    reach (how many participants hit it) and by confidence;
  * task completion — failure and partial completion, halved when the outcome
    is the participant's own verdict rather than measured from the trace;
  * automated accessibility findings — weighted below the heuristics, since
    they are a scan, not a WCAG audit;
  * measured mobile performance, only when the measurement succeeded.

What does not, and is listed as such: findings the verification gate excluded,
first-impression ratings and survey answers. The last two are self-reported and
already shown in their own sections; letting them move the score would double
count the same opinion.

All weights live in RULES. Change them there; the report prints them.
"""
from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from .textutil import decimal_text, pl_plural

START = 5.0
FLOOR = 1.0

RULES = {
    # Base deduction per verified issue, by severity.
    "issue": {"krytyczny": 1.0, "poważny": 0.5, "drobny": 0.15},
    # Reach factor = floor + (1 - floor) × share of participants who hit it.
    # One participant of three still costs two thirds: a real defect seen once
    # is not a third of a defect.
    "reach_floor": 0.5,
    "confidence": {"high": 1.0, "medium": 0.75, "low": 0.5},
    # Cosmetic issues cannot sink a site on their own.
    "minor_cap": 1.0,
    # Task completion, spread per participant.
    "task": {"failed": 1.5, "partial": 0.75},
    "declared_factor": 0.5,
    # Automated accessibility scan, by severity.
    "accessibility": {"krytyczny": 0.5, "poważny": 0.25, "drobny": 0.1},
    # Below this share of unnamed images, an alt-text finding counts as minor.
    "alt_text_systemic_share": 0.2,
    # Mobile PageSpeed performance (0-100), only when measured.
    "performance": [(25, 0.5), (50, 0.25)],
}

_SEVERITY_PL = {"krytyczny": "krytyczny", "poważny": "poważny", "drobny": "drobny"}
_CONFIDENCE_PL = {"high": "wysoka", "medium": "średnia", "low": "niska"}


def alt_text_share(images: dict | None) -> float | None:
    """The share of images whose missing name loses something.

    Unnamed icons inside a named button or link and pixels outside the page
    are not counted (a11y_checks.classify_unnamed_images). Runs measured
    before that split carry only the raw share, which is used as it was.
    """
    images = images or {}
    for key in ("share_unnamed_content", "share_unnamed"):
        if isinstance(images.get(key), (int, float)):
            return images[key]
    return None


def _pct(share: float) -> str:
    value = 100 * share
    if 0 < value < 10 and round(value) != round(value, 1):
        return f"{value:.1f}".replace(".", ",")
    return str(round(value))


def alt_text_measured_text(images: dict | None) -> str:
    """The measured line for alt text, the same in every format.

    "90 z 517 obrazów bez opisu (17%)" alone was read as blind users losing
    the car models, when 88 of the 90 were icons in named buttons and
    tracking pixels. The split is printed with the total, so the reader sees
    both what the scan found and what of it is a barrier.
    """
    images = images or {}
    total = images.get("total") or images.get("images_total")
    unnamed = images.get("unnamed", images.get("images_unnamed"))
    if not total or unnamed is None:
        return ""
    text = (f"Zmierzono: {unnamed} z {total} obrazów bez tekstu alternatywnego "
            f"({_pct(unnamed / total)}%).")
    content = images.get("unnamed_content")
    if content is None or not unnamed:
        return text
    split = []
    if images.get("unnamed_in_named_control"):
        split.append("ikony w przyciskach lub linkach, które mają własną nazwę — "
                     f"{images['unnamed_in_named_control']}")
    if images.get("unnamed_outside_content"):
        split.append("elementy poza treścią strony (np. piksele śledzące) — "
                     f"{images['unnamed_outside_content']}")
    if split:
        text += " Z tego: " + "; ".join(split) + "."
    return text + f" Obrazy w treści strony bez opisu: {content} ({_pct(content / total)}%)."


def accessibility_severity(finding: dict, images: dict | None) -> tuple[str, str]:
    """The severity every format shows for an accessibility finding, and the
    measurement behind a demotion ("" when there is none).

    One place, because three renderers applied the rule separately and one of
    them did not: the Word file printed alt text "poważny" where the HTML, the
    PDF and the score all said "drobny" for 9-10% of images unnamed.
    """
    sev = (finding or {}).get("severity") or "drobny"
    share = alt_text_share(images)
    if ((finding or {}).get("topic") == "alt_text" and share is not None
            and share < RULES["alt_text_systemic_share"]):
        return "drobny", alt_text_measured_text(images)
    return sev, ""


def _reach(affected: int, participants: int) -> tuple[float, float]:
    share = (affected / participants) if participants else 1.0
    share = max(0.0, min(1.0, share))
    floor = RULES["reach_floor"]
    return share, floor + (1 - floor) * share


def _issue_lines(stats: dict, participants: int) -> list[dict]:
    rows = stats.get("prioritization") or []
    lines = []
    for r in rows:
        sev = r.get("severity") or "drobny"
        base = RULES["issue"].get(sev, RULES["issue"]["drobny"])
        affected = int(r.get("personas_affected") or 0)
        reported = int(r.get("personas_reported") or affected)
        n = int(r.get("participant_count") or participants or 0)
        share, reach = _reach(affected, n)
        conf = str(r.get("confidence") or "high")
        cf = RULES["confidence"].get(conf, 1.0)
        amount = round(base * reach * cf, 2)
        h_id = r.get("heuristic_id")
        lines.append({
            "kind": "issue",
            "severity": sev,
            "label": f"H{h_id}: {r.get('heuristic_name') or ''}".strip(),
            "amount": amount,
            # The reach factor is spelled out: "zasięg 1/3 (0.67)" read to every
            # reviewer as an arithmetic error, because the 0.5 floor that turns
            # a third into two thirds was nowhere on the page.
            "basis": (f"problem {_SEVERITY_PL.get(sev, sev)} ({base}) × zasięg "
                      f"{affected}/{n}"
                      + (f" — zgłosił {reported}, na stronie widoczne u {affected}"
                         if reported < affected else "")
                      + f" (0.5 + 0.5 × {share:.2f} = {reach:.2f}) × "
                      f"pewność {_CONFIDENCE_PL.get(conf, conf)} ({cf})"),
            "heuristic_id": h_id,
        })
    return lines


def _task_line(outcomes: dict, sources: dict, held: set | None = None) -> dict | None:
    """Task completion, per participant.

    `held` are participants whose verdict rests on a finding the gate set
    aside. The hyundai run: Krzysztof's "failed" was the model's conclusion that
    the configurator ignored his EXECUTIVE choice — the very claim held back for
    a human check. Counting it would charge the same unconfirmed claim twice,
    once as an issue and once as a failed task.
    """
    if not outcomes:
        return None
    held = held or set()
    n = len(outcomes)
    total = 0.0
    counts = {"failed": 0, "partial": 0}
    declared = 0
    for key, outcome in outcomes.items():
        if key in held:
            continue
        outcome = (outcome or "").lower()
        if outcome == "persona_abandoned":
            outcome = "failed"
        weight = RULES["task"].get(outcome)
        if weight is None:
            continue
        counts[outcome] += 1
        factor = 1.0
        if (sources or {}).get(key) == "declared":
            factor = RULES["declared_factor"]
            declared += 1
        total += weight * factor / n
    if not total:
        return None
    # "Declared" is the model's verdict on the participant's run, not the
    # participant's own rating — the survey is that, and it can disagree
    # (Krzysztof rated his confidence 7/7 and was declared "failed").
    factor = f"{RULES['declared_factor']:g}"
    counted = sum(counts.values())
    measured_note = (f"ocena deklarowana (werdykt modelu, niezmierzony) — waga ×{factor}"
                     if declared == counted
                     else f"{declared} z {counted} to ocena deklarowana (waga ×{factor})"
                     if declared else "zmierzone z zapisu sesji")
    return {
        "kind": "task",
        "label": "Realizacja zadania",
        "amount": round(total, 2),
        "basis": (f"nieosiągnięty: {counts['failed']}/{n} (po {RULES['task']['failed']:g}), "
                  f"częściowo: {counts['partial']}/{n} (po {RULES['task']['partial']:g}); "
                  f"{measured_note}"),
    }


def _accessibility_lines(stats: dict) -> list[dict]:
    a11y = stats.get("accessibility") or {}
    images = (a11y.get("measured") or {}).get("images") or {}
    share = alt_text_share(images)
    counted = images.get("unnamed_content", images.get("unnamed"))
    lines = []
    for f in a11y.get("findings") or []:
        if not isinstance(f, dict) or not f.get("verified"):
            continue
        sev, measured = accessibility_severity(f, images)
        note = ""
        if measured:
            what = "obrazów treści" if "unnamed_content" in images else "obrazów"
            note = (f"; zmierzono {counted} z {images.get('total')} {what} bez opisu "
                    f"({_pct(share)}%) — liczone jako drobny")
        base = RULES["accessibility"].get(sev, RULES["accessibility"]["drobny"])
        lines.append({
            "kind": "accessibility",
            "severity": sev,
            "label": f"Dostępność: {f.get('category')}",
            "amount": round(base, 2),
            "basis": f"skan automatyczny, {sev} ({base}){note}",
        })
    return lines


def _performance_line(pagespeed: dict | None) -> dict | None:
    mobile = (pagespeed or {}).get("mobile") or {}
    if not isinstance(mobile, dict) or mobile.get("error"):
        return None
    score = mobile.get("performance_score")
    if not isinstance(score, (int, float)):
        return None
    for threshold, amount in RULES["performance"]:
        if score < threshold:
            return {
                "kind": "performance",
                "label": "Wydajność na urządzeniach mobilnych",
                "amount": amount,
                "basis": f"PageSpeed mobile {score:g}/100 (poniżej {threshold})",
            }
    return None


def compute_ux_score(stats: dict, *, outcomes: dict | None = None,
                     outcome_sources: dict | None = None,
                     pagespeed: dict | None = None) -> dict:
    """The score, every deduction behind it, and what was deliberately left out."""
    stats = stats or {}
    participants = int(((stats.get("outcomes") or {}).get("participant_count"))
                       or len(outcomes or {}) or 0)

    deductions: list[dict] = []
    issues = _issue_lines(stats, participants)
    deductions.extend(issues)

    # Cap the cumulative cost of minor issues, as its own visible line.
    minor_total = sum(d["amount"] for d in issues if d["severity"] == "drobny")
    if minor_total > RULES["minor_cap"]:
        deductions.append({
            "kind": "adjustment",
            "label": "Limit dla drobnych problemów",
            "amount": -round(minor_total - RULES["minor_cap"], 2),
            "basis": f"łączna kara za drobne problemy ograniczona do {RULES['minor_cap']:g}",
        })

    # Participants whose verdict rests on a held-back finding. Only a declared
    # verdict can: an outcome measured from the trace (session_goal_state) does
    # not come from the model's words, whatever its reflection says. Applying
    # the rule to measured outcomes printed "nieosiągnięty: 1/2 … zmierzone z
    # zapisu sesji" for the lidl run, where both failures were measured and
    # the rest of the report said 2/2.
    held = {r.get("persona_dir") for r in (stats.get("objective_reflections") or [])
            if isinstance(r, dict) and r.get("self_reported_only") and r.get("persona_dir")}
    held &= set((outcomes or {}).keys())
    held = {d for d in held if (outcome_sources or {}).get(d) != "observed"}
    # Ended by the harness after identical actions with no page change — the
    # stuck-loop signature attribution gates as our failure — so whether the
    # site or the tool gave out cannot be told from the trace. Named for a human
    # check, like an unresponsive control, not charged as a failed task.
    abandoned = {d for d, src in (outcome_sources or {}).items() if src == "session"}

    for extra in (_task_line(outcomes or {}, outcome_sources or {}, held | abandoned),
                  *_accessibility_lines(stats),
                  _performance_line(pagespeed)):
        if extra:
            deductions.append(extra)

    total = round(sum(d["amount"] for d in deductions), 2)
    raw = START - total
    # Half-up on the two decimals the breakdown prints. round() rounds halves
    # to even, so "5.0 − 0.75 = 4.25, zaokrąglone 4.2" contradicted itself, and
    # 0.75 is the commonest single deduction here (one critical issue at half
    # reach, two declared failures of two).
    clamped = max(FLOOR, min(START, raw))
    score = float(Decimal(f"{clamped:.2f}").quantize(Decimal("0.1"), rounding=ROUND_HALF_UP))

    sev_counts = (stats.get("heuristic") or {}).get("severity_counts") or {}
    not_counted = []
    gated = int(sev_counts.get("needs_verification") or 0)
    if gated:
        not_counted.append({
            "label": f"{gated} wniosków wykluczonych przez weryfikację",
            "reason": "nieudana interakcja narzędzia, brak potwierdzenia w zapisie "
                      "sesji lub inna domena — nie są dowodem o stronie",
        })
    if held:
        not_counted.append({
            "label": f"Wynik zadania {len(held)} uczestnika(ów)",
            "reason": "werdykt opiera się na wniosku wstrzymanym do weryfikacji — "
                      "liczenie go obciążyłoby ocenę tym samym niepotwierdzonym "
                      "twierdzeniem drugi raz",
        })
    if abandoned:
        not_counted.append({
            "label": f"Wynik zadania {len(abandoned)} uczestnika(ów), których sesja została przerwana",
            "reason": "narzędzie przerwało sesję po kilku identycznych próbach bez zmiany "
                      "strony — z zapisu nie wynika, czy zawiodła strona, czy narzędzie; "
                      "do sprawdzenia ręcznie",
        })
    not_counted.append({
        "label": "Oceny pierwszego wrażenia i odpowiedzi w ankiecie",
        "reason": "samoocena uczestników, pokazana w osobnych sekcjach; wliczenie "
                  "jej do oceny liczyłoby tę samą opinię dwa razy",
    })
    if not _performance_line(pagespeed) and ((pagespeed or {}).get("mobile") or {}).get("error"):
        # "Failed" alone sat beside a measured desktop card in three runs.
        desktop = ((pagespeed or {}).get("desktop") or {}).get("performance_score")
        reason = "pomiar mobilny PageSpeed nie powiódł się — błąd pomiaru nie jest wadą strony"
        if isinstance(desktop, (int, float)):
            reason += (f"; wynik desktopowy ({desktop:g}/100) jest pokazany w raporcie, "
                       "ale ocena liczy tylko wynik mobilny")
        not_counted.append({"label": "Wydajność strony", "reason": reason})

    return {
        "score": score,
        "start": START,
        "floor": FLOOR,
        "total_deduction": total,
        "raw": round(raw, 2),
        "floored": raw < FLOOR,
        "deductions": deductions,
        "not_counted": not_counted,
        "rules": RULES,
    }


def rationale_text(result: dict) -> str:
    """The score's one-line rationale, written from the breakdown itself.

    The narrator wrote its own, and it contradicted the table printed under
    it — "−0.25 za częściowe wykonanie przez wszystkie 3/3" above a line
    counting 2 of 3, "poważny problem" above "liczone jako drobny".
    """
    items = sorted((d for d in result.get("deductions") or [] if d.get("amount")),
                   key=lambda d: -abs(d["amount"]))
    parts = [f"{d['label']} −{decimal_text(d['amount'], 2)}" if d["amount"] >= 0
             else f"{d['label']} +{decimal_text(abs(d['amount']), 2)}" for d in items[:4]]
    more = len(items) - len(parts)
    text = (f"Ocena {decimal_text(result['score'], 1)}/5: od {decimal_text(result['start'], 1)} odjęto "
            f"{decimal_text(result['total_deduction'], 2)} pkt")
    if parts:
        rest = f" i {more} {pl_plural(more, 'mniejsza pozycja', 'mniejsze pozycje', 'mniejszych pozycji')}"
        text += " — " + "; ".join(parts) + (rest if more > 0 else "")
    return text + ". Pełne rozbicie w sekcji „Jak obliczono ocenę”."


def breakdown_text(result: dict) -> str:
    """The breakdown in plain text, for the aggregate prompt to explain."""
    lines = [f"Punkt wyjścia: {decimal_text(result['start'], 1)}"]
    for d in result["deductions"]:
        sign = "−" if d["amount"] >= 0 else "+"
        lines.append(f"  {sign}{decimal_text(abs(d['amount']), 2)}  {d['label']} — {d['basis']}")
    lines.append(f"Suma odliczeń: {decimal_text(result['total_deduction'], 2)}")
    lines.append(f"Wynik: {decimal_text(result['start'], 1)} − {decimal_text(result['total_deduction'], 2)} = "
                 f"{decimal_text(result['raw'], 2)} → {decimal_text(result['score'], 1)}"
                 + (" (minimum 1.0)" if result["floored"] else ""))
    for nc in result["not_counted"]:
        lines.append(f"Nie wliczono: {nc['label']} ({nc['reason']})")
    return "\n".join(lines)
