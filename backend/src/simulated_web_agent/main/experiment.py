import asyncio
import json
import logging
import os
import pathlib
import time
import traceback
import uuid
from collections import Counter
from datetime import datetime
from typing import Callable, Dict, List, Optional

from omegaconf import DictConfig

from ..agent import context, gpt
from ..agent.memory import Thought
from . import cancellation
from ..executor.env import WebAgentEnv
from ..config import load_cfg
from .abort import abandon_threshold
from .model import AgentPolicy  # noqa
from .trace_store import TraceStore, parse_action as _parse_action

log = logging.getLogger("simulated_web_agent.main.experiment")
logging.basicConfig(level=logging.INFO)

_RESERVED_KEYS = {"persona", "intent", "site_context", "analytics_context"}




# Controls whose whole purpose is to be pressed again: a wizard's Next, "load
# more", pagination. The revisit guard must never forbid these.
#
# Found in the hyundai re-run: the configurator is a step wizard, so "Następny
# krok" is the correct action on every step. The guard fired on its third use
# with "Do not open it again — choose a product or link you have not visited
# yet", the agent dutifully navigated around it, and the report told the client
# the button was inactive and required a workaround. The reviewer would have
# falsified that in one click, exactly as she falsified the cookie banner.
#
# Matched against the parser's semantic id, which mangles Polish diacritics
# ("następny_krok" arrives as "nast_pny_krok"), so the stems are chosen to
# survive that and the id is stripped of separators before comparison.
_PROGRESSION_STEMS = (
    "nast", "dalej", "kontynu", "przejdzdalej",
    "next", "continue", "proceed", "forward",
    "loadmore", "showmore", "wiecej", "wicej", "pokazwi",
    "pagination", "nextpage", "strona",
)


def _is_progression_control(target: str) -> bool:
    flat = "".join(ch for ch in str(target or "").lower() if ch.isalnum())
    return any(stem in flat for stem in _PROGRESSION_STEMS)


def _revisit_warning(prior_clicks: int, target: str) -> tuple[Optional[str], Optional[str]]:
    """Warnings for re-clicking a target already clicked this session.

    The consecutive-repeat guard misses A-B-A-B loops (open product, go back,
    open the same product again), which wasted ~20% of steps in real runs.
    Returns (memory_thought, error_message); either may be None. Escalation is
    advisory only — repeated clicks on navigation or "load more" are legitimate.
    """
    if prior_clicks < 1:
        return None, None
    # Pressing "Next" again is the task, not a loop.
    if _is_progression_control(target):
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


def _strip_markdown_noise(value: str) -> str:
    """Strip stray markdown left over when a _LOCATION_PATTERNS regex captures
    across a bold marker. Observed live: the LLM wrote
    '*   **Miejsce zamieszkania:** ul. Legnicka 78/5, 54-206 Wrocław' instead of
    the example's plain 'Miejsce zamieszkania: ...'. The closing ** sits right
    after the colon with no whitespace, so a plain \\s* capture pulled it into
    the value — the agent's prompt then opened with a stray asterisk."""
    import re as _re

    return _re.sub(r"^[\*`\s]+|[\*`\s]+$", "", value)


def _looks_like_full_address(value: str) -> bool:
    """True for a street address (postal code or "ul."/"al." prefix), false for
    a bare city. Mirrors persona.py's twin — kept local since the two modules
    are otherwise decoupled and this is a two-line check."""
    import re

    v = value.strip().lower()
    return bool(re.search(r"\d{2}-\d{3}", v)) or v.startswith(("ul.", "ul ", "al.", "al "))


