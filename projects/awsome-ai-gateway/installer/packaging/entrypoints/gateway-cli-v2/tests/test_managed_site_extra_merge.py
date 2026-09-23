# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""The managed-tier site-extra merge (`managed.write_gateway_settings`, guideline 1-4).

The **user** tier's site-extra merge is characterized (test_char_user_settings) and
``build_gateway_env``'s own dict is pinned (test_char_managed_env), but nothing
exercised ``managed_extra()`` through ``write_gateway_settings`` — the branch at
``managed.py:514-566`` that folds a site's ``managed.env`` into the primary
managed-settings.json. That is the branch with the subtle parts: which side wins on a
key collision, the OTEL_* carve-out, and whether an injected key is recorded well
enough that ``disable`` can take it back out.

The pass-through itself is intentional: site-extra is a build-time file the packager
bakes in (``build.ps1`` copies ``packaging/site-extra.json`` next to ``cli/``), so an
operator can ship arbitrary env keys — including ones that would route Claude Code away
from the gateway (``ANTHROPIC_API_KEY``, ``CLAUDE_CODE_USE_BEDROCK``). It is not user
input and there is no denylist by design. What makes that safe is the last test here:
every injected key is listed in the ``_gatewayCli`` marker's ``envKeys``, so ``disable``
removes it and an org's own keys are the only thing left behind. If a refactor folds
site-extra env in without recording it, the keys become unremovable residue — that is
the failure this file exists to catch.

Runs unelevated by pointing the managed root at a tmp dir and forcing the
"Windows filesystem" write path, whose helpers are plain ``write_text``/``unlink``
(the Unix ones shell out to ``sudo``) — same trick as test_managed_remove_backup.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli import managed

_GW = "https://gw.example.com"
_ADMIN = "https://admin.example.com"
_HELPER = "/opt/gateway/api-key-helper"
_MARKER = managed.GATEWAY_MARKER_KEY


@pytest.fixture
def managed_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    root = tmp_path / "ClaudeCode"
    root.mkdir()
    monkeypatch.setattr(managed, "_managed_root", lambda: root)
    monkeypatch.setattr(managed, "_targets_windows_filesystem", lambda: True)
    monkeypatch.setenv("GATEWAY_CLI_BACKUP_DIR", str(tmp_path / "backups"))
    return root


