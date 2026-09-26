"""The server half of the policy: fetch this user's fragment from the gateway.

The VK comes from api-key-helper's cache (`vk-cache.json`), read only — api-key-helper
refreshes and rewrites that file, and two writers would race. The last good answer
is cached so that a later failure still yields that policy.
"""
from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable
from pathlib import Path

from policy_helper import __version__

FETCH_TIMEOUT_SECONDS = 3.0  # well inside policyHelper.timeoutMs (10 s)


def merge(baseline: dict, fragment: dict) -> dict:
    """Fragment wins; nested objects (e.g. env) are merged one level deep."""
    merged = dict(baseline)
    for key, value in fragment.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def resolve_fragment(
    baseline: dict,
    *,
    vk_path: Path,
    cache_path: Path,
    fetch: Callable[[str, str, float], dict],
    now: float,
    note: Callable[[str], None],
) -> dict:
    """This user's fragment: from the gateway if possible, else the last good one, else {}.

    Never raises for expected trouble — every problem becomes a note and a fallback.
    """
    env = baseline.get("env") or {}
    base_url = env.get("ANTHROPIC_BASE_URL")
    if not isinstance(base_url, str) or not base_url:
        note("baseline has no env.ANTHROPIC_BASE_URL; skipping server policy")
        return {}
    url = base_url.rstrip("/") + "/v1/policy"

    vk = _usable_vk(vk_path, env.get("GATEWAY_CLI_GATEWAY_URL"), now, note)
    if vk is not None:
        try:
            body = fetch(url, vk, FETCH_TIMEOUT_SECONDS)
        except Exception as exc:  # noqa: BLE001 — any network/HTTP failure -> cache
            note(f"policy fetch failed ({type(exc).__name__}: {exc}); using cached policy")
        else:
            fragment = _valid_fragment(body)
            if fragment is not None:
                _save_cache(cache_path, url, body, note)
                return fragment
            note("policy answer was not a usable managedSettings object; using cached policy")
    return _cached_fragment(cache_path, url, note)


def _usable_vk(vk_path: Path, admin_api_url, now: float, note) -> str | None:
    try:
        vk = json.loads(vk_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        note("no VK cached yet (not logged in, or no request made); using cached policy")
        return None
    except (OSError, ValueError) as exc:
        note(f"VK cache unreadable ({exc}); using cached policy")
        return None
    if not isinstance(vk, dict) or not isinstance(vk.get("virtual_key"), str):
        note("VK cache has no virtual_key; using cached policy")
        return None
    if _same_url(vk.get("admin_api_url"), admin_api_url) is False:
        note("VK belongs to another deployment; not sending it")
        return None
    try:
        expired = float(vk.get("expires_at", 0)) <= now
    except (TypeError, ValueError):
        expired = True
    if expired:
        note("VK expired; using cached policy until api-key-helper renews it")
        return None
    return vk["virtual_key"]


def _same_url(a, b) -> bool | None:
    """False only when both are known and differ; None when either is missing."""
    if not isinstance(a, str) or not isinstance(b, str) or not a or not b:
        return None
    return a.rstrip("/").lower() == b.rstrip("/").lower()


def _valid_fragment(body) -> dict | None:
    fragment = body.get("managedSettings") if isinstance(body, dict) else None
    if not isinstance(fragment, dict):
        return None
    if any(not isinstance(k, str) or k.startswith("_") for k in fragment):
        return None
    return fragment


def _save_cache(cache_path: Path, url: str, body: dict, note) -> None:
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = cache_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"source": url, "response": body}), encoding="utf-8")
        tmp.replace(cache_path)  # atomic: a crash never leaves a half-written cache
    except OSError as exc:
        note(f"could not save policy cache ({exc})")


def _cached_fragment(cache_path: Path, url: str, note) -> dict:
    try:
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        note("no cached policy yet; baseline only")
        return {}
    except (OSError, ValueError) as exc:
        note(f"policy cache unreadable ({exc}); ignoring it")
        return {}
    if not isinstance(cached, dict) or cached.get("source") != url:
        note("policy cache is from another gateway; ignoring it")
        return {}
    fragment = _valid_fragment(cached.get("response"))
    return fragment if fragment is not None else {}



def http_fetch(url: str, vk: str, timeout: float) -> dict:
    """GET the policy with the VK. Non-200 and network errors raise OSError."""
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {vk}",
            "Accept": "application/json",
            "User-Agent": f"policy-helper/{__version__}",
        },
    )
    # The URL comes from the admin-only baseline (env.ANTHROPIC_BASE_URL), not from user input.
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def _data_dir(app: str, platform: str, env, home: Path) -> Path:
    if platform == "win32":
        return Path(env.get("LOCALAPPDATA") or home / "AppData" / "Local") / app
    if platform == "darwin":
        return home / "Library" / "Application Support" / app
    return Path(env.get("XDG_DATA_HOME") or home / ".local" / "share") / app


def vk_path_for(platform: str, env, home: Path) -> Path:
    """Where api-key-helper keeps the VK (same rules as gateway-cli's paths.py)."""
    override = env.get("GATEWAY_CLI_VK_CACHE")
    if override:
        return Path(override)
    data_dir = env.get("GATEWAY_CLI_DATA_DIR")
    return (Path(data_dir) if data_dir else _data_dir("gateway-cli", platform, env, home)) / "vk-cache.json"


def cache_path_for(platform: str, env, home: Path) -> Path:
    """Our own folder, so `gateway-cli clear` / uninstall neither touch nor miss it."""
    return _data_dir("policy-helper", platform, env, home) / "policy-cache.json"
