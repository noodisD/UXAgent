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
from .attribution import flag_harness_suspects
from .evidence import resolve_all, resolve_evidence
from .trace_store import TraceStore

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


# ---------------------------------------------------------------------------
# Individual analysis functions — accept TraceStore, return parsed result
# ---------------------------------------------------------------------------

async def _run_first_impression(store: TraceStore) -> dict:
    screenshot_path = store.screenshot_path(0)
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

    prompt = _prompts.get("first_impression").format(persona=persona)

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                {"type": "text", "text": prompt},
            ],
        }
    ]
    # The model intermittently emits only a fragment (observed: a bare array of
    # weakness strings, roughly 1 reply in 10). Persisting that shape breaks
    # every downstream reader — it 500'd the report in run 2026-07-16_13-41-14.
    # The cause is not understood, but it does not reproduce on retry, so ask
    # once more before degrading. Only the bad replies pay for this.
    for attempt in (1, 2):
        try:
            # "large": scoring a screenshot as a specific person is a nuanced
            # visual judgement, and it is one call per persona — the cheap tier
            # anchored on the prompt's example and scored every site alike.
            raw = await async_chat(messages, model="large", log=False)
            result = _safe_json(raw, fallback={"raw": raw}, prefer=dict)
            if isinstance(result, dict):
                return result
            log.warning(
                "[auditor] first_impression returned %s, expected object "
                "(attempt %d/2)", type(result).__name__, attempt,
            )
        except Exception as e:
            log.exception("[auditor] first_impression LLM call failed: %s", e)
            return {"error": str(e)}

    return {"error": "unexpected_shape", "raw": str(result)[:800]}


async def _run_heuristic_evaluation(store: TraceStore) -> list:
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
    for i, a in enumerate(action_trace):
        if isinstance(a, str):
            try:
                a = json.loads(a)
            except Exception:
                a = {"description": a}
        if isinstance(a, dict):
            actions.append(f"[step {i}] {a.get('description', json.dumps(a))}")

    if not observations and not reflections and not actions:
        log.warning("[auditor] No memory data found in %s", store.trace_dir)
        return []

    site_context = store.read_basic_info().get("site_context", "") or ""

    prompt = _prompts.get("heuristic_evaluate").format(
        site_context=site_context,
        observations="\n".join(f"- {o}" for o in observations) or "(none)",
        reflections="\n".join(f"- {r}" for r in reflections) or "(none)",
        actions="\n".join(f"- {a}" for a in actions) or "(none)",
    )
    try:
        raw = await async_chat([{"role": "user", "content": prompt}], model="large", log=False)
        result = _safe_json(raw, fallback=[])
        return result if isinstance(result, list) else []
    except Exception as e:
        log.exception("[auditor] heuristic_evaluate LLM call failed: %s", e)
        return []


async def _run_accessibility_summary(store: TraceStore) -> dict:
    prompt = _prompts.get("accessibility_summarize").format(
        a11y_snapshots=json.dumps(store.read_a11y_snapshots(), ensure_ascii=False, indent=2),
        font_sizes=json.dumps(store.read_font_sizes(), ensure_ascii=False, indent=2),
        urls="\n".join(store.visited_urls()) or "(unknown)",
    )
    try:
        raw = await async_chat([{"role": "user", "content": prompt}], model="small", log=False)
        return _safe_json(raw, fallback={"error": "parse_failed", "raw": raw})
    except Exception as e:
        log.exception("[auditor] accessibility LLM call failed: %s", e)
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

    first_impression, heuristic_findings, accessibility_findings = await asyncio.gather(
        _run_first_impression(store),
        _run_heuristic_evaluation(store),
        _run_accessibility_summary(store),
    )

    # Ground every finding in trace evidence (screenshot, URL, observation
    # quote) and stamp verified/unverified — claims are never trusted as-is.
    heuristic_findings = resolve_all(store, heuristic_findings)
    # Flag findings whose cited step lands on a harness interaction failure
    # (stuck click/type loop) so they aren't shipped as confirmed site defects.
    heuristic_findings = flag_harness_suspects(store, heuristic_findings)
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
