"""Bridge what an Anthropic-format client sends and what a Bedrock upstream accepts.

Behind ``ANTHROPIC_BASE_URL`` Claude Code speaks the full Anthropic Messages format, "even if
your gateway forwards to an Amazon Bedrock ... upstream. Bridging that difference is your
gateway's job" (Claude Code gateway compatibility guide). Bedrock rejects a request outright
when ``tools`` carries a server tool type it does not serve.

2026-09-18 (US): a Claude Code session with the advisor feature on put
``{"type": "advisor_20260301", ...}`` into every request; every request of that user failed
with ``400 ... tool type 'advisor_20260301' is not supported for this model`` — "hi" included.
Claude Code does not strip the tool and retry on that error wording, and ``/advisor off`` did
not help in the affected session.

Such tools are executed by Anthropic's API, not by the client and not by Bedrock. Removing one
turns the feature off quietly — the documented outcome "when both halves are absent together" —
instead of failing the whole session. The type prefixes come from settings
(``BEDROCK_UNSUPPORTED_TOOL_TYPE_PREFIXES``), so the next Anthropic-only tool type is a config
change, not a release.

2026-10-05(US) beta 쪽에서도 같은 차이가 있다. Claude Code 는
``anthropic-beta`` 값을 11개 보내는데, Bedrock InvokeModel 은 beta 를
본문(``anthropic_beta``)으로만 받고 모르는 beta 가 하나라도 있으면 요청
전체를 400 으로 거부한다. 그래서 통째로 넘길 수 없다.
``dangerous-tool-use``(짝 필드 ``safeguards`` 포함)와 ``per-turn-control``
만 넘기자 실제 Claude Code 2.1.289 세션에서 서버 분류기 판정이 오고, 400 과
과금 안내가 사라졌다. 넘길 목록은 설정(``BEDROCK_FORWARD_BETAS``)에서 읽는다.

English: 2026-10-05 (US): the same gap on the beta side. Claude Code sends 11
``anthropic-beta`` values; Bedrock InvokeModel takes betas only in the body
(``anthropic_beta``) and 400s the whole request on one it does not know, so
they cannot be forwarded wholesale. Forwarding exactly ``dangerous-tool-use``
(with its ``safeguards`` field) and ``per-turn-control`` gave server-side
classifier verdicts, no 400 and no billing notice in a real Claude Code 2.1.289
session. The list comes from settings (``BEDROCK_FORWARD_BETAS``).

2026-10-09(US) 대화 중간 도구 추가(``tool_addition``, beta ``inline-tools``)에도 같은
틈이 있다. Claude Code 는 advisor 를 ``tools`` 에 넣고 system 메시지에서 이름으로
가리키는데, ``tools`` 에서만 지우면 "references unknown tool 'advisor'" 400 이다. 그래서
지운 도구를 가리키는 추가·제거 블록도 함께 지운다.

English: 2026-10-09 (US): mid-conversation tool changes (``tool_addition``, beta
``inline-tools``) hit the same gap. Claude Code lists the advisor in ``tools`` and points at
it by name from a system message; removing it from ``tools`` only is a 400 ("references
unknown tool 'advisor'"), so tool-change blocks that point at a removed tool go too.

Not handled here: the native ``web_search_*`` tool (the web-search loop replaces it with the
gateway's own search) and replayed web-search blocks (``web_search_loop``).
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

#: Shown in place of an assistant message that held nothing but blocks of a removed tool
#: (an empty content list is a 400).
_REMOVED_NOTE = "[a server tool result that this endpoint cannot replay was removed]"
#: 지운 도구의 추가·제거 블록만 있던 system 메시지 자리에 남기는 문구(빈 content 는 400).
#: English: stands in for a system message that held only tool changes of a removed tool.
_REMOVED_TOOL_CHANGE_NOTE = "[a tool change that this endpoint cannot apply was removed]"
#: 대화 중간 도구 변경 블록. English: mid-conversation tool change blocks.
_TOOL_CHANGE_BLOCKS = ("tool_addition", "tool_removal")


def unsupported_tool_prefixes(raw: str | None) -> tuple[str, ...]:
    """``"advisor_, web_fetch_"`` → ``("advisor_", "web_fetch_")``; blank → ``()`` (off)."""
    return tuple(p.strip() for p in (raw or "").split(",") if p.strip())


def _family(tool_type: str) -> str:
    """``advisor_20260301`` → ``advisor`` — the prefix of that tool's result block types
    (``advisor_tool_result``), following the API's ``<family>_tool_result`` naming."""
    head, _, tail = tool_type.rpartition("_")
    return head if head and tail.isdigit() else tool_type


