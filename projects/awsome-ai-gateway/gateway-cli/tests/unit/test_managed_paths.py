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
