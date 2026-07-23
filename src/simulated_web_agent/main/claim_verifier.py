"""Claim verification — the truth gate for heuristic findings.

Why this exists (§5.1 ground truth): the diabetyk24.pl team manually verified
the 5-persona report and found ~2 of 8 issues real. The disproven ones were
refutable from the run's own trace: the "product click → login redirect"
critical cited a step whose observation shows a filter panel and no login form;
"no cart icon" cited a step whose observation *contains* the cart's
`aria-label="Koszyk, liczba produktów: 1"`; "the page is empty" cited steps
whose raw HTML was tens of KB. The heuristic auditor never sees the page — only
the agent's narrative, truncated to 400 chars per step — and `verified: True`
(evidence.py) means only "the cited step exists". Nothing compared claim to
evidence. This pass does.

For each finding that survived the deterministic gates (not harness_suspect,
not off_site, verified), one audit-tier LLM call per persona re-reads the
cited step's FULL observation text plus the neighbouring actions and renders a
verdict:

    supported    — the observation shows the claimed problem
    partial      — related friction is visible but the claim overstates it
    unsupported  — the observation does not show it (or shows the opposite)

Consequences are applied deterministically downstream: `unsupported` is
excluded from headline counts / aggregate / prioritization exactly like
harness_suspect (severity.excluded_from_report); `partial` caps confidence to
"medium" (confidence._ceiling); `supported` changes nothing.

Fail-open by contract: any verifier failure (LLM error, malformed reply,
unknown verdict) leaves findings untouched. An unverified report is honest;
a silently emptied one is not.
"""
from __future__ import annotations

import json
import logging
import re

from ..agent.gpt import _parse_json_response, async_chat
from ..agent.prompt_registry import registry as _prompts
from .trace_store import TraceStore

log = logging.getLogger(__name__)

_VALID_VERDICTS = {"supported", "partial", "unsupported"}

# Full observation, but bounded: a pathological transcript must not blow the
# prompt (the a11y pass once built a 1.66M-char prompt this way).
_MAX_OBS_CHARS = 4000
_MAX_REASON_CHARS = 300


def _parse_action(entry) -> dict:
    if isinstance(entry, str):
        try:
            entry = json.loads(entry)
        except Exception:
            return {"description": entry}
    return entry if isinstance(entry, dict) else {}


# --- what the session actually PROVED (deterministic, from the trace) -------
# A claim that an action-class is broken or a feature is absent is contradicted
# when the trace shows that class working. dm ground truth: "product links
# don't work" shipped as serious while the same session opened 7 product pages;
# the one false critical claimed "no status feedback" while the cited
# observation contained the cart indicator.

_PRODUCT_URL_MARKERS = ("/p/", "/produkt", "/product/")
_CART_URL_MARKERS = ("koszyk", "/cart", "checkout", "/kasa")


def session_capabilities(store: TraceStore) -> dict:
    """Ground-truth facts the run demonstrated, to check negative claims against.
    Uses visited URLs (deduped, robust) — not fuzzy text."""
    urls = [(u or "").lower() for u in (store.visited_urls() or [])]
    return {
        "product_pages_opened": sum(
            1 for u in urls if any(m in u for m in _PRODUCT_URL_MARKERS)
        ),
        "reached_cart": any(
            any(m in u for m in _CART_URL_MARKERS) for u in urls
        ),
    }


# A claim asserts something is broken / doesn't respond / doesn't work.
_BROKEN_MARKERS = (
    "nie działa", "nie zadział", "nie reaguj", "nie otwiera", "nie przechodz",
    "nie ładuj", "brak reakcji", "losowo nie", "nie klika", "nie uruchamia",
    "did not respond", "does not work", "not working", "no response",
)
# ...specifically about product links / tiles / results.
_PRODUCT_LINK_MARKERS = ("link do produkt", "linki produkt", "linki do produkt",
                         "kafel", "tile", "product link", "product tile",
                         "link produktu", "wynik", "wynikach wyszukiwania")

# A claim asserts a status/cart element is absent...
_ABSENCE_MARKERS = (
    "nie informuje", "brak informacj", "nie wyświetla", "brak ikony",
    "nie ma ikony", "brak stałej", "brak widocznej", "nie widać", "no feedback",
    "not visible", "brak komunikat", "nie pokazuje",
)
# ...and the observation nonetheless contains a cart/status indicator.
_STATUS_PRESENT_MARKERS = ("koszyk", "cart", "liczba produktów", "shopping cart",
                           "cart icon", "ikona koszyka")


def _has(text: str, markers) -> bool:
    """Whole-word-ish marker match: a word boundary before the marker so
    "nie przechodz" does not match "wielokrotNIE PRZECHODZić" (a real false
    override on a legit compare-friction finding)."""
    return any(re.search(r"(?<!\w)" + re.escape(m), text) for m in markers)


