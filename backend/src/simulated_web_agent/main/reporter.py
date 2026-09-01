"""
Report generation: per-agent and aggregated.

Per-agent:
  generate_per_agent_report(store) → trace_dir/ux_report.json + .md

Aggregated (called after ALL agents finish):
  generate_aggregated_report(stores, task, url) → runs/aggregated_report.json + .md
"""

import json
import logging
import re
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ..agent.gpt import async_chat
from ..agent.prompt_registry import registry as _prompts
from .pagespeed import format_pagespeed_summary, read_pagespeed
from .prioritization import prioritize
from .root_cause import assign_root_causes
from .severity import excluded_from_report
from .trace_store import TraceStore

log = logging.getLogger(__name__)

SEVERITY_ORDER = {"krytyczny": 0, "poważny": 1, "drobny": 2}
SEVERITY_EN = {"krytyczny": "critical", "poważny": "serious", "drobny": "minor"}
HEURISTIC_NAMES = {
    1: "Widoczność statusu systemu",
    2: "Zgodność systemu z rzeczywistością",
    3: "Kontrola użytkownika i swoboda działania",
    4: "Spójność i standardy",
    5: "Zapobieganie błędom",
    6: "Rozpoznawanie zamiast zapamiętywania",
    7: "Elastyczność i efektywność użytkowania",
    8: "Estetyczny i minimalistyczny design",
    9: "Pomoc w rozpoznawaniu i naprawianiu błędów",
    10: "Pomoc i dokumentacja",
}


def _severity_sort_key(finding: dict) -> int:
    return SEVERITY_ORDER.get(finding.get("severity", "drobny"), 99)


# ---------------------------------------------------------------------------
# Per-agent report
# ---------------------------------------------------------------------------

def _build_per_agent_json(store: TraceStore) -> dict:
    basic = store.read_basic_info()
    first_impression = store.read_first_impression() or {}
    heuristic_findings: list = store.read_heuristic_findings()
    accessibility = store.read_accessibility_findings() or {}
    viewport = store.read_viewport_findings()
    survey = store.read_survey_result() or {}

    sorted_findings = sorted(heuristic_findings, key=_severity_sort_key)

    # Headline severity counts exclude findings the gates demoted — harness
    # failures, off-site pages, and claims the verifier could not support
    # (severity.excluded_from_report is the single shared predicate).
    confirmed = [f for f in heuristic_findings if not excluded_from_report(f)]
    severity_counts = Counter(f.get("severity") for f in confirmed)
    needs_verification = sum(1 for f in heuristic_findings if excluded_from_report(f))

    return {
        "trace_dir": str(store.trace_dir),
        "persona": basic.get("persona", ""),
        "intent": basic.get("intent", ""),
        "first_impression": first_impression,
        "heuristic_findings": sorted_findings,
        "severity_counts": {
            "krytyczny": severity_counts.get("krytyczny", 0),
            "poważny": severity_counts.get("poważny", 0),
            "drobny": severity_counts.get("drobny", 0),
            "needs_verification": needs_verification,
        },
        "accessibility": accessibility,
        "viewport_findings": viewport,
        "survey_result": survey,
        "session_result": store.read_session_result(),
    }


def _render_md(report: dict) -> str:
    lines: list[str] = []

    def h(level: int, text: str):
        lines.append(f"{'#' * level} {text}\n")

    def p(text: str):
        lines.append(f"{text}\n")

    def ul(items: list[str]):
        for item in items:
            lines.append(f"- {item}")
        lines.append("")

    h(1, "Raport UX – ocena agenta")
    persona_text = report.get("persona", "")
    # Show first paragraph as the header line; full persona follows
    persona_first = persona_text.split("\n\n")[0] if persona_text else ""
    p(f"**Persona:** {persona_first}")
    if persona_text and "\n\n" in persona_text:
        lines.append(f"\n<details><summary>Pełna persona</summary>\n\n{persona_text}\n\n</details>\n")
    p(f"**Zadanie:** {report.get('intent', '')}")

    # First impression
    fi = report.get("first_impression") or {}
    if not isinstance(fi, dict):
        fi = {}  # malformed LLM shape (e.g. a bare list) — skip the section
    if fi and not fi.get("error"):
        h(2, "Pierwsze wrażenie (test 5 sekund)")
        p(f"**Ocena ogólna:** {fi.get('overall_score', '—')}/5")
        p(fi.get("clarity_summary", ""))
        scores = fi.get("scores") or {}
        if scores:
            h(3, "Oceny cząstkowe")
            score_labels = {
                "purpose_clarity": "Jasność celu",
                "navigation_visibility": "Widoczność nawigacji",
                "call_to_action": "Wezwanie do działania",
                "visual_hierarchy": "Hierarchia wizualna",
                "trustworthiness": "Wiarygodność",
            }
            for key, label in score_labels.items():
                lines.append(f"| {label} | {scores.get(key, '—')}/5 |")
            lines.append("")
        strengths = fi.get("strengths") or []
        weaknesses = fi.get("weaknesses") or []
        if strengths:
            h(3, "Mocne strony")
            ul(strengths)
        if weaknesses:
            h(3, "Słabe strony")
            ul(weaknesses)

    # Heuristic findings
    findings = report.get("heuristic_findings") or []
    counts = report.get("severity_counts") or {}
    h(2, "Ocena heurystyczna (Nielsen)")
    p(
        f"Znaleziono **{len(findings)}** problemów: "
        f"{counts.get('krytyczny', 0)} krytycznych, "
        f"{counts.get('poważny', 0)} poważnych, "
        f"{counts.get('drobny', 0)} drobnych."
    )
    if findings:
        for f in findings:
            severity_badge = f.get("severity", "drobny").upper()
            h(3, f"[{severity_badge}] H{f.get('heuristic_id', '?')}: {f.get('heuristic_name', '')}")
            p(f"**URL:** {f.get('url', '—')}  |  **Krok:** {f.get('step', '—')}")
            p(f"**Dowód:** {f.get('evidence', '')}")
            p(f"**Rekomendacja:** {f.get('recommendation', '')}")

    # Accessibility
    acc = report.get("accessibility") or {}
    if acc and not acc.get("error"):
        h(2, "Dostępność")
        p(f"**Ocena:** {acc.get('overall_score', '—')}/5")
        p(acc.get("summary", ""))
        acc_findings = acc.get("findings") or []
        if acc_findings:
            h(3, "Problemy z dostępnością")
            for af in acc_findings:
                p(f"**[{af.get('severity', '').upper()}]** {af.get('category', '')} — {af.get('detail', '')}")
                p(f"*Rekomendacja:* {af.get('recommendation', '')}")
        font_issues = acc.get("font_size_issues") or []
        if font_issues:
            h(3, "Problemy z rozmiarem czcionki")
            for fi_item in font_issues:
                p(f"Krok {fi_item.get('step', '?')}: min {fi_item.get('min_size_found', '?')} — {fi_item.get('detail', '')}")

    # Viewport
    vp_findings = report.get("viewport_findings") or []
    issues = [v for v in vp_findings if v.get("horizontal_overflow") or v.get("wide_elements") or v.get("small_fonts")]
    h(2, "Responsywność")
    if issues:
        for v in issues:
            p(f"**{v.get('viewport', '')} @ {v.get('url', '')}**")
            if v.get("horizontal_overflow"):
                p("⚠ Wykryto poziomy pasek przewijania (overflow).")
            if v.get("wide_elements"):
                p(f"Elementy szersze niż viewport: {', '.join(v['wide_elements'][:5])}")
            if v.get("small_fonts"):
                for sf in v["small_fonts"]:
                    p(f"Mała czcionka: {sf.get('size')}px: \"{sf.get('text', '')}\"")
    else:
        p("Nie wykryto problemów z responsywnością.")

    # Survey
    survey = report.get("survey_result") or {}
    answers = survey.get("answers") or []
    if answers:
        h(2, "Wyniki ankiety")
        for a in answers:
            qid = a.get("question_id", "")
            val = a.get("value", "")
            reason = a.get("reason", "")
            p(f"**{qid}:** {val}")
            if reason:
                p(f"*{reason}*")

    return "\n".join(lines)


