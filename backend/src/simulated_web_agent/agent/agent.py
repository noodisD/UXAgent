import asyncio
import inspect
import json
import logging
import re
import time
from typing import Optional, Union

from . import context, gpt
from .gpt import async_chat
from .memory import Action, Memory, MemoryPiece, Observation, Plan, Reflection, Thought
from .prompt_registry import registry as _prompts

# Prompts are loaded lazily on first access via the registry — importing
# this module no longer requires the shop_prompts/ directory to exist on disk.
logger = logging.getLogger(__name__)


# context manager to log api calls
class LogApiCall:
    def __enter__(self):
        Agent.api_call_count += 1
        # Captured here, not re-read in __exit__. AgentPolicy.slow_loop runs
        # reflect/wonder concurrently with perceive/plan/act in the SAME
        # persona, so two LogApiCall blocks overlap; re-reading the class
        # counter on exit made both write api_trace_<same-n>.json and one
        # silently overwrote the other.
        self.call_index = Agent.api_call_count
        logger.info("API call count: %s", self.call_index)
        self.method_name = inspect.currentframe().f_back.f_code.co_name
        self.retrieve_result = []
        self.request = []
        self.response = []
        self.start_time = time.time()
        context.api_call_manager.set(self)

    def __exit__(self, exc_type, exc_value, traceback):
        context.api_call_manager.set(None)
        with open(
            context.run_path.get()
            / "api_trace"
            / f"api_trace_{self.call_index}.json",
            "w",
        ) as f:
            json.dump(
                {
                    "request": self.request,
                    "response": self.response,
                    "method_name": self.method_name,
                    "retrieve_result": self.retrieve_result,
                    "time": time.time() - self.start_time,
                },
                f,
            )
        logger.info(f"API Call time: {time.time() - self.start_time}")


def observation_text(value) -> str:
    """The perceiver's answer as one string.

    The prompt asks for {"observations": ["<text>"]}, but the lite model now and
    then returns the text split into a list of sentences or nested one level
    deeper. The step loop writes this value to disk as text, so a list ended the
    whole session with `TypeError: data must be str, not list` — two of six
    participants in the 2026-10-13 hyundai run, both a few steps after opening
    the configurator summary, and the report blamed the site for it."""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return chr(10).join(t for t in (observation_text(v) for v in value) if t)
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False)
    return "" if value is None else str(value)