def extract_home_location(persona_info: dict) -> str | None:
    """Where the persona lives — explicit field, a location-ish demographic, or
    a line in the persona text. Returns None when genuinely unknown (never
    guesses a city).

    The "Miejsce zamieszkania" demographic may hold just a city (the wizard's
    weighted-choice UI does, by design — see persona.py's _looks_like_full_address).
    In that case persona.py instructs the LLM to invent the specific street
    address inside the generated persona TEXT instead. So when the demographic
    is a bare city and the text has a fuller address for the same field, the
    text wins — a bare city cannot locate the nearest store.
    """
    import re

    explicit = persona_info.get("home_location")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()

    demo_val = None
    for k, v in persona_info.items():
        if k in _RESERVED_KEYS or not isinstance(v, str) or not v.strip():
            continue
        if any(loc in k.lower() for loc in _LOCATION_KEYS):
            demo_val = v.strip()
            break

    raw = persona_info.get("persona", "") or ""
    text_val = None
    for pat in _LOCATION_PATTERNS:
        m = re.search(pat, raw, re.IGNORECASE)
        if m and m.group(1).strip():
            text_val = _strip_markdown_noise(m.group(1))
            break

    if text_val and _looks_like_full_address(text_val) and not (
        demo_val and _looks_like_full_address(demo_val)
    ):
        return text_val
    return demo_val or text_val


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
        f"używaj TEJ lokalizacji. W wyszukiwarkę sklepów wpisz kod pocztowy lub "
        f"miasto z tego adresu, a następnie wybierz sklep najbliższy temu adresowi. "
        f"Nie wymyślaj innego miasta ani adresu."
    )


def _exception_summary(exc: BaseException) -> str:
    """Type + first line of the message — the part that identifies the fault.

    format_exception_only returns the whole message as one string, newlines and
    all, so it has to be split again: Playwright appends a multi-line call log
    that would otherwise fill the 500-character field with the least useful part
    of it. The full traceback is kept in error.txt regardless.
    """
    rendered = "".join(traceback.format_exception_only(type(exc), exc)).strip()
    first = rendered.splitlines()[0].strip() if rendered else ""
    return first or repr(exc)


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


# Consent platforms, by their own stable accept-button ids. Tried first and
# alone where they match: a CMP that is present is authoritative, so there is no
# reason to fall through to guesswork on the same page.
_CMP_SELECTORS = [
    "#onetrust-accept-btn-handler",                       # OneTrust
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",  # Cookiebot
    "#CybotCookiebotDialogBodyButtonAccept",              # Cookiebot (older)
    "button#didomi-notice-agree-button",                  # Didomi
    ".cky-btn-accept",                                    # CookieYes
    "#cookiescript_accept",                               # CookieScript
    "button[mode='primary'][aria-label*='Zgadzam']",      # IAB TCF (PL)
    "[data-testid='uc-accept-all-button']",               # Usercentrics
    "#accept-all-cookies",
    "[data-cookiebanner='accept_button']",
]

# Fallback button labels, matched EXACTLY (anchored, case-insensitive).
#
# These used to be `button:has-text('OK')` and friends. Playwright's has-text is
# a case-insensitive *substring* match, so 'OK' also matched "Pokaż",
# "Zablokuj" and "Sklepy w okolicy", and 'Accept' matched anything containing
# it. Because this ran before every single step, the harness was clicking
# unrelated content buttons throughout the session and the agent then perceived
# a page it had never navigated to — reported, of course, as a site defect.
_CONSENT_LABELS = [
    "Zaakceptuj wszystkie", "Zaakceptuj wszystko", "Akceptuj wszystkie",
    "Zezwól na wszystkie", "Zaakceptuj", "Akceptuj", "Zgadzam się", "Zgoda",
    "Rozumiem", "Accept all", "Accept all cookies", "Accept cookies",
    "Allow all", "I agree", "Agree", "Got it",
]

# Containers a consent notice actually lives in. The label search is scoped to
# these, never the whole page, so an exact-matching "Zgoda" button elsewhere in
# the document cannot be mistaken for consent.
_CONSENT_CONTAINERS = [
    "[id*='onetrust']", "[id*='cookie' i]", "[class*='cookie' i]",
    "[id*='consent' i]", "[class*='consent' i]", "[id*='gdpr' i]",
    "[class*='gdpr' i]", "[id*='rodo' i]", "[aria-label*='cookie' i]",
    "dialog[open]", "[role='dialog']", "[role='alertdialog']",
]


