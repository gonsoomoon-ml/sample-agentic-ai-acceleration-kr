# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""Drift guard: the version a user can *see* matches the version we *shipped*.

Four places carry the version, and ``build.ps1`` treats ``pyproject.toml`` as the one
source of truth: it parses ``version = "…"`` and threads that single value into
PyInstaller (``$env:GATEWAY_CLI_VERSION``) and Inno (``/DAppVersion=``), which in turn
name the artifact ``gateway-cli-setup-<version>.exe`` and the Apps & Features entry.

``cli.__version__`` is the odd one out — nothing generates it, so it can silently drift
from pyproject, and it is the *only* one an end user can read on their own machine
(``gateway-cli version``). That matters concretely here: "do you have the build with
``gateway-cli codex``?" is answered by that command, so a stale ``__version__`` makes
a support answer wrong. Hence this test.

``installer.iss``'s ``#define AppVersion`` is a fallback for a hand-run
``ISCC.exe packaging\\installer.iss`` (``build.ps1`` always overrides it with
``/DAppVersion``), so it is checked too — a stale fallback mislabels a hand-built
installer.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from cli import __version__

_PKG_ROOT = Path(__file__).resolve().parents[1]  # …/entrypoints/gateway-cli-v2
_PYPROJECT = _PKG_ROOT / "pyproject.toml"


def _packaging_dir() -> Path | None:
    """Locate the packaging/ dir (holds build.ps1 + installer.iss) by walking up."""
    for parent in _PKG_ROOT.parents:
        if (parent / "build.ps1").is_file():
            return parent
    return None


def _pyproject_version() -> str:
    # Same regex shape build.ps1 uses (Select-String '^version\s*=\s*"([^"]+)"'), so a
    # spelling this test accepts is one the build can also parse.
    text = _PYPROJECT.read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    assert match, f"no parseable version in {_PYPROJECT}"
    return match.group(1)


def test_code_version_matches_pyproject():
    """`gateway-cli version` must print the version the installer was named after."""
    assert __version__ == _pyproject_version(), (
        "cli.__version__ drifted from pyproject.toml. build.ps1 names the installer "
        "from pyproject, but users read cli.__version__ — update both."
    )


def test_installer_iss_fallback_matches_pyproject():
    """The .iss default (used only by a hand-run ISCC) must not be stale."""
    packaging = _packaging_dir()
    if packaging is None:
        pytest.skip("packaging/ not reachable from the package root")
    iss = packaging / "installer.iss"
    if not iss.is_file():
        pytest.skip("installer.iss not present")

    # Inno source is Windows-authored; read leniently so an encoding quirk in an
    # unrelated line cannot fail the version check.
    text = iss.read_text(encoding="utf-8-sig", errors="replace")
    match = re.search(r'#define\s+AppVersion\s+"([^"]+)"', text)
    if match is None:
        pytest.skip("installer.iss no longer hard-codes an AppVersion fallback")
    assert match.group(1) == _pyproject_version(), (
        "installer.iss #define AppVersion is stale. build.ps1 overrides it with "
        "/DAppVersion, but a hand-run ISCC would emit a mislabelled installer."
    )


def test_the_postinstall_comment_matches_the_real_elevation_model(monkeypatch):
    """The `[Run]` comment justifying `runasoriginaluser` must stay true.

    It used to claim "Every surface gateway-cli touches is per-user", which was false:
    managed-settings.json lives under a machine-wide root and `setup`/`onboard` refuse
    outright on a non-elevated Windows token. Wrong here is expensive — it is the note
    the next person reads before deciding whether the flag is still needed, and dropping
    it silently onboards the admin account instead of the user.
    """
    packaging = _packaging_dir()
    if packaging is None:
        pytest.skip("packaging/ not reachable from the package root")
    iss = packaging / "installer.iss"
    if not iss.is_file():
        pytest.skip("installer.iss not present")
    text = iss.read_text(encoding="utf-8-sig", errors="replace")

    assert "runasoriginaluser" in text, "the flag the comment explains is gone"
    # The claim the comment now makes, asserted against the code it describes.
    assert "machine-wide" in text and "ensure_admin_for_setup" in text
    assert "Every surface gateway-cli touches is per-user" not in text

    from cli import managed
    from cli import setup as setup_module

    monkeypatch.setattr(managed.sys, "platform", "win32")
    root = str(managed._managed_root())
    assert "Program Files" in root  # machine-wide: this is the elevated surface
    assert "USERPROFILE" not in root.upper()
    assert callable(setup_module.ensure_admin_for_setup)
