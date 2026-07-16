"""Cognitive walkthrough pass (§4.4).

Task-based inspection from a new user's perspective: for the friction steps of a
run (stuck loops, backtracks) answer NN/g's four questions — right intent,
action noticed, action↔result association, feedback visible. Restricted to
friction steps to keep token cost bounded. Output feeds report evidence.
"""
import json
import logging

from ..agent.gpt import async_chat
from ..agent.prompt_registry import registry as _prompts
from .attribution import _parse_action, harness_suspect_steps
from .auditor import _safe_json
from .trace_store import TraceStore

log = logging.getLogger(__name__)

_MAX_STEPS = 6  # cap LLM cost


def _friction_steps(store: TraceStore, actions: list) -> list[int]:
    friction: set[int] = set(harness_suspect_steps(store))
    for i, a in enumerate(actions):
        if a.get("action") == "back":
            friction.add(max(0, i - 1))
    return sorted(friction)[:_MAX_STEPS]


async def run_cognitive_walkthrough(store: TraceStore) -> list:
    actions = [_parse_action(a) for a in (store.read_action_trace() or [])]
    friction = _friction_steps(store, actions)
    if not friction:
        store.write_cognitive_walkthrough([])
        return []

    card = store.read_persona_card() or {}
    basic = store.read_basic_info() or {}
    steps_payload = []
    for i in friction:
        act = actions[i] if i < len(actions) else {}
        steps_payload.append({
            "step": i,
            "observation": (store.read_observation_step_text(i) or "")[:400],
            "action": act.get("description") or act.get("action") or "",
        })

    prompt = (
        _prompts.get("cognitive_walkthrough")
        .replace("{persona}", (card.get("description") or basic.get("persona", ""))[:600])
        .replace("{intent}", basic.get("intent", "") or "")
        .replace("{steps}", json.dumps(steps_payload, ensure_ascii=False))
    )
    try:
        raw = await async_chat([{"role": "user", "content": prompt}], model="small", log=False)
        result = _safe_json(raw, fallback=[])
        if not isinstance(result, list):
            result = []
    except Exception as e:
        log.warning("[cognitive_walkthrough] failed: %s", e)
        result = []

    store.write_cognitive_walkthrough(result)
    return result