# A notice counts as present only if an *acceptance control* is actually on
# screen — not merely that some element mentions cookies.
#
# Matching a container alone (`[id*='cookie']`) was wrong in both directions:
# on pepco.pl a footer cookie-policy link made it report a banner that was not
# there, which recorded a false "consent stuck" and would have suppressed every
# finding from that step onward. One JS pass is also far cheaper than ~190
# locator round-trips, which matters because this runs before every step.
_CONSENT_PRESENT_JS = """
(labels) => {
  const CMP = [
    '#onetrust-accept-btn-handler',
    '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll',
    '#CybotCookiebotDialogBodyButtonAccept',
    'button#didomi-notice-agree-button',
    '.cky-btn-accept', '#cookiescript_accept',
    "[data-testid='uc-accept-all-button']",
    '#accept-all-cookies', "[data-cookiebanner='accept_button']",
  ];
  const visible = el => !!(el && el.offsetParent);
  for (const s of CMP) {
    try { if (visible(document.querySelector(s))) return true; } catch (_) {}
  }
  const CONSENTISH = /cookie|consent|gdpr|rodo|onetrust|didomi|usercentrics|cookiebot/i;
  const norm = t => (t || '').trim().toLowerCase().replace(/\\s+/g, ' ');
  const wanted = new Set(labels.map(norm));
  for (const el of document.querySelectorAll('button,[role=button],a[role=button]')) {
    if (!visible(el) || !wanted.has(norm(el.innerText))) continue;
    let p = el, depth = 0;
    while (p && depth < 8) {
      const sig = (p.id || '') + ' ' +
                  (typeof p.className === 'string' ? p.className : '');
      const role = p.getAttribute ? p.getAttribute('role') : null;
      if (CONSENTISH.test(sig) || role === 'dialog' || role === 'alertdialog') return true;
      p = p.parentElement; depth++;
    }
  }
  return false;
}
"""


def _consent_unresolved(states: dict) -> bool:
    """Is the most recent recorded consent state a notice left on screen?"""
    if not states:
        return False
    last = states[max(states)]
    return bool(isinstance(last, dict) and last.get("attempted") and not last.get("dismissed"))


async def consent_present(page) -> bool:
    """Is an acceptance control for a consent notice currently on screen?

    Gate for re-running dismissal mid-session. Without it the harness clicked
    its way around every page on every step; with it, nothing happens unless a
    notice actually reappeared — which does happen after some navigations.
    """
    try:
        return bool(await page.evaluate(_CONSENT_PRESENT_JS, list(_CONSENT_LABELS)))
    except Exception:
        return False


