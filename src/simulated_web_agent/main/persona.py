import asyncio
import logging
import random
import re
from typing import Any, Awaitable, Callable, Optional

from ..agent import gpt
from ..config import load_cfg

logger = logging.getLogger(__name__)


# --- utils -------------------------------------------------------------------
def parse_range(range_str: str) -> tuple[int, int]:
    m = re.match(r"(\d+)\s*-\s*(\d+)", range_str)
    if m:
        return int(m.group(1)), int(m.group(2))
    raise ValueError(f"Invalid range format: '{range_str}'")


def prepare_cumulative_distribution(ratio_dict: dict[str, float]):
    items = list(ratio_dict.items())
    total_ratio = sum(ratio_dict.values())
    cumulative, acc = [], 0.0
    for k, v in items:
        acc += v / total_ratio
        cumulative.append((acc, k))

    return cumulative


def sample_from_cumulative(cumulative):
    r = random.random()
    for prob, k in cumulative:
        if r <= prob:
            return k
    return cumulative[-1][1]  # rounding safety


def _prepare_demographics_cumulative(demographics: list[dict[str, Any]]):
    prepped: list[tuple[str, list[tuple[float, str]]]] = []
    for dim in demographics:
        name = dim.get("name")
        choices = dim.get("choices", [])
        # normalize probabilities
        total = sum(float(c.get("weight", 0.0)) for c in choices) or 1.0
        acc = 0.0
        cumulative: list[tuple[float, str]] = []
        for c in choices:
            p = float(c.get("weight", 0.0)) / total
            acc += p
            cumulative.append((acc, str(c.get("name"))))
        if cumulative:
            prepped.append((str(name), cumulative))

    return prepped




async def _generate_one(
    demographics_cum: list[tuple[str, list[tuple[float, str]]]],
    general_intent: str,
    example_persona: str,
    previous_personas: list[str],
    chat_fn: Callable[..., Awaitable[str]] = gpt.async_chat,
    rng_seed: int | None = None,
    sem: asyncio.Semaphore | None = None,
):
    r = random.Random(rng_seed) if rng_seed is not None else random.Random()
    # sample values for each demographic dimension
    sampled: dict[str, str] = {}
    for attr_name, cum in demographics_cum:
        choice = sample_from_cumulative(cum)
        sampled[attr_name] = str(choice)

    # for diversity: pick some previous personas as examples
    # and ensure the generated persona deviates from those
    if previous_personas:
        num_examples = min(len(previous_personas), 3)
        examples = random.sample(previous_personas, num_examples)
        example_text = "\n\n".join(examples)
    else:
        example_text = example_persona

    persona_msg = [
        {
            "role": "system",
            "content": f"""You are a helpful assistant that generates diverse personas.
                        Examples:
                        {example_text}
                        """,
        },
        {
            "role": "user",
            "content": (
                "Generate a persona using the above examples. The persona should be different from previous personas to ensure diversity.\n"
                + "The persona should:\n"
                + "\n".join([f"- have the {k} of {v}" for k, v in sampled.items()])
                + "\n- include a 'Miejsce zamieszkania:' line in the Demographics"
                  " section with a SPECIFIC fictional home address: street and"
                  " number, postal code, and city (e.g. 'ul. Długa 14/3, 31-147"
                  " Kraków'). It is used to locate the nearest store, so a bare"
                  " city name is not enough. Invent a plausible address; never"
                  " use a real person's."
                + "\nProvide the persona in the same format as the examples."
                + "\nOnly output the persona, no other text."
            ),
        },
    ]

    # intent_msg = lambda persona: [
    #     {
    #         "role": "system",
    #         "content": "You output only a single, specific, actionable intent, no extra text.",
    #     },
    #     {
    #         "role": "user",
    #         "content": (
    #             f"The persona is:\n{persona}\n\n"
    #             f"Based on the general intent '{general_intent}', output ONE concrete, executable intention "
    #             f"this persona would take (e.g., buy/compare/book/choose), with enough detail to act on a website."
    #         ),
    #     },
    # ]

    async def call_chat(messages):
        if sem is None:
            return (await chat_fn(messages=messages)).strip()
        async with sem:
            return (await chat_fn(messages=messages)).strip()

    persona = await call_chat(persona_msg)
    # intent = await call_chat(intent_msg(persona))

    # add this generated persona to the example pool
    previous_personas.append(persona)

    return {"persona": persona, "intent": general_intent, **sampled}


async def generate_example_persona(
    general_intent: str,
    start_url: str = "",
    demographics: Optional[list[dict[str, Any]]] = None,
    site_context: str = "",
    chat_fn: Callable[..., Awaitable[str]] = gpt.async_chat,
) -> str:
    """Generate ONE example persona tailored to the audit context, in the config format.

    Used by the wizard's "generate with AI" button. Returns the freeform persona text
    that populates the example_persona field.
    """
    cfg = load_cfg()
    gpt.configure(cfg.llm_provider)
    base_example = cfg.example_persona

    dims: list[str] = []
    for d in demographics or []:
        names = [str(c.get("name")) for c in d.get("choices", []) if c.get("name")]
        if d.get("name") and names:
            dims.append(f"- {d['name']}: e.g. {', '.join(names[:6])}")
    dims_text = "\n".join(dims) if dims else "(no demographic dimensions specified)"

    system = (
        "You generate ONE realistic, richly-detailed example persona for UX research. "
        "Follow EXACTLY the format, section headings, and language of this example:\n\n"
        f"{base_example}"
    )
    user = (
        f"Website under test: {start_url or 'N/A'}\n"
        f"Task the persona will attempt: {general_intent or 'N/A'}\n"
        + (f"Site context: {site_context}\n" if site_context else "")
        + f"\nDemographic dimensions to draw plausible values from:\n{dims_text}\n\n"
        "Write ONE persona tailored to this website and task. Keep the same section "
        "headings and the same language as the example.\n"
        "REQUIRED: the Demographics section must contain a line "
        "'Miejsce zamieszkania:' with a SPECIFIC fictional home address — street "
        "and number, postal code, and city (e.g. 'ul. Długa 14/3, 31-147 Kraków'). "
        "The agent uses it to find the nearest store, so a bare city name is not "
        "enough. Use a plausible, invented address — never a real person's.\n"
        "Output ONLY the persona text."
    )

    persona = await chat_fn(
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
    )
    return persona.strip()


