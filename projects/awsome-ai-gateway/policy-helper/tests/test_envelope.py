"""build_envelope: turns the built-in baseline file into the policyHelper envelope.

An unusable baseline raises BaselineError with a reason; run() turns that into
`{}` (contribute nothing) plus a note on stderr, because a failing helper stops
Claude Code from starting at all.
"""
import json

import pytest

from policy_helper.main import BaselineError, build_envelope


def write(tmp_path, text):
    p = tmp_path / "baseline.json"
    p.write_text(text, encoding="utf-8")
    return p


def test_baseline_is_wrapped_under_managedSettings(tmp_path):
    baseline = {"apiKeyHelper": "helper.exe", "env": {"ANTHROPIC_BASE_URL": "https://gw.example.com"}}
    path = write(tmp_path, json.dumps(baseline))

    assert build_envelope(path) == {"managedSettings": baseline}


def test_missing_baseline_is_rejected(tmp_path):
    with pytest.raises(BaselineError, match="not found"):
        build_envelope(tmp_path / "absent.json")


def test_malformed_baseline_is_rejected(tmp_path):
    with pytest.raises(BaselineError, match="not valid JSON"):
        build_envelope(write(tmp_path, '{"env": '))


def test_non_object_baseline_is_rejected(tmp_path):
    # Claude Code expects managedSettings to be an object; anything else would
    # be a schema violation that refuses the whole run.
    with pytest.raises(BaselineError, match="not a JSON object"):
        build_envelope(write(tmp_path, '["not", "an", "object"]'))


def test_baseline_over_stdout_limit_is_rejected(tmp_path):
    # Claude Code rejects helper stdout larger than 1 MiB and refuses to start.
    huge = {"env": {"PADDING": "x" * (1024 * 1024)}}
    with pytest.raises(BaselineError, match="1 MiB"):
        build_envelope(write(tmp_path, json.dumps(huge)))


def test_baseline_with_private_key_is_rejected(tmp_path):
    # Copying managed-settings.json into baseline.json brings gateway-cli's
    # `_gatewayCli` marker along; an unknown key in helper output fails the run.
    text = json.dumps({"apiKeyHelper": "helper.exe", "_gatewayCli": {"managed": True}})
    with pytest.raises(BaselineError, match="_gatewayCli"):
        build_envelope(write(tmp_path, text))


EXAMPLE = __import__("pathlib").Path(__file__).resolve().parents[1] / "baseline.example.json"


def test_shipped_example_builds_an_envelope():
    envelope = build_envelope(EXAMPLE)

    settings = envelope["managedSettings"]
    assert settings["apiKeyHelper"]
    assert settings["env"]["ANTHROPIC_BASE_URL"].startswith("https://")


def test_baseline_with_utf8_bom_is_accepted(tmp_path):
    # Notepad and `Set-Content -Encoding UTF8` on Windows PowerShell write a BOM.
    path = tmp_path / "baseline.json"
    path.write_bytes(b"\xef\xbb\xbf" + b'{"apiKeyHelper": "helper.exe"}')

    assert build_envelope(path) == {"managedSettings": {"apiKeyHelper": "helper.exe"}}