async def dismiss_cookie_banners(page) -> dict:
    """Accept a consent notice if one is present.

    Returns a record of what happened — {attempted, dismissed, selector} —
    rather than a bare bool. The outcome has to be written to the trace: when
    consent is *not* resolved every later interaction may be blocked by the
    overlay, and attribution.py needs to know that to keep the resulting
    "defects" out of the report. Previously this returned False and told
    nobody.
    """
    result = {"attempted": False, "dismissed": False, "selector": None}

    # CMPs are injected by a late script, so give the common ones a moment to
    # appear before concluding there is no banner. Cheap when there is none:
    # this waits only for the first match, not for each selector in turn.
    try:
        await page.wait_for_selector(", ".join(_CMP_SELECTORS), timeout=3000, state="attached")
    except Exception:
        pass

    # 1. A known CMP is authoritative.
    #
    # Presence in the DOM is enough to commit to clicking: Playwright's click
    # already auto-waits for actionability. The previous `and is_visible()`
    # gate was an *instant* check that raced the CMP's own fade-in — on
    # hyundai.pl it returned False for a perfectly dismissable OneTrust banner,
    # so the overlay stayed up and intercepted every later click in the
    # session. That is the origin of the "cookie defect" in the review.
    for sel in _CMP_SELECTORS:
        try:
            loc = page.locator(sel).first
            if await loc.count() == 0:
                continue
            result["attempted"] = True
            await loc.click(timeout=5000)
            result["dismissed"] = True
            result["selector"] = sel
            log.info("Consent dismissed via CMP selector: %s", sel)
            await asyncio.sleep(0.5)
            return result
        except Exception:
            continue

    # 2. Otherwise an exact-labelled button, but only inside a consent container.
    for container in _CONSENT_CONTAINERS:
        for label in _CONSENT_LABELS:
            sel = f"{container} >> button:text-is('{label}')"
            try:
                loc = page.locator(sel).first
                if await loc.count() > 0 and await loc.is_visible():
                    result["attempted"] = True
                    await loc.click(timeout=2000)
                    result["dismissed"] = True
                    result["selector"] = sel
                    log.info("Consent dismissed via: %s", sel)
                    await asyncio.sleep(0.5)
                    return result
            except Exception:
                continue

    # Nothing found is the normal case (no banner, or already accepted) and is
    # not a failure. attempted=True with dismissed=False is the bad one: a
    # notice was there and would not go away.
    if await consent_present(page):
        result["attempted"] = True
        log.warning("Consent notice present but not dismissed — later "
                    "interactions may be blocked by the overlay")
    return result


# The screen a persona browses on. The walkthrough has always run at one fixed
# desktop-sized window, so every heuristic finding the tool has ever produced
# was implicitly a desktop finding — while the only thing measured on mobile was
# a separate technical scan. The reviewer asked whether the analysis could be
# split by device; this is what makes that answerable.
DEVICE_PROFILES = {
    "desktop": {"width": 1024, "height": 768, "is_mobile": False, "user_agent": None},
    "mobile": {
        "width": 390, "height": 844, "is_mobile": True,
        # A mobile viewport alone is not a phone: sites branch on the UA for
        # layout, redirects to m. hosts and app interstitials. Emulating the
        # size without the UA produces a desktop page squeezed into 390px,
        # which is not what any user sees and would be a dishonest finding.
        "user_agent": (
            "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36"
        ),
    },
}
DEFAULT_DEVICE = "desktop"


def devices_for(device: str | None) -> list[str]:
    """Which device each persona browses on, cycled across the cohort.

    "both" alternates rather than running every persona twice: with N personas
    you get N/2 sessions per device for the same wall-clock cost, and the
    comparison is between different people on different devices — which is what
    a real mixed-device study looks like anyway. Doubling every persona would
    double an already 15-70 minute run to answer the same question.
    """
    choice = (device or DEFAULT_DEVICE).strip().lower()
    if choice == "both":
        return ["desktop", "mobile"]
    if choice in DEVICE_PROFILES:
        return [choice]
    return [DEFAULT_DEVICE]


def apply_device_profile(cfg: DictConfig, device: str) -> None:
    """Point the browser config at one device profile, in place."""
    profile = DEVICE_PROFILES.get(device) or DEVICE_PROFILES[DEFAULT_DEVICE]
    opts = cfg.environment.browser.context_options
    opts.viewport.width = profile["width"]
    opts.viewport.height = profile["height"]
    if profile.get("user_agent"):
        opts.user_agent = profile["user_agent"]
    if "is_mobile" in opts:
        opts.is_mobile = profile["is_mobile"]


# Device profiles a session can browse under.
#
# Until now every session ran at one desktop-sized window, which made the whole
# heuristic layer implicitly a desktop report while the only mobile evidence was
# a technical scan of page layout. The reviewer asked whether the analysis could
# be split by device; this is what makes a real mobile *journey* possible.
_DEVICE_PROFILES = {
    "desktop": {"width": 1440, "height": 900, "is_mobile": False, "user_agent": None},
    "mobile": {
        "width": 390, "height": 844, "is_mobile": True,
        # A real phone UA matters beyond layout: sites serve different markup,
        # different consent flows, sometimes an app interstitial, to mobile.
        "user_agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1"
        ),
    },
}
DEVICE_CHOICES = ("desktop", "mobile", "both")