async def generate_per_agent_report(store: TraceStore) -> dict:
    """Build and save per-agent ux_report.json + ux_report.md."""
    report = _build_per_agent_json(store)
    md = _render_md(report)
    store.write_ux_report(report, md)
    log.info("[reporter] Per-agent report saved to %s", store.trace_dir)
    return report


# ---------------------------------------------------------------------------
# Objective reflection (structured, dashboard-facing)
# ---------------------------------------------------------------------------

_REFLECTION_SYSTEM = """\
Jesteś starszym badaczem UX. Otrzymasz dane z sesji symulowanego użytkownika (persony)
na stronie internetowej: jej cel, oceny heurystyczne, pierwsze wrażenie, dostępność,
responsywność oraz odpowiedzi ankietowe.

Oceń z perspektywy CELU persony, czy udało się go osiągnąć, i zwróć WYŁĄCZNIE obiekt JSON
(bez markdown, bez komentarzy) o następującej strukturze:

{
  "outcome": "success | partial | failed",
  "summary": "2-4 zdania po polsku: czy persona osiągnęła swój cel i dlaczego",
  "blockers": ["konkretna bariera związana z celem", "..."],
  "wins": ["co realnie pomogło personie", "..."],
  "priorityFix": "jedna najważniejsza rzecz do naprawienia, konkretna (UI/przepływ/treść)"
}

Zasady:
- "outcome" MUSI być dokładnie jedną z wartości: success, partial, failed.
- "success" = cel w pełni osiągnięty; "partial" = częściowo / z dużym tarciem;
  "failed" = cel nieosiągnięty.
- Pole "session" opisuje, jak sesja faktycznie się zakończyła (steps_taken,
  max_steps, ended_reason). Jeśli sesja urwała się po kilku krokach, a cel nie
  został nawet podjęty, oceń "failed", a w summary wyraźnie zaznacz, że sesja
  została przerwana przedwcześnie i dane z niej są ograniczone.
- Pole "site_context" opisuje, czym strona faktycznie jest (np. katalog
  produktów bez zakupów online, wyszukiwarka sklepów stacjonarnych). NIE oceniaj
  jako "partial" ani "failed" tego, że persona nie mogła wykonać czynności, której
  ta strona z założenia nie oferuje (np. zakup online na stronie bez koszyka).
  Jeśli persona dotarła do najbliższego osiągalnego odpowiednika swojego celu
  zgodnego z tym, czym strona jest (np. znalazła produkt i sklep stacjonarny
  zamiast kupić online), to jest "success" — nie karz personę za ograniczenie
  strony, którego nie mogła obejść.
- blockers i wins: maksymalnie po 4 pozycje, krótkie i konkretne. Mogą być puste ([]).
- Pisz rzeczowo. Nie opisuj metodologii. Zwróć tylko poprawny JSON.\
"""

_VALID_OUTCOMES = {"success", "partial", "failed"}


def _coerce_reflection(data: Any, persona_name: str) -> dict:
    """Validate/normalize the LLM reflection payload into the dashboard shape."""
    d = data if isinstance(data, dict) else {}
    outcome = str(d.get("outcome", "")).strip().lower()
    if outcome not in _VALID_OUTCOMES:
        outcome = "partial"

    def _str_list(v: Any) -> list[str]:
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()][:4]
        return []

    return {
        "outcome": outcome,
        "summary": str(d.get("summary", "")).strip(),
        "blockers": _str_list(d.get("blockers")),
        "wins": _str_list(d.get("wins")),
        "priorityFix": str(d.get("priorityFix", "")).strip(),
    }


