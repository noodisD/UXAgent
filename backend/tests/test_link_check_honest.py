"""A link the checker could not reach is "unchecked", never "working".

fetch() inside the page returned status 0 for a DNS failure, a timeout, a
refused connection and every CORS-blocked cross-origin link, and status 0 was
on the OK list: none of them could ever be reported, and all counted as
checked. The request client used now gets real statuses across origins.
"""
import asyncio
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from playwright.async_api import async_playwright

from simulated_web_agent.main.link_checker import _check_links_on_page, classify


@pytest.mark.parametrize("status,error,verdict", [
    (200, None, "ok"), (301, None, "ok"), (404, None, "broken"), (503, None, "broken"),
    (403, None, "refused"), (429, None, "refused"),
    (400, None, "refused"),  # facebook.com to an automated request: not a dead link
    (None, "net::ERR_NAME_NOT_RESOLVED", "broken"),
    (None, "getaddrinfo ENOTFOUND nie-ma-takiej.pl", "broken"),
    (None, "Timeout 8000ms exceeded", "unchecked"),
    (None, "connect ECONNREFUSED 127.0.0.1:9", "unchecked"),
    (0, None, "unchecked"),
])
def test_classification(status, error, verdict):
    assert classify(status, error) == verdict


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _reply(self, body=True):
        if self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            if body:
                port = self.server.server_address[1]
                self.wfile.write(f'''<a href="http://127.0.0.1:{port}/ok">ok</a>
                    <a href="http://127.0.0.1:{port}/gone">gone</a>
                    <a href="http://127.0.0.1:1/closed">closed port</a>'''.encode())
        else:
            self.send_response(404 if self.path == "/gone" else 200)
            self.end_headers()

    def do_GET(self):
        self._reply()

    def do_HEAD(self):
        self._reply(body=False)


async def test_on_a_real_server(tmp_path):
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    try:
        page = await browser.new_page()
        f = await _check_links_on_page(page, f"http://127.0.0.1:{port}/")
    finally:
        await browser.close()
        await pw.stop()
        server.shutdown()
    assert [b["url"] for b in f["broken"]] == [f"http://127.0.0.1:{port}/gone"]
    assert [u["url"] for u in f["unchecked"]] == ["http://127.0.0.1:1/closed"]
    assert f["links_checked"] == 2, "an unreachable link is not a checked one"
