import pytest

pytest.importorskip("rich")

from pathlib import Path

from deployment.tui import cli, postdeploy as pd
from deployment.tui.preflight import CheckResult
from deployment.tui.steps import Step

FIX = Path(__file__).parent / "fixtures"


def _step(name, script, *args, skippable=False):
    return Step(name, ["bash", str(FIX / script), *args], skippable=skippable)


def test_preflight_table_all_ok_true():
    checks = [CheckResult("aws", True, "/usr/bin/aws"), CheckResult("jq", True, "/usr/bin/jq")]
    assert cli.preflight_table(checks) is True


def test_preflight_table_reports_failure():
    checks = [CheckResult("aws", True, "ok"), CheckResult("terraform", False, "not found")]
    assert cli.preflight_table(checks) is False


def test_run_and_report_success_over_fake_steps():
    # fake echo/exit-0 스크립트만 실행 — 실제 배포 없음
    wf = [_step("first", "ok.sh"), _step("second", "ok.sh")]
    assert cli.run_and_report(wf, "fake") is True


def test_run_and_report_stops_and_reports_failure():
    wf = [_step("boom", "fail.sh"), _step("never", "ok.sh")]
    assert cli.run_and_report(wf, "fake") is False


def test_run_and_report_treats_skippable_failure_as_success():
    # skippable 스텝이 실패해도 필수 스텝이 모두 통과하면 전체는 성공(완료)
    wf = [_step("skip", "fail.sh", skippable=True), _step("required", "ok.sh")]
    assert cli.run_and_report(wf, "fake") is True


def test_aws_account_id_parses_stdout(monkeypatch):
    import subprocess
    from types import SimpleNamespace

    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="123456789012\n", stderr=""),
    )
    assert cli.aws_account_id() == "123456789012"


def test_aws_account_id_none_on_failure(monkeypatch):
    import subprocess
    from types import SimpleNamespace

    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: SimpleNamespace(returncode=255, stdout="", stderr="denied"),
    )
    assert cli.aws_account_id() is None


def test_aws_account_id_none_when_cli_missing(monkeypatch):
    import subprocess

    def boom(*a, **k):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", boom)
    assert cli.aws_account_id() is None


def test_llm_tfstate_defaults_includes_account_suffix():
    # 버킷은 S3 전역 유일성 때문에 계정 접미 필수, 락테이블은 접미 없음
    bucket, table = cli.llm_tfstate_defaults("123456789012")
    assert bucket == "llm-gateway-vanilla-tfstate-123456789012"
    assert table == "llm-gateway-vanilla-tflock"


def test_llm_tfstate_defaults_fallback_without_account():
    bucket, table = cli.llm_tfstate_defaults(None)
    assert bucket == "llm-gateway-vanilla-tfstate"
    assert table == "llm-gateway-vanilla-tflock"


def test_unwrap_none_raises_cancelled():
    # questionary .ask()는 Ctrl-C/Esc 시 None → 취소로 변환
    with pytest.raises(cli.Cancelled):
        cli._unwrap(None)
    assert cli._unwrap("dev") == "dev"


def test_menu_maps_workflows():
    labels = [label for label, _ in cli.MENU]
    handlers = [handler for _, handler in cli.MENU]
    assert "LLM Gateway 배포" in labels
    assert cli.flow_llm in handlers
    assert cli.flow_tool in handlers
    assert cli.flow_all in handlers


def test_flow_all_runs_tool_after_llm_success(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "flow_llm", lambda: calls.append("llm") or True)
    monkeypatch.setattr(cli, "flow_tool", lambda: calls.append("tool") or True)
    assert cli.flow_all() is True
    assert calls == ["llm", "tool"]  # 순서 A→B 고정


def test_flow_all_skips_tool_when_llm_fails(monkeypatch):
    calls = []
    monkeypatch.setattr(cli, "flow_llm", lambda: calls.append("llm") or False)
    monkeypatch.setattr(cli, "flow_tool", lambda: calls.append("tool") or True)
    assert cli.flow_all() is False
    assert calls == ["llm"]  # A 실패 → B는 게이팅되어 미실행


