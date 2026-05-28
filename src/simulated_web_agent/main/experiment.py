import asyncio
import json
import logging
import pathlib
import traceback
import uuid
from datetime import datetime
from typing import Callable, Dict, List, Optional

from omegaconf import DictConfig

from ..agent import context, gpt
from ..executor.env import WebAgentEnv
from ..config import load_cfg
from .model import AgentPolicy  # noqa
from .trace_store import TraceStore

log = logging.getLogger("simulated_web_agent.main.experiment")
logging.basicConfig(level=logging.INFO)

_FONT_SIZE_JS = """
() => {
  const results = [];
  document.querySelectorAll('p, li, span, a, div, td, th, label, h1, h2, h3, h4, h5, h6').forEach(el => {
    if (!el.offsetParent) return;
    const size = parseFloat(window.getComputedStyle(el).fontSize);
    if (size > 0) results.push(size);
  });
  results.sort((a, b) => a - b);
  const seen = new Set();
  return results.filter(x => { const k = Math.round(x); if (seen.has(k)) return false; seen.add(k); return true; }).slice(0, 20);
}
"""


async def _run_for_persona_and_intent(
    cfg: DictConfig,
    persona_info: Dict,
    start_url: str,
    max_steps: int,
    wait_for_login: bool = False,
    env_setup_hook: Callable = None,
    env_wait_hook: Callable = None,
) -> pathlib.Path:
    persona = persona_info["persona"]
    intent = persona_info["intent"]
    log.info(
        f"\n=== persona (first 200 chars) ===\n{persona[:200]}...\n=== intent ===\n{intent}"
    )
    run_uid = uuid.uuid4().hex[:8]

    task_to_use = {
        "sites": ["shopping"],
        "task_id": 1,
        "require_login": False,
        "start_url": start_url,
        "intent": intent or "Interactive testing session",
    }

    run_name = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{uuid.uuid4().hex[:4]}"
    base_dir = pathlib.Path().resolve()
    store = TraceStore(base_dir / "runs" / run_name)
    store.write_basic_info(persona_info)
    context.run_path.set(store.trace_dir)
    steps_taken = 0

    async def before_action_hook():
        if cfg.environment.recording.enabled:
            return
        await env.page.screenshot(path=store.screenshot_path(steps_taken, full_page=True), full_page=True)
        await env.page.screenshot(path=store.screenshot_path(steps_taken))
        scroll_data = await env.page.evaluate(
            "() => ({ scrollTop: window.scrollY,"
            " pageHeight: document.documentElement.scrollHeight,"
            " viewportHeight: window.innerHeight })"
        )
        dpr = cfg.environment.browser.context_options.device_scale_factor
        store.scroll_top_path(steps_taken).write_text(
            json.dumps({
                "scroll_top": scroll_data["scrollTop"] * dpr,
                "page_height": scroll_data["pageHeight"] * dpr,
                "viewport_height": scroll_data["viewportHeight"] * dpr,
            })
        )
        try:
            # aria_snapshot() replaced page.accessibility.snapshot() in Playwright 1.49+
            a11y = await env.page.aria_snapshot()
            store.write_a11y_snapshot(steps_taken, a11y)
        except Exception as exc:
            log.warning("a11y snapshot failed at step %d: %s", steps_taken, exc)
        try:
            font_sizes = await env.page.evaluate(_FONT_SIZE_JS)
            store.write_font_sizes(steps_taken, font_sizes)
        except Exception as exc:
            log.warning("font size collection failed at step %d: %s", steps_taken, exc)

    env = WebAgentEnv(
        cfg.environment, before_action_hook=before_action_hook, wait_hook=env_wait_hook
    )

    log.info(f"[{run_uid}] env created")
    try:
        policy = AgentPolicy(persona, intent)
        print("setting up env with headless = " + str(cfg.environment.browser.launch_options.headless))
        await env.setup(task_to_use, headless=cfg.environment.browser.launch_options.headless)

        if wait_for_login:
            env.debug_pause()

        if env_setup_hook:
            await env_setup_hook(env)
        obs = await env.observation()

        log.info("Initial observation ready")

        action_trace = []
        while steps_taken < max_steps:
            store.append_observation(obs)
            if obs.get("tabs"):
                print("Current url:", obs["tabs"][0].get("url"))
            store.write_simp_html(steps_taken, obs["html"])
            store.write_raw_html(steps_taken, await env.page.content())

            action = await policy.forward(env)
            action_trace.append(action)
            store.write_action_trace(action_trace)
            store.write_observation_step_text(steps_taken, policy.agent.observation)
            store.write_memory_trace(policy.agent.memory.memories)

            print(f"Taking action {action}")
            print(f"Action: {steps_taken + 1} out of {max_steps}")
            obs = await env.step(action)
            steps_taken += 1

            if obs.get("terminated"):
                break

        log.info(
            f"Finished persona run: terminated={obs.get('terminated')}, "
            f"score={obs.get('score')}, steps={steps_taken}"
        )

        store.write_memory_trace_text(policy.get_formatted_memories())
        log.info(f"Saved memory trace to {store.trace_dir}")

    except Exception:
        err = traceback.format_exc()
        print(err)
        try:
            (store.trace_dir / "error.txt").write_text(err)
        except Exception:
            pass
    finally:
        try:
            log.info(f"[{run_uid}] closing env...")
            await asyncio.wait_for(asyncio.shield(env.close()), timeout=10)
            log.info(f"[{run_uid}] env.close() completed")
        except Exception as e:
            log.exception(f"[{run_uid}] env.close() raised: {e!r}")
    return store.trace_dir


async def experiment_async(
    agents: List[Dict[str, str]],
    start_url: str,
    max_steps: int,
    *,
    headless: bool = False,
    config_name: str = "base",
    config_path: str = ".",
    concurrency: int = 4,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> list:
    cfg = load_cfg(config_name=config_name)
    if concurrency:
        cfg.environment.browser.user_data_dir = None
    gpt.configure(cfg.llm_provider)
    print("llm provider: " + cfg.llm_provider)

    sem = asyncio.Semaphore(concurrency)
    total = len(agents)
    done = 0
    lock = asyncio.Lock()

    async def run_one(entry: Dict[str, str]):
        nonlocal done
        async with sem:
            trace_dir = await _run_for_persona_and_intent(
                cfg=cfg,
                persona_info=entry,
                start_url=start_url,
                max_steps=max_steps,
            )
        async with lock:
            done += 1
            if on_progress:
                try:
                    on_progress(done, total)
                except Exception:
                    pass
        return trace_dir

    tasks = [asyncio.create_task(run_one(e)) for e in agents]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            log.exception("A session failed", exc_info=r)
    return results
