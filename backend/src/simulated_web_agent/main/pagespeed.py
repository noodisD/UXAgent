"""
Page Speed module — Google PageSpeed Insights API (mobile + desktop).

Runs once per analysis run (not per agent) since it measures the site URL,
not an individual browsing session. Results are stored at runs_dir/pagespeed.json.

Requires GOOGLE_PAGESPEED_API_KEY in .env (free, 25 000 req/day).
If the key is absent the module skips gracefully and logs a warning.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

_API_BASE = "https://www.googleapis.com/pagespeedonline/v5/runPagespeed"

# Speed Index pass/fail thresholds (seconds) — sourced from Google Lighthouse
_SI_THRESHOLDS: dict[str, dict[str, float]] = {
    "mobile":  {"good": 3.4, "poor": 5.8},
    "desktop": {"good": 1.3, "poor": 2.3},
}

# LCP thresholds
_LCP_THRESHOLDS: dict[str, float] = {"good": 2500, "poor": 4000}   # ms

# FCP thresholds
_FCP_THRESHOLDS: dict[str, float] = {"good": 1800, "poor": 3000}   # ms


def _threshold_status(numeric_ms: float | None, good: float, poor: float) -> str:
    if numeric_ms is None:
        return "unknown"
    return "good" if numeric_ms <= good else ("needs_improvement" if numeric_ms <= poor else "poor")


async def run_pagespeed_check(url: str, runs_dir: Path) -> dict:
    """
    Fetch PageSpeed Insights for `url` (mobile + desktop) and write
    runs_dir/pagespeed.json.  Returns the findings dict.
    """
    api_key = os.getenv("GOOGLE_PAGESPEED_API_KEY", "").strip()
    if api_key.startswith("YOUR_") or api_key == "":
        api_key = ""

    if not api_key:
        log.warning(
            "[pagespeed] GOOGLE_PAGESPEED_API_KEY not set — skipping. "
            "Add the key to .env to enable page speed analysis."
        )
        findings: dict = {"url": url, "skipped": True, "reason": "no_api_key"}
        _write(findings, runs_dir)
        return findings

    log.info("[pagespeed] Fetching scores for %s (mobile + desktop)...", url)

    results = await asyncio.gather(
        _fetch_strategy(url, "mobile",  api_key),
        _fetch_strategy(url, "desktop", api_key),
        return_exceptions=True,
    )

    findings = {
        "url": url,
        "mobile":  results[0] if not isinstance(results[0], Exception) else {"error": str(results[0])},
        "desktop": results[1] if not isinstance(results[1], Exception) else {"error": str(results[1])},
    }

    _write(findings, runs_dir)

    m_score = (findings.get("mobile") or {}).get("performance_score", "—")
    d_score = (findings.get("desktop") or {}).get("performance_score", "—")
    log.info("[pagespeed] Done — mobile: %s/100, desktop: %s/100", m_score, d_score)
    return findings


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _fetch_strategy(url: str, strategy: str, api_key: str) -> dict:
    params = urllib.parse.urlencode({
        "url": url,
        "strategy": strategy,
        "key": api_key,
        "category": "performance",
    })
    api_url = f"{_API_BASE}?{params}"
    raw = await asyncio.to_thread(_http_get, api_url)
    return _extract_metrics(json.loads(raw), strategy)


def _http_get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    # The API sends nothing until the Lighthouse run ends, and a mobile run takes
    # longer than 30 s: that limit cut the mobile measurement in both runs of
    # 2026-09-26/27 while desktop (faster) came through.
    with urllib.request.urlopen(req, timeout=120) as resp:
        return resp.read()


def _extract_metrics(data: dict, strategy: str) -> dict:
    lhr    = data.get("lighthouseResult", {})
    cats   = lhr.get("categories", {})
    audits = lhr.get("audits", {})

    def _audit(key: str) -> dict:
        a = audits.get(key, {})
        numeric = a.get("numericValue")
        return {
            "display":    a.get("displayValue", "—"),
            "score":      a.get("score"),           # 0–1 Lighthouse score
            "numeric_ms": round(numeric) if numeric is not None else None,
        }

    perf_raw = cats.get("performance", {}).get("score")
    perf_pct = round(perf_raw * 100) if perf_raw is not None else None

    si = _audit("speed-index")
    si_ms = si["numeric_ms"]
    si_thresh = _SI_THRESHOLDS.get(strategy, {})
    si_good_ms = si_thresh.get("good", 9999) * 1000
    si_poor_ms = si_thresh.get("poor", 9999) * 1000

    lcp = _audit("largest-contentful-paint")
    fcp = _audit("first-contentful-paint")

    return {
        "performance_score": perf_pct,
        "performance_label": _perf_label(perf_pct),
        "speed_index": {
            **si,
            "seconds": round(si_ms / 1000, 1) if si_ms is not None else None,
            "status":  _threshold_status(si_ms, si_good_ms, si_poor_ms),
            "thresholds_s": si_thresh,
        },
        "fcp": {
            **fcp,
            "status": _threshold_status(fcp["numeric_ms"],
                                         _FCP_THRESHOLDS["good"],
                                         _FCP_THRESHOLDS["poor"]),
        },
        "lcp": {
            **lcp,
            "status": _threshold_status(lcp["numeric_ms"],
                                         _LCP_THRESHOLDS["good"],
                                         _LCP_THRESHOLDS["poor"]),
        },
        "tbt": _audit("total-blocking-time"),
        "cls": _audit("cumulative-layout-shift"),
        "tti": _audit("interactive"),
    }


def _perf_label(score: int | None) -> str:
    if score is None:
        return "unknown"
    if score >= 90:
        return "good"
    if score >= 50:
        return "needs_improvement"
    return "poor"


def _write(findings: dict, runs_dir: Path) -> None:
    Path(runs_dir).mkdir(parents=True, exist_ok=True)
    (Path(runs_dir) / "pagespeed.json").write_text(
        json.dumps(findings, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def read_pagespeed(runs_dir: Path) -> dict | None:
    """Read pagespeed.json from runs_dir. Returns None if not present."""
    p = Path(runs_dir) / "pagespeed.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def format_pagespeed_summary(ps: dict | None) -> str:
    """
    Produce a concise plain-text summary for LLM prompts.
    Returns empty string if data is absent or skipped.
    """
    if not ps or ps.get("skipped"):
        return "(Page speed data not available)"

    lines = [f"URL: {ps.get('url', '—')}"]

    for strategy in ("mobile", "desktop"):
        data = ps.get(strategy) or {}
        if data.get("error"):
            # A measurement failure is not a site defect — phrase it so the
            # narrator does not read it as a page problem and lower the score.
            lines.append(f"{strategy.capitalize()}: pomiar niedostępny "
                         f"(błąd techniczny pomiaru, nie wada strony)")
            continue

        score = data.get("performance_score")
        label = data.get("performance_label", "").upper()
        si    = data.get("speed_index") or {}
        fcp   = data.get("fcp") or {}
        lcp   = data.get("lcp") or {}
        tbt   = data.get("tbt") or {}
        cls_  = data.get("cls") or {}
        thresh = si.get("thresholds_s") or {}

        lines.append(
            f"\n{strategy.capitalize()}: Performance {score}/100 [{label}]"
        )
        lines.append(
            f"  Speed Index : {si.get('display','—')}  "
            f"(good <{thresh.get('good','?')}s, poor >{thresh.get('poor','?')}s) "
            f"[{si.get('status','?').upper()}]"
        )
        lines.append(f"  FCP         : {fcp.get('display','—')}  [{fcp.get('status','?').upper()}]")
        lines.append(f"  LCP         : {lcp.get('display','—')}  [{lcp.get('status','?').upper()}]")
        lines.append(f"  TBT         : {tbt.get('display','—')}")
        lines.append(f"  CLS         : {cls_.get('display','—')}")

    return "\n".join(lines)
