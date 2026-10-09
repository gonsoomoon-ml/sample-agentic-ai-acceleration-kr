"""대화 중간 도구 추가와 thinking 표시(updates)를 Bedrock 으로 넘긴다 (2026-10-09, US).

계정 기능 플래그가 켜진 Claude Code 2.1.29x 는 beta 를 더 보낸다. 그중 둘이 게이트웨이에서
대화마다 400 을 한 번 냈다(Claude Code 가 그 기능을 빼고 다시 보내 장애는 아님).

- ``thinking-display-updates-2026-08-18``: ``thinking.display: "updates"`` 를 연다. beta 를
  버리면 ``thinking.adaptive.display: Input should be 'summarized', 'omitted'`` 400
  (2026-10-08 US dev 실제 발생).
- ``inline-tools-2026-09-15``: system 메시지의 ``tool_addition`` / ``tool_removal`` 블록을
  연다. beta 를 버리면 ``Input tag 'tool_addition' ... does not match`` 400.

``inline-tools`` 는 설정만으로는 부족하다. Claude Code 는 advisor 도구를 ``tools`` 에 넣고
system 메시지에서 ``tool_addition`` 으로 가리키는데, 게이트웨이는 advisor 를 ``tools`` 에서만
지워 왔다. 그러면 beta 를 넘겨도 ``tool_addition/tool_removal references unknown tool
'advisor'`` 400 이다.

계약:
- 두 beta 는 기본 설정으로 넘어간다(짝 필드 없음). 그 beta 를 보내지 않는 클라이언트의
  본문은 이전과 같다;
- 지운 도구를 가리키는 ``tool_addition`` / ``tool_removal`` 블록도 지운다. 이름으로
  가리키는 것(``tool_reference``)과, 지울 타입의 정의를 직접 실은 것(``tool_definition``)
  모두. 남은 도구를 가리키는 블록은 그대로 둔다;
- 지운 블록의 ``cache_control`` 은 같은 메시지의 남은 마지막 블록으로 옮긴다(캐시 지점의 수와
  위치 유지). 메시지가 비면 안내 문구 한 줄을 넣고, ``output_config`` 같은 다른 키는 둔다;
- 지울 것이 없으면 같은 객체를 돌려준다;
- 웹 검색 루프는 system 메시지의 ``tool_addition`` 을 건드리지 않는다.

English: forward mid-conversation tool changes and thinking "updates" display to Bedrock
(2026-10-09, US). Claude Code 2.1.29x with account feature flags sends more betas; two of them
cost one 400 per conversation behind the gateway (Claude Code retried without the feature).
``inline-tools`` also needs a code change: Claude Code lists the advisor tool in ``tools`` and
points at it with a ``tool_addition`` block in a system message; the gateway removed the advisor
from ``tools`` only, so forwarding the beta alone still 400s ("references unknown tool
'advisor'"). Contract: both betas forward by default (no paired field) and clients that do not
send them get the old body; ``tool_addition`` / ``tool_removal`` blocks that point at a removed
tool — by name (``tool_reference``) or by an inline definition of a removed type
(``tool_definition``) — are removed, blocks for kept tools stay; a removed block's
``cache_control`` moves to the message's last remaining block, and an emptied message gets a
one-line note while its other keys (``output_config``) stay; nothing to remove → same object;
the web-search loop leaves the system message's ``tool_addition`` alone.
"""

from __future__ import annotations

import copy
import json
import time

import pytest

from app.services import upstream_compat
from app.services.upstream_compat import (
    apply_forwarded_betas,
    client_betas,
    forward_beta_map,
    strip_unsupported_server_tools,
)

DTU = "dangerous-tool-use-2026-09-03"
PTC = "per-turn-control-2026-07-01"
IT = "inline-tools-2026-09-15"
TD = "thinking-display-updates-2026-08-18"