def _contradiction_reason(evidence: str, observation: str, caps: dict) -> str | None:
    """A short reason if the session/observation refutes a broken/absent claim,
    else None. Deterministic and conservative — only the two clear classes the
    LLM verifier proved unreliable on."""
    ev = (evidence or "").lower()
    obs = (observation or "").lower()

    if (_has(ev, _BROKEN_MARKERS)
            and _has(ev, _PRODUCT_LINK_MARKERS)
            and caps.get("product_pages_opened", 0) >= 2):
        return ("Wnioskowi przeczy zapis sesji: w tej samej sesji otwarto "
                f"{caps['product_pages_opened']} stron produktów.")

    if (_has(ev, _ABSENCE_MARKERS)
            and _has(ev, ("koszyk", "cart", "status", "ikony"))
            and _has(obs, _STATUS_PRESENT_MARKERS)):
        return ("Cytowana obserwacja zawiera wskaźnik koszyka/statusu, "
                "którego wniosek dotyczy jako rzekomo brakującego.")

    return None


def _step_context(store: TraceStore, step) -> dict:
    """The cited step's full observation + the actions just before/after it."""
    try:
        step = int(step)
    except (TypeError, ValueError):
        return {"observation_full": "", "action_before": "", "action_after": ""}
    obs = store.read_observation_step_text(step).strip()
    if len(obs) > _MAX_OBS_CHARS:
        obs = obs[:_MAX_OBS_CHARS] + "…"
    actions = [_parse_action(a) for a in (store.read_action_trace() or [])]

    def _desc(i):
        if 0 <= i < len(actions):
            return (actions[i].get("description") or "")[:200]
        return ""

    return {
        "observation_full": obs,
        "action_before": _desc(step - 1),
        "action_at_step": _desc(step),
        "action_after": _desc(step + 1),
    }


async def run_claim_verification(store: TraceStore, findings: list) -> list:
    """Verdict every verifiable finding against its cited evidence.

    Mutates and returns `findings`. Idempotent: stale claim_verdict fields are
    cleared first, so re-running after a rule change never leaves a finding
    carrying a verdict it would no longer receive.
    """
    # Clear stale verdicts on everything up front (fail-open then re-stamps).
    for f in findings or []:
        if isinstance(f, dict):
            f.pop("claim_verdict", None)
            f.pop("verdict_reason", None)

    # Only findings still standing after the deterministic gates: the others
    # are already excluded/capped — no LLM cost re-judging them.
    to_verify: list[tuple[int, dict]] = [
        (i, f) for i, f in enumerate(findings or [])
        if isinstance(f, dict)
        and not f.get("harness_suspect")
        and not f.get("off_site")
        and f.get("verified", True)
    ]
    if not to_verify:
        return findings

    # Ground-truth facts this session demonstrated — passed to the LLM and used
    # for the deterministic override below.
    caps = session_capabilities(store)

    payload = []
    obs_by_local: dict[int, str] = {}
    for local_id, (_, f) in enumerate(to_verify):
        ctx = _step_context(store, f.get("step"))
        obs_by_local[local_id] = ctx["observation_full"]
        payload.append({
            "id": local_id,
            "heuristic_id": f.get("heuristic_id"),
            "severity": f.get("severity"),
            "claim": f.get("evidence", ""),
            "step": f.get("step"),
            "url": f.get("url", ""),
            **ctx,
        })

    prompt = (
        _prompts.get("verify_findings")
        .replace("{session_facts}", json.dumps(caps, ensure_ascii=False))
        .replace("{findings_json}", json.dumps(payload, ensure_ascii=False, indent=1))
    )

    # LLM pass — fail-open: on any error, verdicts stay empty and we fall through
    # to the deterministic override (which must run regardless of the LLM).
    by_id: dict[int, tuple[str, str]] = {}
    try:
        raw = await async_chat(
            [{"role": "user", "content": prompt}], model="report", log=False
        )
        result = json.loads(_parse_json_response(raw))
        verdicts = result.get("verdicts") if isinstance(result, dict) else None
        if not isinstance(verdicts, list):
            raise ValueError("no verdicts array in verifier reply")
        for v in verdicts:
            if not isinstance(v, dict):
                continue
            verdict = str(v.get("verdict", "")).strip().lower()
            if verdict not in _VALID_VERDICTS:
                continue  # a creative verdict string must not exclude a finding
            try:
                by_id[int(v.get("id"))] = (
                    verdict, str(v.get("reason", ""))[:_MAX_REASON_CHARS])
            except (TypeError, ValueError):
                continue
    except Exception as e:
        log.warning("[claim_verifier] LLM verification failed, using "
                    "deterministic checks only: %s", e)

    stamped = overridden = 0
    for local_id, (_, f) in enumerate(to_verify):
        if local_id in by_id:
            f["claim_verdict"], f["verdict_reason"] = by_id[local_id]
            stamped += 1
        # Deterministic override: the session/observation refutes a broken/absent
        # claim — force unsupported, winning over an LLM "supported"/"partial".
        # This is what the LLM proved unreliable at (a stale single observation
        # looks consistent while the whole session disproves the claim).
        reason = _contradiction_reason(
            f.get("evidence", ""), obs_by_local.get(local_id, ""), caps)
        if reason and f.get("claim_verdict") != "unsupported":
            f["claim_verdict"] = "unsupported"
            f["verdict_reason"] = reason
            overridden += 1

    counts: dict[str, int] = {}
    for _, (_, f) in enumerate(to_verify):
        v = f.get("claim_verdict")
        if v:
            counts[v] = counts.get(v, 0) + 1
    log.info("[claim_verifier] %d verdicted (%d hard-overridden) of %d: %s",
             stamped, overridden, len(to_verify), counts)
    return findings
