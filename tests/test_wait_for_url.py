"""wait_for_url.py against a local HTTP server, with a fake clock for the timeouts."""

import importlib.util
import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("wait_for_url", ROOT / "actions" / "wait-for-url" / "wait_for_url.py")
wait_for_url = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wait_for_url)


@pytest.fixture
def server():
    """Answers with each status in turn, then the last one for good."""
    statuses = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status = statuses.pop(0) if len(statuses) > 1 else statuses[0]
            self.send_response(status)
            if 300 <= status < 400:
                self.send_header("Location", "/nowhere")
            self.end_headers()

        def log_message(self, *args):
            pass

    httpd = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def start(*answers):
        statuses[:] = answers
        return f"http://127.0.0.1:{httpd.server_port}/"

    yield start
    httpd.shutdown()


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def wait(url, timeout="5"):
    clock = Clock()
    return wait_for_url.main({"URL": url, "TIMEOUT": timeout}, clock=clock, sleep=clock.sleep), clock


def test_answers_at_once(server, capsys):
    status, _ = wait(server(200))
    assert status == 0
    assert "answered after 0s" in capsys.readouterr().out


def test_waits_through_errors(server, capsys):
    status, clock = wait(server(503, 500, 204))
    assert status == 0 and clock.now == 2
    assert "answered after 2s" in capsys.readouterr().out


def test_a_redirect_is_an_answer(server):
    assert wait(server(302))[0] == 0


def test_times_out(server, capsys):
    url = server(404)
    status, clock = wait(url, timeout="3")
    assert status == 1 and clock.now == 3
    assert f"::error::{url} did not answer within 3s" in capsys.readouterr().out


def test_nothing_listening(capsys):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    assert wait(f"http://127.0.0.1:{port}/", timeout="0")[0] == 1


def test_reads_the_process_environment(server, monkeypatch):
    monkeypatch.setenv("URL", server(200))
    monkeypatch.setenv("TIMEOUT", "1")
    assert wait_for_url.main() == 0