#: 2.1.295 가 계정 플래그가 켜진 환경에서 보낸 13개(2026-10-09 캡처) 뒤에, 2026-10-08 US dev
#: 게이트웨이 로그(beta_dropped)에 나온 나머지 이름을 붙였다(이 넷의 순서는 가정).
#: English: the 13 betas 2.1.295 sent with account flags on (captured), followed by the other
#: names seen in the 2026-10-08 US dev gateway log (order of those four assumed).
FLAGGED_HEADER = (
    "claude-code-20250219,interleaved-thinking-2025-05-14,"
    "thinking-token-count-2026-05-13,context-management-2025-06-27,"
    "prompt-caching-scope-2026-01-05,mid-conversation-system-2026-04-07,"
    "per-turn-control-2026-07-01,mid-conversation-tool-changes-2026-07-01,"
    "inline-tools-2026-09-15,advisor-tool-2026-03-01,effort-2025-11-24,"
    "dangerous-tool-use-2026-09-03,afk-mode-2026-01-31,"
    "redact-thinking-2026-02-12,structured-outputs-2025-12-15,"
    "thinking-display-updates-2026-08-18,fallback-credit-2026-06-01"
)
#: 빈 설정(CLAUDE_CONFIG_DIR)의 2.1.295 — 2.1.289 와 같은 11개.
#: English: 2.1.295 with an empty config dir — the same 11 as 2.1.289.
CLEAN_HEADER = (
    "claude-code-20250219,interleaved-thinking-2025-05-14,"
    "thinking-token-count-2026-05-13,context-management-2025-06-27,"
    "prompt-caching-scope-2026-01-05,mid-conversation-system-2026-04-07,"
    "per-turn-control-2026-07-01,mid-conversation-tool-changes-2026-07-01,"
    "effort-2025-11-24,dangerous-tool-use-2026-09-03,afk-mode-2026-01-31"
)

ADVISOR = {"type": "advisor_20260301", "name": "advisor", "model": "claude-fable-5-1"}
READ = {"name": "Read", "description": "read a file", "input_schema": {"type": "object"}}
CC = {"type": "ephemeral"}
SAFEGUARDS = [{"type": "dangerous_tool_use",
               "classifier_context": {"v": 1, "permission_mode": "auto"}}]
PREFIXES = ("advisor_",)


def _add(name: str, *, cache: bool = False, kind: str = "tool_addition") -> dict:
    blk = {"type": kind, "tool": {"type": "tool_reference", "name": name}}
    if cache:
        blk["cache_control"] = dict(CC)
    return blk


def _text(t: str = "reminder", *, cache: bool = False) -> dict:
    blk = {"type": "text", "text": t}
    if cache:
        blk["cache_control"] = dict(CC)
    return blk


def _body(tools, *system_blocks, **system_keys) -> dict:
    """user 'hi' 다음에 system 메시지 하나. English: 'hi', then one system message."""
    return {"model": "claude-sonnet-5-5", "max_tokens": 10, "tools": tools,
            "messages": [{"role": "user", "content": "hi"},
                         {"role": "system", "content": list(system_blocks), **system_keys}]}


def _fmap():
    from app.config import Settings

    return forward_beta_map(Settings().bedrock_forward_betas)


@pytest.fixture(autouse=True)
def _fresh_log_once(monkeypatch):
    monkeypatch.setattr(upstream_compat, "_logged_dropped_betas", set())


# ── 설정 / settings ──────────────────────────────────────────────────────────
def test_default_forwards_inline_tools_and_thinking_display_updates_without_fields():
    fmap = _fmap()
    assert fmap[IT] is None and fmap[TD] is None


def test_flagged_client_gets_four_betas_in_header_order():
    out = {"anthropic_version": "bedrock-2023-05-31"}
    kept = apply_forwarded_betas(out, {"safeguards": SAFEGUARDS},
                                 client_betas([FLAGGED_HEADER]), _fmap())
    assert kept == [PTC, IT, DTU, TD]
    assert out["anthropic_beta"] == [PTC, IT, DTU, TD]


def test_clean_client_still_gets_the_same_two_betas():
    out = {"anthropic_version": "bedrock-2023-05-31"}
    kept = apply_forwarded_betas(out, {"safeguards": SAFEGUARDS},
                                 client_betas([CLEAN_HEADER]), _fmap())
    assert kept == [PTC, DTU]


# ── tool_addition / tool_removal 정리 / cleanup ──────────────────────────────
def test_tool_addition_naming_the_removed_advisor_goes_and_its_cache_mark_moves():
    body = _body([READ, ADVISOR], _text(), _add("advisor", cache=True),
                 output_config={"effort": "medium"})
    before = copy.deepcopy(body)
    out, removed = strip_unsupported_server_tools(body, PREFIXES)
    assert removed == ["advisor_20260301"]
    system = out["messages"][1]
    assert system["content"] == [_text(cache=True)]
    assert system["output_config"] == {"effort": "medium"}
    assert "advisor" not in json.dumps(out)
    assert body == before                            # caller's object untouched


