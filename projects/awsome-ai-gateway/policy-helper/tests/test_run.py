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


# --- with the server step (PoC stage 2) -------------------------------------------

GW = "https://gateway.example.com"
ADMIN = "https://admin-api.example.com"
NOW = 1_800_000_000.0


def server_setup(tmp_path, fetch):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({
        "apiKeyHelper": "helper.exe",
        "env": {"ANTHROPIC_BASE_URL": GW, "GATEWAY_CLI_GATEWAY_URL": ADMIN},
    }), encoding="utf-8")
    vk = tmp_path / "vk-cache.json"
    vk.write_text(json.dumps({"virtual_key": "vk-abc", "expires_at": NOW + 60,
                              "admin_api_url": ADMIN}), encoding="utf-8")
    kwargs = {"vk_path": vk, "cache_path": tmp_path / "c" / "policy-cache.json",
              "fetch": fetch, "now": NOW}
    return kwargs, baseline


def test_run_lays_the_server_fragment_over_the_baseline(tmp_path):
    kwargs, baseline = server_setup(
        tmp_path, lambda url, vk, t: {"managedSettings": {"availableModels": ["claude-sonnet-5"]}})
    out, err = io.StringIO(), io.StringIO()

    assert run(baseline, out, err, **kwargs) == 0
    settings = json.loads(out.getvalue())["managedSettings"]
    assert settings["apiKeyHelper"] == "helper.exe"
    assert settings["availableModels"] == ["claude-sonnet-5"]


def test_run_explains_a_server_fallback_on_stderr(tmp_path):
    def down(url, vk, t):
        raise OSError("connection refused")

    kwargs, baseline = server_setup(tmp_path, down)
    out, err = io.StringIO(), io.StringIO()

    assert run(baseline, out, err, **kwargs) == 0
    assert json.loads(out.getvalue())["managedSettings"]["apiKeyHelper"] == "helper.exe"
    assert "policy fetch failed" in err.getvalue()


def test_run_drops_a_fragment_that_would_overflow_stdout_but_keeps_the_baseline(tmp_path):
    huge = {"env": {"PADDING": "x" * (1024 * 1024)}}
    kwargs, baseline = server_setup(tmp_path, lambda url, vk, t: {"managedSettings": huge})
    out, err = io.StringIO(), io.StringIO()

    assert run(baseline, out, err, **kwargs) == 0
    settings = json.loads(out.getvalue())["managedSettings"]
    assert settings["apiKeyHelper"] == "helper.exe"
    assert "PADDING" not in settings["env"]
    assert "1 MiB" in err.getvalue()
