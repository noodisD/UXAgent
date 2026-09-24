"""
TraceStore — single authority for the trace directory schema.

Every filename, subdirectory name, and JSON encoding decision lives here.
Callers read and write named artifacts; they never construct paths.

Usage:
    store = TraceStore(trace_dir)          # creates all subdirs
    store.append_observation(obs_dict)
    store.write_a11y_snapshot(step, data)
    findings = store.read_heuristic_findings()
"""
import json
from pathlib import Path
from typing import Any


def parse_action(entry: Any) -> dict:
    """Normalise one action_trace entry to a dict.

    Entries are written as dicts, as JSON strings, and occasionally as a
    single-element list wrapping the dict; every consumer has to cope with all
    three. This lived in six places (results_api, attribution, claim_verifier,
    session_goal_state, an inline copy in auditor, and experiment's
    _parse_action_dict) and they did not agree: five kept the raw text as
    `description` when the JSON was unparseable, the sixth returned {}, so the
    same malformed trace yielded a description at five call sites and nothing at
    the last. iso_metrics reached across modules for attribution's private copy.
    """
    if isinstance(entry, str):
        try:
            entry = json.loads(entry)
        except Exception:
            return {"description": entry}
    if isinstance(entry, list):
        entry = entry[0] if entry else {}
    return entry if isinstance(entry, dict) else {}

_SUBDIRS = (
    "simp_html",
    "raw_html",
    "api_trace",
    "screenshot",
    "observation_trace",
    "a11y_snapshots",
    "font_sizes",
)


