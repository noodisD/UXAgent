"""Pull the facts a reviewer would check out of a finished run.

Deliberately mechanical. Every number here is read off disk, so the comparison
against the reports Karolina was given rests on the trace rather than on a
model's summary of it — which is the same discipline the report itself is now
held to.

Usage:  python scripts/review_extract.py runs/<run_id> [runs/<run_id> ...]
"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

# Words that mark a finding as being about the consent notice. Matched against
# evidence + recommendation, which is where the disputed finding lived.
CONSENT_WORDS = ("cookie", "ciastecz", "zgod", "baner zgody", "rodo", "consent",
                 "onetrust", "didomi")
# ...and about the automation being detected rather than the site failing.
BOT_WALL_WORDS = ("cloudflare", "captcha", "recaptcha", "bot", "weryfikacj",
                  "verification", "are not a bot")


def _load(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _findings(persona_dir: Path) -> list:
    data = _load(persona_dir / "heuristic_findings.json", [])
    if isinstance(data, dict):
        data = data.get("findings") or []
    return [f for f in data if isinstance(f, dict)]


def _domains(persona_dir: Path) -> Counter:
    """Every distinct host the session actually landed on."""
    hosts = Counter()
    trace = persona_dir / "observation_trace.jsonl"
    if trace.is_file():
        for line in trace.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                obs = json.loads(line)
            except Exception:
                continue
            for tab in (obs.get("tabs") or []):
                url = str(tab.get("url") or "")
                m = re.match(r"https?://([^/]+)", url)
                if m:
                    hosts[m.group(1).lower()] += 1
    return hosts


def _registrable(host: str) -> str:
    parts = host.split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def examine(run_dir: Path) -> dict:
    manifest = _load(run_dir / "run_manifest.json", {}) or {}
    agg = _load(run_dir / "aggregated_report.json", {}) or {}
    narr = agg.get("narrative") or {}
    stats = agg.get("statistics") or {}

    personas = sorted(p for p in run_dir.iterdir()
                      if p.is_dir() and (p / "heuristic_findings.json").is_file())

    out = {
        "run_id": run_dir.name,
        "site": manifest.get("site"),
        "start_url": manifest.get("start_url"),
        "status": manifest.get("status"),
        "failure": manifest.get("failure"),
        "duration_s": manifest.get("duration_seconds"),
        "device": manifest.get("device"),
        "has_report": (run_dir / "aggregated_report.json").is_file(),
        "score": narr.get("overall_ux_score"),
        "issue_counts": {
            "critical": len(narr.get("critical_issues") or []),
            "serious": len(narr.get("serious_issues") or []),
            "minor": len(narr.get("minor_issues") or []),
        },
        "severity_counts": (stats.get("heuristic") or {}).get("severity_counts") or {},
        "session_devices": (stats.get("viewport") or {}).get("session_devices") or {},
        "scan_viewports": [d.get("viewport")
                           for d in (stats.get("viewport") or {}).get("devices") or []],
        "personas": [],
        # the four things the review actually complained about
        "checks": {},
    }

    all_findings, consent_states, host_counter = [], {}, Counter()
    for p in personas:
        fs = _findings(p)
        all_findings.extend(fs)
        cs = _load(p / "consent_states.json", {}) or {}
        consent_states[p.name] = cs
        hosts = _domains(p)
        host_counter.update(hosts)
        sr = _load(p / "session_result.json", {}) or {}
        out["personas"].append({
            "dir": p.name,
            "device": (_load(p / "persona_card.json", {}) or {}).get("device"),
            "steps": sr.get("steps_taken"),
            "ended_reason": sr.get("ended_reason"),
            "findings": len(fs),
            "gated": sum(1 for f in fs if f.get("harness_suspect")
                         or f.get("claim_verdict") in {"unsupported", "partial"}
                         or f.get("off_site")),
            "consent": cs,
            "hosts": dict(hosts),
        })

    def _text(f):
        return f"{f.get('evidence','')} {f.get('recommendation','')}".lower()

    def _reported(f):
        """Did this finding survive gating into the client-facing counts?"""
        return not (f.get("harness_suspect")
                    or f.get("claim_verdict") in {"unsupported", "partial"}
                    or f.get("off_site"))

    consent_findings = [f for f in all_findings if any(w in _text(f) for w in CONSENT_WORDS)]
    botwall_findings = [f for f in all_findings if any(w in _text(f) for w in BOT_WALL_WORDS)]

    target = _registrable(re.sub(r"^https?://", "", str(out["start_url"] or "")).split("/")[0])
    off_domain = {h: c for h, c in host_counter.items()
                  if _registrable(h) != target and h not in {"", "about:blank"}}

    out["checks"] = {
        # 1. the disputed cookie finding
        "consent_findings_total": len(consent_findings),
        "consent_findings_reported": sum(1 for f in consent_findings if _reported(f)),
        "consent_examples": [
            {"severity": f.get("severity"), "reported": _reported(f),
             "harness_suspect": f.get("harness_suspect"),
             "claim_verdict": f.get("claim_verdict"),
             "evidence": str(f.get("evidence"))[:220]}
            for f in consent_findings[:5]
        ],
        "consent_dismissed_everywhere": all(
            all(s.get("dismissed") for s in cs.values()) if cs else True
            for cs in consent_states.values()
        ),
        # 2. bot-wall blamed on the site (the hebe case)
        "botwall_findings_total": len(botwall_findings),
        "botwall_findings_reported": sum(1 for f in botwall_findings if _reported(f)),
        "botwall_examples": [
            {"severity": f.get("severity"), "reported": _reported(f),
             "harness_suspect": f.get("harness_suspect"),
             "evidence": str(f.get("evidence"))[:220]}
            for f in botwall_findings[:5]
        ],
        # 3. leaving the site under test (the lidl case)
        "target_domain": target,
        "off_domain_hosts": off_domain,
        # 4. ending with nothing (the t-mobile case)
        "terminal_status_written": out["status"] in {"done", "error", "cancelled"},
        "report_or_explicit_failure": bool(out["has_report"]) or bool(out["failure"]),
        # grounding, overall
        "findings_total": len(all_findings),
        "findings_reported": sum(1 for f in all_findings if _reported(f)),
        "gate_reasons": dict(Counter(
            ("harness_suspect" if f.get("harness_suspect") else
             "off_site" if f.get("off_site") else
             f"claim_{f.get('claim_verdict')}" if f.get("claim_verdict") in {"unsupported", "partial"}
             else "reported")
            for f in all_findings)),
    }
    return out


def main() -> int:
    results = [examine(Path(a)) for a in sys.argv[1:]]
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
