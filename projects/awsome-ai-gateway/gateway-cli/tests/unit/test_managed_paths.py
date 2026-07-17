# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""Tests for cli.managed — the managed-settings.d path per platform.

Claude Code reads enterprise managed settings from a DIFFERENT directory on each
OS. Writing to the wrong one fails SILENTLY: `setup` prints success, the file is
created, and Claude Code never reads it — the user keeps their previous auth and
bypasses the gateway entirely (no budget / rate limit / cost accounting), with no
error anywhere. Verified on macOS 2026-07-17: file at /etc/claude-code/... was
ignored (`/status` showed only "User settings"); the same file copied to
/Library/Application Support/... flipped auth to apiKeyHelper immediately.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from cli import managed

WIN = Path(r"C:\Program Files\ClaudeCode\managed-settings.d")
MAC = Path("/Library/Application Support/ClaudeCode/managed-settings.d")
LINUX = Path("/etc/claude-code/managed-settings.d")
WSL = Path("/mnt/c/Program Files/ClaudeCode/managed-settings.d")


class TestManagedDir:
    def test_windows(self) -> None:
        with patch.object(managed.sys, "platform", "win32"):
            assert managed._managed_dir() == WIN

    def test_macos_uses_library_not_etc(self) -> None:
        # The regression this file exists for.
        with patch.object(managed.sys, "platform", "darwin"):
            assert managed._managed_dir() == MAC

    def test_plain_linux(self) -> None:
        with patch.object(managed.sys, "platform", "linux"), patch.object(
            managed, "_is_wsl", return_value=False
        ):
            assert managed._managed_dir() == LINUX

    def test_wsl_uses_windows_path(self) -> None:
        # WSL is sys.platform == "linux", but Claude Code there is the Windows
        # binary and reads the Windows path (via the /mnt/c mount).
        with patch.object(managed.sys, "platform", "linux"), patch.object(
            managed, "_is_wsl", return_value=True
        ):
            assert managed._managed_dir() == WSL


class TestManagedFile:
    def test_filename_appended(self) -> None:
        with patch.object(managed.sys, "platform", "darwin"):
            assert managed._managed_file() == MAC / "50-gateway.json"


class TestIsWsl:
    def test_true_when_proc_version_mentions_microsoft(self) -> None:
        with patch.object(Path, "read_bytes", return_value=b"Linux version 5.15 Microsoft-standard"):
            assert managed._is_wsl() is True

    def test_false_on_plain_linux(self) -> None:
        with patch.object(Path, "read_bytes", return_value=b"Linux version 6.8.0-1060-aws"):
            assert managed._is_wsl() is False

    def test_false_when_proc_version_missing(self) -> None:
        with patch.object(Path, "read_bytes", side_effect=OSError):
            assert managed._is_wsl() is False


class TestOidcEnvShipped:
    """api-key-helper reads OIDC_ISSUER_URL/OIDC_CLIENT_ID from its own process env
    (main.py:_detect_mode). Claude Code runs it with managed-settings' env, so setup
    must ship them — otherwise auth mode silently depends on the launching shell and
    flips to STS ("Run `aws sso login`") after a reboot.
    """

    def _write(self, tmp_path, **kw) -> dict:
        target = tmp_path / "50-gateway.json"
        with patch.object(managed, "_managed_file", return_value=target), patch.object(
            managed, "_write_unix"
        ) as w, patch.object(managed.sys, "platform", "linux"):
            managed.write_gateway_settings(
                gateway_url="http://gw",
                admin_api_url="http://admin",
                api_key_helper_path="api-key-helper",
                **kw,
            )
        import json

        return json.loads(w.call_args[0][1])

    def test_oidc_env_written_when_given(self, tmp_path) -> None:
        s = self._write(
            tmp_path, oidc_issuer_url="https://idp/pool", oidc_client_id="abc123"
        )
        assert s["env"]["OIDC_ISSUER_URL"] == "https://idp/pool"
        assert s["env"]["OIDC_CLIENT_ID"] == "abc123"

    def test_absent_when_not_given(self, tmp_path) -> None:
        s = self._write(tmp_path)
        assert "OIDC_ISSUER_URL" not in s["env"]
        assert "OIDC_CLIENT_ID" not in s["env"]

    def test_absent_when_only_one_given(self, tmp_path) -> None:
        # Half a config would still land in STS; don't pretend otherwise.
        s = self._write(tmp_path, oidc_issuer_url="https://idp/pool")
        assert "OIDC_ISSUER_URL" not in s["env"]
