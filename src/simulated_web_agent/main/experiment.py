import asyncio
import json
import logging
import pathlib
import traceback
import uuid
from collections import Counter
from datetime import datetime
from typing import Callable, Dict, List, Optional

from omegaconf import DictConfig

from ..agent import context, gpt
from ..agent.memory import Thought
from ..executor.env import WebAgentEnv
from ..config import load_cfg
from .abort import abandon_threshold
from .model import AgentPolicy  # noqa
from .trace_store import TraceStore

log = logging.getLogger("simulated_web_agent.main.experiment")
logging.basicConfig(level=logging.INFO)

_RESERVED_KEYS = {"persona", "intent", "site_context", "analytics_context"}


def _parse_action_dict(action_json: str) -> dict:
    """Parse an action-trace JSON string into a dict ({} on any failure)."""
    try:
        a = json.loads(action_json)
        a = a[0] if isinstance(a, list) and a else a
        return a if isinstance(a, dict) else {}
    except Exception:
        return {}


def _revisit_warning(prior_clicks: int, target: str) -> tuple[Optional[str], Optional[str]]:
    """Warnings for re-clicking a target already clicked this session.

    The consecutive-repeat guard misses A-B-A-B loops (open product, go back,
    open the same product again), which wasted ~20% of steps in real runs.
    Returns (memory_thought, error_message); either may be None. Escalation is
    advisory only — repeated clicks on navigation or "load more" are legitimate.
    """
    if prior_clicks < 1:
        return None, None
    thought = (
        f"I already clicked '{target}' earlier in this session. If this is deliberate "
        "navigation (returning to a listing, loading more products), that's fine — "
        "but if I meant to inspect a new product, I should pick one I have not opened yet."
    )
    if prior_clicks >= 2:
        return thought, (
            f"You have clicked '{target}' {prior_clicks + 1} times this session. "
            "Do not open it again — choose a product or link you have not visited yet."
        )
    return thought, None


# Demographic keys that carry where the persona lives. Without one, a task like
# "check availability in a nearby store" has no anchor — the agent invents a
# city (observed: Julia typed "Warszawa", later "Kraków", arbitrarily).
_LOCATION_KEYS = ("miejsce zamieszkania", "miasto", "lokalizacja", "location", "city", "region")

# Fallbacks parsed straight from the persona description.
_LOCATION_PATTERNS = (
    r"Miejsce zamieszkania:\s*([^\n]+)",
    r"Mieszka w\s+([^\n,.;]+)",
    r"Lokalizacja:\s*([^\n]+)",
)


def extract_home_location(persona_info: dict) -> str | None:
    """Where the persona lives — explicit field, a location-ish demographic, or
    a line in the persona text. Returns None when genuinely unknown (never
    guesses a city)."""
    import re

    explicit = persona_info.get("home_location")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    for k, v in persona_info.items():
        if k in _RESERVED_KEYS or not isinstance(v, str) or not v.strip():
            continue
        if any(loc in k.lower() for loc in _LOCATION_KEYS):
            return v.strip()

    raw = persona_info.get("persona", "") or ""
    for pat in _LOCATION_PATTERNS:
        m = re.search(pat, raw, re.IGNORECASE)
        if m and m.group(1).strip():
            return m.group(1).strip()
    return None


def persona_with_location(persona_text: str, home_location: str | None) -> str:
    """Append an explicit home-location instruction to the persona the agent sees.

    The agent only ever reads the freeform persona text, and a demographic row
    buried in prose is easy to miss — in run 2026-07-09_11-49-36 the personas had
    no location at all and invented one ("Warszawa", then "Kraków"). Stating it
    plainly, with the rule to reuse it for any "nearby" lookup, removes the guess.
    Returns the text unchanged when the location is unknown.
    """
    if not home_location or not str(home_location).strip():
        return persona_text
    loc = str(home_location).strip()
    return (
        f"{persona_text}\n\n"
        f"WAŻNE — Twoje miejsce zamieszkania: {loc}.\n"
        f"Gdy szukasz sklepu, punktu odbioru lub sprawdzasz dostępność „w pobliżu”, "
        f"używaj TEJ lokalizacji ({loc}). Nie wymyślaj innego miasta."
    )