def devices_for_run(device, persona_count: int) -> list:
    """Which device each persona browses under, in persona order.

    "both" alternates rather than running every persona twice. Doubling the
    sessions would double an already long run for the same number of
    participants; alternating splits the panel instead, so a 4-persona audit
    gives two desktop journeys and two mobile ones in the usual time. With an
    odd count desktop takes the extra one, since that is what the rest of the
    pipeline is otherwise anchored to.
    """
    choice = (device or "desktop").strip().lower()
    if choice not in DEVICE_CHOICES:
        choice = "desktop"
    if choice != "both":
        return [choice] * max(0, persona_count)
    return ["desktop" if i % 2 == 0 else "mobile" for i in range(max(0, persona_count))]


def apply_device_profile(cfg: DictConfig, device: str) -> DictConfig:
    """A copy of `cfg` with the browser set up for `device`.

    A copy, not a mutation: personas run concurrently off one loaded config, so
    editing it in place would have them racing to set each other's viewport.
    """
    import copy

    from omegaconf import OmegaConf

    profile = _DEVICE_PROFILES.get(device) or _DEVICE_PROFILES["desktop"]
    out = copy.deepcopy(cfg)
    ctx = out.environment.browser.context_options
    ctx.viewport.width = profile["width"]
    ctx.viewport.height = profile["height"]
    if profile.get("user_agent"):
        ctx.user_agent = profile["user_agent"]
    # is_mobile/has_touch are not in the shipped schema, and the config is
    # loaded in struct mode, so assigning them raises rather than adding them.
    # Opened just for this block and closed again, so a typo anywhere else in
    # the pipeline still fails loudly instead of silently creating a key.
    if profile["is_mobile"]:
        OmegaConf.set_struct(ctx, False)
        ctx.is_mobile = True
        ctx.has_touch = True
        OmegaConf.set_struct(ctx, True)
    return out


_DEFAULT_PERSONA_BUDGET_MIN = 45