def test_flow_tool_aborts_when_assets_missing(monkeypatch):
    # Tool GW 스크립트/terraform이 없으면 실제 배포 스텝 없이 즉시 중단
    ran = []
    monkeypatch.setattr(cli, "run_preflight", lambda tools: True)
    monkeypatch.setattr(cli, "tool_gateway_assets_ok", lambda: False)
    monkeypatch.setattr(cli, "run_and_report", lambda wf, title: ran.append(title) or True)
    assert cli.flow_tool() is False
    assert ran == []


def test_flow_teardown_tool_aborts_when_assets_missing(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "ask_select", lambda msg, choices: "tool")
    monkeypatch.setattr(cli, "run_preflight", lambda tools: True)
    monkeypatch.setattr(cli, "tool_gateway_assets_ok", lambda: False)
    monkeypatch.setattr(cli, "run_and_report", lambda wf, title: ran.append(title) or True)
    assert cli.flow_teardown() is False
    assert ran == []


def test_flow_teardown_cancel_runs_nothing(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "ask_select", lambda msg, choices: "__cancel__")
    monkeypatch.setattr(cli, "run_and_report", lambda wf, title: ran.append(title) or True)
    assert cli.flow_teardown() is False
    assert ran == []


def test_flow_teardown_wrong_token_aborts(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "ask_select", lambda msg, choices: "tool")
    monkeypatch.setattr(cli, "run_preflight", lambda tools: True)
    monkeypatch.setattr(cli, "tool_gateway_assets_ok", lambda: True)
    # 확인 문구를 틀리게 입력 → 삭제 안 함
    monkeypatch.setattr(cli, "ask_text", lambda msg, default="": "nope")
    monkeypatch.setattr(cli, "run_and_report", lambda wf, title: ran.append(title) or True)
    assert cli.flow_teardown() is False
    assert ran == []


def test_flow_teardown_correct_token_runs(monkeypatch):
    ran = []
    monkeypatch.setattr(cli, "ask_select", lambda msg, choices: "tool")
    monkeypatch.setattr(cli, "run_preflight", lambda tools: True)
    monkeypatch.setattr(cli, "tool_gateway_assets_ok", lambda: True)
    monkeypatch.setattr(cli, "ask_text", lambda msg, default="": "delete tool-gateway")
    monkeypatch.setattr(cli, "run_and_report", lambda wf, title: ran.append(title) or True)
    assert cli.flow_teardown() is True
    assert ran == ["Teardown Tool Gateway"]


def test_main_menu_exit_does_not_call_str(monkeypatch):
    # 종료 선택 시 센티널 반환 → 핸들러로 호출하지 않고 정상 종료 (회귀: 'str' not callable)
    monkeypatch.setattr(cli, "ask_select", lambda msg, choices: "__exit__")
    cli.main_menu()  # 예외 없이 반환되어야 함


def test_main_menu_runs_selected_handler_then_exits(monkeypatch):
    called = []
    seq = iter([lambda: called.append("ran"), "__exit__"])
    monkeypatch.setattr(cli, "ask_select", lambda msg, choices: next(seq))
    cli.main_menu()
    assert called == ["ran"]


def test_flow_all_returns_false_when_tool_fails(monkeypatch):
    # A 성공 + B 실패 → 전체는 False지만 B는 시도됨(non-fatal)
    calls = []
    monkeypatch.setattr(cli, "flow_llm", lambda: calls.append("llm") or True)
    monkeypatch.setattr(cli, "flow_tool", lambda: calls.append("tool") or False)
    assert cli.flow_all() is False
    assert calls == ["llm", "tool"]


def test_render_endpoints_panel_shows_urls(capsys):
    eps = pd.Endpoints(items=[
        pd.Endpoint("admin-ui", "llm-gateway-admin-ui", "u.elb.amazonaws.com"),
    ])
    cli.render_endpoints_panel(eps)
    out = capsys.readouterr().out
    assert "u.elb.amazonaws.com" in out


