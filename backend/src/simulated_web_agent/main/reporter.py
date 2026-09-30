"""
Report generation: per-agent and aggregated.

Per-agent:
  generate_per_agent_report(store) → trace_dir/ux_report.json

Aggregated (called after ALL agents finish):
  generate_aggregated_report(stores, task, url) → runs/aggregated_report.json (+ .html)
"""

import json
import logging
import math
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
from .severity import (
    SEVERITY_EN,
    excluded_from_report,
    heuristic_id as _heuristic_id,
    severity_rank,
)
from .a11y_checks import a11y_topic, measure_names, merge_pages
from .evidence import page_quote, pages_contain, rendering_artifact
from .textutil import clip_words, view_key
from .scoring import (
    RULES as SCORING_RULES, alt_text_measured_text, alt_text_share, breakdown_text,
    compute_ux_score, rationale_text,
)
from .attribution import harness_suspect_causes, restates_a_gated_finding
from .iso_metrics import current_effectiveness, effective_outcome
from .redaction import clean as _redact, describes_interaction_trouble, mentions_harness_fault
from .trace_store import TraceStore

log = logging.getLogger(__name__)

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


def _persona_label(report: dict) -> str:
    """Display name for a per-agent report ("Marta"), falling back to its dir.

    Aggregated detail is only useful if the reader can tell whose experience it
    was — "navigation confused one of three people" means something different
    from "navigation confused everyone".
    """
    if not isinstance(report, dict):
        return ""
    m = re.search(r"^Persona:\s*(\S+)", report.get("persona", "") or "",
                  re.MULTILINE | re.IGNORECASE)
    if m:
        return m.group(1).rstrip(".,;:")
    trace_dir = str(report.get("trace_dir", ""))
    return trace_dir.replace("\\", "/").rstrip("/").split("/")[-1] if trace_dir else ""


def _harness_reason(cause: dict | None) -> str:
    """What stopped a finding counting, by the signal that marked its step.

    One sentence used to cover every case — "prawdopodobna awaria narzędzia,
    nie strony" — including a remark about page copy made on a step that merely
    carried a blocked click. It claims the tool failed and says nothing of what
    the trace shows. The signal is named instead, and the sentence says what the
    trace cannot settle.
    """
    cause = cause if isinstance(cause, dict) else {}
    code, detail, step = cause.get("code"), cause.get("detail") or "", cause.get("step")
    at = f"w kroku {step}" if step is not None else "w tym kroku"
    if code == "executor_error":
        return (f"narzędzie zgłosiło błąd wykonania akcji {at} lub tuż przed nim"
                f"{f' ({detail})' if detail else ''} — z zapisu nie da się odróżnić "
                "skutku tego błędu od cechy strony")
    if code == "consent":
        return (f"{at} na ekranie był baner zgody, którego narzędzie nie zamknęło — "
                "mógł zasłaniać stronę")
    if code == "bot_wall":
        return f"{at} strona była ekranem weryfikacji antybotowej, a nie serwisem"
    if code == "perception_gap":
        return (f"{at} opis strony był niemal pusty mimo dużej strony — narzędzie nie "
                "odczytało jej treści")
    if code == "fallback":
        return (f"{at} uczestnik wycofał się po nieudanych próbach kliknięcia — "
                "nie wiadomo, czy element nie działał, czy narzędzie go nie trafiło")
    if code == "repeat":
        return (f"{at} to samo kliknięcie powtórzono wielokrotnie bez skutku"
                f"{f' („{detail}”)' if detail else ''} — nie wiadomo, czy element nie działa, "
                "czy narzędzie go nie trafia")
    if code == "claim_contradicted":
        return "wniosek przeczy przebiegowi sesji albo dotyczy elementu, który się zaciął"
    return "w tym kroku lub tuż przed nim wystąpiła usterka narzędzia"


def _gate_reason(finding: dict) -> str:
    """Why this finding was demoted, in Polish — "" if it stands confirmed.

    severity.excluded_from_report answers *whether*; a reader of the per-persona
    file also needs *why*, otherwise a demoted finding is indistinguishable from
    one nobody got round to checking.
    """
    if not isinstance(finding, dict):
        return ""
    if finding.get("unresponsive_control"):
        return ("element nie zareagował na automatyczne kliknięcia — nie wiadomo, "
                "czy to wada strony, czy ograniczenie narzędzia")
    if finding.get("quoted_not_on_page"):
        return ("cytowany tekst pochodzi z automatycznego opisu strony, a nie ze "
                "strony — żadna strona tej sesji go nie zawiera")
    if finding.get("repeated_with_progress"):
        return ("powtórzone kliknięcie za każdym razem zmieniało stronę, więc to nie "
                "awaria narzędzia, ale zapis nie rozstrzyga, czy opisane zachowanie "
                "jest wadą strony")
    if finding.get("harness_suspect"):
        return _harness_reason(finding.get("harness_cause"))
    if finding.get("off_site"):
        return "dotyczy innej domeny niż badana"
    if finding.get("claim_verdict") == "unsupported":
        return "zapis sesji nie potwierdza tego stwierdzenia"
    if finding.get("requires_human_check"):
        return "wymaga weryfikacji przez człowieka"
    return ""


def _control_label(finding: dict, key: str = "unresponsive_control") -> str:
    """The on-screen name of the control a finding is about, for a human to find.

    The parser id ("executive_my27_od_14") is not what anybody sees, so prefer
    the label the finding quotes ('EXECUTIVE MY27') when it matches the control.
    """
    control = str(finding.get(key) or "")
    if not control:
        return ""
    words = [w for w in control.split("_") if w and not w.isdigit()]
    key = re.sub(r"[^a-z0-9]", "", "".join(words[:2]).lower())
    evidence = str(finding.get("evidence") or "")
    for quoted in re.findall(r"['\"‘’„”]([^'\"‘’„”]{2,60})['\"‘’„”]", evidence):
        if key and key in re.sub(r"[^a-z0-9]", "", quoted.lower()):
            return quoted.strip()
    return " ".join(words[:2]).upper()


def _human_check(finding: dict) -> str:
    """What a person should do to settle a finding the trace could not."""
    named = [n for n in (finding.get("named_in_accessibility") or []) if n]
    if named:
        # The name exists — as an accessible name, which only screen readers
        # say. The deterministic check reports it; what stays open is a claim
        # about text a sighted visitor would see.
        cited = " i ".join(f"„{n}”" for n in named[:2])
        return (f"Nazwa {cited} jest nazwą dostępnościową kontrolki (widzą ją tylko czytniki "
                "ekranu) — opisano ją w sekcji Dostępność jako nazwę nieopisową. Jeśli wniosek "
                "dotyczy tekstu widocznego na stronie, sprawdź go ręcznie: widzący użytkownik "
                "tej nazwy nie zobaczy.")
    label = _control_label(finding)
    if label:
        return (f"Kliknij „{label}” w zwykłej przeglądarce i sprawdź, czy wybór "
                "się zmienia. Jeśli nie — to rzeczywista wada strony.")
    if finding.get("off_site"):
        return "Dotyczy innej domeny niż badana — zwykle do pominięcia."
    # Already the label the page shows (attribution reads it from the page).
    progressed = str(finding.get("repeated_with_progress") or "").strip()
    if progressed:
        return (f"Kliknij „{progressed}” kilka razy w zwykłej przeglądarce i sprawdź, "
                "czy strona zachowuje się tak, jak opisano. Strona reagowała na każde "
                "kliknięcie, więc rozstrzyga opis, a nie działanie przycisku.")
    quotes = [q for q in (finding.get("quoted_not_on_page") or []) if q][:2]
    if quotes:
        cited = " i ".join(f"„{q}”" for q in quotes)
        return (f"Sprawdź, czy strona rzeczywiście pokazuje {cited} — w zapisanych "
                "stronach tego tekstu nie ma. Jeśli nie pokazuje, pomiń ustalenie.")
    if finding.get("harness_suspect"):
        return "Powtórz tę interakcję ręcznie i sprawdź, czy strona reaguje."
    if finding.get("claim_verdict") in ("unsupported", "partial"):
        return ("Sprawdź na stronie, czy opisany problem występuje — zapis sesji "
                "go nie potwierdza.")
    return "Zweryfikuj ręcznie na stronie."


