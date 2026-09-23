# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""The writer and the readers of the two cache files must resolve the same path.

``gateway-cli login`` writes ``oidc-tokens.json`` / ``vk-cache.json`` through
:mod:`cli.paths`. Two other programs read them back through
:mod:`gateway_cli_oidc.oidc_client`: ``api-key-helper`` (every Claude Code request) and
``gateway-cli codex run``. Nothing links the two resolutions, so they can drift — and
they had: ``cli.paths`` honoured ``GATEWAY_CLI_DATA_DIR`` while the reader side went
straight to the platform default.

That failure is silent and total. ``login`` reports success, writes the token where it
said it would, and every later request looks for it somewhere else — so Claude Code gets
"OIDC id_token missing in cache. Run gateway-cli login" and Codex gets "no OIDC id_token
cached. Run: gateway-cli login", forever, from a working login. Hence a test on the paths
themselves rather than on either module's behaviour.

The manifest is checked too, because ``config --explain`` and the docs quote it: it
advertises both files as ``<data_dir>/…`` with a per-file env override, which is only
true if both sides implement that precedence.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from cli import manifest, paths
from gateway_cli_oidc import oidc_client

_ENV_VARS = ("GATEWAY_CLI_DATA_DIR", "GATEWAY_CLI_OIDC_CACHE", "GATEWAY_CLI_VK_CACHE")

#: (writer, reader) for each cached file. Both sides are called, never compared to a
#: hard-coded path, so the platform default stays whatever platformdirs says it is.
_PAIRS = (
    ("oidc-tokens.json", paths.oidc_tokens_path, oidc_client._token_cache_path),
    ("vk-cache.json", paths.vk_cache_path, oidc_client._vk_cache_path),
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Start from no overrides — a real one in the developer's shell would mask a bug."""
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)


@pytest.mark.parametrize(("filename", "writer", "reader"), _PAIRS)
def test_writer_and_reader_agree_with_no_overrides(filename, writer, reader):
    assert writer() == reader(), f"{filename}: login writes one path, the readers use another"
    assert writer().name == filename


@pytest.mark.parametrize(("filename", "writer", "reader"), _PAIRS)
def test_data_dir_override_moves_both_sides(filename, writer, reader, monkeypatch, tmp_path):
    """``GATEWAY_CLI_DATA_DIR`` relocates the directory holding both files."""
    monkeypatch.setenv("GATEWAY_CLI_DATA_DIR", str(tmp_path / "relocated"))

    assert writer() == tmp_path / "relocated" / filename
    assert reader() == writer()


@pytest.mark.parametrize(
    ("filename", "writer", "reader", "var"),
    [
        ("oidc-tokens.json", paths.oidc_tokens_path, oidc_client._token_cache_path,
         "GATEWAY_CLI_OIDC_CACHE"),
        ("vk-cache.json", paths.vk_cache_path, oidc_client._vk_cache_path,
         "GATEWAY_CLI_VK_CACHE"),
    ],
)
def test_the_per_file_override_wins_over_the_data_dir(
    filename, writer, reader, var, monkeypatch, tmp_path
):
    """Both sides must agree on precedence, not just on the paths themselves."""
    monkeypatch.setenv("GATEWAY_CLI_DATA_DIR", str(tmp_path / "dir"))
    exact = tmp_path / "exact" / f"custom-{filename}"
    monkeypatch.setenv(var, str(exact))

    assert writer() == exact
    assert reader() == exact


def test_the_data_dir_is_resolved_per_call_not_at_import(monkeypatch, tmp_path):
    """Set-then-read within one process has to work.

    The reader used to capture the directory in a module-level constant at import time,
    so anything setting the variable after the first import — a test, or `setup`
    persisting env into this process — was ignored.
    """
    first = tmp_path / "one"
    monkeypatch.setenv("GATEWAY_CLI_DATA_DIR", str(first))
    assert oidc_client._vk_cache_path().parent == first

    second = tmp_path / "two"
    monkeypatch.setenv("GATEWAY_CLI_DATA_DIR", str(second))
    assert oidc_client._vk_cache_path().parent == second


def test_manifest_matches_the_real_precedence(monkeypatch, tmp_path):
    """The catalog's ``<data_dir>/<name>`` + env_override claim, asserted against code."""
    by_name = {loc.name: loc for loc in manifest.LOCATIONS}
    monkeypatch.setenv("GATEWAY_CLI_DATA_DIR", str(tmp_path / "dd"))

    for name, filename, writer, var in (
        ("oidc_cache", "oidc-tokens.json", paths.oidc_tokens_path, "GATEWAY_CLI_OIDC_CACHE"),
        ("vk_cache", "vk-cache.json", paths.vk_cache_path, "GATEWAY_CLI_VK_CACHE"),
    ):
        loc = by_name[name]
        assert loc.paths["all"] == f"<data_dir>/{filename}"
        assert loc.env_override == var
        # "<data_dir>/" is a claim about the data dir, so it must track that override.
        assert writer() == paths.data_dir() / filename
        assert Path(str(paths.data_dir())) == tmp_path / "dd"
