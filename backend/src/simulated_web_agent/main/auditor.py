"""
Per-agent UX audit orchestrator.

Accepts a TraceStore and runs three analyses in parallel:
  1. First impression (vision, step-0 screenshot)
  2. Heuristic evaluation (memory trace → Nielsen 10)
  3. Accessibility summary (a11y snapshots + font sizes)

All file I/O — reading inputs, writing outputs — is delegated to the TraceStore.
"""

import asyncio
import base64
import json
import logging
import re
from typing import Any

from ..agent.gpt import async_chat
from ..agent.prompt_registry import registry as _prompts
from .a11y_checks import extract_a11y_candidates
from .attribution import flag_harness_suspects, session_blocked_by_bot_wall
from .claim_verifier import run_claim_verification
from .confidence import cap_confidence
from .evidence import resolve_all, resolve_evidence
from .site_scope import flag_off_site, target_url_for
from .trace_store import TraceStore, parse_action

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSON extraction
# ---------------------------------------------------------------------------

def _safe_json(text: str, fallback: Any = None, prefer: type | None = None) -> Any:
    """Extract and parse the outermost JSON object or array from LLM output.

    `prefer` biases candidate selection toward the shape the caller needs. Without
    it we return the longest match of any shape, so a long array inside the
    model's prose can beat the real object — that is how a bare weakness array
    got persisted as a first_impression and broke every downstream reader.
    """
    import regex

    try:
        return json.loads(text.strip())
    except Exception:
        pass

    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        inner = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
        try:
            return json.loads(inner.strip())
        except Exception:
            pass

    candidates: list[tuple[int, Any]] = []
    for pattern in (r"\{(?:[^{}]|(?R))*\}", r"\[(?:[^\[\]]*|(?R))*\]"):
        for match in regex.findall(pattern, text, regex.DOTALL):
            try:
                parsed = json.loads(match)
                candidates.append((len(match), parsed))
            except Exception:
                pass
    if candidates:
        if prefer is not None:
            wanted = [c for c in candidates if isinstance(c[1], prefer)]
            if wanted:
                return max(wanted, key=lambda x: x[0])[1]
        return max(candidates, key=lambda x: x[0])[1]

    return fallback


class _UnusableReply(Exception):
    """The LLM never returned JSON of the shape this pass needs."""


async def _chat_json(messages: list, *, model: str, prefer: type, label: str,
                     attempts: int = 2) -> Any:
    """Ask the LLM for JSON in `prefer` shape, retrying an unusable reply once.

    The model intermittently answers with the wrong shape (observed: a bare
    array of weakness strings where an object belongs) or raises past
    async_chat's own transport retry. It does not reproduce — a hand re-run of
    the same pass succeeds first try — so one more ask is worth far more than a
    degraded artifact. In run 2026-07-16_13-41-14 a single bad reply cost one
    persona their entire accessibility audit and 500'd the report.

    Raises _UnusableReply when every attempt fails, so each caller can degrade
    the way its own artifact requires. Only the bad replies pay for this.
    """
    reason = "no_reply"
    for attempt in range(1, attempts + 1):
        try:
            raw = await async_chat(messages, model=model, log=False)
            result = _safe_json(raw, prefer=prefer)
            if isinstance(result, prefer):
                return result
            reason = "unexpected_shape"
            log.warning(
                "[auditor] %s returned %s, expected %s (attempt %d/%d)",
                label, type(result).__name__, prefer.__name__, attempt, attempts,
            )
        except Exception as e:  # noqa: BLE001 — retry any failure, then report
            # Include the type: some gateway errors carry an empty message, and
            # a bare str(e) then degrades the artifact to {"error": ""}, which
            # says nothing to whoever reads the run later.
            reason = f"{type(e).__name__}: {e}" if str(e) else type(e).__name__
            log.warning("[auditor] %s LLM call failed (attempt %d/%d): %s — "
                        "prompt was %d chars", label, attempt, attempts, reason,
                        sum(len(str(m.get("content", ""))) for m in messages))

    raise _UnusableReply(reason)


# ---------------------------------------------------------------------------
# Individual analysis functions — accept TraceStore, return parsed result
# ---------------------------------------------------------------------------