class TraceStore:
    def __init__(self, trace_dir: Path):
        self.trace_dir = Path(trace_dir)
        self.run_name = self.trace_dir.name
        for sub in _SUBDIRS:
            (self.trace_dir / sub).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Screenshots — binary, callers still do the Playwright write;
    # TraceStore owns the path so filenames stay in one place.
    # ------------------------------------------------------------------

    def screenshot_path(self, step: int, full_page: bool = False) -> Path:
        suffix = "_full_page" if full_page else ""
        return self.trace_dir / "screenshot" / f"screenshot_{step}{suffix}.png"

    def landing_screenshot_path(self) -> Path:
        """The first screen as a visitor lands on it — before any action."""
        return self.trace_dir / "screenshot" / "screenshot_landing.png"

    def first_impression_screenshot(self) -> Path:
        """The frame the first-impression test should judge.

        The landing capture when the session has one; older sessions only have
        step 0, which is scrolled to the first click target.
        """
        landing = self.landing_screenshot_path()
        return landing if landing.exists() else self.screenshot_path(0)

    def scroll_top_path(self, step: int) -> Path:
        return self.trace_dir / "screenshot" / f"scroll_top_{step}.txt"

    # ------------------------------------------------------------------
    # HTML artifacts
    # ------------------------------------------------------------------

    def write_simp_html(self, step: int, html: str) -> None:
        (self.trace_dir / "simp_html" / f"simp_html_{step}.html").write_text(
            html, encoding="utf-8"
        )

    def write_raw_html(self, step: int, html: str) -> None:
        (self.trace_dir / "raw_html" / f"raw_html_{step}.html").write_text(
            html, encoding="utf-8"
        )

    def read_raw_html(self, step: int) -> str:
        """The page as the browser had it, before simplification.

        The only record of what was actually on screen that does not pass
        through the simplifier — which is what makes it usable as independent
        evidence when a finding claims content was missing.
        """
        path = self.trace_dir / "raw_html" / f"raw_html_{step}.html"
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def raw_html_size(self, step: int) -> int:
        """Byte size of the step's raw HTML capture (0 if absent). Used to tell
        a genuinely blank page from a perception artifact: a near-empty
        *observation* over a large raw page means the perceiver failed, not the
        site."""
        path = self.trace_dir / "raw_html" / f"raw_html_{step}.html"
        try:
            return path.stat().st_size if path.exists() else 0
        except Exception:
            return 0

    # ------------------------------------------------------------------
    # Observation trace (JSONL + per-step text)
    # ------------------------------------------------------------------

    def append_observation(self, obs: dict) -> None:
        with open(self.trace_dir / "observation_trace.jsonl", "a", encoding="utf-8") as f:
            json.dump(obs, f)
            f.write("\n")

    def write_observation_step_text(self, step: int, text: str) -> None:
        (self.trace_dir / "observation_trace" / f"observation_trace_{step}.txt").write_text(
            text, encoding="utf-8"
        )

    def read_observations(self) -> list[dict]:
        path = self.trace_dir / "observation_trace.jsonl"
        if not path.exists():
            return []
        result = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    result.append(json.loads(line))
                except Exception:
                    pass
        return result

    def url_at_step(self, step: int) -> str:
        """Return the active tab URL at a given observation step ('' if unknown)."""
        observations = self.read_observations()
        if not 0 <= step < len(observations):
            return ""
        tabs = observations[step].get("tabs") or []
        for tab in tabs:
            if tab.get("is_active") and tab.get("url", "").startswith("http"):
                return tab["url"]
        for tab in tabs:
            if tab.get("url", "").startswith("http"):
                return tab["url"]
        return ""

    def step_urls(self) -> list[str]:
        """The URL at each step, positionally — index i is step i's URL.

        Same definition as url_at_step, deliberately: auditor used to build the
        accessibility prompt from its own copy that always took tabs[0], while
        the heuristic prompt used url_at_step (active tab, http-only). When the
        agent had a second tab open the two passes cited different URLs for the
        same step — and evidence.resolve_evidence rewrites findings using
        url_at_step only, so the accessibility finding's `url` field was
        corrected while the prose the model wrote from the other list was not.
        """
        return [self.url_at_step(i) for i in range(len(self.read_observations()))]

    def read_observation_step_text(self, step: int) -> str:
        path = self.trace_dir / "observation_trace" / f"observation_trace_{step}.txt"
        if not path.exists():
            return ""
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return ""

    def visited_urls(self) -> list[str]:
        """Return unique HTTP URLs seen across all observation steps, in order."""
        urls: list[str] = []
        for obs in self.read_observations():
            for tab in obs.get("tabs") or []:
                url = tab.get("url")
                if url and url.startswith("http") and url not in urls:
                    urls.append(url)
        return urls

    # ------------------------------------------------------------------
    # Action trace
    # ------------------------------------------------------------------

    def write_action_trace(self, actions: list) -> None:
        (self.trace_dir / "action_trace.json").write_text(
            json.dumps(actions, indent=2), encoding="utf-8"
        )

    def read_action_trace(self) -> list:
        return self._read_json(self.trace_dir / "action_trace.json", fallback=[])

    # ------------------------------------------------------------------
    # Memory trace
    # ------------------------------------------------------------------

    def write_memory_trace(self, memories: list) -> None:
        (self.trace_dir / "memory_trace.json").write_text(
            json.dumps(memories), encoding="utf-8"
        )

    def read_memory_trace(self) -> list:
        return self._read_json(self.trace_dir / "memory_trace.json", fallback=[])

    def write_memory_trace_text(self, text: str) -> None:
        (self.trace_dir / f"{self.run_name}.txt").write_text(text, encoding="utf-8")

    # ------------------------------------------------------------------
    # Consent state — which steps ran under an unresolved cookie notice.
    # Written by experiment.py, read by attribution.py: an interaction blocked
    # by a consent overlay is our failure, not the site's, and must not be
    # reported as a defect.
    # ------------------------------------------------------------------

    def write_consent_states(self, states: dict[int, dict]) -> None:
        (self.trace_dir / "consent_states.json").write_text(
            json.dumps({str(k): v for k, v in states.items()}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def read_consent_states(self) -> dict[int, dict]:
        raw = self._read_json(self.trace_dir / "consent_states.json", fallback={}) or {}
        out: dict[int, dict] = {}
        for k, v in raw.items():
            try:
                out[int(k)] = v
            except (TypeError, ValueError):
                continue
        return out

    def unresolved_consent_steps(self) -> set[int]:
        """Steps where a notice was found and could NOT be dismissed.

        `attempted and not dismissed` is the only bad state. Finding no banner
        is the normal case, not a failure.
        """
        return {
            step for step, s in self.read_consent_states().items()
            if isinstance(s, dict) and s.get("attempted") and not s.get("dismissed")
        }

    # ------------------------------------------------------------------
    # Basic info
    # ------------------------------------------------------------------

    def write_basic_info(self, info: dict) -> None:
        (self.trace_dir / "basic_info.json").write_text(
            json.dumps(info, ensure_ascii=False), encoding="utf-8"
        )

    def read_basic_info(self) -> dict:
        return self._read_json(self.trace_dir / "basic_info.json", fallback={})

    # ------------------------------------------------------------------
    # Accessibility snapshots
    # ------------------------------------------------------------------

    def write_a11y_snapshot(self, step: int, data: Any) -> None:
        (self.trace_dir / "a11y_snapshots" / f"a11y_snapshot_{step}.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )

    def read_a11y_snapshots(self) -> dict[int, Any]:
        result: dict[int, Any] = {}
        for f in sorted((self.trace_dir / "a11y_snapshots").glob("a11y_snapshot_*.json")):
            try:
                step = int(f.stem.split("_")[-1])
                result[step] = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                pass
        return result

    # ------------------------------------------------------------------
    # Font sizes
    # ------------------------------------------------------------------

    def write_font_sizes(self, step: int, data: Any) -> None:
        (self.trace_dir / "font_sizes" / f"font_sizes_{step}.json").write_text(
            json.dumps(data), encoding="utf-8"
        )

    def read_font_sizes(self) -> dict[int, Any]:
        result: dict[int, Any] = {}
        for f in sorted((self.trace_dir / "font_sizes").glob("font_sizes_*.json")):
            try:
                step = int(f.stem.split("_")[-1])
                result[step] = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                pass
        return result

    # ------------------------------------------------------------------
    # Audit outputs
    # ------------------------------------------------------------------

    def write_first_impression(self, data: dict) -> None:
        (self.trace_dir / "first_impression.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def read_first_impression(self) -> dict | None:
        return self._read_json(self.trace_dir / "first_impression.json")

    def write_heuristic_findings(self, data: list) -> None:
        (self.trace_dir / "heuristic_findings.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def read_heuristic_findings(self) -> list:
        return self._read_json(self.trace_dir / "heuristic_findings.json", fallback=[])

    def write_accessibility_findings(self, data: dict) -> None:
        (self.trace_dir / "accessibility_findings.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def read_accessibility_findings(self) -> dict | None:
        return self._read_json(self.trace_dir / "accessibility_findings.json")

    def write_viewport_findings(self, data: list) -> None:
        (self.trace_dir / "viewport_findings.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def read_viewport_findings(self) -> list:
        return self._read_json(self.trace_dir / "viewport_findings.json", fallback=[])

    def write_link_findings(self, data: list) -> None:
        (self.trace_dir / "link_findings.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def read_link_findings(self) -> list:
        return self._read_json(self.trace_dir / "link_findings.json", fallback=[])

    def write_scroll_summary(self, data: dict) -> None:
        (self.trace_dir / "scroll_summary.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def read_scroll_summary(self) -> dict | None:
        return self._read_json(self.trace_dir / "scroll_summary.json")

    def write_form_funnel(self, data: dict) -> None:
        (self.trace_dir / "form_funnel.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def read_form_funnel(self) -> dict | None:
        return self._read_json(self.trace_dir / "form_funnel.json")

    def read_scroll_depths(self) -> list[dict]:
        """Read all scroll_top_*.txt files.

        Handles both the legacy format (plain float string written by the old
        experiment hook) and the new JSON format {scroll_top, page_height,
        viewport_height}.  Returns a list of dicts ordered by step, each
        containing scroll_top, page_height, viewport_height, depth_pct.
        """
        result: list[dict] = []
        screenshot_dir = self.trace_dir / "screenshot"
        if not screenshot_dir.exists():
            return result
        for f in sorted(
            screenshot_dir.glob("scroll_top_*.txt"),
            key=lambda p: int(p.stem.rsplit("_", 1)[-1])
            if p.stem.rsplit("_", 1)[-1].isdigit()
            else 0,
        ):
            try:
                step = int(f.stem.rsplit("_", 1)[-1])
                text = f.read_text(encoding="utf-8").strip()
                try:
                    data = json.loads(text)
                    scroll_top = float(data.get("scroll_top") or 0)
                    page_height = float(data.get("page_height") or 0)
                    viewport_height = float(data.get("viewport_height") or 0)
                    # A page no taller than the window cannot be scrolled at the
                    # document level — app-style pages like the Hyundai
                    # configurator scroll inside their own panels, and
                    # window.scrollY stays 0 whatever the participant does. This
                    # used to divide by max(…, 1) and report 0%, which reads as
                    # "measured: nobody scrolled". It is "not measurable".
                    room = page_height - viewport_height
                    depth_pct = (round(min(100.0, scroll_top / room * 100), 1)
                                 if room > 5 else None)
                except (json.JSONDecodeError, ValueError):
                    # Legacy format: plain number
                    scroll_top = float(text)
                    page_height = None
                    viewport_height = None
                    depth_pct = None
                result.append(
                    {
                        "step": step,
                        "scroll_top": scroll_top,
                        "page_height": page_height,
                        "viewport_height": viewport_height,
                        "depth_pct": depth_pct,
                    }
                )
            except Exception:
                pass
        return result

    # ------------------------------------------------------------------
    # Session result (how the browser session ended: steps, reason)
    # ------------------------------------------------------------------

    def write_session_result(self, data: dict) -> None:
        (self.trace_dir / "session_result.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def read_session_result(self) -> dict | None:
        return self._read_json(self.trace_dir / "session_result.json")

    def write_survey_result(self, data: dict) -> None:
        (self.trace_dir / "survey_result.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def read_survey_result(self) -> dict | None:
        return self._read_json(self.trace_dir / "survey_result.json")

    # ------------------------------------------------------------------
    # Persona card (structured persona fields for the dashboard)
    # ------------------------------------------------------------------

    def write_persona_card(self, data: dict) -> None:
        (self.trace_dir / "persona_card.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def read_persona_card(self) -> dict | None:
        return self._read_json(self.trace_dir / "persona_card.json")

    # ------------------------------------------------------------------
    # Objective reflection (structured: outcome, summary, blockers, wins, fix)
    # ------------------------------------------------------------------

    def write_objective_reflection(self, data: dict) -> None:
        (self.trace_dir / "objective_reflection.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def read_objective_reflection(self) -> dict | None:
        return self._read_json(self.trace_dir / "objective_reflection.json")

    def write_iso_metrics(self, data: dict) -> None:
        (self.trace_dir / "iso_metrics.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def read_iso_metrics(self) -> dict | None:
        return self._read_json(self.trace_dir / "iso_metrics.json")

    def write_bias_audit(self, data: dict) -> None:
        (self.trace_dir / "bias_audit.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def read_bias_audit(self) -> dict | None:
        return self._read_json(self.trace_dir / "bias_audit.json")

    def write_cognitive_walkthrough(self, data: list) -> None:
        (self.trace_dir / "cognitive_walkthrough.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def read_cognitive_walkthrough(self) -> list:
        return self._read_json(self.trace_dir / "cognitive_walkthrough.json", fallback=[])

    # ------------------------------------------------------------------
    # UX report
    # ------------------------------------------------------------------

    def write_ux_report(self, json_data: dict, md_text: str) -> None:
        (self.trace_dir / "ux_report.json").write_text(
            json.dumps(json_data, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (self.trace_dir / "ux_report.md").write_text(md_text, encoding="utf-8")

    def read_ux_report(self) -> dict | None:
        return self._read_json(self.trace_dir / "ux_report.json")

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _read_json(self, path: Path, fallback: Any = None) -> Any:
        if not path.exists():
            return fallback
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return fallback
