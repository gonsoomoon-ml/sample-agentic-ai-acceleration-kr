"""http_fetch against a real local HTTP server, and the per-OS default paths."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from policy_helper.policy import cache_path_for, http_fetch, vk_path_for

BODY = {"version": "v1", "managedSettings": {"model": "claude-sonnet-5"}}


class Recorder(BaseHTTPRequestHandler):
    status = 200
    body = BODY
    seen: dict = {}  # noqa: RUF012 — per-test scratch, reset by each request

    def do_GET(self):
        Recorder.seen = {"path": self.path, "auth": self.headers.get("Authorization"),
                         "ua": self.headers.get("User-Agent")}
        payload = json.dumps(Recorder.body).encode()
        self.send_response(Recorder.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    httpd = HTTPServer(("127.0.0.1", 0), Recorder)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    Recorder.status = 200
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_http_fetch_sends_the_vk_and_returns_the_json(server):
    body = http_fetch(f"{server}/v1/policy", "vk-abc", 3.0)

    assert body == Recorder.body
    assert Recorder.seen["path"] == "/v1/policy"
    assert Recorder.seen["auth"] == "Bearer vk-abc"
    assert Recorder.seen["ua"].startswith("policy-helper/")


def test_http_fetch_raises_on_non_200(server):
    Recorder.status = 503
    with pytest.raises(OSError):
        http_fetch(f"{server}/v1/policy", "vk-abc", 3.0)


def test_windows_paths_live_under_localappdata():
    env = {"LOCALAPPDATA": r"C:\Users\u\AppData\Local"}
    home = Path("/home/u")

    assert vk_path_for("win32", env, home) == Path(env["LOCALAPPDATA"]) / "gateway-cli" / "vk-cache.json"
    assert cache_path_for("win32", env, home) == (
        Path(env["LOCALAPPDATA"]) / "policy-helper" / "policy-cache.json"
    )


def test_vk_path_follows_gateway_cli_data_dir_override():
    # api-key-helper honours GATEWAY_CLI_DATA_DIR; read the VK where it writes it.
    env = {"LOCALAPPDATA": "x", "GATEWAY_CLI_DATA_DIR": "/data/gw"}

    assert vk_path_for("win32", env, Path("/home/u")) == Path("/data/gw") / "vk-cache.json"


def test_macos_and_linux_paths():
    home = Path("/home/u")

    assert vk_path_for("darwin", {}, home) == (
        home / "Library" / "Application Support" / "gateway-cli" / "vk-cache.json"
    )
    assert cache_path_for("linux", {}, home) == (
        home / ".local" / "share" / "policy-helper" / "policy-cache.json"
    )
