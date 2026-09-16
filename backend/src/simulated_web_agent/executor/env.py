import asyncio
import base64
import contextlib
import inspect
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, ClassVar, Optional

import numpy
from omegaconf import DictConfig, OmegaConf
from playwright.async_api import Playwright, async_playwright
from playwright.async_api import TimeoutError as PlaywrightTimeoutError


def resolve_url(url: str, base_url: str) -> str:
    """Resolve a possibly-relative URL against the current page URL.

    The action model often emits site-relative paths ("/collections/dom") or
    scheme-less hosts ("pepco.pl/x"); Playwright's page.goto rejects both as
    "Cannot navigate to invalid URL".
    """
    from urllib.parse import urljoin, urlparse

    candidate = (url or "").strip()
    if not candidate:
        return candidate
    if urlparse(candidate).scheme in ("http", "https"):
        return candidate
    # "pepco.pl/x" style — has a dot before the first slash, no scheme
    head = candidate.split("/", 1)[0]
    if not candidate.startswith("/") and "." in head:
        return "https://" + candidate
    return urljoin(base_url or "", candidate)


# Prefix for the refusal written into observation["error"] when the agent tries
# to leave the site under test.
#
# Deliberately NOT "Error executing action:" — attribution.py treats that prefix
# as proof of a harness interaction failure and suppresses every finding
# anchored on the step. A scope refusal is a policy decision that worked exactly
# as intended, so it must not discredit that step's findings.
SCOPE_REFUSAL_PREFIX = "Navigation blocked:"


async def select_places_suggestion(page, timeout: int = 2500) -> bool:
    """Select the first Google Places Autocomplete suggestion, if the dropdown
    is visible. Returns True if a suggestion was selected.

    Google Places (inputs with class "pac-target-input") only updates the
    map/results after a suggestion is *chosen* — this fires the place_changed
    event. Typing the query text alone, even with Enter, does nothing (observed
    on pepco.pl's store locator: the store list stayed unfiltered no matter how
    many times "Warszawa" was typed). Selecting the first suggestion via
    ArrowDown+Enter triggers the filter.
    """
    try:
        await page.locator(".pac-container .pac-item").first.wait_for(
            state="visible", timeout=timeout
        )
    except Exception:
        return False
    await page.keyboard.press("ArrowDown")
    await page.keyboard.press("Enter")
    return True


async def locate_offcanvas_input(page, trigger, timeout: int = 2500):
    """If `trigger` is a Bootstrap offcanvas decoy, return a locator for the real
    input inside the panel it opens; otherwise return None.

    Observed on diabetyk24.pl: the visible "Szukaj produktu" box is
    `<input maxlength="0" data-bs-toggle="offcanvas" data-bs-target="#offcanvasSearch">`
    — a trigger, not an input. The real `<input name="search[box][query]">` lives
    inside a `.offcanvas` panel that is pruned from the parse while closed, so the
    agent only ever sees the decoy, types into it (maxlength 0 → nothing lands),
    and gives up. Same shape as the Google Places fix: detect the special input
    and redirect the interaction transparently.

    A decoy is an element with `data-bs-toggle="offcanvas"`, or a `maxlength="0"`
    input. `type()` has usually already clicked the trigger (which opens the
    panel); if the panel is not open yet, we click it here. We then return the
    first visible, non-decoy input inside the target panel.
    """
    try:
        info = await trigger.evaluate(
            "el => ({toggle: el.getAttribute('data-bs-toggle'),"
            " target: el.getAttribute('data-bs-target'),"
            " maxlen: el.getAttribute('maxlength'),"
            " tag: el.tagName.toLowerCase()})"
        )
    except Exception:
        return None

    is_decoy = info.get("toggle") == "offcanvas" or (
        info.get("tag") == "input" and info.get("maxlen") == "0"
    )
    if not is_decoy:
        return None

    # The panel named by data-bs-target (e.g. "#offcanvasSearch"), else any panel.
    panel_sel = info.get("target") or ".offcanvas"

    # Open the panel if the trigger click has not already done so.
    try:
        already_open = await page.locator(
            f"{panel_sel}.show, {panel_sel}.showing"
        ).count()
    except Exception:
        already_open = 0
    if not already_open:
        try:
            await trigger.click()
        except Exception:
            pass

    # The real input inside the panel — never the maxlength=0 decoy or a hidden
    # field. Text/search inputs, or an untyped input, are all valid.
    input_sel = (
        f"{panel_sel} input[type='search']:not([maxlength='0']), "
        f"{panel_sel} input[type='text']:not([maxlength='0']), "
        f"{panel_sel} input:not([type]):not([maxlength='0'])"
    )
    real_input = page.locator(input_sel).first
    try:
        await real_input.wait_for(state="visible", timeout=timeout)
    except Exception:
        return None
    return real_input


async def dismiss_open_offcanvas(page) -> bool:
    """Close any open Bootstrap offcanvas panel (`.offcanvas.show`), returning
    True if one was dismissed.

    An open offcanvas — especially with `data-bs-backdrop="false"` — covers the
    page and intercepts every click until closed. Its close control is often an
    icon-only `data-bs-dismiss="offcanvas"` button the agent can't identify.
    Bootstrap closes an offcanvas on Escape by default; if that doesn't clear it,
    fall back to the panel's own dismiss control.
    """
    try:
        if await page.locator(".offcanvas.show").count() == 0:
            return False
    except Exception:
        return False

    gone = "!document.querySelector('.offcanvas.show')"
    await page.keyboard.press("Escape")
    try:
        await page.wait_for_function(gone, timeout=1500)
        return True
    except Exception:
        pass
    try:
        await page.locator(
            ".offcanvas.show [data-bs-dismiss='offcanvas']"
        ).first.click(timeout=1000)
        await page.wait_for_function(gone, timeout=1000)
        return True
    except Exception:
        return False


async def click_semantic_element(page, semantic_id: str, timeout: int = 5000) -> None:
    """Click the element with the given parser-semantic-id, recovering from
    sticky-overlay interception.

    Playwright scrolls the target the minimal distance into the viewport, so
    on pages with a sticky header the element lands right at the top edge,
    underneath the overlay — every retry then fails with "... intercepts
    pointer events" until the timeout (observed on pepco.pl, run
    2026-07-09_11-29-52: 20 steps burned re-clicking a sidebar link). A human
    would scroll the element into open view, so on timeout we center it in
    the viewport and try once more. Elements that are genuinely hidden (e.g.
    inside a closed hover flyout) still fail both attempts and the error
    propagates to the agent.
    """
    selector = f'[parser-semantic-id="{semantic_id}"]'
    element = page.locator(selector)
    if await element.count() == 0:
        raise ValueError(f"Element '{semantic_id}' not found on page")
    try:
        await element.click(timeout=timeout)
    except PlaywrightTimeoutError:
        # An open Bootstrap offcanvas panel (data-bs-backdrop="false") covers the
        # page and intercepts every click; dismiss it first. Observed on
        # diabetyk24.pl, where the open search panel trapped agents in click
        # loops on breadcrumbs / "Pokaż więcej".
        await dismiss_open_offcanvas(page)
        await element.evaluate(
            "el => el.scrollIntoView({block: 'center', inline: 'nearest'})"
        )
        await element.click(timeout=timeout)