def build_persona_card(persona_info: dict) -> dict:
    """Build a structured persona card from the raw persona_info dict.

    Extracts a display name from the first "Persona: <Name>" line and separates
    the freeform demographic fields from the reserved pipeline keys.
    """
    import re

    raw = persona_info.get("persona", "")
    match = re.search(r"^Persona:\s*(\S+)", raw, re.MULTILINE | re.IGNORECASE)
    name = match.group(1).rstrip(".,;:") if match else "Unknown"

    demographics = {
        k: v for k, v in persona_info.items() if k not in _RESERVED_KEYS
    }

    # §2.1 structured schema fields. Evidence status defaults to "synthetic"
    # unless the persona was built from declared evidence sources; behavioral
    # fields carry through if the generator provided them, else safe defaults.
    lower = raw.lower()
    if any(w in lower for w in ("niecierpliw", "impatient", "w pośpiechu", "presja czasu")):
        patience = "low"
    elif any(w in lower for w in ("cierpliw", "spokojn", "dokładn", "metodyczn")):
        patience = "high"
    else:
        patience = "medium"

    evidence_sources = persona_info.get("evidence_sources") or []
    status = "evidence_based" if evidence_sources else "synthetic"

    behavioral_profile = persona_info.get("behavioral_profile") or {"patience": patience}

    return {
        "name": name,
        "description": raw,
        "objective": persona_info.get("intent", ""),
        "demographics": demographics,
        "home_location": extract_home_location(persona_info),
        "site_context": persona_info.get("site_context", ""),
        "status": status,
        "evidence_sources": evidence_sources,
        "behavioral_profile": behavioral_profile,
        "barriers": persona_info.get("barriers") or [],
        "mental_models": persona_info.get("mental_models") or [],
        "evaluation_focus": persona_info.get("evaluation_focus") or [],
        "abort_max_confusing_steps": persona_info.get("abort_max_confusing_steps"),
    }


_COOKIE_SELECTORS = [
    # Generic "accept all" patterns (text-based, case-insensitive)
    "button:has-text('Accept all')",
    "button:has-text('Zaakceptuj wszystkie')",
    "button:has-text('Zaakceptuj')",
    "button:has-text('Akceptuj')",
    "button:has-text('Accept cookies')",
    "button:has-text('Accept')",
    "button:has-text('Agree')",
    "button:has-text('OK')",
    "button:has-text('Zgoda')",
    "button:has-text('Rozumiem')",
    "button:has-text('Zezwól na wszystkie')",
    "button:has-text('Zezwól')",
    # Common id/class patterns
    "#onetrust-accept-btn-handler",
    "#accept-all-cookies",
    "[id*='accept'][id*='cookie']",
    "[class*='accept-all']",
    "[class*='cookie-accept']",
    "[data-cookiebanner='accept_button']",
    "[aria-label*='Accept']",
    "[aria-label*='Zaakceptuj']",
]


