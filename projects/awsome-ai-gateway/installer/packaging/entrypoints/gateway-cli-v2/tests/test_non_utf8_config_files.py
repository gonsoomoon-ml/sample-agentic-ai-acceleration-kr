# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""Non-UTF-8 (CP949 / Korean Windows) config files must not raise a raw traceback.

``UnicodeDecodeError`` subclasses :class:`ValueError`, **not** :class:`OSError`, so an
``except OSError`` tuple never catches it. Every read here targets a file the *user* or
*a build* owns, on a Korean Windows host where CP949 is the ANSI code page — so a
non-UTF-8 file is routine, not exotic, and the unguarded form crashes with a traceback.

The two call sites handle it deliberately differently, and these tests pin both:

* :func:`cli.setup._read_settings` **escalates** to :class:`SetupStepError` — returning
  ``{}`` would make the subsequent ``_write_settings`` clobber the user's real settings.
* :func:`cli.site_extra._load_raw` **skips and continues** — site-extra is optional
  build-injected config, so an unreadable one degrades to "absent".

These guards were added in ``6822414``, then lost in ``f4652dd`` (a non-merge commit
that restored the pre-fix blobs verbatim — a stale-snapshot overwrite, not a revert).
This file exists so a third clobber fails CI instead of shipping.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli.setup import SetupStepError, _read_settings
from cli.site_extra import _load_raw

# "한국어 설정" in CP949 — 0xc7 is an invalid UTF-8 continuation byte.
_CP949_KOREAN = "한국어 설정".encode("cp949")


def _cp949_json(path: Path) -> Path:
    """Write syntactically valid JSON whose bytes are CP949, not UTF-8."""
    path.write_bytes(b'{"theme":"' + _CP949_KOREAN + b'"}')
    return path


def test_read_settings_raises_setup_step_error_on_cp949(tmp_path: Path) -> None:
    """A CP949 settings.json surfaces as SetupStepError, not UnicodeDecodeError."""
    path = _cp949_json(tmp_path / "settings.json")
    with pytest.raises(SetupStepError, match="cannot read settings file"):
        _read_settings(path)


def test_read_settings_does_not_leak_unicode_decode_error(tmp_path: Path) -> None:
    """The raw decode error is chained as __cause__, never propagated bare."""
    path = _cp949_json(tmp_path / "settings.json")
    with pytest.raises(SetupStepError) as excinfo:
        _read_settings(path)
    assert isinstance(excinfo.value.__cause__, UnicodeDecodeError)


def test_read_settings_still_reads_utf8_korean(tmp_path: Path) -> None:
    """The guard must not regress the normal path: UTF-8 Korean still parses."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"theme": "한국어 설정"}, ensure_ascii=False), encoding="utf-8")
    assert _read_settings(path) == {"theme": "한국어 설정"}


def test_load_raw_skips_cp949_site_extra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An unreadable site-extra degrades to {} instead of crashing the settings write."""
    path = _cp949_json(tmp_path / "site-extra.json")
    monkeypatch.setenv("GATEWAY_CLI_SITE_EXTRA", str(path))
    assert _load_raw() == {}


def test_load_raw_still_reads_utf8_site_extra(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Normal path intact: a UTF-8 site-extra with Korean values still loads."""
    path = tmp_path / "site-extra.json"
    path.write_text(json.dumps({"otlpServiceName": "한국어"}, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("GATEWAY_CLI_SITE_EXTRA", str(path))
    assert _load_raw() == {"otlpServiceName": "한국어"}