class Agent:
    memory: Memory
    persona: str
    current_plan: Optional[Plan]
    last_reflect_index = 0
    api_call_count = 0
    observation: Optional[Observation] = None
    deny_list = ["Crime", "crime", "Security", "security"]

    def __init__(self, persona, intent):
        self.memory = Memory(self)
        self.persona = persona
        self.intent = intent
        self.current_plan = None
        # {"steps_used", "max_steps", "steps_remaining"} — set by the browser
        # loop each step so the planner can respect the session's step budget.
        self.step_budget = None

    # ~50k tokens of HTML — large pages (e-commerce listings) at 500k chars made
    # single Gemini calls take 4-7 minutes; 200k keeps steps at a usable pace.
    _MAX_HTML_CHARS = 200_000

    async def perceive(self, environment):
        if isinstance(environment, str) and len(environment) > self._MAX_HTML_CHARS:
            environment = environment[:self._MAX_HTML_CHARS] + "\n... [HTML truncated due to length]"

        environment_full = json.dumps(environment, ensure_ascii=False)

        for denied_word in self.deny_list:
            environment_full = environment_full.replace(denied_word, "***")
        # print(environment_full)
        logger.info("agent perceiving environment...")
        with LogApiCall():
            request = [
                {"role": "system", "content": _prompts.get("perceive")},
                {"role": "user", "content": environment_full},
            ]
            # Lite tier: transcription is mechanical and output-bound — flash-lite
            # writes ~40% faster. 16k covers observed payloads; async_chat retries
            # with a doubled budget if the output is still length-truncated.
            result = await async_chat(request, json_mode=True, max_tokens=16000, model="lite")
            result = json.loads(result)
            print(result)
            self.observation = observation_text(result["observations"][0])
            await self.memory.add_memory_piece(
                Observation(self.observation, self.memory, environment)
            )

    # Prompt payload budget: memory grows every step; without a cap the
    # serialized memories pushed plan/act/feedback requests toward 1 MB and
    # LLM latency grew ~10x over a run.
    _MAX_MEMORY_ITEMS = 40
    _MAX_MEMORY_CHARS = 400

    @classmethod
    def format_memories(cls, memories: list[MemoryPiece], sort_by_kind=True, budget=True) -> list[str]:
        if budget and len(memories) > cls._MAX_MEMORY_ITEMS:
            memories = memories[-cls._MAX_MEMORY_ITEMS:]
        # sort by kind and timestamp
        if sort_by_kind:
            memories = sorted(memories, key=lambda x: (x.kind, x.timestamp))
        importances_str = [
            f"{m.importance:.2f}" if m.importance != -1 else "N/A" for m in memories
        ]
        max_chars = cls._MAX_MEMORY_CHARS if budget else None
        memories_str = [
            f"""timestamp: {m.timestamp}; kind: {m.kind}; importance: {i}, content: {str(m.content)[:max_chars]}"""
            for m, i in zip(memories, importances_str)
        ]
        return memories_str

    async def feedback(self, obs, last_action_error: Optional[str] = None):
        if isinstance(obs, str) and len(obs) > self._MAX_HTML_CHARS:
            obs = obs[:self._MAX_HTML_CHARS] + "\n... [HTML truncated due to length]"
        last_action = None
        last_plan = self.current_plan
        # todo: allow many actions in a timestamp.
        for m in self.memory.memories[::-1]:
            if isinstance(m, Action):
                last_action = m
                break
        assert last_action is not None
        assert last_plan is not None
        with LogApiCall():
            resp = await async_chat(
                [
                    {"role": "system", "content": _prompts.get("feedback")},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "persona": self.persona,
                                "last_action": last_action.raw_action,
                                "last_action_error": last_action_error,
                                "last_plan": last_plan.content,
                                "observation": obs,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                json_mode=True,
            )
        resp = json.loads(resp)
        logger.info("feedback: %s", resp)
        for thought in resp["thoughts"]:
            await self.memory.add_memory_piece(Thought(thought, self.memory))

    async def reflect(self):
        # we reflect on the most recent memories
        # two most recent memories (last observasion, reflect, plan, action)
        logger.info("reflecting on memories ...")
        # memories = [
        #     i for i in self.memory.memories if i.timestamp >= self.memory.timestamp - 1
        # ]
        memories = self.memory.memories[self.last_reflect_index :]
        self.last_reflect_index = len(self.memory.memories)
        memories = self.format_memories(memories)
        model_input = {
            "current_timestamp": self.memory.timestamp,
            "memories": memories,
            "persona": self.persona,
        }
        # with LogApiCall():
        reflections = await async_chat(
            [
                {"role": "system", "content": _prompts.get("reflect")},
                {"role": "user", "content": json.dumps(model_input, ensure_ascii=False)},
            ],
            log=False,
            json_mode=True,
            model="large",
        )
        reflections = json.loads(reflections)["insights"]
        logger.info("reflections: %s", reflections)
        for r in reflections:
            await self.memory.add_memory_piece(Reflection(r, self.memory))
        # todo

        # logger.info(
        #     f"reflecting on {questions}",
        # )
        # reflections = []
        # answers = []
        # requests = [
        #     [
        #         {
        #             "role": "system",
        #             "content": REFLECT_ANSWER_PROMPT,
        #         },
        #         {
        #             "role": "user",
        #             "content": json.dumps(
        #                 {
        #                     "question": q,
        #                     "memories": self.format_memories(
        #                         await self.memory.retrieve(q)
        #                     ),
        #                 }
        #             ),
        #         },
        #     ]
        #     for q in questions
        # ]
        # results = await asyncio.gather(
        #     *[
        #         async_chat(
        #             r,
        #             response_format={"type": "json_object"},
        #         )
        #         for r in requests
        #     ]
        # )
        # for answer in results:
        #     answer = json.loads(answer)
        #     if answer["answer"] != "N/A":
        #         answers.append(answer)
        # try:
        #     for answer in answers:
        #         reflections.append(
        #             Reflection(
        #                 answer["answer"],
        #                 self.memory,
        #                 [memories[i] for i in answer["target"]],
        #             )
        #         )
        # except IndexError as e:
        #     logger.error("Reflection failed: %s", e)
        #     await self.memory.add_memory_piece(
        #         Thought("Reflection failed", self.memory)
        #     )

    async def wonder(self):
        logger.info("wondering ...")
        memories = self.memory.memories[-50:]  # get the last 50 memories
        memories = self.format_memories(memories)
        # with LogApiCall():
        resp = await async_chat(
            [
                {"role": "system", "content": _prompts.get("wonder")},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "persona": self.persona,
                            "memories": memories,
                            "intent": self.intent,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            log=False,
            json_mode=True,
            model="large",
        )
        resp = json.loads(resp)
        logger.info("wondering: %s", resp)
        for thought in resp["thoughts"]:
            await self.memory.add_memory_piece(Thought(thought, self.memory))

    async def plan(self):
        logger.info("planning ...")
        with LogApiCall():
            memories = await self.memory.retrieve(
                self.intent,
                include_recent_observation=True,
                include_recent_action=True,
                include_recent_plan=True,
                include_recent_thought=True,
                trigger_update=False,
                kind_weight={"action": 10, "plan": 10, "thought": 10, "reflection": 10},
            )
            memories = self.format_memories(memories)
            new_plan = ""
            rationale = ""
            next_step = ""
            # Bounded retry: an unbounded loop here stalled agents for minutes
            # when the model repeatedly returned malformed plans.
            for attempt in range(3):
                resp = await async_chat(
                    [
                        {
                            "role": "system",
                            "content": _prompts.get("planning"),
                        },
                        {
                            "role": "user",
                            "content": json.dumps(
                                {
                                    "persona": self.persona,
                                    "intent": self.intent,
                                    "memories": memories,
                                    "current_timestamp": self.memory.timestamp,
                                    "step_budget": self.step_budget or "N/A",
                                    "old_plan": "N/A"
                                    if self.current_plan is None
                                    else self.current_plan.content,
                                },
                                ensure_ascii=False,
                            ),
                        },
                    ],
                    json_mode=True,
                    # enable_thinking=True,
                    model="large",
                )
                resp = json.loads(resp)
                if "plan" in resp and "rationale" in resp and "next_step" in resp:
                    new_plan = resp["plan"]
                    rationale = resp["rationale"]
                    next_step = resp["next_step"]
                    # make sure they are str
                    if isinstance(new_plan, str) and isinstance(rationale, str):
                        break
                logger.info("invalid plan response (attempt %d), rethinking...", attempt + 1)
                logger.info("response: %s", resp)
            else:
                # Fallback after exhausted retries: keep the old plan rather than stall.
                logger.warning("plan() failed to produce a valid plan after 3 attempts; using fallback")
                if self.current_plan is not None:
                    new_plan = self.current_plan.content
                else:
                    new_plan = "Explore the page and work toward the intent step by step."
                rationale = "Planning fallback: previous attempts returned malformed plans."
                next_step = "Review the page and take the most reasonable next action toward the intent."
        logger.info("plan: %s", new_plan)
        logger.info("rationale: %s", rationale)
        logger.info("next_step: %s", next_step)
        self.current_plan = Plan(new_plan, self.memory, next_step)
        await self.memory.add_memory_piece(Thought(rationale, self.memory))
        await self.memory.add_memory_piece(self.current_plan)

    async def act(self, env, last_action_error: Optional[str] = None):
        with LogApiCall():
            memories = await self.memory.retrieve(
                self.current_plan.next_step,
                trigger_update=False,
                kind_weight={"observation": 0, "action": 10, "thought": 10},
            )
            memories = self.format_memories(memories)
            assert self.current_plan is not None
            clickables = [e for e in env.get("clickable_elements") or [] if e]
            inputs = [
                e["id"]
                for e in env.get("input_elements") or []
                if isinstance(e, dict) and e.get("id")
            ]
            selects = [
                e["id"]
                for e in env.get("select_elements") or []
                if isinstance(e, dict) and e.get("id")
            ]
            # Elements inside containers that register hover listeners —
            # hovering them may reveal flyout/dropdown content that is
            # invisible (and therefore absent) in this snapshot.
            hoverables = [e for e in env.get("hoverable_elements") or [] if e]
            action = await async_chat(
                [
                    {"role": "system", "content": _prompts.get("action")},
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "valid_targets": {
                                    "inputs": inputs,
                                    "clickable": clickables,
                                    "selects": selects,
                                    "hoverable": hoverables,
                                },
                                "persona": self.persona,
                                "intent": self.intent,
                                "plan": self.current_plan.content,
                                "next_step": self.current_plan.next_step,
                                "last_action_error": last_action_error,
                                "environment": env["html"][:self._MAX_HTML_CHARS],
                                "recent_memories": memories,
                            },
                            ensure_ascii=False,
                        ),
                    },
                ],
                # model="anthropic.claude-3-5-sonnet-20240620-v1:0",
                json_mode=True,
            )
        actions = json.loads(action)
        logger.info("actions: %s", actions)
        chosen = self._guard_terminate(actions["actions"][0])
        await self.memory.add_memory_piece(
            Action(chosen["description"], self.memory, json.dumps(chosen))
        )

        return chosen

    # Plan steps that legitimately end the session must say so explicitly
    # (the planning prompt's termination policy phrases them with these words).
    _EXPLICIT_STOP = re.compile(
        r"\b(terminat\w*|quit|stop|zako[ńn]cz\w*)\b"
        # "conclude/finish/end/complete the task|session|..." — anchored to the
        # session noun so actionable steps like "complete the purchase" or
        # "finish the checkout" are NOT treated as terminal.
        r"|\b(conclud\w+|finish\w*|end\w*|complet\w*|wrap\w*\s+up)\s+(the\s+)?(task|session|visit|goal|browsing)\b",
        re.IGNORECASE,
    )

    def _guard_terminate(self, action: dict) -> dict:
        """Block premature terminates: the action LLM may fall back to
        `terminate` when a plan step is passive ("review the results...")
        and nothing in the action space matches. Unless the step explicitly
        asks to stop, substitute a scroll so the agent keeps observing."""
        if action.get("action") != "terminate":
            return action
        step = (self.current_plan.next_step if self.current_plan else "") or ""
        if self._EXPLICIT_STOP.search(step):
            return action
        logger.warning(
            "Blocked premature terminate for non-terminal step %r — substituting scroll",
            step,
        )
        return {
            "action": "scroll",
            "direction": "down",
            "amount": 3,
            "description": action.get("description")
            or f"Scrolling down to review the page: {step}",
        }

    async def add_thought(self, thought):
        await self.memory.add_memory_piece(Thought(thought, self.memory))
