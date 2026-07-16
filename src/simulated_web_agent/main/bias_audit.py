"""Bias / stereotype audit over a persona run (§1.4, §2.2, §11.4).

LLM personas risk "misportray" and "flatten" identity groups (Nature Machine
Intelligence 2025). This pass reviews the findings and reflection rationale of a
run and flags reasoning that is stereotype-based, uses an identity trait without
task relevance, flattens the persona, or attributes emotion without evidence.
Output is advisory (bias_audit.json), surfaced in the report appendix.
"""
import json
import logging

from ..agent.gpt import async_chat
from ..agent.prompt_registry import registry as _prompts
from .auditor import _safe_json
from .trace_store import TraceStore

log = logging.getLogger(__name__)

_EMPTY = {"flags": [], "summary": "", "bias_detected": False}


async def run_bias_audit(store: TraceStore) -> dict:
    card = store.read_persona_card() or {}
    findings = store.read_heuristic_findings() or []
    reflection = store.read_objective_reflection() or {}

    payload = {
        "persona": {
            "name": card.get("name"),
            "demographics": card.get("demographics"),
            "objective": card.get("objective"),
        },
        "findings": [
            {"heuristic_id": f.get("heuristic_id"),
             "evidence": f.get("evidence"),
             "recommendation": f.get("recommendation")}
            for f in findings if isinstance(f, dict)
        ],
        "reflection": {
            "summary": reflection.get("summary"),
            "blockers": reflection.get("blockers"),
            "priorityFix": reflection.get("priorityFix"),
        },
    }
    prompt = _prompts.get("bias_audit").replace(
        "{payload}", json.dumps(payload, ensure_ascii=False)
    )
    try:
        raw = await async_chat([{"role": "user", "content": prompt}], model="small", log=False)
        result = _safe_json(raw, fallback=dict(_EMPTY))
        if not isinstance(result, dict):
            result = dict(_EMPTY)
    except Exception as e:
        log.warning("[bias_audit] failed: %s", e)
        result = {**_EMPTY, "summary": f"error: {e}"}

    store.write_bias_audit(result)
    return result