class ElementHighlight:
    """
    Context manager for highlighting elements using overlay instead of modifying target element.
    Playwright-based implementation that creates a visual overlay on top of the target element.
    """

    def __init__(
        self,
        semantic_id: Optional[str] = None,
        sleep: float = 1,
        before_hook: Optional[str] = None,
        after_hook: Optional[str] = None,
        center: bool = True,
    ):
        # Automatically get the WebAgentEnv instance from the call stack
        self.env = self._get_env_from_stack()
        self.page = self.env.page
        self.semantic_id = semantic_id
        self.sleep_duration = sleep
        self.before_hook = before_hook
        self.after_hook = after_hook
        self.center = center
        self.logger = logging.getLogger(__name__)

        # Get headless mode from environment config
        try:
            self.headless = bool(
                getattr(self.env.config.browser.launch_options, "headless", False)
            )
        except Exception:
            self.headless = False

    def _get_env_from_stack(self):
        """
        Extract the WebAgentEnv instance from the call stack.
        Looks for 'self' in the calling frame that is an instance of WebAgentEnv.
        """
        frame = inspect.currentframe()
        try:
            # Go up the call stack to find the WebAgentEnv instance
            while frame:
                frame = frame.f_back
                if frame is None:
                    break

                # Check if 'self' exists in the frame's local variables
                if "self" in frame.f_locals:
                    potential_env = frame.f_locals["self"]
                    # Check if it's a WebAgentEnv instance using isinstance
                    if isinstance(potential_env, WebAgentEnv):
                        return potential_env

            raise RuntimeError(
                "Could not find WebAgentEnv instance in call stack. "
                "ElementHighlight must be called from within a WebAgentEnv method."
            )
        finally:
            # Clean up frame reference to avoid memory leaks
            del frame

    async def __aenter__(self):
        try:
            # Only perform highlighting and scrolling if we have a target element
            if self.semantic_id:
                # Get the target element
                selector = f'[parser-semantic-id="{self.semantic_id}"]'
                locator = self.page.locator(selector).first

                # Ensure element exists
                count = await locator.count()
                if count == 0:
                    self.logger.warning(
                        f'Element with parser-semantic-id="{self.semantic_id}" not found, skipping highlight'
                    )
                else:
                    # Scroll element into view with smooth behavior
                    if self.center:
                        await self.page.evaluate(
                            """(sel) => {
                            const el = document.querySelector(sel);
                            if (!el) return;
                            requestAnimationFrame(() => {
                                    el.scrollIntoView({behavior: "smooth", block: "center", inline: "center"});
                            });
                        }""",
                            selector,
                        )
                    else:
                        await locator.scroll_into_view_if_needed(timeout=2000)
                    await asyncio.sleep(1)

                    # Create dual overlay highlight - inner (exact) and outer (with gap)
                    await self.page.evaluate(
                        """(sel) => {
                    const element = document.querySelector(sel);
                    if (!element) return;
                    
                    // Function to calculate cumulative offset (same as Selenium version)
                    var cumulativeOffset = function(element) {
                        var top = 0, left = 0;
                        var rect = element.getBoundingClientRect();
                        do {
                            top += element.offsetTop || 0;
                            left += element.offsetLeft || 0;
                            element = element.offsetParent;
                        } while(element);

                        return {
                            top: top,
                            left: left,
                            width: rect.width,
                            height: rect.height,
                        };
                    };
                    
                    // Get element position and dimensions
                    const rect = cumulativeOffset(element);
                    
                    // Create outer highlight div (larger with gap)
                    const gap = 16; // 8px gap around the element
                    const outerDiv = document.createElement('div');
                    outerDiv.style.position = 'absolute';
                    outerDiv.style.top = (rect.top - gap) + 'px';
                    outerDiv.style.left = (rect.left - gap) + 'px';
                    outerDiv.style.width = (rect.width + gap * 2) + 'px';
                    outerDiv.style.height = (rect.height + gap * 2) + 'px';
                    outerDiv.style.outline = '5px solid rgba(121, 204, 215, 0.6)';
                    outerDiv.style.zIndex = '2147483646';
                    outerDiv.style.pointerEvents = 'none';
                    
                    // Create inner highlight div (exact size)
                    const innerDiv = document.createElement('div');
                    innerDiv.style.position = 'absolute';
                    innerDiv.style.top = rect.top + 'px';
                    innerDiv.style.left = rect.left + 'px';
                    innerDiv.style.width = rect.width + 'px';
                    innerDiv.style.height = rect.height + 'px';
                    innerDiv.style.outline = '3px solid #79ccd7';
                    innerDiv.style.zIndex = '2147483647';
                    innerDiv.style.pointerEvents = 'none';
                    
                    // Add both to DOM
                    document.body.appendChild(outerDiv);
                    document.body.appendChild(innerDiv);
                    
                    // Store references for cleanup
                    document.highlightedElements = [outerDiv, innerDiv];
                        }""",
                        selector,
                    )

                    self.logger.info("Element highlight applied")

            # Always sleep (whether we have a target or not)
            if self.sleep_duration > 0:
                await asyncio.sleep(self.sleep_duration)

            # Run before hook if provided and we have a target element
            if self.before_hook and self.semantic_id:
                selector = f'[parser-semantic-id="{self.semantic_id}"]'
                locator = self.page.locator(selector).first
                try:
                    await locator.evaluate(self.before_hook)
                except Exception as e:
                    self.logger.warning(f"Before hook failed: {e}")

            # Always run environment before action hook
            if self.env.before_action_hook:
                if inspect.iscoroutinefunction(self.env.before_action_hook):
                    await self.env.before_action_hook()
                else:
                    self.env.before_action_hook()

            # Run this after the first snippet
            await self.page.evaluate(
                """() => {
                const refs = document.highlightedElements;
                if (!refs || refs.length < 2) return Promise.resolve();

                const [outerDiv, innerDiv] = refs;

                // Parse target box (the exact element bounds already stored on innerDiv)
                const tTop = parseFloat(innerDiv.style.top || '0');
                const tLeft = parseFloat(innerDiv.style.left || '0');
                const tWidth = parseFloat(innerDiv.style.width || '0');
                const tHeight = parseFloat(innerDiv.style.height || '0');

                // Prepare CSS transition for a smooth 2s shrink
                outerDiv.style.transition = [
                    'top 2000ms ease',
                    'left 2000ms ease',
                    'width 2000ms ease',
                    'height 2000ms ease',
                    'outline-width 2000ms ease',
                    'outline-color 2000ms ease'
                ].join(', ');
                outerDiv.style.willChange = 'top,left,width,height,outline-width,outline-color';

                // Force reflow so transition reliably applies
                void outerDiv.offsetWidth;

                // Animate: shrink outer box to the inner box
                outerDiv.style.top = tTop + 'px';
                outerDiv.style.left = tLeft + 'px';
                outerDiv.style.width = tWidth + 'px';
                outerDiv.style.height = tHeight + 'px';

                // Return a promise that resolves when transition ends
                return new Promise(resolve => {
                    const handler = (e) => {
                        if (e.target === outerDiv) {
                            outerDiv.removeEventListener('transitionend', handler);
                            resolve();
                        }
                    };
                    outerDiv.addEventListener('transitionend', handler);
                });
            }
            """
            )

        except Exception as e:
            self.logger.error(f"Failed to apply element highlight: {e}")
            # Continue without highlighting rather than failing

        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any):
        try:
            # Remove highlight overlays only if we have a target and not in headless mode
            if self.semantic_id:
                await self.page.evaluate(
                    """
                    if (document.highlightedElements) {
                        document.highlightedElements.forEach(el => el && el.remove());
                        document.highlightedElements = null;
                    }
                    // Fallback for old single element cleanup
                    if (document.highlightedElement) {
                        document.highlightedElement.remove();
                        document.highlightedElement = null;
                    }
                    """
                )

            # Run after hook if provided and we have a target element
            if self.after_hook and self.semantic_id:
                selector = f'[parser-semantic-id="{self.semantic_id}"]'
                locator = self.page.locator(selector).first
                try:
                    await locator.evaluate(self.after_hook)
                except Exception as e:
                    self.logger.warning(f"After hook failed: {e}")

            # Always run environment after action hook
            if self.env.after_action_hook:
                if inspect.iscoroutinefunction(self.env.after_action_hook):
                    await self.env.after_action_hook()
                else:
                    self.env.after_action_hook()

        except Exception as e:
            self.logger.warning(f"Failed to remove element highlight: {e}")
            # Don't raise - cleanup failures shouldn't break the flow

    def pause(self, duration: float = None) -> float:
        """Generate a pause duration with some randomness (similar to Selenium version)"""
        if duration is None:
            duration = max(0.2 + numpy.random.normal(0, 0.05), 0)
        return duration

    async def sleep(self, duration: float = None):
        """Async sleep with randomness"""
        if self.headless:
            return
        sleep_time = self.pause(duration)
        await asyncio.sleep(sleep_time)