async def dismiss_cookie_banners(page) -> bool:
    """Try each known cookie-accept selector; click the first one found. Returns True if clicked."""
    for sel in _COOKIE_SELECTORS:
        try:
            locator = page.locator(sel).first
            if await locator.count() > 0 and await locator.is_visible():
                await locator.click(timeout=2000)
                log.info("Cookie banner dismissed via: %s", sel)
                await asyncio.sleep(0.5)
                return True
        except Exception:
            continue
    return False


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
    runs_dir: Optional[pathlib.Path] = None,
    wait_for_login: bool = False,
    env_setup_hook: Callable = None,
    env_wait_hook: Callable = None,
) -> pathlib.Path:
    persona_card = build_persona_card(persona_info)
    # The agent reads only the persona text — state the home location explicitly
    # so "nearby" lookups are anchored instead of guessed.
    persona = persona_with_location(persona_info["persona"], persona_card.get("home_location"))
    intent = persona_info["intent"]
    log.info(
        f"\n=== persona (first 200 chars) ===\n{persona[:200]}...\n=== intent ===\n{intent}"
    )
    if persona_card.get("home_location"):
        log.info("Persona home location: %s", persona_card["home_location"])
    run_uid = uuid.uuid4().hex[:8]

    task_to_use = {
        "sites": ["shopping"],
        "task_id": 1,
        "require_login": False,
        "start_url": start_url,
        "intent": intent or "Interactive testing session",
    }

    run_name = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{uuid.uuid4().hex[:4]}"
    if runs_dir is None:
        runs_dir = pathlib.Path().resolve() / "runs"
    store = TraceStore(runs_dir / run_name)
    store.write_basic_info(persona_info)
    store.write_persona_card(persona_card)
    context.run_path.set(store.trace_dir)
    steps_taken = 0

    async def before_action_hook():
        if cfg.environment.recording.enabled:
            return
        await dismiss_cookie_banners(env.page)
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

        await dismiss_cookie_banners(env.page)

        obs = await env.observation()

        log.info("Initial observation ready")

        def _action_key(action_json: str) -> str:
            """Action identity ignoring the freeform description."""
            try:
                a = json.loads(action_json)
                a = a[0] if isinstance(a, list) and a else a
                a.pop("description", None)
                return json.dumps(a, sort_keys=True)
            except Exception:
                return action_json

        def _current_url(o: dict) -> str:
            tabs = o.get("tabs") or []
            return tabs[0].get("url", "") if tabs else ""

        action_trace = []
        prev_action_key = None
        repeat_count = 0
        click_counts: Counter = Counter()
        fail_counts: Counter = Counter()
        abandon_at = abandon_threshold(store.read_persona_card())
        abandoned = False
        while steps_taken < max_steps:
            store.append_observation(obs)
            if obs.get("tabs"):
                print("Current url:", obs["tabs"][0].get("url"))
            store.write_simp_html(steps_taken, obs["html"])
            store.write_raw_html(steps_taken, await env.page.content())

            # Step-budget awareness: the planner reads this to pull
            # intent-critical steps forward before the session runs out.
            policy.agent.step_budget = {
                "steps_used": steps_taken,
                "max_steps": max_steps,
                "steps_remaining": max_steps - steps_taken,
            }

            url_before = _current_url(obs)
            action = await policy.forward(env, obs)

            # Hard loop-breaker: advisory warnings (memory thoughts + error
            # channel) fire after the 2nd repeat, but the model can still keep
            # following its plan. If it picks the SAME action a 3rd time, we
            # physically override it with a 'back' navigation to force a new
            # page state — a stuck agent burns 5-10 minutes per wasted step.
            if repeat_count >= 2 and _action_key(action) == prev_action_key:
                log.warning("Forcing escape from action loop: %s", prev_action_key)
                action = json.dumps({
                    "action": "back",
                    "description": "Ta metoda nie działała mimo kilku prób — wracam, by spróbować innej drogi.",
                })
                await policy.agent.memory.add_memory_piece(
                    Thought(
                        "I tried the same action several times and it did not work. "
                        "I went back to the previous page to find a different way.",
                        policy.agent.memory,
                    )
                )
                repeat_count = 0

            # Failed-action circuit breaker: the consecutive-repeat guard is
            # defeated when the model alternates (click X fails → scroll →
            # click X fails → goto → click X ...). Once the SAME action has
            # already failed twice at execution (element unclickable, invalid
            # URL), retrying is hopeless — block it and force a new route.
            intended_key = _action_key(action)
            if fail_counts[intended_key] >= 2:
                log.warning(
                    "Blocking action that failed %d times: %s",
                    fail_counts[intended_key], intended_key,
                )
                blocked_desc = _parse_action_dict(action).get("description", "")
                await policy.agent.memory.add_memory_piece(
                    Thought(
                        "I already tried this several times and it kept failing: "
                        f"{blocked_desc or intended_key}. That element or route is "
                        "broken — a UX finding worth noting. I must NOT try it "
                        "again; I'll go back and take a completely different path "
                        "toward my goal.",
                        policy.agent.memory,
                    )
                )
                action = json.dumps({
                    "action": "back",
                    "description": "Ten element nie działał mimo kilku prób — wracam i wybieram inną drogę.",
                })

            # Revisit guard: catches A-B-A-B product loops the consecutive
            # repeat guard can't see. Advisory thought on the 2nd click of a
            # target, error-channel escalation from the 3rd.
            revisit_error = None
            parsed_action = _parse_action_dict(action)
            if parsed_action.get("action") == "click" and parsed_action.get("target"):
                _target = str(parsed_action["target"])
                revisit_thought, revisit_error = _revisit_warning(
                    click_counts[_target], _target
                )
                if revisit_thought:
                    await policy.agent.memory.add_memory_piece(
                        Thought(revisit_thought, policy.agent.memory)
                    )
                click_counts[_target] += 1

            action_trace.append(action)
            store.write_action_trace(action_trace)
            store.write_observation_step_text(steps_taken, policy.agent.observation)
            store.write_memory_trace(policy.agent.memory.memories)

            print(f"Taking action {action}")
            print(f"Action: {steps_taken + 1} out of {max_steps}")
            obs = await env.step(action)
            steps_taken += 1

            # Count execution failures per action identity (feeds the
            # circuit breaker). Read before the guards below inject their
            # own advisory messages into obs["error"].
            if obs.get("error"):
                fail_counts[_action_key(action)] += 1

            # Repeat-action guard: same action + page unchanged = the agent is
            # looping. Warn it via memory; escalate via the error channel it
            # already reads (observation["error"] -> last_action_error).
            action_key = _action_key(action)
            if action_key == prev_action_key and _current_url(obs) == url_before:
                repeat_count += 1
                warning = (
                    "I already tried exactly this action and the page did not change. "
                    "I must try a different element or a different approach."
                )
                await policy.agent.memory.add_memory_piece(
                    Thought(warning, policy.agent.memory)
                )
                if repeat_count >= 2 and not obs.get("error"):
                    obs["error"] = (
                        f"Repeated identical action {repeat_count + 1} times with no page change: "
                        f"{action_key}. Choose a different element or strategy."
                    )
                # Persona drop-off: after this persona's patience runs out on a
                # stuck loop, it abandons the task (a realistic UX signal, not a
                # maxed-out step budget).
                if repeat_count >= abandon_at:
                    abandoned = True
                    log.info(
                        "Persona abandoned after %d stuck steps (threshold %d): %s",
                        repeat_count, abandon_at, action_key,
                    )
                    break
            else:
                repeat_count = 0
            prev_action_key = action_key

            if revisit_error and not obs.get("error"):
                obs["error"] = revisit_error

            if obs.get("terminated"):
                break

        log.info(
            f"Finished persona run: terminated={obs.get('terminated')}, "
            f"score={obs.get('score')}, steps={steps_taken}"
        )

        store.write_session_result({
            "steps_taken": steps_taken,
            "max_steps": max_steps,
            "terminated": bool(obs.get("terminated")),
            "ended_reason": (
                "persona_abandoned" if abandoned
                else "terminated" if obs.get("terminated")
                else "max_steps"
            ),
        })

        store.write_memory_trace_text(policy.get_formatted_memories())
        log.info(f"Saved memory trace to {store.trace_dir}")

    except Exception:
        err = traceback.format_exc()
        print(err)
        try:
            (store.trace_dir / "error.txt").write_text(err)
        except Exception:
            pass
        try:
            store.write_session_result({
                "steps_taken": steps_taken,
                "max_steps": max_steps,
                "terminated": False,
                "ended_reason": "error",
            })
        except Exception:
            pass
    finally:
        try:
            await policy.close()  # cancel the background reflect/wonder loop
        except Exception:
            pass
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
    runs_dir: Optional[pathlib.Path] = None,
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
                runs_dir=runs_dir,
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