def _severity_sort_key(finding: dict) -> int:
    """Ascending sort key (worst first) over severity.severity_rank."""
    return -severity_rank(finding)


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



async def generate_per_agent_report(store: TraceStore) -> dict:
    """Build and save the per-agent ux_report.json."""
    report = _build_per_agent_json(store)
    store.write_ux_report(report)
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
    # "unknown", not a guess. This used to be "partial", which the score then
    # charged as the model's verdict (0.75 × 0.5 per participant) and the
    # report printed as "Cel osiągnięty częściowo", for a verdict nobody gave.
    if outcome not in _VALID_OUTCOMES:
        outcome = "unknown"

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
            # "report" -> the Pro tier (gpt.py). This pass decides `outcome`
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
            "outcome": "unknown",  # the call failed: no verdict, see _coerce_reflection
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


# Phrases a survey answer's reason uses to say the question could not be
# answered from the session. The agents then entered the scale midpoint as a
# placeholder, and those 3s went into the averages as neutral ratings: "Czułem
# kontrolę przy podawaniu danych do płatności — śr. 3,00" on a car configurator
# where no participant ever reached a payment step. One answered "N/A" to a 1–5
# question, which aggregation then filed as an open-text answer.
#
# Only phrases that say "I could not judge". "neutral rating" and "did not
# reach" were measured and left out: both also introduce real ratings ("a
# neutral rating as a compromise", "I did not reach payment, but the costs were
# clear — 4").
_NOT_ASSESSABLE_MARKERS = (
    "cannot assess", "can't assess", "unable to assess", "impossible to assess",
    "not possible to assess", "no experience to form", "cannot form an opinion",
    "not applicable", "do not contain any information", "does not contain any information",
    "no information about", "nie mogę ocenić", "nie da się ocenić", "nie można ocenić",
    "nie dotyczy", "brak doświadczenia", "brak informacji",
    # Missed by the list above, found by the dashboard parity check: three
    # placeholder 3s still averaged in (2026-09-24 hyundai e6, e7; hebe q2).
    "cannot evaluate", "can't evaluate", "unable to evaluate",
    "cannot judge", "can't judge", "unable to judge", "no experience to judge",
    "too short to assess", "too short to evaluate", "too short to judge",
    "no task execution", "nie mogę tego ocenić", "nie jestem w stanie ocenić",
    # The first live run on the current code: "I had no opportunity to assess
    # feelings of control … regarding payment" averaged as a 1,00/5.
    "no opportunity to assess", "no opportunity to evaluate", "no opportunity to judge",
    "had no opportunity", "nie miałem okazji ocenić", "nie miałam okazji ocenić",
)


def _not_assessable(value, reason) -> bool:
    """Was this survey answer a placeholder rather than a rating?"""
    v = str(value or "").strip().lower()
    if v in {"n/a", "na", "n.d.", "nd", "nie dotyczy", "-", "—"}:
        return True
    r = str(reason or "").lower()
    return any(m in r for m in _NOT_ASSESSABLE_MARKERS)


def _build_survey_questions(survey_scores: dict, survey_open: dict,
                            q_meta: list | None, avg, survey_na: dict | None = None) -> list[dict]:
    """One entry per question in questionnaire order (synthetic metrics last):
    rated -> value distribution + average + anchored scale; open -> the answers.
    This is what lets the report show the real prompt, the response spread (vital
    at n=5), and the correct scale instead of a bare '/5' bar on an id."""
    q_list = [q for q in (q_meta or []) if isinstance(q, dict)]
    q_map = {q.get("id"): q for q in q_list}
    order = [q.get("id") for q in q_list]
    for qid in list(survey_scores) + list(survey_open) + list(survey_na or {}):
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
                # Participants who said they could not answer — shown beside n
                # so the reader knows how thin the average is.
                "not_assessable": int((survey_na or {}).get(qid, 0)),
                "distribution": [{"value": v, "count": counts[v]}
                                 for v in range(lo, hi + 1)],
            })
        elif survey_open.get(qid):
            answers = [str(a).strip() for a in survey_open[qid] if str(a).strip()]
            if answers:
                out.append({"id": qid, "prompt": _survey_prompt(qid, meta),
                            "kind": "open", "answers": answers})
        elif (survey_na or {}).get(qid):
            # Nobody could answer it. Dropping the question would hide that the
            # questionnaire asked about something the sessions never reached.
            out.append({"id": qid, "prompt": _survey_prompt(qid, meta),
                        "kind": "not_assessable", "not_assessable": int(survey_na[qid])})
    return out


def _page_of(url: str) -> str:
    """A page's identity for grouping: scheme, host and path, no query or fragment."""
    return str(url or "").split("#")[0].split("?")[0].rstrip("/")


def _order_a11y_instances(entry: dict) -> None:
    """Proof from the barrier the entry describes; other barriers named apart.

    Merging by topic put different barriers under one entry — an unnamed
    combobox on the test-drive form and two unnamed buttons on the homepage —
    and took the first three examples as they came, so the homepage was cited
    as proof of the combobox and "2 przyciski bez dostępnej nazwy" appeared
    nowhere. The worst instance's page supplies the examples first; instances
    on other pages are listed as further occurrences with their own text.
    """
    instances = entry.pop("_instances", [])
    worst = _page_of(entry.pop("_worst_url", ""))
    ordered = sorted(instances, key=lambda i: (_page_of(i["example"]["url"]) != worst,
                                               -severity_rank(i["severity"])))
    entry["examples"] = [i["example"] for i in ordered[:3]]
    others, seen = [], {worst}
    for i in ordered:
        page = _page_of(i["example"]["url"])
        if page in seen or not i["detail"]:
            continue
        seen.add(page)
        others.append({"detail": clip_words(i["detail"], 300), "url": i["example"]["url"],
                       "persona": i["persona"], "severity": i["severity"]})
    entry["other_instances"] = others[:3]