async def generate_audit_settings(
    start_url: str,
    chat_fn: Callable[..., Awaitable[str]] = gpt.async_chat,
) -> dict:
    """Given only a URL, generate sensible wizard defaults for a UX audit run."""
    cfg = load_cfg()
    gpt.configure(cfg.llm_provider)

    system = (
        "You are a UX research expert. Given a website URL, generate sensible audit settings. "
        "Respond with ONLY valid JSON matching this exact schema:\n"
        "{\n"
        '  "general_intent": "string — one concrete task a user would try on this site",\n'
        '  "num_subjects": integer between 2 and 5,\n'
        '  "max_steps": integer between 15 and 30,\n'
        '  "demographics": [\n'
        '    { "name": "string", "choices": [{ "name": "string", "weight": number }, ...] },\n'
        "    ...\n"
        "  ]\n"
        "}\n"
        "demographics should have 2-4 dimensions relevant to the site (e.g. Age, Gender, Experience). "
        "Each dimension should have 2-4 choices with weights that sum to roughly equal proportions. "
        "Write the general_intent in the same language as the website (Polish for .pl domains)."
    )
    user = f"Website URL: {start_url}"

    raw = await chat_fn(messages=[
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ])

    import json as _json
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return _json.loads(raw.strip())


async def generate_objectives(
    example_objective: str,
    n: int,
    start_url: str = "",
    general_intent: str = "",
    chat_fn: Callable[..., Awaitable[str]] = gpt.async_chat,
    max_concurrency: int = 8,
) -> list[str]:
    """Generate N distinct objectives derived from an example objective.

    Each objective is meaningfully different from the others so concurrent agents
    explore different user journeys on the same site.
    """
    if n <= 0:
        return []
    if n == 1:
        return [example_objective]

    sem = asyncio.Semaphore(max_concurrency)
    generated: list[str] = []
    lock = asyncio.Lock()

    async def _one(index: int) -> str:
        system = (
            "You generate ONE concise, realistic user objective for a UX research agent "
            "that will autonomously browse a website. "
            "The objective must be a single actionable task a real user would attempt "
            "(e.g. 'Find and purchase a winter jacket under 200 PLN', "
            "'Compare contract plans and identify the cheapest option for light users'). "
            "Output ONLY the objective text — no numbering, no extra commentary."
        )
        async with lock:
            seen = list(generated)

        diversity_note = (
            f"\n\nAlready generated objectives (yours must be clearly different):\n"
            + "\n".join(f"- {o}" for o in seen)
            if seen else ""
        )

        user = (
            f"Website under test: {start_url or 'N/A'}\n"
            f"Example objective to use as style/format reference: {example_objective}\n"
            + (f"General research intent: {general_intent}\n" if general_intent else "")
            + diversity_note
            + "\n\nWrite ONE distinct objective for this website. "
            "Same style and language as the example. Output ONLY the objective."
        )

        async with sem:
            result = (await chat_fn(messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ])).strip()

        async with lock:
            generated.append(result)
        return result

    tasks = [asyncio.create_task(_one(i)) for i in range(n)]
    return await asyncio.gather(*tasks)


async def generate_personas(
    demographics: list[dict[str, Any]],
    general_intent: str,
    n: int = 1,
    chat_fn: Callable[..., Awaitable[str]] = gpt.async_chat,
    max_concurrency: int = 8,
    rng_seed: int | None = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
    example_text: Optional[str] = None,
) -> list[dict[str, str]]:
    """
    Concurrent persona generation using asyncio and async_chat.
    """
    demographics_cum = _prepare_demographics_cumulative(demographics)

    sem = asyncio.Semaphore(max_concurrency) if max_concurrency else None

    # base example, default to the one in config if not provided
    cfg = load_cfg()
    base_example = cfg.example_persona
    gpt.configure(cfg.llm_provider)

    if example_text and example_text != "":
        logger.info("Using custom persona example: " + example_text[:100] + "...")
        base_example = example_text
    else:
        logger.info(
            "No custom persona example provided. Using the default example to start..."
        )

    # store generated personas to use as example, ensuring diversity
    previous_personas = []

    # Wrap each task with its index so we can restore order later.
    async def _one_indexed(idx: int, seed_i: int | None):
        res = await _generate_one(
            demographics_cum,
            general_intent,
            base_example,
            previous_personas,
            chat_fn,
            seed_i,
            sem,
        )
        return idx, res

    tasks = []
    for i in range(n):
        seed_i = (rng_seed + i) if rng_seed is not None else None
        tasks.append(asyncio.create_task(_one_indexed(i, seed_i)))

    results: list[dict[str, str]] = [None] * n  # type: ignore
    done = 0

    # As each task finishes, record its result and ping progress.
    for fut in asyncio.as_completed(tasks):
        idx, res = await fut
        results[idx] = res
        done += 1
        if on_progress:
            try:
                on_progress(done, n)
            except Exception:
                pass  # progress should never crash the job

    return results