class WebAgentEnv:
    _shared_playwright: ClassVar[Playwright | None] = None
    _shared_playwright_users: ClassVar[int] = 0
    # Guards the check-then-start in _ensure_playwright. Without it, concurrent
    # run_one tasks each see None and each start a driver; every assignment but
    # the last is leaked, and the refcount then describes an instance nobody
    # holds, so _cleanup_playwright can never reach 0 for the leaked ones.
    _shared_playwright_lock: ClassVar[asyncio.Lock | None] = None
    before_action_hook: Callable[[], None] = None
    after_action_hook: Callable[[], None] = (None,)
    wait_hook: Callable[[], None] = None

    def __init__(
        self,
        environment_config: DictConfig,
        before_action_hook: Callable[[], None] = None,
        after_action_hook: Callable[[], None] = None,
        wait_hook: Callable[[], None] = None,
    ):
        self.config = environment_config
        self.before_action_hook = before_action_hook
        self.after_action_hook = after_action_hook
        self.wait_hook = wait_hook
        self.context_manager = None
        self.browser = None
        self.context = None
        self.page = None  # current active page
        # note: pages are managed by self.context.pages
        self.uuid = (
            environment_config.uuid
            if hasattr(environment_config, "uuid")
            else str(uuid.uuid4())
        )
        self.logger = logging.getLogger(__name__)
        self.task_config: dict | None = None
        self.model_answer: str | None = None  # Model's final answer/response
        self.trace_file_path: str | None = None  # Path to the current trace file

        # Disable evaluation if recording is enabled
        if getattr(self.config, "recording", {}).get("enabled", False):
            if hasattr(self.config, "evaluation"):
                self.config.evaluation.enabled = False
            self.logger.warning(
                "Recording is enabled - evaluation disabled to avoid interference"
            )

    @classmethod
    def reset_shared_playwright(cls) -> None:
        """Forget any shared driver left over from a previous event loop.

        Each run() is its own asyncio.run(), i.e. its own event loop, and runs
        are serialised. A Playwright object created under a destroyed loop is
        unusable: awaiting it hangs forever rather than raising. If a previous
        run ended abnormally (cancellation, a hung browser close) the refcount
        could be left above zero, so the driver was never stopped and the stale
        object would be handed to the next run. Called at the start of a run,
        when no env can legitimately be alive.
        """
        cls._shared_playwright = None
        cls._shared_playwright_users = 0
        cls._shared_playwright_lock = None

    @classmethod
    async def _ensure_playwright(cls) -> Playwright:
        """Ensure shared Playwright instance exists and return it"""
        if cls._shared_playwright_lock is None:
            cls._shared_playwright_lock = asyncio.Lock()
        async with cls._shared_playwright_lock:
            if cls._shared_playwright is None:
                cls._shared_playwright = await async_playwright().start()
            cls._shared_playwright_users += 1
            return cls._shared_playwright

    @classmethod
    def _release_playwright(cls) -> Playwright | None:
        """Drop this env's claim on the shared driver. Synchronous, on purpose.

        Returns the driver to stop() if this was the last user, else None.

        The bookkeeping must not contain an await. close() runs under a
        wait_for timeout, so an await here can be interrupted and leave the
        refcount permanently above zero — after which the driver is never
        stopped and the next run inherits a Playwright object bound to a
        destroyed event loop, which hangs instead of failing. Stopping is
        best-effort and belongs to the caller; the count is what must be exact.
        """
        # max(0, ...) so a double-close cannot drive the count negative, which
        # would make the `== 0` test unreachable for every later env.
        cls._shared_playwright_users = max(0, cls._shared_playwright_users - 1)
        if cls._shared_playwright_users == 0 and cls._shared_playwright is not None:
            pw, cls._shared_playwright = cls._shared_playwright, None
            return pw
        return None

    async def _setup_tracing(self) -> None:
        """Setup Playwright tracing if enabled in config"""
        if not self.config.tracing.enabled:
            self.logger.debug("Tracing not enabled")
            return

        # Use trace output path directly as file path
        self.trace_file_path = self.config.tracing.output_path

        # Start tracing with configured options
        await self.context.tracing.start(
            screenshots=self.config.tracing.get("screenshots", True),
            snapshots=self.config.tracing.get("snapshots", True),
            sources=self.config.tracing.get("sources", True),
        )
        self.logger.info(f"Tracing started, will save to: {self.trace_file_path}")

    async def _stop_tracing(self) -> None:
        """Stop tracing and save trace file"""
        if self.trace_file_path and self.context:
            try:
                await self.context.tracing.stop(path=self.trace_file_path)
                self.logger.info(f"Trace saved to: {self.trace_file_path}")
            except Exception as e:
                self.logger.warning(f"Failed to save trace: {e}")
            finally:
                self.trace_file_path = None

    async def _start_recording(self) -> None:
        """Start screen recording using QuickRecorder"""
        try:
            self.logger.info("Starting screen recording setup...")

            # Create a new page for recording identification
            recording_page = await self.context.new_page()

            # Set the page title to the current UUID for window identification
            await recording_page.evaluate(
                f"""
                document.title = "{self.uuid}";
            """
            )

            # Give the page a moment to update the title
            await asyncio.sleep(2)

            # Use AppleScript to start QuickRecorder
            # Quickrecorder's recordings seem to be corrupted for chromium windows only
            # So far setting a lower quality and fps seem to fix it
            applescript = f"""
            tell application "QuickRecorder"
                activate
                configure {{fps:30, quality:2}}
                record window titled "{self.uuid}" in application "Chromium"
            end tell
            """

            # Execute AppleScript
            import subprocess

            result = subprocess.run(
                ["osascript", "-e", applescript],
                capture_output=True,
                text=True,
                timeout=10,
            )

            if result.returncode == 0:
                self.logger.info(
                    f"Successfully started recording for window with UUID: {self.uuid}"
                )
            else:
                self.logger.error(f"Failed to start recording: {result.stderr}")

            await asyncio.sleep(2)

            # Close the recording setup page
            await recording_page.close()

        except Exception as e:
            self.logger.error(f"Error starting recording: {e}")
            # Don't raise the exception - recording failure shouldn't stop the task

    async def _get_tabs_info(self) -> list[dict]:
        """Get information about all open tabs"""
        tabs_info = []
        for i, page in enumerate(self.context.pages):
            tabs_info.append(
                {
                    "id": i,
                    "title": await page.title(),
                    "url": page.url,
                    "is_active": page == self.page,
                }
            )
        return tabs_info

    async def setup(
        self,
        task_config: dict | None = None,
        headless: Optional[bool] = None,
    ):
        """Initialize the browser environment with configuration"""
        self.task_config = task_config
        # Registrable domain of the site under test. Everything off it is
        # refused: a session that wanders onto a competitor spends the step
        # budget there and produces findings about a site nobody asked about
        # (a lidl.pl run ended up auditing reserved.pl).
        from ..main.site_scope import registrable_domain

        self._allowed_domain = registrable_domain((task_config or {}).get("start_url", ""))
        self.context_manager = await self._ensure_playwright()

        # Get launch options from config and convert to dict
        launch_options = OmegaConf.to_container(
            self.config.browser.launch_options, resolve=True
        )

        if headless is not None:
            # update both the runtime options AND the config object,
            # so other code (like highlight_element’s “respect_headless”) reads the same value.
            launch_options["headless"] = bool(headless)
            try:
                self.config.browser.launch_options.headless = bool(headless)
            except Exception:
                pass

        # Add cache directory if configured
        if hasattr(self.config.browser, "cache_dir") and self.config.browser.cache_dir:
            # Use absolute path for cache directory
            cache_dir = Path(self.config.browser.cache_dir).resolve()
            cache_dir.mkdir(parents=True, exist_ok=True)  # Ensure directory exists
            cache_arg = f"--disk-cache-dir={cache_dir}"
            launch_options["args"] = launch_options.get("args", []) + [cache_arg]
            self.logger.info(f"Browser cache configured: {cache_arg}")

        # Get context options from config and convert to dict
        context_options = OmegaConf.to_container(
            self.config.browser.context_options, resolve=True
        )

        # Check if user_data_dir is specified - use launch_persistent_context if so
        user_data_dir = None
        if (
            hasattr(self.config.browser, "user_data_dir")
            and self.config.browser.user_data_dir
        ):
            user_data_dir = self.config.browser.user_data_dir

        if user_data_dir:
            # Use launch_persistent_context for user data directory
            # Remove --disk-cache-dir from args since persistent context manages its own cache
            persistent_options = {**launch_options, **context_options}
            if "args" in persistent_options:
                persistent_options["args"] = [
                    arg
                    for arg in persistent_options["args"]
                    if not arg.startswith("--disk-cache-dir")
                ]

            self.context = (
                await self.context_manager.chromium.launch_persistent_context(
                    user_data_dir, **persistent_options
                )
            )
            self.browser = self.context.browser
            self.logger.info(
                f"Using persistent context with cache in user data dir: {user_data_dir}"
            )
        else:
            # Regular launch without persistent context
            self.browser = await self.context_manager.chromium.launch(**launch_options)
            self.context = await self.browser.new_context(**context_options)

        # Start tracing if enabled
        await self._setup_tracing()

        # Set default timeout for all locator actions
        self.context.set_default_timeout(self.config.browser.timeouts.default)

        # Add init script if it exists
        init_script_path = Path(self.config.init_script_path)
        if init_script_path.exists():
            with open(init_script_path) as f:
                await self.context.add_init_script(f.read())
        else:
            self.logger.warning(f"Init script not found: {init_script_path}")

        # Create initial page (or use existing one from persistent context)
        if self.context.pages:
            # Use existing page from persistent context
            self.page = self.context.pages[0]
        else:
            # Create new page for regular context
            self.page = await self.context.new_page()

        # Handle authentication before navigating to start_url
        """
        if self.task_config and "sites" in self.task_config:
            required_sites = self.task_config["sites"]
            await self.ensure_logged_in(required_sites)
            """

        # Start recording if enabled
        if self.config.recording.enabled:
            await self._start_recording()

        # Navigate to start URL from task config
        if self.task_config and "start_url" in self.task_config:
            await self.page.goto(
                self.task_config["start_url"],
                wait_until="domcontentloaded",
                timeout=self.config.browser.timeouts.page_load_domcontent,
            )
        else:
            self.logger.warning("No start_url specified in task config")
        return await self.observation()

    async def new_tab(self, url: str | None = None) -> int:
        """Create a new tab and optionally navigate to URL. Returns tab ID."""
        async with ElementHighlight(sleep=0.5):
            page = await self.context.new_page()
            if url:
                await page.goto(url, wait_until="domcontentloaded")
            self.page = page  # Make new tab active
            return len(self.context.pages) - 1

    async def switch_tab(self, tab_id: int) -> None:
        """Switch to a different tab by ID"""
        async with ElementHighlight(sleep=0.3):
            if 0 <= tab_id < len(self.context.pages):
                self.page = self.context.pages[tab_id]
                await self.page.bring_to_front()
            else:
                raise ValueError(f"Invalid tab ID: {tab_id}")

    async def close_tab(self, tab_id: int) -> None:
        """Close a tab by ID"""
        async with ElementHighlight(sleep=0.3):
            if 0 <= tab_id < len(self.context.pages):
                page = self.context.pages[tab_id]
                await page.close()
                # If we closed the active tab, switch to the currently activated tab from context
                if page == self.page and self.context.pages:
                    # Find the currently active/focused tab in the context
                    for p in self.context.pages:
                        try:
                            if await p.evaluate("document.hasFocus()"):
                                self.page = p
                                break
                        except Exception:
                            continue
                    else:
                        # Fallback to last tab if no focused tab found
                        self.page = self.context.pages[-1]

                    # Ensure the new active page is brought to front
                    await self.page.bring_to_front()
            else:
                raise ValueError(f"Invalid tab ID: {tab_id}")

    async def screenshot(self) -> str:
        """
        Capture screenshot of the current tab and return the base64 encoded image.
        """
        screenshot_bytes = await self.page.screenshot(full_page=False)
        return base64.b64encode(screenshot_bytes).decode("utf-8")

    async def reset(self):
        """Reset the environment to initial state"""
        # Close all tabs
        for page in self.context.pages:
            await page.close()
        self.page = await self.context.new_page()

        # Return to start URL from task config
        if self.task_config and "start_url" in self.task_config:
            await self.page.goto(
                self.task_config["start_url"], wait_until="domcontentloaded"
            )
        else:
            self.logger.warning("No start_url specified in task config")
        return await self.observation()

    # ===================================================================
    # SITE SCOPE
    # ===================================================================

    def _off_site_refusal(self, url) -> str | None:
        """Refusal message if `url` would leave the site under test, else None.

        Checked before navigating so the competitor's page is never loaded at
        all — no wasted page load, no step spent, nothing for the perceiver to
        describe.
        """
        if not getattr(self, "_allowed_domain", "") or not url:
            return None
        from ..main.site_scope import registrable_domain

        target = resolve_url(str(url), self.page.url if self.page else "")
        found = registrable_domain(target)
        if not found or found == self._allowed_domain:
            return None
        return (
            f"{SCOPE_REFUSAL_PREFIX} {found} is outside the site under test "
            f"({self._allowed_domain}). The navigation was not performed. "
            f"Continue the task on {self._allowed_domain}."
        )

    async def _return_to_site_if_left(self) -> str | None:
        """Undo a navigation that left the site, and say so. None if on-site.

        Covers what the pre-check cannot see: a click, a form submit, or a
        redirect chain that ends on another domain.
        """
        if not getattr(self, "_allowed_domain", "") or not self.page:
            return None
        from ..main.site_scope import registrable_domain

        current = self.page.url or ""
        if not current.startswith("http"):
            return None  # about:blank and friends are not a scope breach
        found = registrable_domain(current)
        if not found or found == self._allowed_domain:
            return None

        # Back first (preserves history); fall back to the start URL if that
        # still leaves us off-site — e.g. the hop opened in a fresh tab, where
        # there is nothing to go back to.
        with contextlib.suppress(Exception):
            await self.page.go_back(wait_until="domcontentloaded")
        if registrable_domain(self.page.url or "") != self._allowed_domain:
            start = (self.task_config or {}).get("start_url")
            if start:
                with contextlib.suppress(Exception):
                    await self.page.goto(start, wait_until="domcontentloaded")

        self.logger.info("[scope] left %s for %s — returned", self._allowed_domain, found)
        return (
            f"{SCOPE_REFUSAL_PREFIX} that led to {found}, outside the site under "
            f"test ({self._allowed_domain}). You have been returned to the site. "
            f"Do not follow links that leave {self._allowed_domain}."
        )

    async def step(
        self,
        action: str,
        before_hook: Callable[[], None] = None,
        after_hook: Callable[[], None] = None,
    ):
        """
        Execute an action in the environment using JSON string format and return the next observation.

        Args:
            action: JSON string describing the action to execute

        Returns:
            dict: The observation after executing the action (same format as observation() method)

        Examples:
            obs = await env.step('{"action": "click", "target": "login_button"}')
            obs = await env.step('{"action": "type", "target": "username", "text": "john_doe", "enter": true}')
            obs = await env.step('{"action": "select", "target": "country", "value": "US"}')
            obs = await env.step('{"action": "goto_url", "url": "https://example.com"}')
            obs = await env.step('{"action": "back"}')
            obs = await env.step('{"action": "new_tab", "url": "https://example.com"}')
            obs = await env.step('{"action": "switch_tab", "tab_id": 1}')
            obs = await env.step('{"action": "close_tab", "tab_id": 1}')
            obs = await env.step('{"action": "terminate", "answer": "The product costs $29.99"}')
        """
        import json

        try:
            action_data = json.loads(action)
            action_name = action_data.get("action")

            if action_name == "click":
                await self.click(action_data["target"])

            elif action_name== "mouse_click":
                await self.mouse_click(action_data["at_x"], action_data["at_y"])

            elif action_name == "type":
                text = action_data["text"]
                target = action_data["target"]
                press_enter = action_data.get("enter", False)
                await self.type(target, text, press_enter)

            elif action_name == "raw_type":
                await self.raw_type(action_data["text"])

            elif action_name == "scroll":
                await self.scroll(action_data["direction"], action_data["amount"])

            elif action_name == "hover":
                await self.hover(action_data["target"])

            elif action_name == "select":
                await self.select(action_data["target"], action_data["value"])

            elif action_name == "clear":
                await self.clear(action_data["target"])

            elif action_name == "key_press":
                key = action_data["key"]
                target = action_data.get("target")
                await self.key_press(key, target)

            elif action_name == "goto_url":
                refusal = self._off_site_refusal(action_data["url"])
                if refusal:
                    observation = await self.observation()
                    observation["error"] = refusal
                    return observation
                await self.goto_url(action_data["url"])

            elif action_name == "back":
                await self.back()

            elif action_name == "forward":
                await self.forward()

            elif action_name == "refresh":
                await self.refresh()

            elif action_name == "new_tab":
                url = action_data.get("url")
                refusal = self._off_site_refusal(url) if url else None
                if refusal:
                    observation = await self.observation()
                    observation["error"] = refusal
                    return observation
                await self.new_tab(url)

            elif action_name == "switch_tab":
                tab_id = action_data["tab_id"]
                await self.switch_tab(tab_id)

            elif action_name == "close_tab":
                tab_id = action_data["tab_id"]
                await self.close_tab(tab_id)

            elif action_name == "terminate":
                answer = action_data.get("answer", "")
                await self.terminate(answer)

            else:
                self.logger.error(f"Unknown action: {action_name}")
                raise ValueError(f"Unknown action: {action_name}")

            # Sleep after action if configured
            if self.config.browser.sleep_after_action > 0:
                await asyncio.sleep(self.config.browser.sleep_after_action)

            # A click or redirect can leave the site without any explicit URL
            # for the pre-check above to inspect, so enforce again afterwards.
            scope_error = await self._return_to_site_if_left()

            # Return the next observation after executing the action
            observation = await self.observation()
            observation["error"] = scope_error
            return observation

        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON action format: {action}")
            observation = await self.observation()
            observation["error"] = f"Invalid JSON action format: {e}"
            return observation
        except KeyError as e:
            self.logger.error(f"Missing required parameter in action: {e}")
            observation = await self.observation()
            observation["error"] = f"Missing required parameter in action: {e}"
            return observation
        except Exception as e:
            self.logger.error(f"Error executing action: {action}, error: {e}")
            observation = await self.observation()
            observation["error"] = f"Error executing action: {e}"
            return observation

    # ===================================================================
    # ACTION METHODS
    # ===================================================================

    async def click(self, semantic_id: str) -> None:
        """
        Click on an element identified by its semantic ID.

        Args:
            semantic_id: The parser-semantic-id of the element to click

        Example:
            await env.click("login_button")
            await env.click("menu.settings")
        """
        async with ElementHighlight(semantic_id, sleep=0.5, center=False):
            try:
                await click_semantic_element(self.page, semantic_id)
            except ValueError:
                # id likely stripped by an SPA re-render — re-parse and retry once
                await self._reparse()
                await click_semantic_element(self.page, semantic_id)
            self.logger.info(f"Clicked element: {semantic_id}")

    async def mouse_move(self, x: int, y: int) -> None:
        """
        Move the cursor/mouse to a target coordinate.

        Args:
            x: The x-coordinate to move the cursor/mouse
            y: The y-coordinate to move the cursor/mouse

        Example:
            await env.mouse_move(200, 100)
        """

        await self.page.mouse.move(x, y)

        self.logger.info(f"Moved mouse to [{x}, {y}]")

    async def mouse_click(self, at_x: int = None, at_y: int = None) -> None:
        """
        Perform a mouse click on the given coordinates. Defaults to the
        current mouse position if coordinates not given.

        Example:
            await env.mouse_click()
            await env.mouse_click(200, 100)
        """

        await self.page.mouse.click(x = at_x, y = at_y)

        self.logger.info(f"Performed a raw click at [{at_x}, {at_y}]")

    async def _reparse(self) -> None:
        """Re-run the parser to re-inject parser-semantic-id attributes onto the
        live DOM.

        SPA re-renders (e.g. pepco.pl's store locator loading ~2000 stores +
        Google Maps between perceive and act) replace DOM nodes and strip the
        parser-* ids injected during the last observation. An element that was
        in the observation is then "not found" by the time the action runs.
        The parser assigns ids deterministically, so re-parsing restores the
        same id on the re-rendered element and the selector resolves again.
        """
        code = getattr(self, "_parser_code", None)
        if code is None:
            try:
                code = Path(self.config.parser_script_path).read_text()
            except Exception:
                code = ""
            self._parser_code = code
        if code:
            try:
                await self.page.evaluate(code)
            except Exception as e:
                self.logger.warning(f"reparse failed: {e}")

    async def _locate_or_reparse(self, semantic_id: str):
        """Locate an element by parser-semantic-id; if it vanished (SPA
        re-render stripped the id), re-parse once and retry before giving up."""
        selector = f'[parser-semantic-id="{semantic_id}"]'
        element = self.page.locator(selector)
        if await element.count() == 0:
            await self._reparse()
            element = self.page.locator(selector)
        return element

    async def type(
        self, semantic_id: str, text: str, press_enter: bool = False
    ) -> None:
        """
        Type text into an input element.

        Args:
            semantic_id: The parser-semantic-id of the input element
            text: Text to type
            press_enter: Whether to press Enter after typing

        Example:
            await env.type("search_input", "hello world")
            await env.type("username", "john_doe", press_enter=True)
        """
        async with ElementHighlight(semantic_id, sleep=0.5, center=True):
            element = await self._locate_or_reparse(semantic_id)
            if await element.count() == 0:
                raise ValueError(f"Element '{semantic_id}' not found on page")

            # Short timeout scroll - fail fast on hallucinated elements
            await element.scroll_into_view_if_needed(timeout=500)

            # Click the target, then type via the PAGE keyboard, not the element:
            # some sites (e.g. Pepco) move focus to a different overlay input on
            # click — element-bound typing would land in the decoy input and be
            # ignored, while page-level keystrokes follow the real focus. This
            # also fixes JS-driven inputs that ignore values set by fill().
            await element.click()
            await asyncio.sleep(0.3)  # let the site shift focus (overlays, modals)

            # Bootstrap offcanvas decoy: the visible "search" box is a trigger
            # (data-bs-toggle="offcanvas" / maxlength="0") whose click just opened
            # a panel holding the REAL input. Type into that instead — the
            # focus-assurance below would otherwise force focus back onto the
            # dead decoy, fighting the panel's autofocus. Same shape as the Places
            # branch: detect the special input, redirect, early-return.
            real_input = await locate_offcanvas_input(self.page, element)
            if real_input is not None:
                await real_input.scroll_into_view_if_needed(timeout=500)
                await real_input.click()
                await self.page.keyboard.press("Control+a")
                await self.page.keyboard.press("Delete")
                await self.page.keyboard.type(text, delay=40)
                if press_enter:
                    await asyncio.sleep(0.5)
                    await self.page.keyboard.press("Enter")
                self.logger.info(
                    f"Typed '{text}' into offcanvas panel input (via {semantic_id})"
                )
                return

            # Ensure the real input actually holds focus. In SPA/overlay states a
            # click can be swallowed (focus stays on <body>), so page-level
            # keystrokes land nowhere and the value never changes — observed on
            # pepco.pl's store locator, where every "type" left value="".
            try:
                focused = await element.evaluate("el => el === document.activeElement")
                if not focused:
                    await element.focus()
            except Exception:
                pass

            await self.page.keyboard.press("Control+a")
            await self.page.keyboard.press("Delete")
            await self.page.keyboard.type(text, delay=40)

            # Google Places Autocomplete (class "pac-target-input"): the map/list
            # only updates once a *suggestion* is chosen, which fires the
            # place_changed event. Typing the text — even with Enter — does
            # nothing on its own. For such inputs, select the first suggestion.
            # This is the store-locator "search does nothing" fix. Gated on the
            # Places class so normal inputs don't pay the dropdown-wait latency.
            try:
                is_places = await element.evaluate(
                    "el => el.classList.contains('pac-target-input')"
                )
            except Exception:
                is_places = False
            if is_places and await select_places_suggestion(self.page):
                await asyncio.sleep(0.8)  # let place_changed re-render results
                self.logger.info(
                    f"Selected Google Places suggestion for '{text}' ({semantic_id})"
                )
                return

            if press_enter:
                # Give debounced JS (suggestion dropdowns, query state) a moment
                # to register the text before submitting.
                await asyncio.sleep(0.5)
                await self.page.keyboard.press("Enter")

            self.logger.info(f"Typed '{text}' into element: {semantic_id}")

    async def raw_type(self, text: str):
        """
        Types the given string into the page as raw keyboard input.
        Will do nothing if an input element is not selected

        Args:
            text: Text to type

        Example:
            await env.raw_type("Best star wars movies")
        """
        await self.page.keyboard.type(text, delay=50)

        self.logger.info(f"Typed '{text}' into the page as raw keyboard input")

    async def scroll(self, direction: str, amount: int) -> None:
        """
        Performs a mouse scroll at the page inside the viewport.

        Args:
            direction: "up", "down", "left", or "right". The direction to scroll by.
            amount: The amount of times to perform the scroll in that direction.
        """
        if direction not in ["up", "down", "left", "right"]:
            raise ValueError(f"Invalid direction: {direction}")

        scroll_button = {
            "up": "ArrowUp",
            "down": "ArrowDown",
            "left": "ArrowLeft",
            "right": "ArrowRight",
        }[direction]

        for _ in range(amount*3): # one mouse scroll event ~= 3 arrow keys
            await self.page.keyboard.press(scroll_button, delay=50)

        self.logger.info(f"Scrolled the page {direction} by {amount} times")

    async def hover(self, semantic_id: str) -> None:
        """
        Hover over an element to trigger tooltips or dropdown menus.

        Args:
            semantic_id: The parser-semantic-id of the element to hover over

        Example:
            await env.hover("menu_item")
            await env.hover("tooltip_trigger")
        """
        async with ElementHighlight(semantic_id, sleep=0.5, center=True):
            element = await self._locate_or_reparse(semantic_id)
            if await element.count() == 0:
                raise ValueError(f"Element '{semantic_id}' not found on page")

            # Short timeout scroll - fail fast on hallucinated elements
            await element.scroll_into_view_if_needed(timeout=500)
            await element.hover(force=True)
            self.logger.info(f"Hovered over element: {semantic_id}")

    async def select(self, semantic_id: str, value: str) -> None:
        """
        Select an option from a dropdown/select element.

        Args:
            semantic_id: The parser-semantic-id of the select element
            value: The value of the option to select

        Example:
            await env.select("country_dropdown", "USA")
            await env.select("language_select", "en")
        """
        async with ElementHighlight(semantic_id, sleep=0.5, center=True):
            element = await self._locate_or_reparse(semantic_id)
            if await element.count() == 0:
                raise ValueError(f"Element '{semantic_id}' not found on page")

            # Short timeout scroll - fail fast on hallucinated elements
            await element.scroll_into_view_if_needed(timeout=500)
            await element.select_option(value, force=True)
            self.logger.info(f"Selected '{value}' in element: {semantic_id}")

    async def clear(self, semantic_id: str) -> None:
        """
        Clear the content of an input element.

        Args:
            semantic_id: The parser-semantic-id of the input element to clear

        Example:
            await env.clear("search_input")
            await env.clear("comment_textarea")
        """
        async with ElementHighlight(semantic_id, sleep=0.5, center=True):
            element = await self._locate_or_reparse(semantic_id)
            if await element.count() == 0:
                raise ValueError(f"Element '{semantic_id}' not found on page")

            # Short timeout scroll - fail fast on hallucinated elements
            await element.scroll_into_view_if_needed(timeout=500)
            await element.clear(force=True)
            self.logger.info(f"Cleared element: {semantic_id}")

    async def key_press(self, key: str, semantic_id: str | None = None) -> None:
        """
        Press a keyboard key, optionally on a specific element.

        Args:
            key: Key to press (e.g., "Enter", "Escape", "Tab", "ArrowDown")
            semantic_id: Optional element to focus before pressing key

        Example:
            await env.key_press("Escape")  # Press Escape globally
            await env.key_press("Enter", "search_input")  # Press Enter on search input
            await env.key_press("ArrowDown", "dropdown")  # Navigate dropdown
        """
        async with ElementHighlight(semantic_id, sleep=0.3, center=True):
            if semantic_id:
                selector = f'[parser-semantic-id="{semantic_id}"]'
                element = self.page.locator(selector)
                # Short timeout scroll - fail fast on hallucinated elements
                await element.scroll_into_view_if_needed(timeout=500)
                await element.press(key, force=True)
                self.logger.info(f"Pressed '{key}' on element: {semantic_id}")
            else:
                await self.page.keyboard.press(key)
                self.logger.info(f"Pressed '{key}' globally")

    # ===================================================================
    # NAVIGATION ACTIONS
    # ===================================================================

    async def goto_url(self, url: str) -> None:
        """
        Navigate to a specific URL in the current tab.

        Args:
            url: URL to navigate to. Relative URLs (e.g. "/collections/dom")
                are resolved against the current page — the model often emits
                site-relative paths, which Playwright rejects as invalid.

        Example:
            await env.goto_url("https://google.com")
            await env.goto_url("http://localhost:3000/login")
        """
        url = resolve_url(url, self.page.url)
        async with ElementHighlight(sleep=0.5):
            await self.page.goto(url, wait_until="domcontentloaded")
            self.logger.info(f"Navigated to: {url}")

    async def back(self) -> None:
        """
        Navigate back in browser history.

        Example:
            await env.back()
        """
        async with ElementHighlight(sleep=0.5):
            await self.page.go_back(wait_until="domcontentloaded")
            self.logger.info("Navigated back")

    async def forward(self) -> None:
        """
        Navigate forward in browser history.

        Example:
            await env.forward()
        """
        async with ElementHighlight(sleep=0.5):
            await self.page.go_forward(wait_until="domcontentloaded")
            self.logger.info("Navigated forward")

    async def refresh(self) -> None:
        """
        Refresh/reload the current page.

        Example:
            await env.refresh()
        """
        async with ElementHighlight(sleep=0.5):
            await self.page.reload(wait_until="domcontentloaded")
            self.logger.info("Page refreshed")

    async def terminate(self, answer: str = "") -> None:
        """
        Terminate the task with an optional answer.

        Args:
            answer: The model's final answer/response for the task

        Example:
            await env.terminate("The product costs $29.99")
            await env.terminate()  # Terminate without answer
        """
        async with ElementHighlight(sleep=0.3):
            self.model_answer = answer
            if answer:
                self.logger.info(f"Task terminated with answer: {answer}")
            else:
                self.logger.info("Task terminated without answer")

    async def _wait_for_custom_network_idle(
        self, timeout_ms: int = 10000, idle_time_ms: int = 500
    ) -> None:
        """
        Custom network idle detection that works with XHR/fetch requests.
        Uses async JavaScript Promise-based waiting for better performance.
        """
        self.logger.info(
            f"Waiting for custom network idle (timeout: {timeout_ms}ms, idle: {idle_time_ms}ms)"
        )

        try:
            # Add Python-side timeout as a safety net
            timeout_future = asyncio.create_task(asyncio.sleep(timeout_ms / 1000))
            evaluate_future = asyncio.create_task(
                self.page.evaluate(
                    """
                async ([idleTimeMs, timeoutMs]) => {
                    if (typeof window.__networkActivity === 'undefined') {
                        console.log('Network activity tracker not available');
                        return true; // Fallback if tracker not available
                    }

                    console.log('Starting network idle wait...');
                    try {
                        const isIdle = await window.__networkActivity.waitForIdle(idleTimeMs, timeoutMs);
                        console.log('Network idle wait completed:', isIdle);
                        return isIdle;
                    } catch (error) {
                        console.warn('Network idle wait error:', error);
                        return false;
                    }
                }
            """,
                    [idle_time_ms, timeout_ms],
                )
            )

            # Race between evaluation and timeout
            done, pending = await asyncio.wait(
                [evaluate_future, timeout_future], return_when=asyncio.FIRST_COMPLETED
            )

            # Cancel pending tasks
            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            if evaluate_future in done:
                result = await evaluate_future
                if result:
                    self.logger.info("Custom network idle detected")
                else:
                    self.logger.warning(
                        f"Custom network idle timeout after {timeout_ms}ms"
                    )
            else:
                self.logger.warning(
                    "Custom network idle detection timed out on Python side"
                )

        except Exception as e:
            self.logger.warning(f"Custom network idle check failed: {e}")
            # Fallback to old polling method
            await self._wait_for_custom_network_idle_fallback(timeout_ms, idle_time_ms)

    async def _wait_for_custom_network_idle_fallback(
        self, timeout_ms: int = 10000, idle_time_ms: int = 500
    ) -> None:
        """
        Fallback polling-based network idle detection.
        """
        start_time = asyncio.get_event_loop().time()
        timeout_seconds = timeout_ms / 1000

        self.logger.info("Using fallback network idle detection")

        while True:
            try:
                # Check if our network tracker is available and if network is idle
                is_idle = await self.page.evaluate(
                    """
                    (idleTimeMs) => {
                        if (typeof window.__networkActivity === 'undefined') {
                            return true; // Fallback if tracker not available
                        }
                        return window.__networkActivity.isIdle(idleTimeMs);
                    }
                """,
                    idle_time_ms,
                )

                if is_idle:
                    self.logger.info("Custom network idle detected (fallback)")
                    break

                # Check timeout
                if (asyncio.get_event_loop().time() - start_time) >= timeout_seconds:
                    self.logger.warning(
                        f"Custom network idle timeout after {timeout_ms}ms (fallback)"
                    )
                    break

                # Wait a bit before checking again
                await asyncio.sleep(0.1)

            except Exception as e:
                self.logger.warning(f"Custom network idle fallback check failed: {e}")
                break

    async def observation(self):
        """Get parsed page content using the parser script"""
        parser_script_path = Path(self.config.parser_script_path)
        content = {}

        # Wait for page to be fully loaded and stable
        try:
            self.logger.info("Waiting for page to be fully loaded and stable")
            await self.page.wait_for_load_state(
                "domcontentloaded",
                timeout=self.config.browser.timeouts.page_load_domcontent,
            )

            # Use both original networkidle (for page loads) and custom detection (for XHR/fetch)
            try:
                # First wait for Playwright's networkidle (handles initial page loads well)
                await self.page.wait_for_load_state(
                    "networkidle",
                    timeout=self.config.browser.timeouts.page_load_networkidle,
                )  # Shorter timeout
                self.logger.info("Playwright networkidle detected")
            except Exception as e:
                self.logger.info(f"Playwright networkidle timeout (normal): {e}")

            # Then wait for custom network idle detection (handles XHR/fetch after interactions)
            await self._wait_for_custom_network_idle(
                timeout_ms=self.config.browser.timeouts.page_load_networkidle,
                idle_time_ms=self.config.browser.timeouts.custom_network_idle,
            )
            if self.wait_hook:
                await self.wait_hook(self.page)

            self.logger.info("Page loaded and stable")
        except Exception as e:
            self.logger.warning(f"Page load wait timeout: {e}")

        # Additional safety check - wait for body element
        # try:
        #     await self.page.wait_for_selector(
        #         "body", timeout=self.config.browser.timeouts.element_wait
        #     )
        # except Exception as e:
        #     self.logger.warning(f"Body element not found: {e}")

        if parser_script_path.exists():
            with open(parser_script_path) as f:
                parser_code = f.read()
            try:
                content = await self.page.evaluate(parser_code)
            except Exception as e:
                self.logger.error(f"Parser script failed: {e}")
                # Fallback to basic HTML content
                content = {"html": await self.page.content()}
        else:
            self.logger.warning(f"Parser script not found: {parser_script_path}")
            content = {"html": await self.page.content()}

        # Add tabs information to the observation
        content["tabs"] = await self._get_tabs_info()

        # Add model answer if available
        content["model_answer"] = self.model_answer

        # Add evaluation information
        if (
            self.task_config
            and "eval" in self.task_config
            and self.config.get("evaluation", {}).get("enabled", True)
        ):
            score = await self.evaluate_task()
            content["score"] = score

            # Always terminate if model called terminate
            content["terminated"] = self.model_answer is not None or score != 0.0
        else:
            content["score"] = 0.0
            content["terminated"] = self.model_answer is not None

        return content

    async def close(self):
        """Clean up pages/contexts/browsers deterministically, then shrink PW refcount.

        Steps 1-3 talk to a browser that may already be wedged, so the caller
        runs this under a timeout. Everything that must happen regardless lives
        in the finally: releasing the shared-driver claim is what the *next*
        run depends on, and it cannot be allowed to depend on a hung browser.
        """
        try:
            # 1) Stop tracing/recording scoped to this context
            with contextlib.suppress(Exception):
                await self._stop_tracing()

            # 2) Close per-env resources (these are always safe to close)
            with contextlib.suppress(Exception):
                if self.page:
                    await self.page.close()
            with contextlib.suppress(Exception):
                if self.context:
                    await self.context.close()

            # 3) Close this env's browser (you launch a new browser per env in setup)
            with contextlib.suppress(Exception):
                if self.browser:
                    await self.browser.close()

            # 4) Null out handles (helps debugging and double-close safety)
            self.page = None
            self.context = None
            self.browser = None
        finally:
            # 5) Release the shared Playwright claim; stop the driver only when
            #    the last user is gone (stopping early would nuke other envs).
            if self.context_manager:  # doubles as the "shared PW" handle
                self.context_manager = None
                pw = self._release_playwright()
                if pw is not None:
                    with contextlib.suppress(Exception):
                        await pw.stop()

    async def _delete_container_with_retry(
        self,
        incus_server_url: str,
        container_name: str,
        proxy_server: str | None,
        max_retries: int = 2,
    ) -> bool:
        """
        Delete a container with retry logic.

        Args:
            incus_server_url: Incus server URL
            container_name: Name of container to delete
            proxy_server: Optional proxy server URL for HTTP requests
            max_retries: Maximum number of retry attempts

        Returns:
            bool: True if deletion was successful, False otherwise
        """
        from rl_web_agent.incus_client import delete_container

        for attempt in range(max_retries + 1):
            try:
                await delete_container(
                    incus_server_url, container_name, proxy_server=proxy_server
                )
                self.logger.info(f"🗑️ Deleted container {container_name}")
                return True
            except Exception as e:
                if attempt < max_retries:
                    self.logger.warning(
                        f"⚠️ Failed to delete {container_name} (attempt {attempt + 1}/{max_retries + 1}): {e}"
                    )
                    await asyncio.sleep(1)  # Wait 1 second before retry
                else:
                    self.logger.error(
                        f"❌ Final failure deleting {container_name}: {e}"
                    )
                    return False
        return False

    # ===================================================================
    # DEBUG HELPERS
    # ===================================================================

    def debug_pause(self):
        input(
            "[DEBUG] Environment paused for manual page navigation, log in, etc. Press ENTER to continue."
        )
