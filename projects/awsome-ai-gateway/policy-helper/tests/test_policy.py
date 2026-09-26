"""resolve_fragment / merge: getting the server's policy fragment onto the baseline.

The helper reads the VK that api-key-helper cached (never writes it), asks the
gateway for this user's fragment, and remembers the last good answer so a later
failure (VPN off, gateway down, VK expired) still yields that policy.
"""
import json

from policy_helper.policy import merge, resolve_fragment

GW = "https://gateway.example.com"
ADMIN = "https://admin-api.example.com"
BASELINE = {
    "apiKeyHelper": "helper.exe",
    "env": {"ANTHROPIC_BASE_URL": GW, "GATEWAY_CLI_GATEWAY_URL": ADMIN, "ENABLE_TOOL_SEARCH": "true"},
}
NOW = 1_800_000_000.0
FRAGMENT = {"availableModels": ["claude-sonnet-5"], "enforceAvailableModels": True}


def write_vk(tmp_path, **over):
    vk = {"virtual_key": "vk-abc", "expires_at": NOW + 3600, "issuer_url": "https://idp",
          "admin_api_url": ADMIN, "user_id": "u1", "team_id": "t1"}
    vk.update(over)
    path = tmp_path / "vk-cache.json"
    path.write_text(json.dumps(vk), encoding="utf-8")
    return path


class Server:
    """Records calls; answers with a fixed body or raises."""

    def __init__(self, body=None, error=None):
        self.body, self.error, self.calls = body, error, []

    def __call__(self, url, vk, timeout):
        self.calls.append({"url": url, "vk": vk, "timeout": timeout})
        if self.error:
            raise self.error
        return self.body


def resolve(tmp_path, server, vk_path=None, notes=None):
    return resolve_fragment(
        BASELINE,
        vk_path=vk_path if vk_path is not None else write_vk(tmp_path),
        cache_path=tmp_path / "cache" / "policy-cache.json",
        fetch=server,
        now=NOW,
        note=(notes.append if notes is not None else (lambda m: None)),
    )


def test_merge_adds_fragment_keys_and_merges_nested_objects():
    merged = merge(BASELINE, {"model": "claude-sonnet-5", "env": {"ENABLE_TOOL_SEARCH": "false"}})

    assert merged["model"] == "claude-sonnet-5"
    assert merged["apiKeyHelper"] == "helper.exe"
    assert merged["env"] == {"ANTHROPIC_BASE_URL": GW, "GATEWAY_CLI_GATEWAY_URL": ADMIN,
                             "ENABLE_TOOL_SEARCH": "false"}
    assert BASELINE["env"]["ENABLE_TOOL_SEARCH"] == "true"  # inputs untouched


def test_fragment_is_fetched_with_the_vk_from_the_gateway(tmp_path):
    server = Server(body={"version": "v1", "managedSettings": FRAGMENT})

    assert resolve(tmp_path, server) == FRAGMENT
    assert server.calls == [{"url": f"{GW}/v1/policy", "vk": "vk-abc", "timeout": 3.0}]


def test_successful_fetch_is_cached_with_its_source(tmp_path):
    server = Server(body={"version": "v1", "managedSettings": FRAGMENT})
    resolve(tmp_path, server)

    cached = json.loads((tmp_path / "cache" / "policy-cache.json").read_text(encoding="utf-8"))
    assert cached["source"] == f"{GW}/v1/policy"
    assert cached["response"]["managedSettings"] == FRAGMENT


def prime_cache(tmp_path, fragment=None, source=f"{GW}/v1/policy"):
    """Leave a cached answer behind, as an earlier successful run would."""
    path = tmp_path / "cache" / "policy-cache.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"version": "old", "managedSettings": fragment or {"model": "cached"}}
    path.write_text(json.dumps({"source": source, "response": body}), encoding="utf-8")
    return body["managedSettings"]


def test_missing_vk_skips_the_server_and_uses_the_cache(tmp_path):
    cached = prime_cache(tmp_path)
    server = Server(body={"managedSettings": FRAGMENT})
    notes = []

    assert resolve(tmp_path, server, vk_path=tmp_path / "absent.json", notes=notes) == cached
    assert server.calls == []
    assert any("VK" in n for n in notes)


def test_expired_vk_skips_the_server(tmp_path):
    cached = prime_cache(tmp_path)
    server = Server(body={"managedSettings": FRAGMENT})

    assert resolve(tmp_path, server, vk_path=write_vk(tmp_path, expires_at=NOW - 1)) == cached
    assert server.calls == []


def test_vk_from_another_deployment_is_not_sent(tmp_path):
    # e.g. Claude Code set up for prod, Cowork logged in to dev on the same PC.
    cached = prime_cache(tmp_path)
    server = Server(body={"managedSettings": FRAGMENT})
    other = write_vk(tmp_path, admin_api_url="https://admin-api.other.example.com")

    assert resolve(tmp_path, server, vk_path=other) == cached
    assert server.calls == []


def test_server_error_falls_back_to_the_cache(tmp_path):
    cached = prime_cache(tmp_path)

    assert resolve(tmp_path, Server(error=OSError("timed out"))) == cached


def test_malformed_answer_is_ignored_and_does_not_replace_the_cache(tmp_path):
    cached = prime_cache(tmp_path)
    bad = Server(body={"managedSettings": {"_internal": True}})

    assert resolve(tmp_path, bad) == cached
    saved = json.loads((tmp_path / "cache" / "policy-cache.json").read_text(encoding="utf-8"))
    assert saved["response"]["managedSettings"] == cached


def test_non_object_answer_is_ignored(tmp_path):
    cached = prime_cache(tmp_path)

    assert resolve(tmp_path, Server(body={"managedSettings": ["x"]})) == cached


def test_no_cache_and_no_server_contributes_nothing(tmp_path):
    assert resolve(tmp_path, Server(error=OSError("down"))) == {}


def test_cache_from_another_gateway_is_not_used(tmp_path):
    prime_cache(tmp_path, source="https://gateway.other.example.com/v1/policy")

    assert resolve(tmp_path, Server(error=OSError("down"))) == {}


def test_corrupt_cache_contributes_nothing(tmp_path):
    path = tmp_path / "cache" / "policy-cache.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")

    assert resolve(tmp_path, Server(error=OSError("down"))) == {}


def test_missing_cache_is_named_in_the_notes(tmp_path):
    notes = []
    resolve(tmp_path, Server(error=OSError("down")), notes=notes)

    assert any("no cached policy" in n for n in notes)
