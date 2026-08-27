"""
Tests for run_viewport_scan() in viewport_scan.py.

Behavior under test:
  7. Detects horizontal overflow when a page has fixed-width elements wider than the mobile viewport
  8. Reports no overflow issues for a fully responsive page

A minimal HTTP server is started per test using Python's built-in http.server,
so Playwright can navigate to a real URL without any external network calls.
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from src.simulated_web_agent.main.trace_store import TraceStore
from src.simulated_web_agent.main.viewport_scan import run_viewport_scan

# ---------------------------------------------------------------------------
# Local HTTP server fixture
# ---------------------------------------------------------------------------

_OVERFLOW_HTML = b"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Overflow Test</title></head>
<body>
  <!-- This div is intentionally wider than any mobile viewport -->
  <div style="width:1200px; background:red;">Too wide</div>
  <p>Normal content</p>
</body></html>
"""

_RESPONSIVE_HTML = b"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{margin:0}div{max-width:100%;box-sizing:border-box}</style>
<title>Responsive Test</title></head>
<body>
  <div style="max-width:100%"><p>Fully responsive content</p></div>
</body></html>
"""


def _make_server(html: bytes) -> tuple[HTTPServer, str]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *args):
            pass  # suppress access log noise

    server = HTTPServer(("127.0.0.1", 0), Handler)  # port 0 = OS assigns free port
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{port}/"


def _seed_store_url(store: TraceStore, url: str) -> None:
    """Write a minimal observation so store.visited_urls() returns the given URL."""
    store.append_observation({"tabs": [{"url": url}]})


# ---------------------------------------------------------------------------
# Slice 7: detects horizontal overflow on a too-wide page at mobile viewport
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_viewport_scan_detects_overflow_on_non_responsive_page(store: TraceStore):
    server, url = _make_server(_OVERFLOW_HTML)
    try:
        _seed_store_url(store, url)
        findings = await run_viewport_scan(store, headless=True)
    finally:
        server.shutdown()

    mobile_findings = [f for f in findings if f.get("viewport") == "mobile"]
    assert mobile_findings, "Expected findings for mobile viewport"

    overflow_detected = any(f.get("horizontal_overflow") for f in mobile_findings)
    assert overflow_detected, "Expected horizontal overflow to be detected on the 1200px-wide page at mobile"

    # Results must also be persisted via store
    saved = store.read_viewport_findings()
    assert len(saved) == len(findings)


# ---------------------------------------------------------------------------
# Slice 8: no overflow issues for a responsive page
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_viewport_scan_reports_no_overflow_for_responsive_page(store: TraceStore):
    server, url = _make_server(_RESPONSIVE_HTML)
    try:
        _seed_store_url(store, url)
        findings = await run_viewport_scan(store, headless=True)
    finally:
        server.shutdown()

    overflow_detected = any(f.get("horizontal_overflow") for f in findings)
    assert not overflow_detected, "Responsive page should not trigger horizontal overflow"