def aggregate_statistics(reports: list[dict], outcomes: dict[str, str] | None = None,
                         q_meta: list | None = None, stores: list | None = None) -> dict:
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

    # Caller's TraceStores, keyed by persona dir. Needed by several blocks
    # below: report["trace_dir"] is recorded relative to the directory the run
    # executed in, so resolving it from anywhere else silently yields nothing.
    _stores_by_dir = {
        str(s.trace_dir).replace("\\", "/").rstrip("/").split("/")[-1]: s
        for s in (stores or [])
    }

    fi_dimension_scores: dict[str, list[float]] = defaultdict(list)
    # Why each dimension scored what it did. first_impression.txt asks for a
    # per-dimension rationale ("the biggest barrier for this person, and why
    # that score") and for strengths/weaknesses, all grounded in the step-0
    # screenshot — and aggregation kept only the numbers. The first external
    # review's sharpest complaint was that the report shows scores without ever
    # saying what lowered them; the answer was being generated and discarded.
    fi_dimension_reasons: dict[str, list[dict]] = defaultdict(list)
    fi_weaknesses: list[dict] = []
    fi_strengths: list[dict] = []
    # The very screen these scores were formed on. The five-second scores come
    # from the step-0 screenshot (auditor.py), and the section showed only
    # numbers — the reviewer specifically wanted screenshots so a reader knows
    # what is being talked about. Path is run-relative, matching the evidence
    # thumbnails on issues, so it resolves in the HTML and in the PDF alike.
    fi_screens: list[dict] = []
    for r in reports:
        fi = r.get("first_impression") or {}
        who = _persona_label(r)
        p_dir = str(r.get("trace_dir", "")).replace("\\", "/").rstrip("/").split("/")[-1]
        shot = _stores_by_dir.get(p_dir)
        if p_dir and shot is not None and shot.first_impression_screenshot().exists():
            fi_screens.append({
                "persona": who,
                # The frame the ratings were given for, so the thumbnail shows
                # what was actually judged.
                "screenshot": f"{p_dir}/screenshot/{shot.first_impression_screenshot().name}",
                "score": fi.get("overall_score"),
            })
        scores = fi.get("scores") or {}
        for k, v in scores.items():
            if v is not None:
                fi_dimension_scores[k].append(float(v))
        # "Co można zaproponować do zmiany?" — the reviewer's follow-up to the
        # missing why. One change per dimension, answering that dimension's
        # barrier, so the proposal travels with the reason it answers. Older
        # runs have no suggested_changes and render the reason alone.
        changes = fi.get("suggested_changes") or {}
        if not isinstance(changes, dict):
            changes = {}
        for k, why in (fi.get("score_rationale") or {}).items():
            if str(why or "").strip():
                fi_dimension_reasons[k].append(
                    {"persona": who, "score": scores.get(k), "why": str(why).strip(),
                     "change": str(changes.get(k) or "").strip()}
                )
        for w in fi.get("weaknesses") or []:
            if str(w or "").strip():
                fi_weaknesses.append({"persona": who, "text": str(w).strip()})
        for s_ in fi.get("strengths") or []:
            if str(s_ or "").strip():
                fi_strengths.append({"persona": who, "text": str(s_).strip()})

    # What each persona was actually trying to do, and how it went. Produced by
    # generate_objective_reflection for every persona and rendered nowhere — so
    # the report read identically whatever the objective was, which is what the
    # reviewer meant by "it gave me the same report when I asked for something
    # else". blockers/wins are also the only per-persona account of what worked.
    objective_reflections: list[dict] = []
    for r in reports:
        # Read from the store, not from the report dict: the reflection is
        # generated *after* generate_per_agent_report (analysis_runner runs the
        # two in that order), so ux_report.json never contains it. Aggregation
        # runs last, by which point it is on disk.
        # Prefer the caller's TraceStore: report["trace_dir"] is recorded
        # relative to the working directory the run executed in, so resolving
        # it anywhere else silently yields nothing. Fall back to the path only
        # when no stores were passed (older callers, and the tests).
        refl = {}
        trace_dir = str(r.get("trace_dir", ""))
        key = trace_dir.replace("\\", "/").rstrip("/").split("/")[-1]
        store = _stores_by_dir.get(key)
        try:
            if store is not None:
                refl = store.read_objective_reflection() or {}
            elif trace_dir:
                refl = TraceStore(Path(trace_dir)).read_objective_reflection() or {}
        except Exception:
            refl = {}
        if not isinstance(refl, dict) or not refl:
            continue
        # Free text, so it bypasses every gate attribution.py applies to
        # findings — and it is written by the same persona whose frustration
        # may have come from our failed clicks. Redact this tool's internal
        # vocabulary (a survey answer once quoted "last_action_error" verbatim)
        # and flag, rather than delete, a complaint that describes the
        # automation failing: the aggregate pass is told not to build a
        # headline defect on it alone.
        entry = _redact({
            "persona": _persona_label(r),
            # Keyed like `outcomes`, so scoring can set aside a verdict whose
            # reasoning rests on a finding the gate held back.
            "persona_dir": str(r.get("trace_dir", "")).replace("\\", "/").rstrip("/").split("/")[-1],
            "objective": r.get("intent", ""),
            "outcome": refl.get("outcome", ""),
            "summary": refl.get("summary", ""),
            "blockers": [b for b in (refl.get("blockers") or []) if str(b).strip()],
            "wins": [w for w in (refl.get("wins") or []) if str(w).strip()],
            "priority_fix": refl.get("priorityFix", ""),
        })
        refl_texts = (
            [refl.get("summary", ""), refl.get("priorityFix", "")]
            + [str(b) for b in (refl.get("blockers") or [])]
        )
        # Two ways a reflection fails to be evidence about the site: it names
        # our own machinery, or it restates a claim the gate already threw out.
        # The second is how both re-run reports carried a suppressed finding to
        # the client — gating protects the findings list, not the persona's own
        # account of the same session.
        try:
            own_findings = TraceStore(Path(trace_dir)).read_heuristic_findings() or []
        except Exception:
            own_findings = []
        # Third way: the session itself recorded the tool failing (a blocked or
        # timed-out click, a retry loop) and the account describes interactions
        # not working. Each alone is weak — people do report broken buttons, and
        # sessions do have a stray error — together the account may be the error.
        tool_trouble = False
        if store is not None:
            try:
                tool_trouble = any(code in ("executor_error", "fallback", "repeat")
                                   for code, _ in harness_suspect_causes(store).values())
            except Exception:
                tool_trouble = False
        entry["self_reported_only"] = (
            any(mentions_harness_fault(t) for t in refl_texts)
            or restates_a_gated_finding(refl_texts, own_findings)
            or (tool_trouble and any(describes_interaction_trouble(t) for t in refl_texts))
        )
        # What the score counts for this participant, and from where — so the
        # objectives section shows the same outcome (iso_metrics.effective_outcome).
        if store is not None:
            entry["effective_outcome"], entry["outcome_source"] = effective_outcome(store)
        objective_reflections.append(entry)

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

    # Who else's pages showed the same rendering artifact. Reach counted the
    # participants who wrote a finding down, not those who met the defect:
    # "null kW (null KM)" was on the engine step in all three hyundai sessions
    # and was reported as affecting 1 of 3. Deliberately limited to broken
    # rendering — null, undefined, NaN, unrendered templates — which anyone on
    # the page sees; "the button did not work" still counts only who hit it.
    for f in all_findings:
        artifact = rendering_artifact(str(f.get("evidence") or ""))
        if artifact:
            f["_encountered_by"] = {
                d for d, st in _stores_by_dir.items() if pages_contain(st, artifact)
            }

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
            severity_counted.append(max(root_cause_ids[rc], key=severity_rank))
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
        worst = max(members, key=severity_rank)
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
        by_heuristic[_heuristic_id(f)].append(f)

    def _example(f: dict) -> dict:
        screenshot = f.get("screenshot")
        # The page's own words, centred on the claim. Findings recorded before
        # evidence.page_quote existed get it here, from the same page on disk.
        quote_from_page = f.get("page_quote")
        if quote_from_page is None:
            store = _stores_by_dir.get(f.get("_persona_dir", ""))
            quote_from_page = (page_quote(store, f.get("step"), str(f.get("evidence") or ""))
                               if store is not None else "")
        return {
            "persona_dir": f.get("_persona_dir", ""),
            "persona_name": f.get("_persona_name", ""),
            "step": f.get("step"),
            "url": f.get("url", ""),
            # persona-relative → run-relative so report.html can embed it
            "screenshot": f"{f.get('_persona_dir')}/{screenshot}" if screenshot else None,
            "observation_quote": f.get("observation_quote", ""),
            "page_quote": quote_from_page or "",
            "evidence": f.get("evidence", ""),
            "verified": bool(f.get("verified")),
            # This example's own severity. A heuristic's group reports the worst
            # of its findings and everyone who filed any of them, so two
            # unrelated defects under one Nielsen number lent each other their
            # severity and reach; the narrator had no way to tell them apart.
            "severity": f.get("severity", ""),
        }

    # Everything the gate held back, with the reason and what a person should
    # do to settle it. excluded_from_report's docstring always promised that
    # "excluded findings are still rendered, separately, as needing
    # verification" — the aggregate report only ever showed a count, so the
    # heuristic list read "nie stwierdzono naruszeń" over findings that had been
    # raised and set aside. A specialist can use this list; a hidden count she
    # cannot.
    verification_items: list[dict] = []
    excluded_by_heuristic: dict[int, int] = defaultdict(int)
    for f in all_findings:
        if not excluded_from_report(f):
            continue
        h_id = _heuristic_id(f)
        excluded_by_heuristic[h_id] += 1
        verification_items.append({
            **_redact(_example(f)),
            "heuristic_id": h_id,
            "heuristic_name": HEURISTIC_NAMES.get(h_id, ""),
            "severity_claimed": f.get("severity", ""),
            "reason": _gate_reason(f),
            # What the verifier read in the session that contradicts the claim
            # ("model 'i20' appears just before 'Dane kontaktowe', so the field
            # is pre-filled"). Dropped until now, leaving only the generic
            # "the record does not confirm it" — the part a specialist needs
            # to settle the item in a minute.
            "verifier_reason": str(f.get("claim_verdict_reason") or "").strip(),
            "check": _human_check(f),
        })

    heuristic_summary = []
    for h_id in sorted(by_heuristic):
        group = by_heuristic[h_id]
        freq = len(group)
        # Personas, not findings: one persona filing the same heuristic three
        # times is still ONE affected participant. Counting findings produced
        # impossible frequencies ("10 z 5 uczestników") that the narrative then
        # repeated to the client verbatim.
        affected_dirs = {f.get("_persona_dir") for f in group if f.get("_persona_dir")}
        for f in group:
            affected_dirs.update(f.get("_encountered_by") or ())
        personas_affected = len(affected_dirs) or 1
        worst_severity = max(group, key=severity_rank).get("severity", "drobny")
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
    heuristic_summary.sort(key=lambda x: (-x["frequency"], -severity_rank(x["worst_severity"])))

    # Accessibility
    a11y_scores = [
        r["accessibility"].get("overall_score")
        for r in reports
        if isinstance(r.get("accessibility"), dict)
        and r["accessibility"].get("overall_score") is not None
    ]

    # The a11y pass produces findings with a category, a severity, the page and
    # step they were seen on and a recommendation — and aggregation kept only
    # the average score, so the whole section rendered as a single bare number.
    # Deduplicated by category: the same barrier on five pages is one problem to
    # fix, and listing it five times buries everything else.
    a11y_by_category: dict[str, dict] = {}
    # Which device each session ran on, so an instance can say "mobile".
    device_by_dir: dict[str, str] = {}
    for _s in (stores or []):
        try:
            _card = _s.read_persona_card() or {}
        except Exception:
            continue
        _name = Path(str(getattr(_s, "trace_dir", ""))).name
        if _name and _card.get("device"):
            device_by_dir[_name] = str(_card["device"]).strip().lower()
    for r in reports:
        acc = r.get("accessibility") or {}
        if not isinstance(acc, dict):
            continue
        who = _persona_label(r)
        p_dir = str(r.get("trace_dir", "")).replace("\\", "/").rstrip("/").split("/")[-1]
        for f in (acc.get("findings") or []):
            if not isinstance(f, dict):
                continue
            category = str(f.get("category") or "").strip() or "Inne"
            detail = str(f.get("detail") or "").strip()
            # Which measured check this is, so a WCAG issue in the narrative
            # can be joined back to it — narrative issues carry no
            # heuristic_id, so the heuristic join could never find them.
            topic = a11y_topic(f"{category} {detail}")
            # Keyed by that check where there is one, not by the wording. Each
            # persona's pass names its category freely, so one unnamed combobox
            # on the test-drive form came back as "Brak dostępnej nazwy dla
            # kontrolki", "Brakujące etykiety kontrolek" and "Brak nazwy dla
            # kontrolki": three barriers of one persona each, charged three
            # times in the score, while the narrative rightly called it one
            # issue seen by 3 of 3. 3 of the 8 archived runs with accessibility
            # findings had such a split.
            entry = a11y_by_category.setdefault(topic or category, {
                "category": category, "severity": f.get("severity", "drobny"),
                "detail": detail,
                "recommendation": str(f.get("recommendation") or "").strip(),
                "urls": [], "personas": set(),
                "topic": topic,
                "examples": [], "verified": False,
                "instances": [],
                "_instances": [], "_worst_url": str(f.get("url") or "").strip(),
            })
            if severity_rank(f.get("severity")) > severity_rank(entry["severity"]):
                # The worst instance names the merged barrier and describes it.
                entry["severity"] = f.get("severity", "drobny")
                entry["category"], entry["detail"] = category, detail
                entry["recommendation"] = (str(f.get("recommendation") or "").strip()
                                           or entry["recommendation"])
                entry["_worst_url"] = str(f.get("url") or "").strip()
            url = str(f.get("url") or "").strip()
            if url and url not in entry["urls"]:
                entry["urls"].append(url)
            if who:
                entry["personas"].add(who)
            # The per-persona finding carries step, screenshot and a quote, and
            # aggregation used to drop all three. Both WCAG issues in the hyundai
            # run then rendered with no proof while their evidence sat on disk.
            shot = str(f.get("screenshot") or "").strip()
            entry["_instances"].append({
                "severity": f.get("severity", "drobny"), "detail": detail, "persona": who,
                "example": {
                    "persona_dir": p_dir, "persona_name": who,
                    "step": f.get("step"), "url": url,
                    "screenshot": f"{p_dir}/{shot}" if shot and p_dir else None,
                    "observation_quote": str(f.get("observation_quote") or "")[:300],
                    "evidence": detail[:300],
                    "verified": bool(f.get("verified")),
                }})
            entry["verified"] = entry["verified"] or bool(f.get("verified"))
            # Each occurrence with its OWN page, device and wording. The merged
            # entry keeps the worst instance's description beside a flat list of
            # every page, and the aggregate model paired them: a combobox on the
            # test-drive form became "pola wyboru … na stronie głównej", where the
            # home page had two unnamed buttons, on mobile only.
            if len(entry["instances"]) < 12:
                entry["instances"].append({
                    "page": _page_of(url), "device": device_by_dir.get(p_dir, ""),
                    "persona": who, "severity": f.get("severity", "drobny"),
                    "detail": detail[:300],
                })
    for e in a11y_by_category.values():
        _order_a11y_instances(e)
    a11y_findings = sorted(
        ({**e, "personas": sorted(e["personas"]), "urls": e["urls"][:5]}
         for e in a11y_by_category.values()),
        key=lambda e: (-severity_rank(e["severity"]), e["category"]),
    )

    # The denominators the candidate checks never recorded: images and controls
    # seen, and how many lacked a name. Summed across personas; the share is
    # what matters, and it is what _attach_issue_evidence checks a narrative
    # claim against.
    # Each distinct page once, across all participants (a11y_checks.merge_pages).
    _measured = []
    for _st in (stores or []):
        try:
            _measured.append(measure_names(_st))
        except Exception:
            continue
    a11y_measured = merge_pages(_measured)

    # Viewport — count sessions (not pages) with at least one overflow
    mobile_overflow = 0
    tablet_overflow = 0
    # …and the per-device detail the scan already measures and nothing read:
    # tap targets under the WCAG 2.5.8 minimum, text below AA contrast, fonts
    # under 12px, elements wider than the viewport. The scan visits every width
    # regardless of the run's device choice, so it stays comparable between
    # runs; which width the *walkthrough* used is session_devices below.
    device_detail: dict[str, dict] = {}
    for r in reports:
        vp = r.get("viewport_findings") or []
        if any(v.get("viewport") == "mobile" and v.get("horizontal_overflow") for v in vp):
            mobile_overflow += 1
        if any(v.get("viewport") == "tablet" and v.get("horizontal_overflow") for v in vp):
            tablet_overflow += 1
        for v in vp:
            if not isinstance(v, dict) or v.get("error"):
                continue
            label = str(v.get("viewport") or "").strip()
            if not label:
                continue
            d = device_detail.setdefault(label, {
                "viewport": label, "pages_scanned": 0, "pages_with_overflow": 0,
                "_views": set(), "_overflow_views": set(),
                "tap_targets_below_min": 0, "contrast_failures": 0,
                "small_font_examples": [], "wide_element_examples": [],
                "_tap_keys": set(), "_contrast_keys": set(),
                "_tap_page_max": 0, "_contrast_page_max": 0,
            })
            # Distinct pages, not one per session: every participant's scan
            # visits the same handful of pages, so three sessions of five pages
            # printed "30 zeskanowanych podstron" and "overflow na 16 z 30".
            view = view_key(v.get("url")) or f"?{len(d['_views'])}"
            d["_views"].add(view)
            d["pages_scanned"] = len(d["_views"])
            if v.get("horizontal_overflow"):
                d["_overflow_views"].add(view)
                d["pages_with_overflow"] = len(d["_overflow_views"])
            # Distinct failures, not a per-page sum. The same header and footer
            # sit on every page, so summing counted one footer colour six times
            # a page across every page and persona: Hyundai's single contrast
            # issue reached the client as "73 przypadki". Scans that record a
            # key per failure are counted by key; older scans by the worst page,
            # which cannot overstate.
            tap = v.get("tap_targets") or {}
            con = v.get("contrast") or {}
            d["_tap_keys"].update(tap.get("keys") or [])
            d["_contrast_keys"].update(con.get("keys") or [])
            d["_tap_page_max"] = max(d["_tap_page_max"], int(tap.get("below_24px") or 0))
            d["_contrast_page_max"] = max(d["_contrast_page_max"],
                                          int(con.get("below_threshold") or 0))
            d["tap_targets_below_min"] = len(d["_tap_keys"]) or d["_tap_page_max"]
            d["contrast_failures"] = len(d["_contrast_keys"]) or d["_contrast_page_max"]
            for sf in (v.get("small_fonts") or [])[:2]:
                if len(d["small_font_examples"]) < 5 and isinstance(sf, dict):
                    d["small_font_examples"].append(sf)
            for we in (v.get("wide_elements") or [])[:2]:
                if len(d["wide_element_examples"]) < 5:
                    d["wide_element_examples"].append(we)

    # Which viewport the journeys themselves ran at. The heuristic findings
    # come from these sessions, not from the scan above, and the report used to
    # state flatly that the walkthrough was desktop — true until the run could
    # choose. Reading it back per persona keeps the caveat honest for mixed
    # runs, and leaves it empty rather than guessing for runs recorded before
    # the field existed.
    session_devices: dict[str, int] = {}
    for _s in (stores or []):
        try:
            card = _s.read_persona_card() or {}
        except Exception:
            continue
        dev = str(card.get("device") or "").strip().lower()
        if dev:
            session_devices[dev] = session_devices.get(dev, 0) + 1

    # Survey — average numeric answers; collect open-text answers.
    # Sessions that ended after only a few steps carry no real experience:
    # their survey answers are excluded from the averages (first-impression
    # scores stay in — those are formed on the first page and remain valid).
    survey_scores: dict[str, list[float]] = defaultdict(list)
    survey_open: dict[str, list[str]] = defaultdict(list)
    # Answers the participant said it could not give. Kept out of the
    # averages and counted instead — see _not_assessable.
    survey_na: dict[str, int] = defaultdict(int)
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
            if _not_assessable(val, a.get("reason") or a.get("reasoning")):
                survey_na[qid] += 1
                continue
            try:
                survey_scores[qid].append(float(val))
            except (ValueError, TypeError):
                # Same reasoning as the reflections above: free text straight
                # from the persona, never gated, and read by the aggregate pass.
                survey_open[qid].append(_redact(str(val)))

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
            # Why the scores are what they are — the answer to "what exactly
            # lowered this?", generated per persona and until now discarded.
            "dimension_reasons": dict(fi_dimension_reasons),
            "weaknesses": fi_weaknesses,
            "strengths": fi_strengths,
            "screenshots": fi_screens,
        },
        # What each persona set out to do and how it went. The report's spine:
        # without it every audit reads the same regardless of the objective.
        "objective_reflections": objective_reflections,
        "heuristic": {
            "total_findings": len(confirmed_findings),
            "severity_counts": {
                "krytyczny": severity_totals.get("krytyczny", 0),
                "poważny": severity_totals.get("poważny", 0),
                "drobny": severity_totals.get("drobny", 0),
                "needs_verification": needs_verification,
            },
            # The findings behind that count, each with its reason and the check
            # that would settle it — rendered as "Do weryfikacji przez człowieka".
            "needs_verification_items": verification_items,
            "excluded_by_heuristic": dict(excluded_by_heuristic),
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
            # The actual barriers, deduplicated by category — the section used
            # to be a bare number with nothing a developer could act on.
            "findings": a11y_findings,
            "measured": a11y_measured,
        },
        "viewport": {
            "mobile_overflow_sessions": mobile_overflow,
            "tablet_overflow_sessions": tablet_overflow,
            # Per-device measurements the scan already takes: tap targets below
            # the WCAG minimum, contrast failures, tiny text, overflowing
            # elements. Nothing read these before.
            # Working sets stripped: they are not JSON and not for the reader.
            "devices": [{k: v for k, v in device_detail[key].items() if not k.startswith("_")}
                        for key in sorted(device_detail)],
            # What the walkthrough itself ran on, as {device: session count}.
            "session_devices": session_devices,
        },
        "survey": {
            "avg_scores": {k: _avg(v) for k, v in survey_scores.items()},
            "open_answers": {k: v for k, v in survey_open.items()},
            # Enriched, questionnaire-ordered view: real prompts, per-value
            # distribution, correct scale + anchors. reporter_html renders this.
            "questions": _build_survey_questions(survey_scores, survey_open, q_meta, _avg,
                                                 survey_na),
            "not_assessable": dict(survey_na),
            "surveys_included": n - len(excluded_surveys),
            "excluded_surveys": excluded_surveys,
        },
    }


