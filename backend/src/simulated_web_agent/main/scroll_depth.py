"""
Scroll depth analyzer — post-hoc analysis of scroll_top_*.txt files.

Each agent session already stores a scroll_top_<step>.txt file at every
step (written by experiment.py's before_action_hook).  Since the new format
also records page_height and viewport_height we can compute the scroll depth
percentage; old traces (plain float) degrade gracefully — max_depth_pct is
None but max_scroll_px is still available.

Saves:  trace_dir/scroll_summary.json
"""

from __future__ import annotations

import logging

from .trace_store import TraceStore

log = logging.getLogger(__name__)


def run_scroll_analysis(store: TraceStore) -> dict:
    """
    Aggregate scroll depth data from existing scroll_top_*.txt files.

    Returns and saves a summary dict with max/avg depth metrics.
    """
    depths = store.read_scroll_depths()

    if not depths:
        log.warning("[scroll] No scroll data in %s", store.trace_dir)
        summary: dict = {
            "max_depth_pct": None,
            "avg_depth_pct": None,
            "max_scroll_px": None,
            "steps_count": 0,
            "has_depth_pct": False,
        }
        store.write_scroll_summary(summary)
        return summary

    # The first recording is made before the participant has acted, so it is
    # never their behaviour. On the Hyundai configurator it read 2090 px of a
    # 3890 px page — 69.9%, the figure the report printed as every session's
    # "maximum scroll depth", identical across all three because it was the
    # page's landing position, not anything a participant did.
    first = min((d["step"] for d in depths), default=None)
    acted = [d for d in depths if d["step"] != first] or []
    depth_pcts = [d["depth_pct"] for d in acted if d.get("depth_pct") is not None]
    scroll_tops = [d["scroll_top"] for d in acted if d.get("scroll_top") is not None]
    # Steps whose page could not be scrolled at the document level at all.
    unmeasurable = sum(1 for d in acted if d.get("depth_pct") is None
                       and d.get("page_height") is not None)

    if depth_pcts:
        max_depth = round(max(depth_pcts), 1)
        avg_depth = round(sum(depth_pcts) / len(depth_pcts), 1)
        has_depth = True
    else:
        max_depth = None
        avg_depth = None
        has_depth = False

    summary = {
        "max_depth_pct": max_depth,
        "avg_depth_pct": avg_depth,
        "max_scroll_px": round(max(scroll_tops)) if scroll_tops else None,
        "steps_count": len(depths),
        "has_depth_pct": has_depth,
        # How much of the session the figure actually rests on, so the report
        # can say "measured on 4 of 12 steps" instead of implying all of them.
        "steps_measured": len(depth_pcts),
        "steps_unmeasurable": unmeasurable,
    }

    store.write_scroll_summary(summary)
    log.info(
        "[scroll] %s — max_depth=%s%%, avg_depth=%s%%, max_px=%s",
        store.trace_dir.name,
        max_depth,
        avg_depth,
        summary["max_scroll_px"],
    )
    return summary
