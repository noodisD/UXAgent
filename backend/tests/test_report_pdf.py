"""The downloadable report is a PDF rendered from the HTML by Chromium.

These exercise a real browser (like tests/test_viewport_a11y.py does), because
the whole point of the design is that Chromium's print engine — not a
re-implementation — produces the file.
"""

from pathlib import Path

from simulated_web_agent.main.report_pdf import HTML_NAME, PDF_NAME, ensure_report_pdf

_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Raport</title>
<style>@media print { body { background: #fff; } }</style></head>
<body><h1>Raport UX — zażółć gęślą jaźń</h1>
<img src="shot.png" alt="evidence">
</body></html>"""

# 1x1 PNG, so the relative-path resolution is exercised without a fixture file.
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753"
    "de0000000c4944415408d763f8cfc000000301010018dd8db00000000049454e"
    "44ae426082"
)


def _make_report(tmp_path: Path) -> Path:
    (tmp_path / HTML_NAME).write_text(_HTML, encoding="utf-8")
    (tmp_path / "shot.png").write_bytes(_PNG)
    return tmp_path


def test_returns_none_when_there_is_no_html_report(tmp_path):
    """The caller turns this into a 404. Rendering an empty PDF instead would
    hand the user a blank file and call it a report."""
    assert ensure_report_pdf(tmp_path) is None


def test_renders_a_real_pdf(tmp_path):
    run_dir = _make_report(tmp_path)
    pdf = ensure_report_pdf(run_dir)

    assert pdf is not None and pdf.name == PDF_NAME
    data = pdf.read_bytes()
    assert data.startswith(b"%PDF-")
    assert b"/Type /Page" in data or b"/Type/Page" in data


def test_is_cached_and_regenerates_when_the_html_changes(tmp_path):
    """Reports are re-rendered whenever styling or data changes; a cached PDF
    that outlived its HTML would quietly ship the previous version."""
    run_dir = _make_report(tmp_path)

    first = ensure_report_pdf(run_dir)
    stamp = first.stat().st_mtime_ns

    # Unchanged HTML: must reuse, not re-render.
    assert ensure_report_pdf(run_dir).stat().st_mtime_ns == stamp

    # Newer HTML: must re-render.
    html = run_dir / HTML_NAME
    html.write_text(_HTML.replace("Raport UX", "Raport UX v2"), encoding="utf-8")
    import os
    later = html.stat().st_mtime + 10
    os.utime(html, (later, later))

    assert ensure_report_pdf(run_dir).stat().st_mtime_ns != stamp