def test_cache_mark_is_not_doubled_when_the_kept_block_already_has_one():
    body = _body([READ, ADVISOR], _text(cache=True), _add("advisor", cache=True))
    out, _ = strip_unsupported_server_tools(body, PREFIXES)
    assert out["messages"][1]["content"] == [_text(cache=True)]


def test_tool_addition_naming_a_kept_tool_stays():
    body = _body([READ, ADVISOR], _text(), _add("Read"), _add("advisor"))
    out, _ = strip_unsupported_server_tools(body, PREFIXES)
    assert out["messages"][1]["content"] == [_text(), _add("Read")]


def test_tool_removal_naming_the_removed_advisor_goes():
    body = _body([READ, ADVISOR], _text(), _add("advisor", kind="tool_removal"))
    out, _ = strip_unsupported_server_tools(body, PREFIXES)
    assert out["messages"][1]["content"] == [_text()]


def test_inline_definition_of_a_removed_type_goes_even_without_it_in_tools():
    inline = {"type": "tool_addition",
              "tool": {"type": "tool_definition", "definition": dict(ADVISOR)}}
    body = _body([READ], _text(), inline)
    body["messages"].append({"role": "system", "content": [
        _text("later"), _add("advisor", kind="tool_removal")]})
    out, removed = strip_unsupported_server_tools(body, PREFIXES)
    assert removed == ["advisor_20260301"]
    assert out["tools"] == [READ]
    assert out["messages"][1]["content"] == [_text()]
    assert out["messages"][2]["content"] == [_text("later")]


def test_system_message_left_empty_gets_a_note_and_keeps_its_other_keys():
    body = _body([READ, ADVISOR], _add("advisor", cache=True),
                 output_config={"effort": "high"})
    out, _ = strip_unsupported_server_tools(body, PREFIXES)
    system = out["messages"][1]
    [note] = system["content"]
    assert note["type"] == "text" and note["text"].strip()
    assert note["cache_control"] == CC
    assert system["output_config"] == {"effort": "high"}


def test_same_object_when_nothing_points_at_a_removed_tool():
    body = _body([READ], _text(), _add("Read"))
    out, removed = strip_unsupported_server_tools(body, PREFIXES)
    assert out is body and removed == []


# ── 라우트 / route: /v1/messages → Bedrock body ──────────────────────────────
async def test_route_sends_no_advisor_reference_and_forwards_inline_tools(monkeypatch):
    from tests.regression.test_high_bedrock_beta_forwarding import _sent_to_bedrock

    messages = [{"role": "user", "content": "hi"},
                {"role": "system", "output_config": {"effort": "medium"},
                 "content": [_text(), _add("advisor", cache=True)]}]
    sent = await _sent_to_bedrock(
        monkeypatch, header=FLAGGED_HEADER,
        extra={"tools": [READ, ADVISOR], "messages": messages,
               "safeguards": SAFEGUARDS})
    assert "advisor" not in json.dumps(sent)
    assert sent["anthropic_beta"] == [PTC, IT, DTU, TD]
    assert sent["messages"][1]["content"] == [_text(cache=True)]
    assert sent["safeguards"] == SAFEGUARDS


# ── 웹 검색 루프 / web-search loop ───────────────────────────────────────────
async def test_web_search_loop_keeps_the_system_tool_addition():
    from app.services import web_search_loop as wsl
    from tests.regression.test_high_websearch_safeguard_results import (
        BASH,
        CLIENT,
        SEARCH,
        _turn,
    )
    from tests.unit.test_web_search_loop import FakeMcp, FakeRequest, _aiter

    turns = iter([_turn(tool=SEARCH), _turn(text="done", tool=CLIENT)])
    sent: list[dict] = []

    async def invoke_stream(turn_body):
        sent.append(copy.deepcopy(turn_body))
        return 200, _aiter(next(turns)), {}, None

    async def on_usage(_u):
        return None

    base = {"messages": [{"role": "user", "content": "hi"},
                         {"role": "system", "content": [_text(), _add("Bash")]}],
            "tools": [BASH]}
    async for _ in wsl._anthropic_stream(
            invoke_stream=invoke_stream, base_body=base, mcp_client=FakeMcp(),
            request=FakeRequest(), on_usage=on_usage, max_iterations=5,
            deadline=time.monotonic() + 60, default_max_results=10):
        pass
    assert len(sent) == 2
    for turn_body in sent:
        system = turn_body["messages"][1]
        assert system["role"] == "system"
        assert [b["type"] for b in system["content"]] == ["text", "tool_addition"]
        assert system["content"][1]["tool"] == {"type": "tool_reference", "name": "Bash"}
