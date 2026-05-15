import json
import logging
from typing import Any

from ..agent.gpt import async_chat
from ..agent.prompt_registry import registry as _prompts
from .trace_store import TraceStore

logger = logging.getLogger(__name__)


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

    memories = store.read_memory_trace()
    memory_str = json.dumps(memories, ensure_ascii=False)

    request = [
        {"role": "system", "content": survey_prompt},
        {
            "role": "user",
            "content": json.dumps(questionnaire) + "\n" + memory_str,
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