def _tool_change_target(block: Any) -> tuple[str | None, str | None]:
    """``tool_addition`` / ``tool_removal`` 블록이 가리키는 도구의 ``(name, type)``.

    English: ``(name, type)`` of the tool a ``tool_addition`` / ``tool_removal`` block points
    at — a ``tool_reference`` carries only the name, a ``tool_definition`` the whole tool.
    """
    if not (isinstance(block, dict) and block.get("type") in _TOOL_CHANGE_BLOCKS):
        return None, None
    tool = block.get("tool")
    if not isinstance(tool, dict):
        return None, None
    if tool.get("type") == "tool_definition":
        d = tool.get("definition")
        if isinstance(d, dict):
            t = d.get("type")
            return d.get("name"), t if isinstance(t, str) else None
        return None, None
    return tool.get("name"), None


def _inline_removed_types(messages: Any, prefixes: tuple[str, ...]) -> list[dict]:
    """system 메시지의 ``tool_addition`` 이 직접 실은, 지울 타입의 도구 정의들.

    English: tool definitions of a removed type carried inline by ``tool_addition`` blocks in
    system messages (the tool need not be in ``tools`` at all).
    """
    found: list[dict] = []
    if not isinstance(messages, list):
        return found
    for m in messages:
        if not (isinstance(m, dict) and m.get("role") == "system"
                and isinstance(m.get("content"), list)):
            continue
        for b in m["content"]:
            name, ttype = _tool_change_target(b)
            if ttype and ttype.startswith(prefixes):
                found.append({"type": ttype, "name": name})
    return found


def strip_unsupported_server_tools(body: Any, prefixes: tuple[str, ...]) -> tuple[Any, list[str]]:
    """Remove tools whose ``type`` starts with one of ``prefixes``.

    Returns ``(body, removed_types)``. The SAME object comes back when nothing matched, so
    requests without such a tool take a byte-identical path; otherwise a shallow copy with new
    ``tools`` / ``tool_choice`` / ``messages`` — the caller's object is never mutated.

    Also removed, because they are equally unknown to the upstream: history blocks produced by
    a removed tool — ``server_tool_use`` naming it and ``<family>_tool_result`` blocks. A
    ``tool_choice`` that names a removed tool becomes ``auto``; with no tool left, ``tools``
    and ``tool_choice`` are dropped (a tool choice without tools is rejected).

    2026-10-09: also ``tool_addition`` / ``tool_removal`` blocks (mid-conversation tool
    changes, beta ``inline-tools``) that point at a removed tool — by name, or by an inline
    ``tool_definition`` of a removed type, which counts as removed even when the tool is not in
    ``tools``. Left in place they are a 400 ("references unknown tool 'advisor'"). A removed
    block's ``cache_control`` moves to the message's last remaining block (not onto thinking,
    which cannot carry one), so the cache breakpoints keep their count and position.
    """
    if not prefixes or not isinstance(body, dict):
        return body, []
    tools = body.get("tools")
    gone = ([t for t in tools
             if isinstance(t, dict) and isinstance(t.get("type"), str)
             and t["type"].startswith(prefixes)]
            if isinstance(tools, list) else [])
    inline = _inline_removed_types(body.get("messages"), prefixes)
    if not gone and not inline:
        return body, []

    out = dict(body)
    kept = tools
    if gone:
        kept = [t for t in tools if not any(t is g for g in gone)]
    names = {g.get("name") for g in gone + inline if g.get("name")}
    result_types = {f"{_family(g['type'])}_tool_result" for g in gone + inline}
    tool_names = {g.get("name") for g in gone if g.get("name")}
    if gone and kept:
        out["tools"] = kept
        tc = out.get("tool_choice")
        if isinstance(tc, dict) and tc.get("type") == "tool" and tc.get("name") in tool_names:
            out["tool_choice"] = {"type": "auto"}
    elif gone:
        out.pop("tools", None)
        out.pop("tool_choice", None)

    def _drop(b: Any) -> bool:
        if not isinstance(b, dict):
            return False
        if b.get("type") == "server_tool_use" and b.get("name") in names:
            return True
        if b.get("type") in result_types:
            return True
        name, ttype = _tool_change_target(b)
        return bool((name and name in names) or (ttype and ttype.startswith(prefixes)))

    msgs = body.get("messages")
    if isinstance(msgs, list):
        new_msgs: list = []
        changed = False
        for m in msgs:
            content = m.get("content") if isinstance(m, dict) else None
            if not isinstance(content, list):
                new_msgs.append(m)
                continue
            keep = [b for b in content if not _drop(b)]
            if len(keep) == len(content):
                new_msgs.append(m)
                continue
            changed = True
            cache = next((b["cache_control"] for b in content
                          if _drop(b) and isinstance(b.get("cache_control"), dict)), None)
            if not keep:
                note = _REMOVED_TOOL_CHANGE_NOTE if m.get("role") == "system" else _REMOVED_NOTE
                keep = [{"type": "text", "text": note}]
            last = keep[-1]
            if (cache is not None and isinstance(last, dict) and "cache_control" not in last
                    and last.get("type") not in ("thinking", "redacted_thinking")):
                keep[-1] = {**last, "cache_control": cache}
            new_msgs.append({**m, "content": keep})
        if changed:
            out["messages"] = new_msgs

    removed = list(dict.fromkeys(g["type"] for g in gone + inline))
    logger.info("upstream_compat.unsupported_server_tool_stripped", tool_types=removed,
                tools_left=len(kept) if isinstance(kept, list) else 0)
    return out, removed