async def _run_first_impression(store: TraceStore) -> dict:
    # The landing frame, not step 0's evidence frame: the latter is scrolled to
    # the first click target (see TraceStore.first_impression_screenshot).
    screenshot_path = store.first_impression_screenshot()
    if not screenshot_path.exists():
        log.warning("[auditor] No step-0 screenshot found in %s", store.trace_dir)
        return {"error": "screenshot_missing"}

    b64 = base64.standard_b64encode(screenshot_path.read_bytes()).decode("utf-8")

    # The screenshot alone is identical for every persona on a site, so without
    # the persona this pass asks one question N times and returns one answer.
    card = store.read_persona_card() or {}
    persona = (card.get("description") or "").strip()
    if not persona:
        # Pre-persona_card runs, and any run whose card failed to write.
        persona = (store.read_basic_info().get("persona") or "").strip()
    if not persona:
        log.warning("[auditor] No persona for %s; first impression will not be "
                    "persona-specific", store.trace_dir)
        persona = "(brak danych o osobie — oceniaj jako przeciętny pierwszy użytkownik)"

    # Without this, purpose_clarity and every other dimension are judged against
    # an implicit "this is an online shop" frame — a catalog/store-locator site
    # like pepco.pl then gets marked down for having no cart. See auditor's
    # module docstring / CLAUDE.md "site_context" note.
    site_context = (store.read_basic_info().get("site_context") or "").strip()
    if not site_context:
        site_context = "(nie podano — nie zakładaj, że to sklep internetowy; oceniaj wyłącznie na podstawie tego, co widać na zrzucie ekranu)"

    prompt = _prompts.get("first_impression").format(persona=persona, site_context=site_context)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": prompt},
            ],
        }
    ]
    try:
        # "report" -> gemini-2.5-pro. The audit passes are the one place worth
        # the expensive tier: one call per persona, versus the agent loop's one
        # per step. "large" is NOT a stronger model here — it resolves to the
        # same flash entry as "small" (see CLAUDE.md); "report" is the only real
        # lever. Scoring a page as a specific person is exactly the nuanced
        # judgement that the cheap tier renders shallowly.
        return await _chat_json(messages, model="report", prefer=dict,
                                label="first_impression")
    except _UnusableReply as e:
        return {"error": str(e)}


async def _run_heuristic_evaluation(store: TraceStore) -> list:
    findings, _error = await _heuristic_pass(store)
    return findings


async def _heuristic_pass(store: TraceStore) -> tuple[list, str | None]:
    """The heuristic findings, and why the pass failed if it did.

    A failure returned [] as well, which no reader could tell from "no
    usability problems": the persona's report said "Znaleziono 0 potwierdzonych
    problemów", and a run with one such persona silently lost its findings —
    first impression and accessibility at least record {"error": …}.
    """
    memories = store.read_memory_trace()
    reflections = [m["content"] for m in memories if m.get("kind") == "reflection"]
    action_trace = store.read_action_trace()

    # Step-indexed session context so the LLM can cite verifiable steps/URLs.
    # Without the [step N] markers, claimed step/url values were guesses.
    n_steps = len(store.read_observations())
    observations = []
    for i in range(n_steps):
        text = store.read_observation_step_text(i).strip().replace("\n", " ")[:400]
        url = store.url_at_step(i)
        observations.append(f"[step {i}] {url} :: {text}")

    actions = []
    for i, raw in enumerate(action_trace):
        a = parse_action(raw)
        if a:
            actions.append(f"[step {i}] {a.get('description', json.dumps(a))}")

    if not observations and not reflections and not actions:
        log.warning("[auditor] No memory data found in %s", store.trace_dir)
        return [], "no_evidence"

    site_context = store.read_basic_info().get("site_context", "") or ""

    prompt = _prompts.get("heuristic_evaluate").format(
        site_context=site_context,
        observations="\n".join(f"- {o}" for o in observations) or "(none)",
        reflections="\n".join(f"- {r}" for r in reflections) or "(none)",
        actions="\n".join(f"- {a}" for a in actions) or "(none)",
    )
    try:
        # prefer=list: a stray object in the model's prose must not win here, or
        # every finding is dropped and the audit looks cleanly empty.
        return await _chat_json([{"role": "user", "content": prompt}],
                                model="report", prefer=list,
                                label="heuristic_evaluate"), None
    except _UnusableReply as e:
        log.error("[auditor] heuristic_evaluate unusable for %s — no findings "
                  "will be reported for this persona", store.trace_dir)
        return [], str(e) or "unusable reply"