def _top_issues_text(stats: dict, threshold: float = 0.5) -> str:
    n = stats.get("participant_count", 1)
    # ceil, not round: "≥50%" must mean at least half. Python's round() is
    # banker's rounding, so round(5 * 0.5) is 2 — the cutoff became 2 of 5
    # (40%) at the most common panel size, under a literal that promises 50%,
    # both in the report text and in the prompt slot this fills.
    cutoff = max(1, math.ceil(n * threshold))
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
        # What the figure rests on. Older summaries lack these and read as 0.
        "sessions": len(summaries),
        "steps_measured": sum(int(s.get("steps_measured") or 0) for s in summaries),
        "steps_unmeasurable": sum(int(s.get("steps_unmeasurable") or 0) for s in summaries),
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
    sessions_with_broken = 0
    checked_urls: set[str] = set()
    per_page_count: dict[str, int] = {}
    unchecked: set[str] = set()

    for store in stores:
        findings = store.read_link_findings()
        session_broken = set()
        for page_result in findings:
            # Distinct links, not a sum over pages and personas: the same
            # header and footer sit on every page and every persona revisits the
            # same pages. The summed figure reached the report as a strength
            # ("brak niedziałających linków na 313 sprawdzonych"). Checks that
            # recorded their URLs are counted by URL; older ones once per page.
            urls = page_result.get("checked_urls")
            if urls:
                checked_urls.update(urls)
            else:
                page = str(page_result.get("url") or "")
                per_page_count[page] = max(per_page_count.get(page, 0),
                                           int(page_result.get("links_checked") or 0))
            unchecked.update(u.get("url", "") for u in page_result.get("unchecked") or [])
            for b in page_result.get("broken") or []:
                url = b.get("url", "")
                session_broken.add(url)
                if url not in all_broken:
                    all_broken.append(url)
        if session_broken:
            sessions_with_broken += 1

    total_checked = len(checked_urls) + sum(per_page_count.values())
    data = {
        "total_links_checked": total_checked,
        "unique_broken_urls": len(all_broken),
        "sessions_with_broken_links": sessions_with_broken,
        "broken_urls": all_broken[:20],
        # Links whose server did not answer the check (timeout, refused
        # connection) — neither working nor broken.
        "unchecked_links": len(unchecked - checked_urls),
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


_AUDIT_PASS_PL = {
    "heuristic_evaluation": "ocena heurystyczna",
    "first_impression": "test pierwszego wrażenia",
    "accessibility": "analiza dostępności",
}


def _audit_failures(stores: list) -> list[dict]:
    """Participants for whom an audit pass failed, and which.

    A failed heuristic pass wrote [], indistinguishable from "no problems", so a
    persona's findings could vanish from the report with nothing to say so.
    "no_evidence" is not a failure — there was nothing to judge — and is left
    out. Sessions audited before audit_status.json existed still show their
    first-impression and accessibility errors, which were always recorded.
    """
    out = []
    for s in stores:
        status = s.read_audit_status() or {}
        if not status:
            fi = s.read_first_impression() or {}
            acc = s.read_accessibility_findings() or {}
            status = {"first_impression": fi.get("error") if isinstance(fi, dict) else None,
                      "accessibility": acc.get("error") if isinstance(acc, dict) else None}
        failed = [_AUDIT_PASS_PL[k] for k in _AUDIT_PASS_PL
                  if status.get(k) and status.get(k) != "no_evidence"]
        if failed:
            out.append({"persona": _persona_label(s.read_ux_report() or {}) or s.trace_dir.name,
                        "persona_dir": s.trace_dir.name, "passes": failed})
    return out


def _group_counts(stats: dict) -> Counter:
    """Confirmed issue groups by worst severity (the prioritization rows)."""
    return Counter(r.get("severity") for r in (stats.get("prioritization") or [])
                   if isinstance(r, dict))


def _a11y_for_prompt(acc: dict) -> dict:
    """The accessibility statistics as the aggregate model should read them.

    The stored entry merges one barrier topic across pages and participants:
    one description (the worst instance's), every page in one list, every
    participant in another. Read together they invite pairing one instance's
    wording with another's page. The model gets each finding's `instances`
    instead — page, device, participant and what was found THERE — and none of
    the flat lists that could be re-paired.
    """
    out = {k: v for k, v in (acc or {}).items() if k != "findings"}
    out["findings"] = [
        {k: f.get(k) for k in ("category", "severity", "topic", "recommendation", "verified")}
        | {"instances": f.get("instances") or []}
        for f in (acc or {}).get("findings") or [] if isinstance(f, dict)
    ]
    return out


def _survey_legend(survey_stats: dict | None) -> str:
    """"id = wording (scale)" for every question of this study's questionnaire.

    The prompt carried a hand-written legend (q1=ease of finding products …)
    from an earlier questionnaire. This one's ids are g1…g9, e1, e2 and the SEQ
    runs 1-7, so every id in the data was either unexplained or explained wrongly.
    """
    lines = []
    for q in (survey_stats or {}).get("questions") or []:
        if not isinstance(q, dict) or not q.get("id"):
            continue
        scale = (f"skala {q.get('min', 1)}–{q.get('max', 5)}" if q.get("kind") == "scale"
                 else "pytanie otwarte" if q.get("kind") == "open" else "")
        lines.append(f"- {q['id']} = {q.get('prompt') or ''}" + (f" ({scale})" if scale else ""))
    return "\n".join(lines) or "(brak pytań ankiety)"


def _format_outcome_distribution(outcomes: dict, reached_rungs: list, n: int,
                                 sources: dict | None = None) -> str:
    """Task-completion summary for the aggregate narrator, labelled by source.

    It used to open with "zmierzone z zapisu sesji … nie samoocena agenta"
    unconditionally, whatever the outcomes actually were. When the goal ladder
    cannot score a task — a configurator, say — every outcome is the persona's
    own verdict, and the narrator was told that verdict was trace evidence. The
    aggregate prompt calls measured completion its strongest evidence, so this
    sentence decided the score. It now says what it is.
    """
    if not n:
        return "Brak danych o ukończeniu zadania."
    src = Counter((sources or {}).values())
    observed_n, declared_n = src.get("observed", 0), src.get("declared", 0)
    c = Counter((v or "").lower() for v in outcomes.values())
    if sources and observed_n == 0:
        parts = [f"sukces: {c.get('success', 0)}/{n}", f"częściowo: {c.get('partial', 0)}/{n}",
                 f"porażka: {c.get('failed', 0)}/{n}"]
        if c.get("persona_abandoned"):
            parts.append(f"porzucono: {c['persona_abandoned']}/{n}")
        if c.get("unknown"):
            parts.append(f"wynik nieustalony: {c['unknown']}/{n}")
        return ("UKOŃCZENIE ZADANIA — DEKLAROWANE, nie zmierzone. Zapis sesji nie "
                "pozwala automatycznie ocenić tego typu zadania, więc poniższe liczby "
                "to werdykt modelu o przebiegu każdej sesji (ocena deklarowana), a nie "
                "pomiar ani ocena samych uczestników. Traktuj je jako słabszy dowód niż "
                "zweryfikowane problemy i zaznacz w podsumowaniu, że są to "
                "deklaracje, a nie pomiar:\n- " + "; ".join(parts) + ".")
    succ, part, fail = c.get("success", 0), c.get("partial", 0), c.get("failed", 0)
    ab = c.get("persona_abandoned", 0)
    parts = [f"sukces: {succ}/{n}", f"częściowo: {part}/{n}", f"porażka: {fail}/{n}"]
    if ab:
        parts.append(f"porzucono: {ab}/{n}")
    if c.get("unknown"):
        parts.append(f"wynik nieustalony: {c['unknown']}/{n}")
    line2 = ""
    if reached_rungs:
        m = len(reached_rungs)
        added = sum(1 for r in reached_rungs if r in _ADDED_RUNGS)
        checkout = sum(1 for r in reached_rungs if r == "order_summary_reached")
        line2 = (f"\n- {added}/{m} uczestników dodało produkt do koszyka; "
                 f"{checkout}/{m} dotarło do etapu kasy / podsumowania zamówienia.")
    mixed = ""
    if sources and declared_n:
        mixed = (f"\n- Uwaga: {observed_n}/{n} wyników zmierzono z zapisu sesji, "
                 f"{declared_n}/{n} to ocena deklarowana (werdykt modelu, niezmierzony).")
    return ("UKOŃCZENIE ZADANIA — zmierzone z zapisu sesji (dowód z przebiegu, "
            "nie samoocena agenta):\n- " + "; ".join(parts) + "." + line2 + mixed)


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
    # Sessions that never saw the site are not participants (see
    # attribution.why_site_unseen). Left out whenever another session did see
    # it — and named in the report, so the smaller count is explained. When
    # none did, run.py fails the run before getting here.
    from .attribution import why_site_unseen

    excluded_sessions: list[dict] = []
    seen = []
    for s in stores:
        why = why_site_unseen(s)
        if why:
            excluded_sessions.append({
                "persona": _persona_label(s.read_ux_report() or {}) or s.trace_dir.name,
                "persona_dir": s.trace_dir.name, "reason": why})
        else:
            seen.append(s)
    if seen and excluded_sessions:
        log.info("[reporter] %d session(s) never saw the site; left out of the report",
                 len(excluded_sessions))
        stores = seen
    else:
        excluded_sessions = []

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
    # Where each outcome came from. They are not equally strong — one is the
    # trace, the other is the persona grading its own run — and the aggregate
    # prompt treats measured completion as its strongest evidence.
    outcome_sources: dict[str, str] = {}
    reached_rungs: list[str] = []
    for s in stores:
        # Current code, as effective_outcome computes it — not the stored file.
        eff = current_effectiveness(s)
        outcome, source = effective_outcome(s)
        outcomes[s.trace_dir.name] = outcome
        outcome_sources[s.trace_dir.name] = source
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

    stats = aggregate_statistics(reports, outcomes=outcomes, q_meta=q_meta, stores=stores)
    stats["excluded_sessions"] = excluded_sessions
    stats["audit_failures"] = _audit_failures(stores)
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
    a11y_summary = json.dumps(_a11y_for_prompt(stats["accessibility"]), ensure_ascii=False, indent=2)
    vp_summary = json.dumps(stats["viewport"], ensure_ascii=False, indent=2)
    sc = stats["heuristic"]["severity_counts"]
    top_issues = _top_issues_text(stats)
    survey_summary = json.dumps(stats.get("survey", {}), ensure_ascii=False, indent=2)

    # Measured task-completion — anchors the score to observed reality (§4.2),
    # not only synthetic self-report. Also recorded in stats for the payload.
    outcome_distribution = _format_outcome_distribution(
        outcomes, reached_rungs, n, outcome_sources)
    stats["outcomes"] = {
        "counts": dict(Counter(outcomes.values())),
        "participant_count": n,
        # How many of those counts the trace can vouch for, so no renderer can
        # present self-assessed completion as measured.
        "sources": dict(Counter(outcome_sources.values())),
    }
    root_cause_summary = _format_root_causes(stats["heuristic"].get("root_causes") or [])

    # The score is computed here, from evidence, before the narrative exists —
    # the model is handed the number and its itemised breakdown to explain, and
    # its own figure is overwritten afterwards. See scoring.py for why a score
    # the model chooses cannot be honestly itemised.
    score_result = compute_ux_score(stats, outcomes=outcomes,
                                    outcome_sources=outcome_sources,
                                    pagespeed=pagespeed_data)
    stats["score_breakdown"] = score_result

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
        # One per confirmed heuristic group or root cause, at its worst
        # severity — the unit the cards and the score count, and what the
        # prompt says these numbers are. severity_counts counts findings: H1
        # filed by two personas was "Serious: 2" above one serious row.
        critical_count=_group_counts(stats).get("krytyczny", 0),
        serious_count=_group_counts(stats).get("poważny", 0),
        minor_count=_group_counts(stats).get("drobny", 0),
        top_issues=top_issues,
        survey_summary=survey_summary,
        survey_legend=_survey_legend(stats.get("survey")),
        computed_score=f"{score_result['score']:.1f}",
        score_breakdown=breakdown_text(score_result),
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

    # The computed score is the score. The model was told it and asked to
    # explain it; if it returned something else anyway, the headline must still
    # match the breakdown printed beneath it.
    if isinstance(narrative, dict) and "error" not in narrative:
        narrative["overall_ux_score"] = score_result["score"]
        # The explanation too: the files take it from the breakdown at render
        # time, but the dashboard shows the stored narrative, and the model's
        # own prose ("3/3 partial" over a count of 2 of 3) disagreed with it.
        narrative["score_rationale"] = rationale_text(score_result)

    _attach_issue_evidence(narrative, stats, measurements={
        "links": link_data, "pagespeed": pagespeed_data})

    try:
        survey_summaries = await _summarize_survey(reports)
    except Exception as e:
        log.exception("[reporter] survey summaries failed (non-fatal): %s", e)
        survey_summaries = {}

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


    from .reporter_html import save_html_report
    save_html_report(aggregated, runs_dir)

    log.info("[reporter] Aggregated report saved to %s", runs_dir)
    return aggregated


async def _summarize_survey(reports: list[dict]) -> dict[str, str]:
    """One Polish 'why agents answered this way' summary per survey question.

    Keyed by question id. It used to return a positional list ordered by
    first-encountered qid, which the dashboard then joined to its own question
    list by array index — but that list is questionnaire-ordered and omits
    questions nobody answered, so any gap shifted every later summary onto the
    wrong question. The LLM already replies keyed by id; keep it that way.
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
        "Describe only why they answered as they did. Do NOT state as fact that buttons, "
        "links or pages malfunctioned (did not respond, looped, blocked clicks, script "
        "errors): participants sometimes describe the automation failing, so report such "
        "remarks as what some participants said, or leave them out. Do NOT say whether the task was "
        "completed, or whether anything blocked or prevented it: the answers do not "
        "show that, and the outcome is measured elsewhere (a summary that said no one "
        "met a barrier sat beside a participant who only partly finished). "
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
    return {qid: str(summaries.get(qid, "")) for qid in order}


def _attach_issue_evidence(narrative: dict, stats: dict, measurements: dict | None = None) -> None:
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
    a11y = stats.get("accessibility") or {}
    # Worst-first, so the first entry per topic is the one to cite.
    by_topic: dict = {}
    for entry in a11y.get("findings") or []:
        topic = entry.get("topic") if isinstance(entry, dict) else None
        if topic and topic not in by_topic:
            by_topic[topic] = entry
    images = (a11y.get("measured") or {}).get("images") or {}
    perf_line = next((d for d in (stats.get("score_breakdown") or {}).get("deductions") or []
                      if isinstance(d, dict) and d.get("kind") == "performance"), None)

    demoted: list = []
    unconfirmed: list = []
    for key in ("critical_issues", "serious_issues", "minor_issues"):
        kept = []
        for issue in narrative.get(key) or []:
            if not isinstance(issue, dict):
                kept.append(issue)
                continue
            # Normalised: the model writes the id as 1, "1" or null.
            h_num = _heuristic_id(issue)
            group = by_h.get(h_num) if h_num else by_h.get(issue.get("heuristic_id"))
            if group:
                issue["examples"] = group.get("examples", [])
                issue["verified"] = bool(group.get("verified"))
            elif _is_wcag_issue(issue):
                # WCAG issues carry heuristic_id null, so the join above could
                # never find them and both of the hyundai run's WCAG issues
                # rendered as unproven while their evidence sat on disk.
                text = " ".join(str(issue.get(k) or "") for k in
                                ("impact", "recommendation", "frequency", "heuristic_name"))
                topic = a11y_topic(text)
                source = by_topic.get(topic)
                issue["examples"] = (source or {}).get("examples", [])
                issue["verified"] = bool((source or {}).get("verified"))
                if topic == "alt_text" and images.get("total"):
                    issue["measured"] = {
                        "images_total": images["total"],
                        "images_unnamed": images["unnamed"],
                        "share_unnamed": images.get("share_unnamed"),
                        "share_unnamed_content": images.get("share_unnamed_content"),
                        "text": alt_text_measured_text(images),
                    }
            elif h_num:
                # A usability issue under a heuristic with no confirmed finding.
                # The prompt tells the model such issues are discarded, and the
                # methodology note tells the client unconfirmed conclusions were
                # excluded — yet they were kept, unverified, and counted: in the
                # 2026-09-17 hyundai run the only critical and the only serious
                # issue were of this kind (header 1/1/4 over confirmed 0/0/2).
                # Their underlying findings are already in the verification list;
                # the issue itself is recorded and named there, not counted.
                issue["examples"] = []
                issue["verified"] = False
                unconfirmed.append({**issue, "held_from": key})
                continue
            elif _measured_basis(issue, stats, perf_line, measurements):
                # An issue proven by a measurement, not by the trace: PageSpeed,
                # the viewport scan, the link check. It was badged
                # NIEZWERYFIKOWANE beside the score line deducting for the same
                # number.
                issue["examples"] = []
                issue["verified"] = True
                issue["measured"] = {"basis": _measured_basis(issue, stats, perf_line, measurements)}
            else:
                # No confirmed finding and no measurement behind it — the same
                # as a Nielsen issue with none, so held out the same way. The
                # first live run on the current code counted "the Facebook link
                # is broken" (a 400 to our request) as a minor issue this way.
                issue["examples"] = []
                issue["verified"] = False
                unconfirmed.append({**issue, "held_from": key})
                continue

            reason = _wcag_demotion_reason(issue, key)
            if reason:
                issue["demoted_from"] = key
                issue["demotion_reason"] = reason
                # Explicit, so no later setdefault can restore the old label.
                issue["severity"] = "drobny"
                demoted.append(issue)
            else:
                kept.append(issue)
        narrative[key] = kept
    if demoted:
        narrative["minor_issues"] = (narrative.get("minor_issues") or []) + demoted
    # Added to what an earlier pass set aside: aggregation runs this once and
    # stores the result, and every render runs it again on what is left — which
    # replaced the stored list with an empty one, so the note vanished.
    prior = [i for i in (narrative.get("unconfirmed_issues") or []) if isinstance(i, dict)]
    seen = {(i.get("heuristic_id"), i.get("heuristic_name"), i.get("impact")) for i in prior}
    narrative["unconfirmed_issues"] = prior + [
        i for i in unconfirmed if (i.get("heuristic_id"), i.get("heuristic_name"), i.get("impact")) not in seen]


def _issue_text(issue: dict) -> str:
    return " ".join(str(issue.get(k) or "") for k in ("heuristic_name", "impact")).lower()


def _measured_basis(issue: dict, stats: dict, perf_line: dict | None,
                    measurements: dict | None) -> str:
    """The measurement that proves an issue with no heuristic finding, or ""."""
    if _heuristic_id(issue):
        return ""
    text = _issue_text(issue)
    m = measurements or {}
    if _is_performance_issue(issue):
        if perf_line:
            return perf_line.get("basis", "")
        mobile = ((m.get("pagespeed") or {}).get("mobile") or {})
        score = mobile.get("performance_score")
        if isinstance(score, (int, float)) and score < 50:
            return f"PageSpeed mobile {score:g}/100"
        return ""
    if "link" in text or "odnośnik" in text:
        broken = (m.get("links") or {}).get("broken_urls") or []
        return (f"test linków: {len(broken)} uszkodzony(ch) — " + ", ".join(broken[:3])) if broken else ""
    if any(w in text for w in ("responsyw", "mobiln", "dotyk", "przewijani", "kontrast")):
        devices = [d for d in ((stats.get("viewport") or {}).get("devices") or []) if isinstance(d, dict)]
        hits = [f"{d.get('viewport')}: {d.get('tap_targets_below_min', 0)} małych celów dotykowych, "
                f"{d.get('contrast_failures', 0)} błędów kontrastu, "
                f"{d.get('pages_with_overflow', 0)} stron z przewijaniem poziomym"
                for d in devices if (d.get("tap_targets_below_min") or d.get("contrast_failures")
                                     or d.get("pages_with_overflow"))]
        return "skan urządzeń — " + "; ".join(hits) if hits else ""
    return ""


def _is_performance_issue(issue: dict) -> bool:
    if _heuristic_id(issue):
        return False
    text = " ".join(str(issue.get(k) or "") for k in ("heuristic_name", "impact")).lower()
    return "pagespeed" in text or "wydajno" in text


# Below this share of unnamed images, "the images lack alt text" is not a
# systemic barrier. In the hyundai runs about 10% of images were unnamed — icons
# inside links that already carried their text — while every car was named by
# its model, and the report still called missing alt text a serious problem
# "on 13 of 13 pages". Applied to alt text only: for controls a single unnamed
# submit button on a booking form is a genuine barrier at any share. The
# scoring rule is the one definition; this name stays for the narrative check.
_ALT_TEXT_SYSTEMIC_SHARE = SCORING_RULES["alt_text_systemic_share"]


def _is_wcag_issue(issue: dict) -> bool:
    # Any id that is not a Nielsen heuristic 1-10, not only null: the prompt
    # never says what an accessibility issue's id is, and in the 2026-09-25
    # replay the model wrote "A11Y-1" — so the conversion-form barrier the scan
    # had verified was badged NIEZWERYFIKOWANE, with its evidence left behind.
    if _heuristic_id(issue):
        return False
    name = str(issue.get("heuristic_name") or "").lower()
    return "wcag" in name or "dostępn" in name


def _wcag_demotion_reason(issue: dict, key: str) -> str | None:
    """Why a WCAG issue cannot stay critical or serious, if it cannot.

    Enforces in code what aggregate_synthesize.txt already asks for and the
    model did not do: a critical or serious issue needs cover in verified
    evidence. Scoped to WCAG issues — the channel that was shown to break it —
    so measured PageSpeed results and heuristic issues keep their placement.
    """
    if key == "minor_issues" or not _is_wcag_issue(issue):
        return None
    if not issue.get("verified"):
        return "no_evidence"
    share = alt_text_share(issue.get("measured"))
    if share is not None and share < _ALT_TEXT_SYSTEMIC_SHARE:
        return "measured_share"
    return None


def resolve_narrative(agg: dict) -> dict:
    """The narrative to render, with the LLM-failure fallback already applied.

    One function because the same three-clause guard used to be written out in
    each renderer, and only one of them (the Markdown report, since retired)
    called _attach_issue_evidence. When the aggregate LLM call failed, that copy
    showed screenshots, URLs, quotes and correct `verified` flags while
    report.html — the artifact actually served to the client — showed the same
    issues stripped of all evidence. Every renderer now takes this path.
    """
    stats = agg.get("statistics") or {}
    narr = agg.get("narrative") or {}
    if not narr or "error" in narr or not narr.get("executive_summary"):
        narr = _narrative_from_stats(stats)
    # Every render path — HTML, PDF, Word, the LLM-failure fallback — shows the
    # computed score when there is one, so the headline can never disagree with
    # the breakdown printed under it. Runs made before scoring.py have no
    # breakdown and keep the score they were delivered with.
    breakdown = stats.get("score_breakdown") or {}
    computed = breakdown.get("score")
    if computed is not None:
        narr["overall_ux_score"] = computed
        # The rationale from the breakdown too, not the narrator's prose: the
        # two disagreed in the first live run on the current code.
        if isinstance(breakdown.get("deductions"), list) and "start" in breakdown:
            narr["score_rationale"] = rationale_text(breakdown)
    _normalise_issue_severity(narr)
    _attach_issue_evidence(narr, stats, measurements={
        "links": agg.get("links"), "pagespeed": agg.get("pagespeed")})
    return narr


_LIST_SEVERITY = {"critical_issues": "krytyczny", "serious_issues": "poważny",
                  "minor_issues": "drobny"}


def _normalise_issue_severity(narr: dict) -> None:
    """Every issue's severity is one of the three labels.

    It is the aggregate model's JSON and was never checked: an int crashed the
    HTML and PDF build, a list raised in the issue card, and an unknown string
    was printed as a label. The list an issue sits in is the model's own
    severity call, so that is what a value outside the three falls back to.
    """
    for key, default in _LIST_SEVERITY.items():
        for issue in narr.get(key) or []:
            if isinstance(issue, dict) and issue.get("severity") not in _LIST_SEVERITY.values():
                issue["severity"] = default


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

    # Bucket by worst_severity. `critical` used to have no bucket at all, so a
    # heuristic whose worst finding was krytyczny matched neither list and was
    # silently dropped from the fallback report — while critical_issues was
    # additionally hardcoded to []. A site with critical findings therefore
    # rendered a report showing none.
    critical = [h for h in by_h if h.get("worst_severity") == "krytyczny"]
    serious = [h for h in by_h if h.get("worst_severity") == "poważny"]
    minor = [h for h in by_h if h.get("worst_severity") == "drobny"]

    exec_parts = [
        f"Badanie z udziałem {n} uczestników ujawniło "
        f"{sc.get('krytyczny', 0)} krytycznych, {sc.get('poważny', 0)} poważnych "
        f"i {sc.get('drobny', 0)} drobnych naruszeń heurystyk Nielsena."
    ]
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
    for i, h in enumerate((critical + serious)[:5], 1):
        recs.append({
            "priority": i,
            "area": h.get("heuristic_name", ""),
            "action": h.get("top_recommendation", ""),
            "expected_impact": "",
        })

    return {
        "executive_summary": " ".join(exec_parts),
        "overall_ux_score": round(fi_avg, 1) if fi_avg else None,
        "score_rationale": f"Ocena odzwierciedla wyniki testu pierwszego wrażenia ({fi_avg:.2f}/5) oraz rozkład naruszeń: {sc.get('krytyczny', 0)} krytycznych, {sc.get('poważny', 0)} poważnych, {sc.get('drobny', 0)} drobnych." if fi_avg else "",
        "key_strengths": strengths or ["Brak wyraźnych mocnych stron możliwych do wyodrębnienia z danych."],
        "critical_issues": [_to_issue(h) for h in critical],
        "serious_issues": [_to_issue(h) for h in serious],
        "minor_issues": [_to_issue(h) for h in minor],
        "accessibility_assessment": f"Średnia ocena dostępności: {a11y_avg}/5." if a11y_avg else "",
        "responsiveness_assessment": f"Sesje z overflowem tabletowym: {vp.get('tablet_overflow_sessions', 0)}/{n}. Sesje z overflowem mobilnym: {vp.get('mobile_overflow_sessions', 0)}/{n}.",
        "first_impression_assessment": f"Pierwsze wrażenie: {fi_avg:.2f}/5. Najsłabszy wymiar: wezwanie do działania ({dim.get('call_to_action', '—')}/5)." if fi_avg else "",
        "priority_recommendations": recs,
        "methodology_note": f"Badanie przeprowadzono przy użyciu agentów LLM symulujących rzeczywistych użytkowników, z których każdy otrzymał to samo zadanie. Ocena opiera się na heurystykach Nielsena, teście pierwszego wrażenia (5 sekund), analizie dostępności WCAG 2.1 oraz sprawdzeniu responsywności na urządzeniach mobilnych i tabletach.",
    }