#: 한 번만 기록할 이름 수의 상한 — 헤더 값은 클라이언트가 정하기 때문이다.
#: English: cap on names remembered for the log-once message — the header is
#: client-controlled.
_LOG_ONCE_CAP = 64
_logged_dropped_betas: set[str] = set()


def forward_beta_map(raw: str | None) -> dict[str, str | None]:
    """``"a:safeguards, b"`` → ``{"a": "safeguards", "b": None}``;
    빈 값 → ``{}``(끔).

    English: ``"a:safeguards, b"`` → ``{"a": "safeguards", "b": None}``;
    blank → ``{}`` (off).
    """
    out: dict[str, str | None] = {}
    for item in (raw or "").split(","):
        name, _, field = item.partition(":")
        name, field = name.strip(), field.strip()
        if name:
            out[name] = field or None
    return out


def client_betas(header_values: Iterable[str] | None) -> list[str]:
    """``anthropic-beta`` 헤더 줄(여러 줄 가능) → beta 이름 목록. 쉼표로
    나누고, 공백을 지우고, 빈 값을 빼고, 처음 나온 순서를 지키며 중복을
    없앤다(프록시가 여러 줄을 쉼표로 합치기도 하고 그대로 두기도 한다).

    English: ``anthropic-beta`` header line(s) → beta names: split on commas,
    trimmed, blanks dropped, duplicates removed in first-seen order (a proxy may
    join repeated header lines with commas or keep them apart).
    """
    seen: dict[str, None] = {}
    for line in header_values or ():
        for b in str(line).split(","):
            if b.strip():
                seen.setdefault(b.strip(), None)
    return list(seen)


def apply_forwarded_betas(out_body: dict, src_body: Any, betas: list[str],
                          fmap: dict[str, str | None]) -> list[str]:
    """넘길 수 있는 beta 를 ``out_body["anthropic_beta"]`` 에 넣고, 각 beta 의
    짝 필드를 ``src_body`` 에서 복사한다. 넘긴 beta 목록을 돌려준다.

    beta 는 클라이언트가 보냈고, ``fmap`` 에 있고, 짝 필드가 있다면 그 필드가
    ``src_body`` 에 있을 때만 넘긴다. 필드가 없는 beta 는 열 것이 없기
    때문이다(Claude Code 의 보조 요청은 ``safeguards`` 없이 beta 만 붙인다).
    짝 필드는 반드시 그 beta 와 함께만 넘어간다 — ``dangerous-tool-use`` 없는
    ``safeguards`` 는 Bedrock 400 이다. ``out_body`` 에 이미 있던
    ``anthropic_beta`` 값은 유지하고, ``src_body`` 는 바꾸지 않는다. ``fmap``
    에 없는 이름은 프로세스당 한 번만 기록한다. 예외를 던지지 않으며, 뜻밖의
    오류가 나면 아무것도 넣지 않는다(이전 동작).

    English: put the forwardable betas into ``out_body["anthropic_beta"]`` and
    copy each one's paired field from ``src_body``. Returns the betas forwarded.

    A beta is forwarded when the client sent it, it is in ``fmap``, and — if it
    has a paired field — that field is in ``src_body``: a beta whose field is
    absent opens nothing (Claude Code's helper requests carry the beta without
    ``safeguards``). A paired field travels ONLY with its beta: ``safeguards``
    without ``dangerous-tool-use`` is a Bedrock 400. Values already in
    ``out_body["anthropic_beta"]`` are kept; ``src_body`` is never mutated.
    Names not in ``fmap`` are logged once per process. Never raises: on an
    unexpected error nothing is added (the previous behaviour).
    """
    try:
        src = src_body if isinstance(src_body, dict) else {}
        kept = [b for b in betas
                if b in fmap and (fmap[b] is None or fmap[b] in src)]
        fields = {fmap[b]: src[fmap[b]] for b in kept if fmap[b]}
        existing = out_body.get("anthropic_beta")
        merged = list(existing) if isinstance(existing, list) else []
        merged += [b for b in kept if b not in merged]
    except Exception:
        logger.warning("upstream_compat.beta_forward_failed", exc_info=True)
        return []
    if kept:
        out_body.update(fields)
        out_body["anthropic_beta"] = merged
    _log_dropped_once([b for b in betas if b not in fmap])
    return kept


def _log_dropped_once(names: list[str]) -> None:
    """버린 beta 이름을 프로세스당 한 번씩, ``_LOG_ONCE_CAP`` 개까지만 기록한다.

    English: log beta names we drop, once each per process, up to
    ``_LOG_ONCE_CAP`` names.
    """
    room = _LOG_ONCE_CAP - len(_logged_dropped_betas)
    new = [n for n in names if n not in _logged_dropped_betas][:max(room, 0)]
    if new:
        _logged_dropped_betas.update(new)
        logger.info("upstream_compat.beta_dropped", betas=new)
