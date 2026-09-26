"""run: the process contract Claude Code relies on.

stdout carries exactly one JSON object; exit status is always 0. A non-zero exit,
a timeout or unparsable stdout makes Claude Code refuse to start, so every
failure has to end as `{}` (contribute nothing) plus a note on stderr.
"""
import io
import json

from policy_helper.main import run


def test_run_prints_one_envelope_and_exits_zero(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text('{"apiKeyHelper": "helper.exe"}', encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()

    code = run(path, out, err)

    assert code == 0
    assert json.loads(out.getvalue()) == {"managedSettings": {"apiKeyHelper": "helper.exe"}}


def test_run_survives_unexpected_error(tmp_path):
    # A directory where the file should be raises IsADirectoryError/PermissionError,
    # which build_envelope does not anticipate.
    (tmp_path / "baseline.json").mkdir()
    out, err = io.StringIO(), io.StringIO()

    code = run(tmp_path / "baseline.json", out, err)

    assert code == 0
    assert json.loads(out.getvalue()) == {}
    assert err.getvalue() != ""


def test_run_explains_unusable_baseline_on_stderr(tmp_path):
    out, err = io.StringIO(), io.StringIO()

    code = run(tmp_path / "absent.json", out, err)

    assert code == 0
    assert json.loads(out.getvalue()) == {}
    assert "not found" in err.getvalue()


def ascii_stream():
    # Stands in for a Windows pipe in a legacy code page: characters it cannot
    # encode raise UnicodeEncodeError on write.
    return io.TextIOWrapper(io.BytesIO(), encoding="ascii")


def test_run_survives_stderr_that_cannot_encode_the_message(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text('{"_gatewayCli": {}}', encoding="utf-8")  # message will name the key
    out = io.StringIO()
    err = ascii_stream()
    missing = tmp_path / "사용자" / "baseline.json"  # non-ASCII path in the reason

    assert run(missing, out, err) == 0
    assert json.loads(out.getvalue()) == {}


def test_stdout_is_ascii_even_for_non_ascii_values(tmp_path):
    path = tmp_path / "baseline.json"
    path.write_text('{"env": {"NOTE": "게이트웨이"}}', encoding="utf-8")
    out = ascii_stream()

    assert run(path, out, io.StringIO()) == 0
    out.flush()
    raw = out.buffer.getvalue()
    assert json.loads(raw)["managedSettings"]["env"]["NOTE"] == "게이트웨이"