def test_render_endpoints_panel_pending_message_when_empty(capsys):
    cli.render_endpoints_panel(pd.Endpoints(items=[]))
    out = capsys.readouterr().out
    assert "프로비저닝" in out or "준비" in out


def test_render_next_steps_includes_doc_path(capsys):
    cli.render_next_steps("dev")
    out = capsys.readouterr().out
    assert "09-post-deploy-tui.md" in out
    assert "KUBECONFIG" in out


def test_render_health_table_renders_all_states(capsys):
    results = [
        pd.HealthResult("pods", "ok", "6/6 Ready"),
        pd.HealthResult("gateway", "pending", "연결 안 됨"),
        pd.HealthResult("admin-ui", "check", "HTTP 500"),
    ]
    cli.render_health_table(results)
    out = capsys.readouterr().out
    assert "pods" in out and "gateway" in out and "admin-ui" in out


def test_show_postdeploy_summary_calls_discover_and_renders(monkeypatch):
    calls = []
    monkeypatch.setattr(cli.postdeploy, "discover_endpoints",
                        lambda **k: calls.append("discover") or pd.Endpoints(items=[]))
    monkeypatch.setattr(cli, "render_endpoints_panel", lambda e: calls.append("panel"))
    monkeypatch.setattr(cli, "render_next_steps", lambda env: calls.append(f"steps:{env}"))
    cli._show_postdeploy_summary("dev")
    assert calls == ["discover", "panel", "steps:dev"]


def test_show_postdeploy_summary_swallows_errors(monkeypatch):
    # discover 가 터져도 배포 성공 흐름을 깨면 안 된다
    def boom(**k):
        raise RuntimeError("kubectl exploded")
    monkeypatch.setattr(cli.postdeploy, "discover_endpoints", boom)
    monkeypatch.setattr(cli, "render_next_steps", lambda env: None)
    cli._show_postdeploy_summary("dev")  # 예외 없이 반환


def test_flow_llm_shows_summary_on_success(monkeypatch):
    # flow_llm 이 배포 성공(run_and_report True) 후 summary 를 부르는지
    seen = []
    monkeypatch.setattr(cli, "_show_postdeploy_summary", lambda env: seen.append(env))
    cli._maybe_postdeploy(True, "dev")
    assert seen == ["dev"]
    seen.clear()
    cli._maybe_postdeploy(False, "dev")  # 실패면 호출 안 함
    assert seen == []


def test_menu_includes_verify():
    labels = [label for label, _ in cli.MENU]
    handlers = [h for _, h in cli.MENU]
    assert "배포 검증 (Health Check)" in labels
    assert cli.flow_verify in handlers


def test_flow_verify_runs_discover_health_and_smoke(monkeypatch):
    order = []
    monkeypatch.setattr(cli, "run_preflight", lambda tools: True)
    monkeypatch.setattr(cli, "ask_select", lambda msg, choices: "dev")
    monkeypatch.setattr(cli.postdeploy, "discover_endpoints",
                        lambda **k: order.append("discover") or pd.Endpoints(items=[]))
    monkeypatch.setattr(cli, "render_endpoints_panel", lambda e: order.append("panel"))
    monkeypatch.setattr(cli.postdeploy, "live_healthcheck",
                        lambda eps, **k: order.append("health") or [])
    monkeypatch.setattr(cli, "render_health_table", lambda r: order.append("htable"))
    monkeypatch.setattr(cli, "run_and_report",
                        lambda wf, title: order.append("smoke") or True)
    monkeypatch.setattr(cli, "render_next_steps", lambda env: order.append("steps"))
    assert cli.flow_verify() is True
    assert order == ["discover", "panel", "health", "htable", "smoke", "steps"]


def test_flow_verify_aborts_on_preflight_fail(monkeypatch):
    monkeypatch.setattr(cli, "run_preflight", lambda tools: False)
    ran = []
    monkeypatch.setattr(cli, "run_and_report", lambda wf, title: ran.append(title) or True)
    assert cli.flow_verify() is False
    assert ran == []