async def generate_objective_reflection(store: TraceStore) -> dict:
    """Call the LLM to produce a structured objective_reflection.json for one agent."""
    persona_card = store.read_persona_card() or {}
    basic = store.read_basic_info()
    heuristic_findings = store.read_heuristic_findings()
    first_impression = store.read_first_impression() or {}
    accessibility = store.read_accessibility_findings() or {}
    survey = store.read_survey_result() or {}
    scroll = store.read_scroll_summary() or {}
    form = store.read_form_funnel() or {}
    persona_name = persona_card.get("name", "Unknown")

    payload = {
        "persona_name": persona_name,
        "objective": persona_card.get("objective", basic.get("intent", "")),
        # What the site actually is (e.g. "catalog, no online checkout"), so a
        # persona correctly stopping at the site's real ceiling is not scored
        # the same as one who got stuck. See _REFLECTION_SYSTEM's site_context rule.
        "site_context": basic.get("site_context", "") or "(nie podano)",
        "demographics": persona_card.get("demographics", {}),
        "first_impression_score": first_impression.get("overall_score"),
        "first_impression_summary": first_impression.get("clarity_summary", ""),
        "heuristic_findings": [
            {
                "heuristic_id": f.get("heuristic_id"),
                "severity": f.get("severity"),
                "evidence": f.get("evidence", ""),
                "recommendation": f.get("recommendation", ""),
            }
            for f in heuristic_findings
        ],
        "accessibility_score": accessibility.get("overall_score"),
        "accessibility_summary": accessibility.get("summary", ""),
        "scroll_summary": scroll,
        "form_engagement": form,
        "survey_answers": survey.get("answers", []),
        "session": store.read_session_result() or {},
    }

    try:
        result = await async_chat(
            [
                {"role": "system", "content": _REFLECTION_SYSTEM},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            json_mode=True,
            # "report" -> gemini-2.5-pro. This pass decides `outcome`
            # (success/partial/failed), which iso_metrics turns into
            # task_success and goal_state_reached — the headline effectiveness
            # signal — and which bias_audit and the dashboard verdict both read.
            # Nothing checks it (§4.2), so it is the single most consequential
            # judgement in the run and belongs on the audit tier.
            model="report",
        )
        parsed = json.loads(result)
        reflection = _coerce_reflection(parsed, persona_name)
    except Exception as exc:
        log.warning("[reflection] LLM call failed for %s: %s", store.trace_dir, exc)
        reflection = {
            "outcome": "partial",
            "summary": "",
            "blockers": [],
            "wins": [],
            "priorityFix": "",
        }

    store.write_objective_reflection(reflection)
    log.info("[reflection] Saved objective_reflection.json for %s", store.trace_dir)
    return reflection


# ---------------------------------------------------------------------------
# Aggregated report
# ---------------------------------------------------------------------------

# Sessions shorter than this carry no meaningful post-session survey signal.
MIN_VALID_SESSION_STEPS = 5


def _is_truncated_session(report: dict) -> bool:
    """True when the session ended after too few steps to trust its survey."""
    sr = report.get("session_result") or {}
    steps = sr.get("steps_taken")
    return isinstance(steps, int) and steps < MIN_VALID_SESSION_STEPS


# Post-task metrics appended by the survey pass (not in the questionnaire); they
# ride a 1-7 scale, not the 1-5 Likert, and each carries its own anchors + label.
_SYNTHETIC_SCALES = {
    "synthetic_seq": (1, 7, "bardzo trudne", "bardzo łatwe",
                      "Łatwość wykonania zadania (SEQ)"),
    "synthetic_confidence": (1, 7, "zupełnie niepewny/a", "bardzo pewny/a",
                             "Pewność co do ukończenia zadania"),
}


def _survey_scale(qid: str, meta: dict | None) -> tuple[int, int, str, str]:
    """(min, max, low_anchor, high_anchor) for a rated question. Numeric options
    define the range; agreement statements read low=nie / high=tak."""
    if qid in _SYNTHETIC_SCALES:
        lo, hi, low, high, _ = _SYNTHETIC_SCALES[qid]
        return lo, hi, low, high
    opts = (meta or {}).get("options") or []
    nums = [int(o) for o in opts if str(o).strip().isdigit()]
    if nums:
        return min(nums), max(nums), "zdecydowanie nie", "zdecydowanie tak"
    return 1, 5, "zdecydowanie nie", "zdecydowanie tak"


def _survey_prompt(qid: str, meta: dict | None) -> str:
    if meta and meta.get("prompt"):
        return meta["prompt"]
    if qid in _SYNTHETIC_SCALES:
        return _SYNTHETIC_SCALES[qid][4]
    return qid


def _build_survey_questions(survey_scores: dict, survey_open: dict,
                            q_meta: list | None, avg) -> list[dict]:
    """One entry per question in questionnaire order (synthetic metrics last):
    rated -> value distribution + average + anchored scale; open -> the answers.
    This is what lets the report show the real prompt, the response spread (vital
    at n=5), and the correct scale instead of a bare '/5' bar on an id."""
    q_list = [q for q in (q_meta or []) if isinstance(q, dict)]
    q_map = {q.get("id"): q for q in q_list}
    order = [q.get("id") for q in q_list]
    for qid in list(survey_scores) + list(survey_open):
        if qid not in order:
            order.append(qid)  # data present but not declared (e.g. synthetic_*)

    out: list[dict] = []
    for qid in order:
        meta = q_map.get(qid)
        if survey_scores.get(qid):
            vals = [v for v in survey_scores[qid] if v is not None]
            lo, hi, low_l, high_l = _survey_scale(qid, meta)
            counts = {v: 0 for v in range(lo, hi + 1)}
            for v in vals:
                iv = int(round(v))
                if iv in counts:
                    counts[iv] += 1
            out.append({
                "id": qid, "prompt": _survey_prompt(qid, meta), "kind": "scale",
                "min": lo, "max": hi, "low_label": low_l, "high_label": high_l,
                "avg": avg(vals), "n": len(vals),
                "distribution": [{"value": v, "count": counts[v]}
                                 for v in range(lo, hi + 1)],
            })
        elif survey_open.get(qid):
            answers = [str(a).strip() for a in survey_open[qid] if str(a).strip()]
            if answers:
                out.append({"id": qid, "prompt": _survey_prompt(qid, meta),
                            "kind": "open", "answers": answers})
    return out


def aggregate_statistics(reports: list[dict], outcomes: dict[str, str] | None = None,
                         q_meta: list | None = None) -> dict:
    """Compute statistics across all per-agent reports.

    `outcomes` maps persona_dir -> outcome/ended_reason and is what turns the
    §6.2 prioritization matrix on; without it the matrix is omitted rather than
    guessed (impact needs to know who failed). `q_meta` is the questionnaire's
    `questions` list — it supplies the real prompts + scales for the survey.
    """
    n = len(reports)
    if n == 0:
        return {}

    # First impression scores
    fi_scores = [
        r["first_impression"].get("overall_score")
        for r in reports
        if isinstance(r.get("first_impression"), dict)
        and r["first_impression"].get("overall_score") is not None
    ]

    fi_dimension_scores: dict[str, list[float]] = defaultdict(list)
    for r in reports:
        fi = r.get("first_impression") or {}
        scores = fi.get("scores") or {}
        for k, v in scores.items():
            if v is not None:
                fi_dimension_scores[k].append(float(v))

    # Heuristic findings aggregation — annotate each finding with its persona
    # so aggregated issues can point back to concrete evidence.
    all_findings: list[dict] = []
    for r in reports:
        trace_dir = str(r.get("trace_dir", ""))
        persona_dir = trace_dir.replace("\\", "/").rstrip("/").split("/")[-1] if trace_dir else ""
        m = re.search(r"^Persona:\s*(\S+)", r.get("persona", ""), re.MULTILINE | re.IGNORECASE)
        persona_name = m.group(1).rstrip(".,;:") if m else persona_dir
        for f in r.get("heuristic_findings") or []:
            if isinstance(f, dict):
                all_findings.append({**f, "_persona_dir": persona_dir, "_persona_name": persona_name})

    # Exclude gated findings (harness failures, off-site pages, claims the
    # verifier could not support) from the headline severity counts and
    # per-heuristic grouping, so they can't drive the critical/serious verdict
    # or the score. They remain in all_findings and are surfaced separately.
    confirmed_findings = [f for f in all_findings if not excluded_from_report(f)]
    needs_verification = sum(1 for f in all_findings if excluded_from_report(f))

    # Root-cause clustering (root_cause.py): the same underlying defect can be
    # filed under several different Nielsen heuristics depending on the
    # persona's context — e.g. "search doesn't refresh results" was filed as 6
    # separate krytyczny findings across H1/H2/H3/H4/H5/H9 in one real run.
    # Stamps root_cause_id/root_cause_name on confirmed_findings (mutates the
    # same dict objects also referenced by all_findings, so prioritize() sees
    # the tags too).
    assign_root_causes(confirmed_findings)

    # Headline severity_counts must count each root-cause cluster ONCE, at its
    # worst member severity — not once per heuristic it was filed under.
    # Otherwise one bug inflates the critical count and, downstream, anchors
    # the aggregate score near the floor for a defect that isn't 6x as bad as
    # it looks.
    root_cause_ids: dict[str, list[dict]] = defaultdict(list)
    for f in confirmed_findings:
        rc = f.get("root_cause_id")
        if rc:
            root_cause_ids[rc].append(f)
    severity_counted: list[dict] = []
    seen_rc: set[str] = set()
    for f in confirmed_findings:
        rc = f.get("root_cause_id")
        if rc:
            if rc in seen_rc:
                continue
            seen_rc.add(rc)
            severity_counted.append(min(root_cause_ids[rc], key=_severity_sort_key))
        else:
            severity_counted.append(f)
    severity_totals = Counter(f.get("severity") for f in severity_counted)

    # One summary row per cluster: which heuristics it manifested under, how
    # many personas independently hit it (union across all member findings —
    # confirmed in N different contexts is STRONGER evidence, not weaker), and
    # the worst severity. Fed to the aggregate-synthesis prompt explicitly so
    # the narrator treats it as one issue, not N.
    root_causes_summary = []
    for rc_id, members in root_cause_ids.items():
        heuristics_involved = sorted({m.get("heuristic_id") for m in members if m.get("heuristic_id")})
        personas_affected = len({m.get("_persona_dir") for m in members if m.get("_persona_dir")})
        worst = min(members, key=_severity_sort_key)
        root_causes_summary.append({
            "root_cause_id": rc_id,
            "name": worst.get("root_cause_name", rc_id),
            "worst_severity": worst.get("severity"),
            "heuristics_involved": heuristics_involved,
            "personas_affected": personas_affected,
            "finding_count": len(members),
            "example_evidence": worst.get("evidence", ""),
        })

    # Group findings by heuristic_id — kept per-heuristic for detail (which
    # Nielsen category each manifestation was filed under); root_causes_summary
    # above is the deduplicated cross-heuristic view.
    by_heuristic: dict[int, list[dict]] = defaultdict(list)
    for f in confirmed_findings:
        by_heuristic[f.get("heuristic_id", 0)].append(f)

    def _example(f: dict) -> dict:
        screenshot = f.get("screenshot")
        return {
            "persona_dir": f.get("_persona_dir", ""),
            "persona_name": f.get("_persona_name", ""),
            "step": f.get("step"),
            "url": f.get("url", ""),
            # persona-relative → run-relative so report.html can embed it
            "screenshot": f"{f.get('_persona_dir')}/{screenshot}" if screenshot else None,
            "observation_quote": f.get("observation_quote", ""),
            "evidence": f.get("evidence", ""),
            "verified": bool(f.get("verified")),
        }

    heuristic_summary = []
    for h_id in sorted(by_heuristic):
        group = by_heuristic[h_id]
        freq = len(group)
        # Personas, not findings: one persona filing the same heuristic three
        # times is still ONE affected participant. Counting findings produced
        # impossible frequencies ("10 z 5 uczestników") that the narrative then
        # repeated to the client verbatim.
        personas_affected = len({f.get("_persona_dir") for f in group if f.get("_persona_dir")}) or 1
        worst_severity = min(group, key=_severity_sort_key).get("severity", "drobny")
        verified_group = [f for f in group if f.get("verified")]
        examples = [_example(f) for f in (verified_group or group)[:3]]
        heuristic_summary.append({
            "heuristic_id": h_id,
            "heuristic_name": HEURISTIC_NAMES.get(h_id, ""),
            "frequency": freq,
            "personas_affected": personas_affected,
            "sessions_affected": f"{min(personas_affected, n)} z {n} uczestników",
            "worst_severity": worst_severity,
            "top_evidence": group[0].get("evidence", ""),
            "top_recommendation": group[0].get("recommendation", ""),
            "examples": examples,
            "verified": bool(verified_group),
        })
    heuristic_summary.sort(key=lambda x: (-x["frequency"], SEVERITY_ORDER.get(x["worst_severity"], 99)))

    # Accessibility
    a11y_scores = [
        r["accessibility"].get("overall_score")
        for r in reports
        if isinstance(r.get("accessibility"), dict)
        and r["accessibility"].get("overall_score") is not None
    ]

    # Viewport — count sessions (not pages) with at least one overflow
    mobile_overflow = 0
    tablet_overflow = 0
    for r in reports:
        vp = r.get("viewport_findings") or []
        if any(v.get("viewport") == "mobile" and v.get("horizontal_overflow") for v in vp):
            mobile_overflow += 1
        if any(v.get("viewport") == "tablet" and v.get("horizontal_overflow") for v in vp):
            tablet_overflow += 1

    # Survey — average numeric answers; collect open-text answers.
    # Sessions that ended after only a few steps carry no real experience:
    # their survey answers are excluded from the averages (first-impression
    # scores stay in — those are formed on the first page and remain valid).
    survey_scores: dict[str, list[float]] = defaultdict(list)
    survey_open: dict[str, list[str]] = defaultdict(list)
    excluded_surveys: list[dict] = []
    for r in reports:
        if _is_truncated_session(r):
            sr = r.get("session_result") or {}
            excluded_surveys.append({
                "persona_dir": str(r.get("trace_dir", "")).replace("\\", "/").rstrip("/").split("/")[-1],
                "steps_taken": sr.get("steps_taken"),
                "reason": f"sesja przerwana po {sr.get('steps_taken')} krokach — ankieta wykluczona ze średnich",
            })
            continue
        survey = r.get("survey_result") or {}
        for a in survey.get("answers") or []:
            qid = a.get("question_id", "")
            val = a.get("value", "")
            try:
                survey_scores[qid].append(float(val))
            except (ValueError, TypeError):
                survey_open[qid].append(str(val))

    def _avg(lst: list) -> float | None:
        clean = [x for x in lst if x is not None]
        return round(statistics.mean(clean), 2) if clean else None

    return {
        "participant_count": n,
        "first_impression": {
            "avg_score": _avg(fi_scores),
            "dimension_avgs": {k: _avg(v) for k, v in fi_dimension_scores.items()},
            # Per-dimension response distribution (1-5), so the report can show the
            # spread + anchored scale like the survey, not just a bar.
            "dimension_dist": {
                k: {
                    "avg": _avg(v),
                    "n": len([x for x in v if x is not None]),
                    "distribution": [
                        {"value": i, "count": sum(
                            1 for x in v if x is not None and int(round(x)) == i)}
                        for i in range(1, 6)
                    ],
                }
                for k, v in fi_dimension_scores.items()
            },
        },
        "heuristic": {
            "total_findings": len(confirmed_findings),
            "severity_counts": {
                "krytyczny": severity_totals.get("krytyczny", 0),
                "poważny": severity_totals.get("poważny", 0),
                "drobny": severity_totals.get("drobny", 0),
                "needs_verification": needs_verification,
            },
            "by_heuristic": heuristic_summary,
            # Cross-heuristic dedup (root_cause.py): findings for the same
            # underlying defect, wherever the Nielsen category, collapsed once.
            "root_causes": root_causes_summary,
        },
        # §6.2: severity x impact x confidence, worst first. Impact is derived
        # from reach and task consequence (prioritization.py), never asked of
        # the LLM. Omitted rather than guessed when outcomes are unavailable.
        "prioritization": prioritize(all_findings, outcomes) if outcomes else [],
        "accessibility": {
            "avg_score": _avg(a11y_scores),
        },
        "viewport": {
            "mobile_overflow_sessions": mobile_overflow,
            "tablet_overflow_sessions": tablet_overflow,
        },
        "survey": {
            "avg_scores": {k: _avg(v) for k, v in survey_scores.items()},
            "open_answers": {k: v for k, v in survey_open.items()},
            # Enriched, questionnaire-ordered view: real prompts, per-value
            # distribution, correct scale + anchors. reporter_html renders this.
            "questions": _build_survey_questions(survey_scores, survey_open, q_meta, _avg),
            "surveys_included": n - len(excluded_surveys),
            "excluded_surveys": excluded_surveys,
        },
    }


def _top_issues_text(stats: dict, threshold: float = 0.5) -> str:
    n = stats.get("participant_count", 1)
    cutoff = max(1, round(n * threshold))
    top = [
        h for h in stats["heuristic"]["by_heuristic"]
        # Unique personas, not finding counts — "in ≥50% of sessions" must mean
        # sessions. (Falls back to frequency for pre-dedupe stored reports.)
        if h.get("personas_affected", h["frequency"]) >= cutoff
    ]
    if not top:
        return "(brak problemów w ≥50% sesji)"
    return "\n".join(
        f"- H{h['heuristic_id']} {h['heuristic_name']}: {h['sessions_affected']} — {h['top_evidence'][:120]}"
        for h in top[:5]
    )


def _aggregate_scroll(stores: list[TraceStore]) -> tuple[dict, str]:
    """Return (data_dict, plain_text_summary) for scroll depth across all sessions."""
    summaries = [s.read_scroll_summary() for s in stores]
    summaries = [s for s in summaries if s]

    max_depths = [s["max_depth_pct"] for s in summaries if s.get("max_depth_pct") is not None]
    max_pxs = [s["max_scroll_px"] for s in summaries if s.get("max_scroll_px") is not None]

    def _avg(lst: list) -> float | None:
        return round(sum(lst) / len(lst), 1) if lst else None

    data = {
        "sessions_with_depth_pct": len(max_depths),
        "avg_max_depth_pct": _avg(max_depths),
        "min_max_depth_pct": round(min(max_depths), 1) if max_depths else None,
        "max_max_depth_pct": round(max(max_depths), 1) if max_depths else None,
        "avg_max_scroll_px": _avg(max_pxs),
    }

    if max_depths:
        text = (
            f"avg max depth: {data['avg_max_depth_pct']}% "
            f"(range {data['min_max_depth_pct']}–{data['max_max_depth_pct']}%) "
            f"across {len(max_depths)} sessions"
        )
    elif max_pxs:
        text = f"avg max scroll: {_avg(max_pxs):.0f}px (depth % unavailable — old trace format)"
    else:
        text = "(no scroll data available)"

    return data, text


def _aggregate_links(stores: list[TraceStore]) -> tuple[dict, str]:
    """Return (data_dict, plain_text_summary) for broken links across all sessions."""
    all_broken: list[str] = []
    total_checked = 0
    sessions_with_broken = 0

    for store in stores:
        findings = store.read_link_findings()
        session_broken = set()
        for page_result in findings:
            checked = page_result.get("links_checked", 0)
            total_checked += checked
            for b in page_result.get("broken") or []:
                url = b.get("url", "")
                session_broken.add(url)
                if url not in all_broken:
                    all_broken.append(url)
        if session_broken:
            sessions_with_broken += 1

    data = {
        "total_links_checked": total_checked,
        "unique_broken_urls": len(all_broken),
        "sessions_with_broken_links": sessions_with_broken,
        "broken_urls": all_broken[:20],
    }

    if not total_checked:
        text = "(link check not available — run with new traces)"
    elif not all_broken:
        text = f"No broken links detected across {total_checked} links checked."
    else:
        text = (
            f"{len(all_broken)} unique broken link(s) found "
            f"in {sessions_with_broken}/{len(stores)} sessions "
            f"({total_checked} total links checked). "
            f"Broken: {', '.join(all_broken[:5])}"
            + (" …" if len(all_broken) > 5 else "")
        )

    return data, text


def _aggregate_forms(stores: list[TraceStore]) -> tuple[dict, str]:
    """Return (data_dict, plain_text_summary) for form funnel across all sessions."""
    engaged = 0
    submitted = 0
    total_fields: list[int] = []

    for store in stores:
        ff = store.read_form_funnel()
        if not ff:
            continue
        if ff.get("engaged_form"):
            engaged += 1
        if ff.get("submitted_form"):
            submitted += 1
        fields = ff.get("fields_filled", 0)
        total_fields.append(fields)

    n = len(stores)
    avg_fields = round(sum(total_fields) / len(total_fields), 1) if total_fields else None

    data = {
        "sessions_engaged_form": engaged,
        "sessions_submitted_form": submitted,
        "avg_fields_filled": avg_fields,
        "total_sessions": n,
    }

    if not total_fields:
        text = "(form funnel data not available — run with new traces)"
    else:
        text = (
            f"{engaged}/{n} sessions engaged with a form field; "
            f"{submitted}/{n} sessions reached a submit action; "
            f"avg fields filled per session: {avg_fields}"
        )

    return data, text


def _format_root_causes(root_causes: list) -> str:
    """Tell the narrator explicitly which heuristic_summary rows are the SAME
    underlying defect (root_cause.py), so it reports one confirmed cross-context
    issue instead of N independent severe failures for one bug."""
    if not root_causes:
        return "(brak wykrytych powiązań między różnymi heurystykami)"
    lines = []
    for rc in root_causes:
        heur = ", ".join(f"H{h}" for h in rc["heuristics_involved"])
        lines.append(
            f"- „{rc['name']}” — JEDNA przyczyna, zgłoszona pod {len(rc['heuristics_involved'])} "
            f"różnymi heurystykami ({heur}), potwierdzona niezależnie przez "
            f"{rc['personas_affected']} uczestników. Najwyższa istotność: {rc['worst_severity']}."
        )
    return ("Poniższe pozycje w PODSUMOWANIU NARUSZEŃ HEURYSTYK dotyczą TEJ SAMEJ "
            "przyczyny źródłowej mimo różnych numerów heurystyk — traktuj każdą "
            "jako JEDEN potwierdzony problem, nie osobny błąd na heurystykę:\n"
            + "\n".join(lines))


_ADDED_RUNGS = {"cart_has_items", "order_summary_reached"}


def _format_outcome_distribution(outcomes: dict, reached_rungs: list, n: int) -> str:
    """Measured task-completion summary for the aggregate narrator — evidence
    from the trace (§4.2), so the UX score is anchored to what participants
    actually achieved, not only their synthetic self-report (survey/SEQ)."""
    if not n:
        return "Brak danych o ukończeniu zadania."
    c = Counter((v or "").lower() for v in outcomes.values())
    succ, part, fail = c.get("success", 0), c.get("partial", 0), c.get("failed", 0)
    ab = c.get("persona_abandoned", 0)
    parts = [f"sukces: {succ}/{n}", f"częściowo: {part}/{n}", f"porażka: {fail}/{n}"]
    if ab:
        parts.append(f"porzucono: {ab}/{n}")
    line2 = ""
    if reached_rungs:
        m = len(reached_rungs)
        added = sum(1 for r in reached_rungs if r in _ADDED_RUNGS)
        checkout = sum(1 for r in reached_rungs if r == "order_summary_reached")
        line2 = (f"\n- {added}/{m} uczestników dodało produkt do koszyka; "
                 f"{checkout}/{m} dotarło do etapu kasy / podsumowania zamówienia.")
    return ("UKOŃCZENIE ZADANIA — zmierzone z zapisu sesji (dowód z przebiegu, "
            "nie samoocena agenta):\n- " + "; ".join(parts) + "." + line2)


async def generate_aggregated_report(
    stores: list[TraceStore],
    task_description: str,
    website_url: str,
    site_context: str = "",
    analytics_context: str = "",
) -> dict:
    """
    Load all per-agent reports, compute statistics, call large LLM for narrative,
    save runs/aggregated_report.json + .md.
    """
    reports = [s.read_ux_report() for s in stores]
    reports = [r for r in reports if r]

    if not reports:
        log.warning("[reporter] No per-agent reports found for aggregation")
        return {}

    # Derive site_context / analytics_context from first store if not supplied by caller
    if not site_context and stores:
        site_context = stores[0].read_basic_info().get("site_context", "") or ""
    if not analytics_context and stores:
        analytics_context = stores[0].read_basic_info().get("analytics_context", "") or ""

    # Per-persona outcome, for the §6.2 impact derivation. Prefer the OBSERVED
    # outcome (iso_metrics effectiveness, evidence-grounded per §4.2) over the
    # LLM's declared one; fall back to declared where the target was undecidable.
    # A persona who abandoned did not finish, whatever either concluded, so
    # ended_reason wins where it says so.
    outcomes: dict[str, str] = {}
    reached_rungs: list[str] = []
    for s in stores:
        reflection = s.read_objective_reflection() or {}
        session = s.read_session_result() or {}
        eff = (s.read_iso_metrics() or {}).get("effectiveness", {}) or {}
        ended = str(session.get("ended_reason") or "")
        if eff.get("outcome_source") == "observed":
            outcome = str(eff.get("observed_outcome") or "")
        else:
            outcome = str(reflection.get("outcome") or "")
        outcomes[s.trace_dir.name] = (
            ended if ended == "persona_abandoned" else outcome
        )
        if eff.get("outcome_source") == "observed":
            reached_rungs.append(str(eff.get("reached_rung") or ""))

    # Questionnaire (from the run manifest) supplies real prompts + scales for
    # the survey section — without it the report shows bare question ids.
    q_meta = None
    try:
        if stores:
            _mani = json.loads(
                (stores[0].trace_dir.parent / "run_manifest.json").read_text(encoding="utf-8"))
            _qn = _mani.get("questionnaire")
            if isinstance(_qn, str):
                _qn = json.loads(_qn)
            if isinstance(_qn, dict):
                q_meta = _qn.get("questions")
    except Exception:
        q_meta = None

    stats = aggregate_statistics(reports, outcomes=outcomes, q_meta=q_meta)
    n = stats["participant_count"]

    # Determine runs/ dir and read page speed (must happen before prompt build)
    runs_dir = stores[0].trace_dir.parent if stores else Path("runs")
    pagespeed_data = read_pagespeed(runs_dir)
    pagespeed_summary = format_pagespeed_summary(pagespeed_data)

    # Aggregate new data passes
    scroll_data, scroll_text = _aggregate_scroll(stores)
    link_data, link_text = _aggregate_links(stores)
    form_data, form_text = _aggregate_forms(stores)

    # Build LLM prompt
    prompt_template = _prompts.get("aggregate_synthesize")

    fi_stats = json.dumps(stats["first_impression"], ensure_ascii=False, indent=2)
    heuristic_summary = json.dumps(
        stats["heuristic"]["by_heuristic"][:10], ensure_ascii=False, indent=2
    )
    a11y_summary = json.dumps(stats["accessibility"], ensure_ascii=False, indent=2)
    vp_summary = json.dumps(stats["viewport"], ensure_ascii=False, indent=2)
    sc = stats["heuristic"]["severity_counts"]
    top_issues = _top_issues_text(stats)
    survey_summary = json.dumps(stats.get("survey", {}), ensure_ascii=False, indent=2)

    # Measured task-completion — anchors the score to observed reality (§4.2),
    # not only synthetic self-report. Also recorded in stats for the payload.
    outcome_distribution = _format_outcome_distribution(outcomes, reached_rungs, n)
    stats["outcomes"] = {"counts": dict(Counter(outcomes.values())), "participant_count": n}
    root_cause_summary = _format_root_causes(stats["heuristic"].get("root_causes") or [])

    prompt = prompt_template.format(
        outcome_distribution=outcome_distribution,
        root_cause_summary=root_cause_summary,
        site_context=site_context,
        analytics_context=analytics_context or "(not provided)",
        participant_count=n,
        task_description=task_description,
        website_url=website_url,
        first_impression_stats=fi_stats,
        heuristic_summary=heuristic_summary,
        accessibility_summary=a11y_summary,
        viewport_summary=vp_summary,
        pagespeed_summary=pagespeed_summary,
        scroll_summary=scroll_text,
        link_summary=link_text,
        form_summary=form_text,
        critical_count=sc.get("krytyczny", 0),
        serious_count=sc.get("poważny", 0),
        minor_count=sc.get("drobny", 0),
        top_issues=top_issues,
        survey_summary=survey_summary,
    )

    messages = [{"role": "user", "content": prompt}]
    try:
        raw = await async_chat(messages, model="report", log=False)
        # extract JSON from LLM output
        import regex
        matches = regex.findall(r"\{(?:[^{}]|(?R))*\}", raw, regex.DOTALL)
        narrative = json.loads(matches[0]) if matches else {"raw": raw}
    except Exception as e:
        log.exception("[reporter] aggregate LLM call failed: %s", e)
        narrative = {"error": str(e)}

    _attach_issue_evidence(narrative, stats)

    try:
        survey_summaries = await _summarize_survey(reports)
    except Exception as e:
        log.exception("[reporter] survey summaries failed (non-fatal): %s", e)
        survey_summaries = []

    aggregated = {
        "statistics": stats,
        "narrative": narrative,
        "pagespeed": pagespeed_data,
        "scroll": scroll_data,
        "links": link_data,
        "forms": form_data,
        "task": task_description,
        "website": website_url,
        "survey_summaries": survey_summaries,
    }

    (runs_dir / "aggregated_report.json").write_text(
        json.dumps(aggregated, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    md = _render_aggregated_md(aggregated)
    (runs_dir / "aggregated_report.md").write_text(md, encoding="utf-8")

    from .reporter_html import save_html_report
    save_html_report(aggregated, runs_dir)

    log.info("[reporter] Aggregated report saved to %s", runs_dir)
    return aggregated


async def _summarize_survey(reports: list[dict]) -> list[str]:
    """One Polish 'why agents answered this way' summary per survey question.

    Returns a list aligned to the questionnaire's question order (taken from the
    first persona's answers — all personas answer the same questionnaire).
    """
    per_question: dict[str, list[str]] = {}
    order: list[str] = []
    for r in reports:
        if _is_truncated_session(r):
            continue
        survey = r.get("survey_result") or {}
        for a in survey.get("answers") or []:
            qid = a.get("question_id")
            if not qid:
                continue
            if qid not in per_question:
                per_question[qid] = []
                order.append(qid)
            per_question[qid].append(
                f"odpowiedź: {a.get('value', '')} | uzasadnienie: {str(a.get('reason', ''))[:400]}"
            )

    if not order:
        return []

    payload = {qid: per_question[qid] for qid in order}
    system = (
        "You are a UX researcher. For EACH survey question (keyed by question id), "
        "synthesise WHY the agents answered the way they did, based on their stated "
        "reasoning. 2-4 sentences per question, in Polish. "
        'Respond with ONLY JSON: {"summaries": {"<question_id>": "<polish summary>", ...}}'
    )
    raw = await async_chat(
        [
            {"role": "system", "content": system},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ],
        json_mode=True,
        log=False,
    )
    summaries = (json.loads(raw).get("summaries")) or {}
    return [str(summaries.get(qid, "")) for qid in order]


def _attach_issue_evidence(narrative: dict, stats: dict) -> None:
    """Deterministically join per-heuristic evidence examples onto narrative issues.

    The LLM writes the prose; evidence (screenshots, URLs, observation quotes)
    comes straight from the trace via aggregate_statistics — never from the LLM.
    Issues whose heuristic has no verified example are marked verified: False.
    """
    if not isinstance(narrative, dict):
        return
    by_h = {
        h.get("heuristic_id"): h
        for h in ((stats.get("heuristic") or {}).get("by_heuristic") or [])
    }
    for key in ("critical_issues", "serious_issues", "minor_issues"):
        for issue in narrative.get(key) or []:
            if not isinstance(issue, dict):
                continue
            group = by_h.get(issue.get("heuristic_id"))
            if group:
                issue["examples"] = group.get("examples", [])
                issue["verified"] = bool(group.get("verified"))
            else:
                issue["examples"] = []
                issue["verified"] = False


def _narrative_from_stats(stats: dict) -> dict:
    """Build a minimal narrative dict purely from statistics (no LLM needed)."""
    n = stats.get("participant_count", 0)
    fi = stats.get("first_impression") or {}
    fi_avg = fi.get("avg_score")
    dim = fi.get("dimension_avgs") or {}
    heur = stats.get("heuristic") or {}
    sc = heur.get("severity_counts") or {}
    by_h = heur.get("by_heuristic") or []
    a11y_avg = (stats.get("accessibility") or {}).get("avg_score")
    vp = stats.get("viewport") or {}
    survey = stats.get("survey") or {}
    q3_avg = (survey.get("avg_scores") or {}).get("q3")

    serious = [h for h in by_h if h.get("worst_severity") == "poważny"]
    minor = [h for h in by_h if h.get("worst_severity") == "drobny"]

    exec_parts = [f"Badanie z udziałem {n} uczestników ujawniło {sc.get('poważny', 0)} poważnych i {sc.get('drobny', 0)} drobnych naruszeń heurystyk Nielsena."]
    if q3_avg is not None:
        exec_parts.append(f"Kluczową barierą był brak ścieżki zakupu online — uczestnicy ocenili prostotę dodania do koszyka na {q3_avg:.1f}/5.")
    if fi_avg is not None:
        exec_parts.append(f"Pierwsze wrażenie oceniono na {fi_avg:.2f}/5.")

    strengths = []
    if dim.get("trustworthiness", 0) >= 3.5:
        strengths.append(f"Wiarygodność serwisu oceniona na {dim['trustworthiness']:.1f}/5 — strona buduje zaufanie.")
    if dim.get("purpose_clarity", 0) >= 3.0:
        strengths.append(f"Cel serwisu jest rozpoznawalny przy pierwszym kontakcie (jasność celu: {dim['purpose_clarity']:.1f}/5).")

    def _to_issue(h: dict) -> dict:
        return {
            "heuristic_id": h.get("heuristic_id"),
            "heuristic_name": h.get("heuristic_name", ""),
            "frequency": h.get("sessions_affected", ""),
            "impact": h.get("top_evidence", "")[:200],
            "recommendation": h.get("top_recommendation", ""),
        }

    recs = []
    for i, h in enumerate(serious[:5], 1):
        recs.append({
            "priority": i,
            "area": h.get("heuristic_name", ""),
            "action": h.get("top_recommendation", ""),
            "expected_impact": "",
        })

    return {
        "executive_summary": " ".join(exec_parts),
        "overall_ux_score": round(fi_avg, 1) if fi_avg else None,
        "score_rationale": f"Ocena odzwierciedla wyniki testu pierwszego wrażenia ({fi_avg:.2f}/5) oraz dominację problemów poważnych ({sc.get('poważny', 0)}) i drobnych ({sc.get('drobny', 0)})." if fi_avg else "",
        "key_strengths": strengths or ["Brak wyraźnych mocnych stron możliwych do wyodrębnienia z danych."],
        "critical_issues": [],
        "serious_issues": [_to_issue(h) for h in serious],
        "minor_issues": [_to_issue(h) for h in minor],
        "accessibility_assessment": f"Średnia ocena dostępności: {a11y_avg}/5." if a11y_avg else "",
        "responsiveness_assessment": f"Sesje z overflowem tabletowym: {vp.get('tablet_overflow_sessions', 0)}/{n}. Brak overflowu mobilnego: {vp.get('mobile_overflow_sessions', 0)}/{n}.",
        "first_impression_assessment": f"Pierwsze wrażenie: {fi_avg:.2f}/5. Najsłabszy wymiar: wezwanie do działania ({dim.get('call_to_action', '—')}/5)." if fi_avg else "",
        "priority_recommendations": recs,
        "methodology_note": f"Badanie przeprowadzono przy użyciu agentów LLM symulujących rzeczywistych użytkowników. Każdy agent wykonał przydzielone zadanie. Ocena opiera się na heurystykach Nielsena, teście pierwszego wrażenia (5 sekund), analizie dostępności WCAG 2.1 oraz sprawdzeniu responsywności na urządzeniach mobilnych i tabletach.",
    }


def _render_aggregated_md(agg: dict) -> str:
    lines: list[str] = []
    stats = agg.get("statistics") or {}
    narr = agg.get("narrative") or {}
    # Fall back to stats-derived narrative when LLM call failed or returned empty
    if not narr or "error" in narr or not narr.get("executive_summary"):
        narr = _narrative_from_stats(stats)
        _attach_issue_evidence(narr, stats)
    n = stats.get("participant_count", 0)

    def h(level: int, text: str):
        lines.append(f"{'#' * level} {text}\n")

    def p(text: str):
        lines.append(f"{text}\n")

    def ul(items: list):
        for i in items:
            lines.append(f"- {i if isinstance(i, str) else json.dumps(i, ensure_ascii=False)}")
        lines.append("")

    h(1, f"Raport zbiorczy UX — {agg.get('website', '')}")
    p(f"**Zadanie:** {agg.get('task', '')}  |  **Liczba uczestników:** {n}")

    if narr.get("executive_summary"):
        h(2, "Podsumowanie wykonawcze")
        p(narr["executive_summary"])

    overall = narr.get("overall_ux_score")
    if overall is not None:
        p(f"**Ogólna ocena UX: {overall}/5** — {narr.get('score_rationale', '')}")

    # Key strengths
    strengths = narr.get("key_strengths") or []
    if strengths:
        h(2, "Mocne strony")
        ul(strengths)

    # Critical issues
    for sev_key, sev_label in [("critical_issues", "Problemy krytyczne"), ("serious_issues", "Problemy poważne"), ("minor_issues", "Problemy drobne")]:
        issues = narr.get(sev_key) or []
        if issues:
            h(2, sev_label)
            for issue in issues:
                h(3, f"H{issue.get('heuristic_id', '?')}: {issue.get('heuristic_name', '')}")
                if issue.get("verified") is False:
                    p("⚠ **Niezweryfikowane** — brak dowodu w zapisie sesji.")
                p(f"**Częstotliwość:** {issue.get('frequency', '')}")
                p(f"**Wpływ:** {issue.get('impact', '')}")
                p(f"**Rekomendacja:** {issue.get('recommendation', '')}")
                for ex in issue.get("examples") or []:
                    parts = [f"**Dowód:** {ex.get('url', '')}"]
                    if ex.get("persona_name"):
                        parts.append(f"(uczestnik {ex['persona_name']}, krok {ex.get('step')})")
                    if ex.get("screenshot"):
                        parts.append(f"— zrzut: `{ex['screenshot']}`")
                    p(" ".join(parts))
                    if ex.get("observation_quote"):
                        p(f"> {ex['observation_quote'][:300]}")

    # Assessments
    for key, label in [
        ("first_impression_assessment", "Pierwsze wrażenie"),
        ("accessibility_assessment", "Dostępność"),
        ("responsiveness_assessment", "Responsywność"),
    ]:
        val = narr.get(key)
        if val:
            h(2, label)
            p(val)

    # Priority recommendations
    recs = narr.get("priority_recommendations") or []
    if recs:
        h(2, "Priorytetowe rekomendacje")
        for rec in recs:
            h(3, f"{rec.get('priority', '?')}. {rec.get('area', '')}")
            p(rec.get("action", ""))
            expected = rec.get("expected_impact")
            if expected:
                p(f"*Oczekiwany efekt:* {expected}")

    # Survey scores
    survey_stats = stats.get("survey") or {}
    avg_scores = survey_stats.get("avg_scores") or {}
    open_answers = survey_stats.get("open_answers") or {}
    if avg_scores or open_answers:
        h(2, "Wyniki ankiety")
        _QUESTION_LABELS = {
            "q1": "Łatwość znalezienia produktu",
            "q2": "Przejrzystość i czytelność strony",
            "q3": "Łatwość sprawdzenia dostępności / znalezienia sklepu",
            "q4": "Sprawność działania strony",
        }
        for qid, avg in sorted(avg_scores.items()):
            label = _QUESTION_LABELS.get(qid, qid)
            avg_str = f"{avg:.2f}" if avg is not None else "—"
            p(f"**{label} ({qid}):** {avg_str}/5")
        for qid, answers in sorted(open_answers.items()):
            label = _QUESTION_LABELS.get(qid, qid)
            h(3, label)
            ul([f'„{a}"' for a in answers])

    # Page speed
    ps = agg.get("pagespeed")
    if ps and not ps.get("skipped"):
        h(2, "Szybkość strony (PageSpeed Insights)")
        for strategy in ("mobile", "desktop"):
            data = ps.get(strategy) or {}
            if data.get("error"):
                continue
            label = strategy.capitalize()
            score = data.get("performance_score", "—")
            perf_label = data.get("performance_label", "").upper()
            si = data.get("speed_index") or {}
            fcp = data.get("fcp") or {}
            lcp = data.get("lcp") or {}
            tbt = data.get("tbt") or {}
            cls_ = data.get("cls") or {}
            p(f"**{label}** — Performance score: **{score}/100** [{perf_label}]")
            p(f"| Metryka | Wartość | Status |")
            p(f"|---|---|---|")
            p(f"| Speed Index | {si.get('display','—')} | {si.get('status','').upper()} |")
            p(f"| LCP (Largest Contentful Paint) | {lcp.get('display','—')} | {lcp.get('status','').upper()} |")
            p(f"| FCP (First Contentful Paint) | {fcp.get('display','—')} | {fcp.get('status','').upper()} |")
            p(f"| TBT (Total Blocking Time) | {tbt.get('display','—')} | — |")
            p(f"| CLS (Cumulative Layout Shift) | {cls_.get('display','—')} | — |")

    # Scroll depth
    scroll = agg.get("scroll") or {}
    avg_max = scroll.get("avg_max_depth_pct")
    if avg_max is not None:
        h(2, "Głębokość przewijania")
        mn = scroll.get("min_max_depth_pct", "—")
        mx = scroll.get("max_max_depth_pct", "—")
        p(f"Średnia maks. głębokość przewijania: **{avg_max}%** (zakres {mn}–{mx}%)")
        if avg_max < 30:
            p("⚠ Uczestnicy rzadko przewijali poniżej 30% strony — treści below-the-fold mogą być pomijane.")

    # Broken links
    links = agg.get("links") or {}
    broken_urls = links.get("broken_urls") or []
    total_checked = links.get("total_links_checked", 0)
    if total_checked:
        h(2, "Uszkodzone linki")
        if broken_urls:
            p(f"Znaleziono **{len(broken_urls)}** uszkodzony(ch) link(ów) spośród {total_checked} sprawdzonych:")
            for u in broken_urls[:10]:
                lines.append(f"- {u}")
            lines.append("")
        else:
            p(f"Nie wykryto uszkodzonych linków (sprawdzono {total_checked} linków).")

    # Form funnel
    forms = agg.get("forms") or {}
    sessions_engaged = forms.get("sessions_engaged_form")
    sessions_submitted = forms.get("sessions_submitted_form")
    total_sessions = forms.get("total_sessions")
    if sessions_engaged is not None:
        h(2, "Lejek formularzy")
        p(f"Sesje, które wypełniły pole formularza: **{sessions_engaged}/{total_sessions}**")
        p(f"Sesje, które dotarły do akcji submit: **{sessions_submitted}/{total_sessions}**")
        avg_fields = forms.get("avg_fields_filled")
        if avg_fields is not None:
            p(f"Średnia liczba pól wypełnionych na sesję: **{avg_fields}**")

    # Statistics table
    h(2, "Statystyki zbiorcze")
    heur = stats.get("heuristic", {})
    sc = heur.get("severity_counts", {})
    p(f"Łączna liczba problemów: **{heur.get('total_findings', 0)}** "
      f"({sc.get('krytyczny', 0)} kryt. / {sc.get('poważny', 0)} poważnych / {sc.get('drobny', 0)} drobnych)")

    fi_avg = (stats.get("first_impression") or {}).get("avg_score")
    if fi_avg is not None:
        p(f"Średnia ocena pierwszego wrażenia: **{fi_avg}/5**")

    a11y_avg = (stats.get("accessibility") or {}).get("avg_score")
    if a11y_avg is not None:
        p(f"Średnia ocena dostępności: **{a11y_avg}/5**")

    vp = stats.get("viewport") or {}
    p(f"Sesje z problemami responsywności: mobile {vp.get('mobile_overflow_sessions', 0)}/{n}, "
      f"tablet {vp.get('tablet_overflow_sessions', 0)}/{n}")

    # Methodology note
    mn = narr.get("methodology_note")
    if mn:
        h(2, "Nota metodologiczna")
        p(mn)

    return "\n".join(lines)