def persona_budget_seconds() -> int:
    """Wall-clock ceiling for one persona's browsing session, in seconds.

    Read per call rather than cached at import so a deployment can change it
    without a rebuild, and so tests can shorten it. Values <= 0 disable the
    budget entirely, which is the escape hatch for a deliberately long run.
    """
    raw = os.getenv("UXAGENT_PERSONA_BUDGET_MIN", "").strip()
    try:
        minutes = int(raw) if raw else _DEFAULT_PERSONA_BUDGET_MIN
    except ValueError:
        minutes = _DEFAULT_PERSONA_BUDGET_MIN
    return minutes * 60 if minutes > 0 else 10**9


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
    headless: Optional[bool] = None,
    device: str = "desktop",
) -> pathlib.Path:
    persona_card = build_persona_card(persona_info)
    # Recorded on the card so the report can say which journeys were phone
    # journeys. Without it a mixed run is indistinguishable from a desktop one.
    persona_card["device"] = device
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
    # runs_dir is runs/<run_id> — used for cooperative cancellation checks.
    run_id = runs_dir.name
    # step -> {attempted, dismissed, selector}. Persisted at the end of the
    # session so attribution.py can tell which steps ran under an unresolved
    # consent overlay, and keep the resulting "defects" out of the report.
    consent_states: dict[int, dict] = {}

    async def before_action_hook():
        if cfg.environment.recording.enabled:
            return
        # Only act when a notice is actually on screen. This used to run
        # unconditionally on every step, so on banner-free pages the label
        # matchers clicked whatever unrelated button they happened to hit.
        if await consent_present(env.page):
            state = await dismiss_cookie_banners(env.page)
            consent_states[steps_taken] = state
        elif _consent_unresolved(consent_states):
            # The notice that could not be dismissed is no longer on screen —
            # the persona accepted it, or navigation removed it. Recorded so
            # attribution can stop treating later steps as blocked.
            consent_states[steps_taken] = {"attempted": False, "dismissed": False,
                                           "present": False, "selector": None}
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

    # wait_hook was only ever threaded a permanent None: env_wait_hook, like
    # wait_for_login and env_setup_hook, was a parameter no caller ever passed.
    env = WebAgentEnv(cfg.environment, before_action_hook=before_action_hook)

    log.info(f"[{run_uid}] env created")
    try:
        policy = AgentPolicy(persona, intent)
        # The caller's choice wins; the config value is the fallback. This
        # parameter used to be accepted by experiment_async and never read, so
        # the wizard's headless toggle changed viewport_scan and link_checker
        # (which do receive it) but not the agent sessions themselves — the
        # flag half-worked, which is worse than not existing.
        use_headless = (
            cfg.environment.browser.launch_options.headless if headless is None else headless
        )
        print("setting up env with headless = " + str(use_headless))
        await env.setup(task_to_use, headless=use_headless)

        # Landing consent, before the first observation. Recorded under step 0:
        # if it could not be dismissed, everything the session goes on to do
        # happens under an overlay.
        consent_states[0] = await dismiss_cookie_banners(env.page)

        # The page exactly as it landed: consent handled, nothing scrolled,
        # nothing drawn. The first-impression pass is a "5-second test" of the
        # first screen, and it used to be given step 0's evidence frame instead,
        # which is taken once the first click target has been scrolled into view
        # — on the Hyundai configurator 2090 px down a 3890 px page, a screen no
        # visitor lands on.
        try:
            store.landing_screenshot_path().parent.mkdir(parents=True, exist_ok=True)
            await env.page.screenshot(path=store.landing_screenshot_path())
        except Exception as exc:
            log.warning("landing screenshot failed: %s", exc)

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
        deadline = time.monotonic() + persona_budget_seconds()
        while steps_taken < max_steps:
            # Wall-clock budget. max_steps bounds how many steps a session
            # takes but not how long they take: on a heavy page a single
            # perceive can run for minutes, and the first external review had a
            # run that produced nothing at all after 70 minutes. Checked
            # between steps so the session ends cleanly — everything written so
            # far stays usable and still yields a partial report, which beats
            # returning nothing.
            if time.monotonic() > deadline:
                log.warning(
                    "[%s] persona out of time after %d steps (budget %d min) — "
                    "ending the session, keeping the partial trace",
                    run_uid, steps_taken, persona_budget_seconds() // 60,
                )
                break
            if cancellation.is_cancelled(run_id):
                log.info("[%s] run cancelled — stopping session at step %d", run_id, steps_taken)
                break
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
            # Safety net: a single step's LLM call (perceive/plan/act) can still
            # fail after gpt.py's repair+retries — a transient upstream error, or
            # JSON that even repair can't salvage. That must not kill the whole
            # persona session, so re-observe and retry the step once before
            # letting it propagate. Combined with the JSON repair, an unrecovered
            # "error" end is now genuinely rare.
            try:
                action = await policy.forward(env, obs)
            except Exception as step_exc:
                log.warning("[%s] step %d forward failed (%r) — re-observing and "
                            "retrying once", run_uid, steps_taken, step_exc)
                obs = await env.observation()
                action = await policy.forward(env, obs)

            # Hard loop-breaker: advisory warnings (memory thoughts + error
            # channel) fire after the 2nd repeat, but the model can still keep
            # following its plan. If it picks the SAME action a 3rd time, we
            # physically override it with a 'back' navigation to force a new
            # page state — a stuck agent burns 5-10 minutes per wasted step.
            forced_escape = False
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
                # Deliberately does NOT reset repeat_count, and the tracking
                # below leaves prev_action_key alone. This escape is our
                # intervention, not the persona making progress: resetting here
                # capped repeat_count at 2 while the smallest abandon_at is 3,
                # so `abandoned` could never become True and every consumer of
                # it (abort.py, the persona_abandoned outcome, prioritization's
                # blocked branch, the "porzucono" report line) was dead code.
                forced_escape = True

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
                blocked_desc = _parse_action(action).get("description", "")
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
            parsed_action = _parse_action(action)
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
            if forced_escape:
                # The 'back' we injected is not the persona's own choice, so it
                # is neither a repeat nor progress. Hold both the counter and
                # prev_action_key so that if the model returns to the same dead
                # end next step, it still registers as a consecutive no-progress
                # step rather than starting over.
                pass
            elif action_key == prev_action_key and _current_url(obs) == url_before:
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
            if not forced_escape:
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

        store.write_consent_states(consent_states)
        store.write_memory_trace_text(policy.get_formatted_memories())
        log.info(f"Saved memory trace to {store.trace_dir}")

    except Exception as exc:
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
                # The exception type and the FIRST line of its message.
                # Without this, session_result recorded a bare "error" and the
                # run's own record gave no clue why a session died — the "błąd
                # sesji" badge with no cause. Full traceback stays in error.txt.
                #
                # Not the traceback's last line: a Playwright error's message is
                # itself multi-line, and its diagnostic part comes first —
                # "net::ERR_CERT_AUTHORITY_INVALID" on line one, then a call log.
                # Taking the last line reported 'navigating to "…", waiting until
                # "domcontentloaded"', which says only that a page load was
                # attempted, hiding the one word that explained why it failed.
                "error": _exception_summary(exc)[:500],
            })
        except Exception:
            pass
        # Also on this path: written only at a normal end, a session that
        # crashed under a consent overlay it could not dismiss left no record
        # of it, so its findings passed the consent gate.
        try:
            store.write_consent_states(consent_states)
        except Exception:
            pass
    finally:
        try:
            await policy.close()  # cancel the background reflect/wonder loop
        except Exception:
            pass
        try:
            log.info(f"[{run_uid}] closing env...")
            # No asyncio.shield: shielding meant the 10s timeout cancelled only
            # the waiter, leaving close() pending. run_async then returned and
            # asyncio.run tore the loop down, destroying that task before it
            # released the shared-driver claim — which is what made the next run
            # hang. Cancelling close() for real lets its finally block run.
            await asyncio.wait_for(env.close(), timeout=10)
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
    device: str = "desktop",
) -> list:
    cfg = load_cfg(config_name=config_name)
    if concurrency:
        cfg.environment.browser.user_data_dir = None
    gpt.configure(cfg.llm_provider)
    print("llm provider: " + cfg.llm_provider)

    # 0 (an empty wizard field sends it) blocked every session at the
    # semaphore for good, after the persona LLM calls were already spent.
    sem = asyncio.Semaphore(max(1, int(concurrency or 1)))
    total = len(agents)
    done = 0
    lock = asyncio.Lock()

    run_id = runs_dir.name if runs_dir is not None else None

    async def run_one(entry: Dict[str, str], persona_device: str):
        nonlocal done
        async with sem:
            # Queued personas must not start once the run is cancelled — with
            # concurrency 1 and 50 personas, most are still waiting here.
            if cancellation.is_cancelled(run_id):
                return None
            trace_dir = await _run_for_persona_and_intent(
                cfg=apply_device_profile(cfg, persona_device),
                persona_info=entry,
                start_url=start_url,
                max_steps=max_steps,
                runs_dir=runs_dir,
                headless=headless,
                device=persona_device,
            )
        async with lock:
            done += 1
            if on_progress:
                try:
                    on_progress(done, total)
                except Exception:
                    pass
        return trace_dir

    assigned = devices_for_run(device, len(agents))
    if len(set(assigned)) > 1:
        log.info("[devices] %s", ", ".join(
            f"{i + 1}:{d}" for i, d in enumerate(assigned)))
    tasks = [asyncio.create_task(run_one(e, assigned[i]))
             for i, e in enumerate(agents)]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            log.exception("A session failed", exc_info=r)
    return results