@pytest.fixture
def site_extra(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Write a site-extra JSON and point GATEWAY_CLI_SITE_EXTRA at it."""

    def _write(payload: dict) -> Path:
        path = tmp_path / "site_extra.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setenv("GATEWAY_CLI_SITE_EXTRA", str(path))
        return path

    return _write


def _written(root: Path) -> dict:
    return json.loads((root / managed.MANAGED_SETTINGS_FILENAME).read_text(encoding="utf-8"))


def _write_settings() -> Path:
    return managed.write_gateway_settings(
        gateway_url=_GW, admin_api_url=_ADMIN, api_key_helper_path=_HELPER
    )


def test_site_extra_env_lands_in_the_managed_env(managed_root, site_extra) -> None:
    """A baked site key reaches the file verbatim — including a gateway-bypassing one.

    ``CLAUDE_CODE_USE_BEDROCK`` would send Claude Code straight to Bedrock, past the
    gateway (no VK, no cost row, no budget check). It is still written, because
    site-extra is the packager's own build-time file, not untrusted input. Pinned so
    that if a denylist is ever wanted it is a deliberate change with a failing test,
    not a silent one.
    """
    site_extra({"managed": {"env": {"HTTPS_PROXY": "http://proxy:8080",
                                    "CLAUDE_CODE_USE_BEDROCK": "1"}}})
    _write_settings()
    env = _written(managed_root)["env"]
    assert env["HTTPS_PROXY"] == "http://proxy:8080"
    assert env["CLAUDE_CODE_USE_BEDROCK"] == "1"
    # ...and our own routing keys are still there alongside it.
    assert env["ANTHROPIC_BASE_URL"] == _GW
    assert env["GATEWAY_CLI_GATEWAY_URL"] == _ADMIN


def test_site_extra_cannot_hijack_a_gateway_owned_key(managed_root, site_extra) -> None:
    """``gateway_env`` is applied last, so ANTHROPIC_BASE_URL can't be redirected.

    This is the whole reason for the ``{**extra_env, **gateway_env}`` order. Flip it
    and a site-extra could point Claude Code at any endpoint while `setup` still
    reported success.
    """
    site_extra({"managed": {"env": {"ANTHROPIC_BASE_URL": "https://evil.example.com",
                                    "GATEWAY_CLI_GATEWAY_URL": "https://evil.example.com"}}})
    _write_settings()
    env = _written(managed_root)["env"]
    assert env["ANTHROPIC_BASE_URL"] == _GW
    assert env["GATEWAY_CLI_GATEWAY_URL"] == _ADMIN


def test_otel_keys_are_the_deliberate_exception(managed_root, site_extra) -> None:
    """A site pins its own collector: site-extra OTEL_* wins over our derived value.

    Documented in OTEL_PRECEDENCE.md and implemented by the second loop in the
    branch. Without that loop the auto-derived per-signal endpoint would win and a
    corporate collector override would be silently ignored.
    """
    site_extra({"managed": {"env": {
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT": "http://site-collector:4318/v1/metrics",
    }}})
    managed.write_gateway_settings(
        gateway_url=_GW,
        admin_api_url=_ADMIN,
        api_key_helper_path=_HELPER,
        otel_endpoint="http://derived-collector:4318",
    )
    env = _written(managed_root)["env"]
    assert env["OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"] == "http://site-collector:4318/v1/metrics"
    # Only the overridden signal is replaced; the rest still derive from our base.
    assert env["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"].startswith("http://derived-collector:4318")


def test_site_extra_user_id_stays_dynamic(managed_root, site_extra) -> None:
    """A seeded OTEL_RESOURCE_ATTRIBUTES keeps its attrs but never its baked user.id.

    The one OTEL key site-extra does NOT fully own: a baked ``user.id`` would land
    identically on every PC, making per-user telemetry useless.
    """
    site_extra({"managed": {"env": {
        "OTEL_RESOURCE_ATTRIBUTES": "service.name=cc,user.id=baked@example.com",
    }}})
    managed.write_gateway_settings(
        gateway_url=_GW,
        admin_api_url=_ADMIN,
        api_key_helper_path=_HELPER,
        otel_endpoint="http://collector:4318",
        user_id="alice@corp",
    )
    attrs = _written(managed_root)["env"]["OTEL_RESOURCE_ATTRIBUTES"]
    assert "service.name=cc" in attrs
    assert "user.id=alice@corp" in attrs
    assert "baked@example.com" not in attrs


def test_site_extra_non_env_sections_are_merged_and_recorded(managed_root, site_extra) -> None:
    """Top-level site sections (permissions, …) are deep-merged and land in topKeys.

    ``topKeys`` is what `disable` reads to unmerge them, so a section merged without
    being recorded would be permanent.
    """
    site_extra({"managed": {"permissions": {"allow": ["Bash(git*)"]}}})
    _write_settings()
    data = _written(managed_root)
    assert data["permissions"] == {"allow": ["Bash(git*)"]}
    assert "permissions" in data[_MARKER]["topKeys"]


def test_injected_site_env_is_recorded_so_disable_takes_it_back_out(
    managed_root, site_extra
) -> None:
    """The property that makes the pass-through safe: it is fully reversible.

    Site-extra env is folded into ``gateway_env`` specifically so it lands in the
    marker's ``envKeys``; ``remove_gateway_settings`` then strips exactly those keys
    and leaves the org's own. An unrecorded key would survive `disable` forever — for
    a bypass key like ANTHROPIC_API_KEY that means a machine that quietly stops
    routing through the gateway even after the tool is disabled.
    """
    site_extra({"managed": {"env": {"HTTPS_PROXY": "http://proxy:8080"}}})
    mf = managed._managed_file()
    mf.write_text(json.dumps({"env": {"ORG_KEEP": "v"}}), encoding="utf-8")

    _write_settings()
    assert "HTTPS_PROXY" in _written(managed_root)[_MARKER]["envKeys"]

    assert managed.remove_gateway_settings() is True
    # fileExisted was True, so the reduced file is written back rather than deleted.
    reduced = _written(managed_root)
    assert reduced["env"] == {"ORG_KEEP": "v"}, "site-extra env must not survive disable"
    assert _MARKER not in reduced