async def _run_accessibility_summary(store: TraceStore) -> dict:
    # Deterministic checks decide what is broken (a11y_checks.py); the LLM only
    # narrates and scores. Pasting the raw trees here built a 1.66M-char prompt
    # that failed near the context limit — see a11y_checks for the numbers.
    scan = extract_a11y_candidates(store)
    # Nothing scanned is no measurement. The prompt maps no candidates to 5, so
    # every session that saw nothing of the site — 4 that never loaded, 4 stuck
    # on a Cloudflare page — was written up as 5/5 "strona jest dostępna", and
    # the dashboard showed that in green. All 13 sessions that did see a site
    # scored 1 or 2.
    if not scan.get("steps_scanned") or session_blocked_by_bot_wall(store):
        return {"error": "no_evidence"}
    # Step-indexed, not store.visited_urls(): that de-duplicates and drops the
    # step mapping, so the model had to guess which URL a cited step meant — and
    # it guessed wrong in prose, where resolve_all cannot correct it.
    step_urls = "\n".join(
        f"- step {i}: {url}" for i, url in enumerate(store.step_urls()) if url
    ) or "(unknown)"
    prompt = _prompts.get("accessibility_summarize").format(
        candidates=json.dumps(scan["candidates"], ensure_ascii=False, indent=2),
        steps_scanned=scan["steps_scanned"],
        pages_scanned=scan.get("pages_scanned", scan["steps_scanned"]),
        urls=step_urls,
    )
    try:
        # "report" -> gemini-2.5-pro; see _run_first_impression. Note the
        # 62%-return-the-example anchoring was fixed by the prompt, not the tier.
        return await _chat_json([{"role": "user", "content": prompt}],
                                model="report", prefer=dict,
                                label="accessibility_summarize")
    except _UnusableReply as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def run_audit_analysis(store: TraceStore) -> dict:
    """
    Orchestrate the full per-agent audit.

    Reads inputs via store, writes results via store:
      first_impression.json, heuristic_findings.json, accessibility_findings.json
    """
    log.info("[auditor] Starting audit for %s", store.trace_dir)

    first_impression, (heuristic_findings, heuristic_error), accessibility_findings = await asyncio.gather(
        _run_first_impression(store),
        _heuristic_pass(store),
        _run_accessibility_summary(store),
    )
    # Written every time, so a successful re-audit clears an old failure.
    def _err(result):
        return result.get("error") if isinstance(result, dict) else None
    store.write_audit_status({
        "heuristic_evaluation": heuristic_error,
        "first_impression": _err(first_impression),
        "accessibility": _err(accessibility_findings),
    })

    # Ground every finding in trace evidence (screenshot, URL, observation
    # quote) and stamp verified/unverified — claims are never trusted as-is.
    heuristic_findings = resolve_all(store, heuristic_findings)
    # Findings about pages outside the site under test (agents wandering to
    # doz.pl / gemini.pl fed the target site's report) are excluded up front —
    # compared on the trace-resolved URL, which resolve_all just stamped.
    heuristic_findings = flag_off_site(heuristic_findings, target_url_for(store))
    # Flag findings whose cited step lands on a harness interaction failure
    # (stuck click/type loop, perception artifact) so they aren't shipped as
    # confirmed site defects.
    heuristic_findings = flag_harness_suspects(store, heuristic_findings)
    # The truth gate: re-read each surviving claim against the cited step's
    # FULL observation and verdict it (supported/partial/unsupported). Ground
    # truth (diabetyk24 team review: ~2 of 8 issues real) showed most false
    # claims are refutable from the run's own trace — this is where they die.
    heuristic_findings = await run_claim_verification(store, heuristic_findings)
    # All stamps are now in place, so the LLM's self-reported confidence can be
    # capped at what they actually support. Must stay last.
    heuristic_findings = cap_confidence(heuristic_findings)
    if isinstance(accessibility_findings, dict):
        accessibility_findings["findings"] = resolve_all(
            store, accessibility_findings.get("findings") or []
        )
        accessibility_findings["font_size_issues"] = resolve_all(
            store, accessibility_findings.get("font_size_issues") or []
        )
    if isinstance(first_impression, dict) and "error" not in first_impression:
        first_impression = resolve_evidence(store, {**first_impression, "step": 0})

    store.write_first_impression(first_impression)
    store.write_heuristic_findings(heuristic_findings)
    store.write_accessibility_findings(accessibility_findings)

    log.info("[auditor] Audit complete for %s", store.trace_dir)
    return {
        "first_impression": first_impression,
        "heuristic_findings": heuristic_findings,
        "accessibility_findings": accessibility_findings,
    }
