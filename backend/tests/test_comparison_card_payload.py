"""The comparison card is translatable and does not cut words in half.

It carried English metric labels ("Overall score", "Task success") under Polish
headings, a C-locale month ("26 Sep 2026") and issue texts sliced at 160
characters mid-word."""
import json

from simulated_web_agent.main.results_api import _build_comparison, _clip


def test_clip_cuts_at_a_word_with_an_ellipsis():
    text = "Uczestnicy nie mogli znaleźć przycisku, który prowadzi do formularza " * 4
    clipped = _clip(text, 60)
    assert len(clipped) <= 61 and clipped.endswith("…")
    assert clipped[:-1] in text and not clipped[:-1].endswith(("który pro", "prowad"))
    assert _clip("krótki tekst", 60) == "krótki tekst"
    assert _clip(None) == ""


def _write_run(root, name, started, score):
    d = root / name
    d.mkdir()
    (d / "run_manifest.json").write_text(json.dumps(
        {"site": "hyundai.com", "status": "done", "started_at": started}), encoding="utf-8")
    (d / "aggregated_report.json").write_text(json.dumps({
        "narrative": {"executive_summary": "S.", "overall_ux_score": score, "critical_issues": [],
                      "serious_issues": [], "minor_issues": []},
        "statistics": {"heuristic": {"severity_counts": {}}, "accessibility": {"findings": []}},
    }), encoding="utf-8")
    return d


def test_the_payload_carries_keys_and_an_iso_date(tmp_path):
    _write_run(tmp_path, "2026-09-26_a", "2026-09-26T19:54:51+00:00", 3.1)
    now = _write_run(tmp_path, "2026-09-27_b", "2026-09-27T08:09:28+00:00", 3.4)
    manifest = json.loads((now / "run_manifest.json").read_text(encoding="utf-8"))
    report = json.loads((now / "aggregated_report.json").read_text(encoding="utf-8"))

    cmp = _build_comparison("2026-09-27_b", manifest, report, [], tmp_path)

    assert [m["key"] for m in cmp["metrics"]] == ["score", "taskSuccess", "critical", "total"]
    assert cmp["vsDateIso"] == "2026-09-26T19:54:51+00:00"
    assert cmp["vsDate"], "the server's string stays for older clients"


def test_every_metric_key_has_a_label_in_both_languages():
    from pathlib import Path
    i18n = (Path(__file__).resolve().parents[2] / "frontend" / "src" / "i18n.ts").read_text(encoding="utf-8")
    for key in ("score", "taskSuccess", "critical", "total"):
        assert i18n.count(f"'cmp.m.{key}'") == 2, key
