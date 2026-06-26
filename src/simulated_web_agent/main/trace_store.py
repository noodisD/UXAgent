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
                    scrollable = max(page_height - viewport_height, 1)
                    depth_pct = round(min(100.0, scroll_top / scrollable * 100), 1)
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
