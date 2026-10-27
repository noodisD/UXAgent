import json
import logging
from typing import Any

from ..agent.gpt import async_chat
from ..agent.prompt_registry import registry as _prompts
from .trace_store import TraceStore

logger = logging.getLogger(__name__)

# Fixed synthetic post-task questions (§4.3): SEQ and confidence on a 1–7 scale,
# asked of every persona regardless of the user's questionnaire. Stored as
# synthetic self-report and never presented as real-user SEQ (MeasuringU
# benchmark 5.5 is context only).
_POST_TASK_QUESTIONS = [
    {
        "id": "synthetic_seq",
        "type": "multiple_choice",
        "prompt": "Jak łatwe było wykonanie zadania? (1 = bardzo trudne, 7 = bardzo łatwe)",
        "options": ["1", "2", "3", "4", "5", "6", "7"],
    },
    {
        "id": "synthetic_confidence",
        "type": "multiple_choice",
        "prompt": "Na ile jesteś pewien/pewna, że zadanie zostało wykonane poprawnie? (1 = wcale, 7 = całkowicie)",
        "options": ["1", "2", "3", "4", "5", "6", "7"],
    },
]

# Question keys read by the scoring (sus.py), never by the persona.
_SCORING_MARKERS = ("sus_item", "reverse")


async def run_survey(
    store: TraceStore,
    questionnaire: dict[str, Any],
) -> dict[str, Any]:
    """
    Run the survey for one agent session using memory trace stored in `store`.

    Reads: store.read_memory_trace()
    Writes: store.write_survey_result(result)

    Returns the parsed survey result dict.
    """
    survey_prompt = _prompts.get("survey")

    # Append the fixed synthetic post-task questions (SEQ + confidence). The
    # scoring markers stay in the stored questionnaire but not in what the
    # persona reads: "this is SUS item 4, reverse-scored" would prime its answer.
    questionnaire = dict(questionnaire or {})
    questionnaire["questions"] = [
        {k: v for k, v in q.items() if k not in _SCORING_MARKERS} if isinstance(q, dict) else q
        for q in questionnaire.get("questions", [])
    ] + _POST_TASK_QUESTIONS

    # Prepend site_context so the persona answers calibrated to the site's purpose
    site_context = store.read_basic_info().get("site_context", "") or ""
    if site_context:
        site_context_block = (
            "SITE CONTEXT (read before answering — calibrate your answers accordingly):\n"
            + site_context.strip()
            + "\n\n"
        )
        survey_prompt = site_context_block + survey_prompt

    memories = store.read_memory_trace()
    memory_str = json.dumps(memories, ensure_ascii=False)

    # Session end-state grounds the answers: a session that died after a few
    # steps must not produce confident scores about unexperienced features.
    session = store.read_session_result()
    session_block = (
        "\nSESSION RESULT (how my browsing session actually ended):\n"
        + json.dumps(session, ensure_ascii=False)
        + "\n"
        if session
        else ""
    )

    request = [
        {"role": "system", "content": survey_prompt},
        {
            "role": "user",
            "content": json.dumps(questionnaire) + "\n" + session_block + memory_str,
        },
    ]
    try:
        result_text = await async_chat(request, json_mode=True)
        try:
            result = json.loads(result_text)
        except json.JSONDecodeError:
            logger.warning("[survey] Model did not return valid JSON; storing raw text.")
            result = {"raw": result_text}
    except Exception as e:
        logger.exception("[survey] LLM call failed: %s", e)
        result = {"error": str(e)}

    store.write_survey_result(result)
    return result
