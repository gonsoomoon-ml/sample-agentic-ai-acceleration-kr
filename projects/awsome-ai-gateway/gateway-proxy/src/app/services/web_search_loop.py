# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.
"""Server-side web-search loop: gives Bedrock/Mantle models a 1P-style ``web_search`` tool.

Bedrock and Mantle do not expose Anthropic's native server-side web search (they reject the
``web_search_20250305`` tool type). The gateway emulates it:

1. inject a ``web_search`` tool into the request,
2. run the model,
3. intercept OUR ``tool_use`` blocks, run the search through the AgentCore Gateway
   (managed WebSearch connector over MCP), feed the results back as ``tool_result``,
4. run the model again, and repeat until it answers,
5. stitch every model turn into ONE client-facing response (one SSE envelope, or one JSON
   body) while hiding the internal tool plumbing.

The client declares nothing and sees one uninterrupted answer, plus a one-line trace per
search (``🔎 [gateway web_search] "<query>" — 5 results (hosts)``).

How to read this module
-----------------------
Sections, in file order:

- **Tool definition and injection** — the tool the model sees, its description (which
  carries behavioural guidance the model needs), and ``tool_choice`` handling.
- **Forced-final turn** — what happens when the search budget or deadline is exhausted:
  the model must answer without another search.
- **Client-tool detection / native-tool stripping** — telling OUR tool calls apart from the
  client's own tools, and removing Anthropic/OpenAI native web-search tools that Bedrock
  rejects.
- **Prompt-cache breakpoints** — a ``cache_control`` marker on the newest search result so
  later turns of the same request read it at the cached price.
- **Usage accounting** — billing sums every turn; the client sees the FIRST turn's prompt
  buckets (its real context), never the multi-turn sum.
- **Search execution** — per-turn concurrency, per-turn cap, deadline.
- **Trace lines (text mode)** — the ``🔎`` line and the filter that removes model-written
  imitations of it.
- **Native trace blocks (Cowork)** — ``server_tool_use`` + ``web_search_tool_result`` blocks
  that the client replays verbatim, and the inbound rewrite that turns them back into
  ``tool_use``/``tool_result`` history the model treats as genuine tool records.
- **Evidence set** — one canonical, de-duplicated, size-bounded result list per search that
  feeds the model, the replay digest and the trace line alike.
- **The four loops** — Anthropic Messages × {stream, non-stream} and OpenAI Responses ×
  {stream, non-stream}. All four share the helpers above; only the wire format differs.
- **Dispatcher** — ``run_web_search_loop``, the router's entry point.

Interception rule (both dialects)
---------------------------------
- A turn with OUR ``web_search`` tool_use and no client tool → run the search, continue.
- A turn with a CLIENT tool_use (any tool that is not ours) → TERMINAL: forward it verbatim
  so the client's own tool loop runs. A multi-tool assistant message is never partially
  stripped; our search calls in such a turn are simply not run (a trace line says so).
- A text-only / non-tool-stop turn → TERMINAL (final answer).
- Guardrails: ``max_iterations`` (search turns per request), ``max_searches_per_turn``,
  a total deadline, and size caps on results. Search failures produce an error
  ``tool_result`` so the stream never dies. ``web_search_count`` counts SUCCESSFUL
  searches only.

Design notes
------------
- Every model turn is streamed (``is_stream=True`` path). Events are parsed as they arrive
  and only the ``web_search`` tool_use blocks are buffered, so token-by-token streaming is
  preserved even when no search happens. Non-streaming client requests loop with
  ``invoke()`` and return the assembled final body.
- Backend-agnostic: the router passes bound ``invoke``/``invoke_stream`` callables that
  apply the adapter-specific body transform (Bedrock path suffix, Mantle profile/endpoint).
  This module only shapes the LOGICAL turn body (messages, tools, stream flag).
- Dates in comments (2026-09-16, 2026-09-17) refer to production measurements on the US dev
  stack that motivated a rule. They are kept because each rule exists to prevent a specific
  observed failure.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Optional
from urllib.parse import urlparse

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.providers.openai_usage import extract_responses_usage
from app.schemas.domain import TokenUsage
from app.services.agentcore_mcp_client import AgentCoreMcpClient, AgentCoreMcpError

logger = structlog.get_logger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Tool definition and injection
# ═══════════════════════════════════════════════════════════════════════════════

# Name of the tool the model sees. Interception is a plain name check against it, decoupled
# from the MCP tool name on the AgentCore side (<target>___WebSearch).
GW_WEB_SEARCH_NAME = "web_search"

_WEB_SEARCH_DESCRIPTION = (
    "Search the public web for current, factual, or recent information. Use this when "
    "the answer may depend on events, data, docs, or facts that are recent or external. "
    "Returns titles, URLs, and snippets to cite. "
    # Restraint. Search results come back as input on every later turn of the request and
    # are billed again each time (2026-09-16: one call that searched the same fact four
    # times in parallel had a 132k-token input). The gateway caps result size and searches
    # per turn, but the cheapest search is the one the model does not make.
    "Prefer ONE focused query per fact; results are capped in size and count, so pick "
    "the query carefully instead of issuing several. Do not search again for a fact you "
    "already have unless the first result was empty or contradictory. "
    # One query per entity. 2026-09-17 (Cowork): a combined query such as "SMIC UMC
    # GlobalFoundries Q2 2026" returned results for one company and the model filled the
    # others with estimates (2 of 6 companies grounded). Read together with the budget
    # sentence appended by _budget_sentence().
    "When a question covers several companies, products, or other entities, use one query "
    "per entity rather than one combined query — a combined query returns results for only "
    "some of them. "
    # Trace lines. The model imitated the trace format from its history and claimed a
    # search it never made (2026-09-16, 1 of 3 requests); _TraceLineFilter is the second
    # line of defence. Telling it only "never write such lines" made it read the gateway's
    # own lines as its violation and "discard" them (2026-09-17, 9 real searches thrown
    # away), so the description first states that lines in the history are genuine.
    "Lines starting with '🔎 [gateway web_search]' that appear in your EARLIER messages were "
    "inserted by the gateway: each records a real search you made, whose full results were "
    "shown to you in that turn. They are genuine evidence — never 'discard' them or apologize "
    "for them. Do not compose such lines yourself; to search, call this tool."
)

# The loop builds the LOGICAL turn body (messages/input + tools + stream flag). The router's
# bound callable applies the PHYSICAL transform (allowed-field filter, anthropic_version,
# model id, metadata) and calls the adapter. Body shaping stays in the router; this module
# never sees adapter-specific kwargs.
InvokeFn = Callable[[dict], Awaitable[tuple[int, bytes, dict, TokenUsage]]]
InvokeStreamFn = Callable[[dict], Awaitable[tuple[int, AsyncIterator[bytes], dict, Optional[str]]]]


def _budget_sentence(budget: Optional[tuple[int, int]]) -> str:
    """Sentence appended to the tool description telling the model its search budget.

    Without it the model re-verified single facts up to the cap (2026-09-16 benchmark of 14
    semiconductor questions: 2 turns × 3 searches spent on one fact). Knowing the budget, it
    spreads searches over the entities that are actually asked about. The caps themselves
    are not lowered — quality first.
    """
    if not budget:
        return ""
    per_turn, iterations = budget
    parts = []
    if per_turn > 0:
        parts.append(f"up to {per_turn} searches per turn")
    if iterations > 0:
        parts.append(f"{iterations} search turn{'s' if iterations != 1 else ''} per request")
    if not parts:
        return ""
    return (" Budget for this request: " + " and ".join(parts)
            + "; plan queries so the budget covers every entity or fact asked about, and do "
            "not spend it re-verifying facts you already have.")


def _anthropic_tool_def(budget: Optional[tuple[int, int]] = None) -> dict:
    """The ``web_search`` tool as an Anthropic Messages tool definition."""
    return {
        "name": GW_WEB_SEARCH_NAME,
        "description": _WEB_SEARCH_DESCRIPTION + _budget_sentence(budget),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (<=200 chars)"},
                "max_results": {
                    "type": "integer",
                    "description": ("Max distinct results to return (1-25); the gateway caps "
                                    "this per search and removes duplicate/mirror pages"),
                    "minimum": 1,
                    "maximum": 25,
                },
            },
            "required": ["query"],
        },
    }


def _responses_tool_def(budget: Optional[tuple[int, int]] = None) -> dict:
    """The ``web_search`` tool as an OpenAI Responses function tool definition."""
    return {
        "type": "function",
        "name": GW_WEB_SEARCH_NAME,
        "description": _WEB_SEARCH_DESCRIPTION + _budget_sentence(budget),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (<=200 chars)"},
                "max_results": {"type": "integer", "minimum": 1, "maximum": 25},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
    }


def _tool_choice_names_ours(tc: Any, dialect: str) -> bool:
    """True when ``tool_choice`` forces OUR tool by name (dialect-specific shape)."""
    if not isinstance(tc, dict) or tc.get("name") != GW_WEB_SEARCH_NAME:
        return False
    return tc.get("type") == ("function" if dialect == "responses" else "tool")


def _tool_choice_is_forced_any(tc: Any) -> bool:
    """True when ``tool_choice`` forces *some* tool call ("required" / {"type": "any"})."""
    return tc == "required" or (isinstance(tc, dict) and tc.get("type") == "any")


def _relax_tool_choice(out: dict, dialect: str, include: bool, first_turn: bool) -> None:
    """Keep ``tool_choice`` consistent with the tools the turn actually carries (in place).

    Why (2026-09-16, US): Cowork's built-in WebSearch helper calls the model with the native
    web_search tool AND ``tool_choice: {"type": "tool", "name": "web_search"}``. We replace
    the native tool with ours under the same name, so the forced choice survived: the model
    searched on EVERY turn until ``max_iterations``, and the final turn (tools removed at the
    time) still carried the ``tool_choice`` → Bedrock 400 "Tool 'web_search' not found". Three
    billed searches, no answer, and the Cowork sub-agent retried 8 times (~$1.5).

    Rules:
    - A forced choice naming our tool is honoured on the FIRST turn only (the client's intent
      is "search at least once"); later turns are relaxed to ``auto`` so the model can answer.
    - On a forced-final turn, ``any``/``required`` are relaxed to ``auto`` too — that turn
      exists to produce the answer.
    - If the request carries no tools at all, ``tool_choice`` is removed (a tool choice
      without tools is rejected).
    """
    tc = out.get("tool_choice")
    if tc is None:
        return
    if "tools" not in out:
        out.pop("tool_choice", None)
        return
    if (_tool_choice_names_ours(tc, dialect) and not (include and first_turn)) or (
        _tool_choice_is_forced_any(tc) and not include
    ):
        out["tool_choice"] = "auto" if dialect == "responses" else {"type": "auto"}


def _with_web_search_tool(
    body: dict, dialect: str, include: bool, *, first_turn: bool = True,
    budget: Optional[tuple[int, int]] = None, final: bool = False,
) -> dict:
    """Return a shallow copy of ``body`` with our tool appended (``include``) or removed.

    Client-provided tools are preserved. ``first_turn`` lets a client-forced ``tool_choice``
    on our tool apply once (see ``_relax_tool_choice``). ``final`` marks the forced-final
    turn: for the Anthropic dialect the tool stays in place and ``tool_choice: none`` blocks
    further calls — removing the tool would force us to rewrite the history's
    tool_use/tool_result blocks as text (Bedrock rejects tool blocks without a tool
    definition), which changes the prompt prefix and defeats the prompt cache, and that
    rewrite was the cause of empty ``<br>`` answers (2026-09-16). Bedrock accepts
    ``tool_choice: none`` together with tool blocks in the history (verified). The Responses
    dialect still removes the tool and strips the plumbing (``include=False``).
    """
    out = dict(body)
    existing = list(out.get("tools") or [])
    # Drop any prior copy of our tool (idempotent across turns).
    existing = [t for t in existing if not _is_our_tool(t)]
    if include:
        existing.append(_anthropic_tool_def(budget) if dialect == "anthropic"
                        else _responses_tool_def(budget))
    if existing:
        out["tools"] = existing
    elif "tools" in out:
        out.pop("tools")
    _relax_tool_choice(out, dialect, include, first_turn)
    if final and dialect == "anthropic":
        out["tool_choice"] = {"type": "none"}
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# Forced-final turn
# ═══════════════════════════════════════════════════════════════════════════════
# When the search budget (max_iterations) or the deadline is exhausted, one more turn runs
# in which the model must answer from what it already has.

#: Replaces our tool_use block when the history has to be rewritten as text (Responses
#: dialect). The model must know that no further searches are possible AND that searches
#: did happen — deleting the block silently made it answer "I could not search".
_FINAL_TURN_TOOL_NOTE = "[web search results provided below; no further searches available]"
#: Re-prompt used ONCE when the forced-final turn produced thinking but no visible text
#: (2026-09-17, Cowork: 6k thinking tokens, zero text → a blank screen). A second empty
#: turn is returned as-is (no infinite loop).
_FINAL_TURN_TEXT_NUDGE = ("[Your previous turn contained no visible text. Write the final answer "
                          "now as plain text.]")
#: Appended to the last user message of the forced-final turn. With only result JSON as the
#: last user content, Opus 5 answered with a single ``<br>`` (2026-09-16: four billed
#: searches and no answer).
#: 2026-09-18 (Cowork): the forced-final turn is sent with ``tool_choice: none``, which blocks
#: EVERY tool — the client's own tools too, not only web_search. The instruction used to say
#: only that no further searches can be made; the model kept its plan ("save a note with the
#: memory tool, then write the news"), could not call the tool, and ended the turn after the
#: announcement alone (191 chars of text out of 2,895 output tokens). The empty-text nudge
#: does not fire in that case because there IS visible text, so the instruction itself must
#: say that no tool is available and that the whole answer is due now.
_FINAL_TURN_ANSWER_NOW = ("[These are all the search results available for this request. "
                          "No tool of any kind can be called in this turn — not web_search "
                          "and not any other tool — so do not announce or plan further "
                          "steps such as saving notes or more lookups; anything that needs "
                          "a tool can happen in a later turn. Write the complete final "
                          "answer now.]")
#: Appended to the last user message of the SOFT final turn (``final_turn_soft``): the first
#: turn after the search budget is used up. Every tool stays callable there — a client tool
#: call goes to the client as usual, a further web_search is answered with an error instead
#: of being run (what ``max_uses`` does for a server tool in the Messages API). The hard final
#: turn above follows only if the model still asks for a search.
#: Why (2026-09-18, Cowork): "search the news and save it as samsung-news.md" reached the
#: hard final turn, ``tool_choice: none`` blocked the client's Write tool too, and the model
#: delivered the summary as text with "the file tool is not available in this turn".
_FINAL_TURN_SEARCH_EXHAUSTED = ("[The web search budget for this request is used up: further "
                                "web_search calls will not be run. Every other tool is still "
                                "available. Answer from the search results above, or call "
                                "the other tool you need.]")


def _strip_anthropic_web_search_plumbing(
    messages: list, our_tool_use_ids: set[str]
) -> list:
    """Rewrite OUR web_search tool_use/tool_result blocks as text for a turn without tools.

    Why it exists: a turn sent without a ``tools`` definition is rejected by
    Anthropic-on-Bedrock if the conversation still contains tool_use/tool_result blocks. That
    failure is the worst possible shape — only the LAST turn 400s, so N billed model turns
    and N billed searches are thrown away and the user gets nothing. (The Anthropic loops no
    longer take this path — they keep the tool and use ``tool_choice: none`` — but the helper
    stays for any caller that removes the tool.)

    What it does:
    - ``tool_result`` → a text block with the same content (the results were paid for and the
      answer must stay grounded in them).
    - our ``tool_use`` → dropped; if an assistant message would end up with no text block,
      ``_FINAL_TURN_TOOL_NOTE`` is appended (empty content is rejected, and a thinking-only
      assistant turn reads as "said nothing", which produced ``<br>`` answers).
    - client-owned tool blocks are left untouched.
    - the answer-now instruction is appended to the last user message (``_with_answer_now``).

    Returns the input object itself (no copy) when nothing had to change, so requests without
    searches take a byte-identical path.
    """
    # Every tool_use named web_search in the history counts as ours, not only the ids of this
    # request. Inside the loop the client has not declared a tool of that name (otherwise the
    # loop is skipped — F-7), so such blocks can only be our plumbing that leaked to the
    # client on an earlier turn and came back. 2026-09-16: one leaked block made the final
    # turn fail with Bedrock 400 after N billed searches; the client's three retries failed
    # the same way.
    ids: set[str] = set(our_tool_use_ids)
    for msg in messages:
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if (
                isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") == GW_WEB_SEARCH_NAME
                and block.get("id")
            ):
                ids.add(block["id"])
    if not ids:
        return messages

    out: list = []
    for msg in messages:
        if not isinstance(msg, dict):
            out.append(msg)
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            out.append(msg)
            continue

        new_content: list = []
        changed = False
        for block in content:
            if not isinstance(block, dict):
                new_content.append(block)
                continue
            btype = block.get("type")
            if btype == "tool_use" and block.get("id") in ids:
                changed = True
                continue  # a note is added below if the message ends up without text
            if btype == "tool_result" and block.get("tool_use_id") in ids:
                changed = True
                raw = block.get("content")
                if isinstance(raw, list):
                    # content given as a block list — keep the text parts only
                    text = "".join(
                        b.get("text", "") for b in raw if isinstance(b, dict)
                    )
                else:
                    text = raw if isinstance(raw, str) else json.dumps(raw)
                new_content.append({"type": "text", "text": text})
                continue
            new_content.append(block)

        if not changed:
            out.append(msg)
            continue
        if msg.get("role") == "assistant" and not any(
            isinstance(b, dict) and b.get("type") == "text" for b in new_content
        ):
            # Empty content is rejected, and a thinking-only assistant turn made the model
            # answer with a blank ``<br>`` (2026-09-16). Keep the thinking blocks (the same
            # model replays them) and add the note after them.
            new_content = new_content + [{"type": "text", "text": _FINAL_TURN_TOOL_NOTE}]
        out.append({**msg, "content": new_content})
    return _with_answer_now(out)


def _with_user_note(messages: list, text: str) -> list:
    """Append a gateway instruction to the LAST user message (copy-on-write, idempotent).

    A string ``content`` becomes two text blocks. If the note is already present the input is
    returned unchanged.
    """
    out = list(messages)
    for i in range(len(out) - 1, -1, -1):
        m = out[i]
        if not (isinstance(m, dict) and m.get("role") == "user"):
            continue
        c = m.get("content")
        note = {"type": "text", "text": text}
        if isinstance(c, str):
            out[i] = {**m, "content": [{"type": "text", "text": c}, note]}
        elif isinstance(c, list):
            if any(isinstance(b, dict) and b.get("text") == text for b in c):
                return messages
            out[i] = {**m, "content": list(c) + [note]}
        break
    return out


def _with_answer_now(messages: list) -> list:
    """Hard final turn: append ``_FINAL_TURN_ANSWER_NOW``.

    With nothing but result JSON in the last user message the model tends to wait for further
    instructions and answers blank (2026-09-16).
    """
    return _with_user_note(messages, _FINAL_TURN_ANSWER_NOW)


def _with_search_exhausted(messages: list) -> list:
    """Soft final turn: append ``_FINAL_TURN_SEARCH_EXHAUSTED``."""
    return _with_user_note(messages, _FINAL_TURN_SEARCH_EXHAUSTED)


def _final_turn_mode(search_attempts: int, max_iterations: int, past_deadline: bool,
                     final_turn_soft: bool, refused_rounds: int) -> str:
    """"" (budget left) | "soft" | "hard" — what kind of turn comes next.

    - budget left → a normal turn.
    - budget used up, ``final_turn_soft`` on, nothing refused yet → SOFT: tools stay callable,
      a web_search call is refused with an error (``_exhausted_error``).
    - otherwise → HARD: ``tool_choice: none`` + answer-now. A passed deadline is always hard
      (a soft round could cost one more model turn), and so is every turn after one refused
      round — the loop is bounded by ``max_iterations + 2`` model calls.
    """
    if search_attempts < max_iterations and not past_deadline:
        return ""
    if final_turn_soft and not past_deadline and refused_rounds == 0:
        return "soft"
    return "hard"


def _strip_responses_web_search_items(input_items: list, our_call_ids: set[str]) -> list:
    """Responses-dialect twin of ``_strip_anthropic_web_search_plumbing``.

    Our ``function_call`` / ``function_call_output`` pairs are removed and each output becomes
    a user text message (the results were paid for). The ``reasoning`` item that directly
    precedes a removed ``function_call`` is removed with it: the Responses API rejects a
    reasoning item whose call is gone, so stripping only the pair would 400 again.
    """
    # Every function_call named web_search counts as ours, leaked blocks included — see the
    # same comment in _strip_anthropic_web_search_plumbing.
    ids: set[str] = set(our_call_ids)
    for item in input_items:
        if (
            isinstance(item, dict)
            and item.get("type") == "function_call"
            and item.get("name") == GW_WEB_SEARCH_NAME
            and item.get("call_id")
        ):
            ids.add(item["call_id"])
    if not ids:
        return input_items

    out: list = []
    for item in input_items:
        if not isinstance(item, dict):
            out.append(item)
            continue
        itype = item.get("type")
        if itype == "function_call" and item.get("call_id") in ids:
            # A reasoning item immediately before this call belongs to it — drop it too.
            if out and isinstance(out[-1], dict) and out[-1].get("type") == "reasoning":
                out.pop()
            continue
        if itype == "function_call_output" and item.get("call_id") in ids:
            raw = item.get("output")
            text = raw if isinstance(raw, str) else json.dumps(raw)
            out.append({"role": "user", "content": [{"type": "input_text", "text": text}]})
            continue
        out.append(item)
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# Client-tool detection and native web-search stripping
# ═══════════════════════════════════════════════════════════════════════════════

def _is_our_tool(tool: dict) -> bool:
    return isinstance(tool, dict) and tool.get("name") == GW_WEB_SEARCH_NAME


# Which Anthropic blocks / Responses items are tool calls the CLIENT must execute?
#
# Earlier versions matched exactly "tool_use" / "function_call". That is not the full set of
# call items in either dialect: Responses also has custom_tool_call, local_shell_call and
# computer_call; Anthropic also has server_tool_use and mcp_tool_use. When such an item is
# NOT recognised as a client call, the turn is treated as a search turn, the gateway runs
# the search and loops again — and the client's own tool call is swallowed while the client
# waits for it. That reproduces whenever the model calls our web_search and a client tool in
# the same turn (both dialects support parallel tool calls).
#
# Hence a SUFFIX test rather than an allow-list: an unknown future call type errs on the
# side of forwarding (the turn ends one round early) instead of swallowing.
def _is_client_tool_use_block(block: dict) -> bool:
    """Anthropic: a tool-use block that is not ours."""
    if not isinstance(block, dict):
        return False
    btype = block.get("type")
    if not isinstance(btype, str) or "tool_use" not in btype:
        return False
    return block.get("name") != GW_WEB_SEARCH_NAME


def _is_client_tool_call_item(item: dict, our_call_ids: set[str] | None = None) -> bool:
    """Responses: a tool-call item that is not ours."""
    if not isinstance(item, dict):
        return False
    itype = item.get("type")
    if not isinstance(itype, str) or not itype.endswith("_call"):
        return False
    if item.get("name") == GW_WEB_SEARCH_NAME:
        return False
    if our_call_ids and item.get("call_id") in our_call_ids:
        return False
    return True


def _client_declares_web_search(body: dict) -> bool:
    """True if the client's ORIGINAL request already declares a tool named web_search.

    If so we must NOT inject/hijack it (F-7) — the loop is skipped and the request passes
    through so the client's own tool loop runs unmodified.
    """
    for t in (body.get("tools") or []):
        if isinstance(t, dict) and t.get("name") == GW_WEB_SEARCH_NAME:
            return True
    return False


# Anthropic/OpenAI NATIVE server-side web_search tools carry a `type` naming the
# server tool (Messages: "web_search_20250305"; Responses: "web_search_preview").
# Bedrock/Mantle reject these ("tool type ... is not supported for this model").
# We STRIP them and fulfill the intent with our own injected tool + search loop.
# A genuinely custom client tool merely NAMED web_search has no such type and is
# left alone (F-7). Clients like Claude Code CLI attach the native tool by default.
def _is_native_web_search(tool: dict) -> bool:
    if not isinstance(tool, dict):
        return False
    t = tool.get("type")
    return isinstance(t, str) and t.startswith("web_search")


def _strip_native_web_search(body: dict) -> dict:
    """Remove native web-search tool definitions; return the same object when none exist."""
    tools = body.get("tools")
    if not isinstance(tools, list):
        return body
    filtered = [t for t in tools if not _is_native_web_search(t)]
    if len(filtered) == len(tools):
        return body  # unchanged — no native tool present
    out = dict(body)
    if filtered:
        out["tools"] = filtered
    else:
        out.pop("tools", None)
    return out


# ═══════════════════════════════════════════════════════════════════════════════
# Prompt-cache breakpoints on our search results
# ═══════════════════════════════════════════════════════════════════════════════

#: Anthropic's limit on ``cache_control`` markers per request.
_MAX_CACHE_BREAKPOINTS = 4


def _has_cc(b: Any) -> bool:
    return isinstance(b, dict) and bool(b.get("cache_control"))


def _count_cache_breakpoints(
    base_body: dict, conversation: list
) -> tuple[int, list[tuple[int, int]]]:
    """(total breakpoints, [(msg_idx, block_idx)] of the MESSAGE-level ones, in order)."""
    n = 0
    sysb = base_body.get("system")
    if isinstance(sysb, list):
        n += sum(1 for b in sysb if _has_cc(b))
    n += sum(1 for t in (base_body.get("tools") or []) if _has_cc(t))
    positions: list[tuple[int, int]] = []
    for i, m in enumerate(conversation):
        c = m.get("content") if isinstance(m, dict) else None
        if isinstance(c, list):
            for j, b in enumerate(c):
                if _has_cc(b):
                    n += 1
                    positions.append((i, j))
    return n, positions


def _without_cc(block: dict) -> dict:
    return {k: v for k, v in block.items() if k != "cache_control"}


def _place_cache_breakpoint(
    base_body: dict, conversation: list, tool_results: list, our_tool_use_ids: set
) -> list:
    """Put ONE ``cache_control`` on the newest tool_result we inject; keep ≤ 4 per request.

    Why: a request with N searches is N+1 Bedrock turns, and every later turn re-sends the
    earlier prompt plus the search results. The client's own prefix (system, history) is
    cached by the client's markers, but the results we splice in (4–6k tokens per search) had
    no marker and were billed at full price on every turn (2026-09-16: a 3-search request
    paid 6R result tokens; with a marker about 3.8R). One marker on the NEWEST result is
    enough — Anthropic looks back ~20 block boundaries for a cache hit — and it saves the
    per-request limit of four. When the limit is reached, the oldest MESSAGE-level marker is
    removed (system/tools markers are never touched). The client's original dicts are not
    mutated (copy-on-write); the internal conversation lives only inside this request.
    """
    if not tool_results:
        return conversation
    conv = list(conversation)
    # 1) Remove the marker we put on an earlier round — ours is always exactly one.
    for i, m in enumerate(conv):
        c = m.get("content") if isinstance(m, dict) else None
        if not isinstance(c, list):
            continue
        ours = [j for j, b in enumerate(c)
                if _has_cc(b) and b.get("type") == "tool_result"
                and b.get("tool_use_id") in our_tool_use_ids]
        if ours:
            new_c = list(c)
            for j in ours:
                new_c[j] = _without_cc(new_c[j])
            conv[i] = {**m, "content": new_c}
    n, positions = _count_cache_breakpoints(base_body, conv)
    if n >= _MAX_CACHE_BREAKPOINTS:
        if not positions:
            return conv   # all four sit on system/tools — leave them alone
        i, j = positions[0]
        c = list(conv[i]["content"])
        c[j] = _without_cc(c[j])
        conv[i] = {**conv[i], "content": c}
    tool_results[-1]["cache_control"] = {"type": "ephemeral"}
    return conv


# ═══════════════════════════════════════════════════════════════════════════════
# Usage accounting
# ═══════════════════════════════════════════════════════════════════════════════
# Billing sums every turn (on_usage receives the merged total). The CLIENT sees the first
# turn's prompt buckets — see _client_prompt_usage for why.

def _merge_usage(acc: TokenUsage, turn: TokenUsage) -> TokenUsage:
    """Sum usage across turns. reasoning_tokens stays a submetric (already inside
    output_tokens) — summed for visibility but total is recomputed from input+output,
    never with reasoning re-added. Booleans OR."""
    acc.input_tokens += turn.input_tokens
    acc.output_tokens += turn.output_tokens
    acc.cache_creation_input_tokens += turn.cache_creation_input_tokens
    acc.cache_read_input_tokens += turn.cache_read_input_tokens
    acc.reasoning_tokens += turn.reasoning_tokens
    acc.total_tokens = acc.input_tokens + acc.output_tokens
    acc.cache_ttl_1h = acc.cache_ttl_1h or turn.cache_ttl_1h
    acc.estimated = acc.estimated or turn.estimated
    return acc


def _wire_input(usage: TokenUsage) -> int:
    """Billing buckets → the cache-INCLUSIVE prompt count the OpenAI wires report.

    The inverse of ``split_openai_input``: TokenUsage keeps the three prompt buckets
    mutually exclusive for costing, while the client (Codex CLI reads this to track its
    context window) expects the grand total with both cache buckets folded in. Kept as one
    function because the streaming and non-streaming loops both rewrite usage on the way
    out and must agree.
    """
    return (
        usage.input_tokens
        + usage.cache_read_input_tokens
        + usage.cache_creation_input_tokens
    )


def _prompt_snapshot(input_tokens: int, cache_creation: int, cache_read: int) -> TokenUsage:
    """The three prompt buckets of ONE turn, frozen for the client-facing usage."""
    return TokenUsage(
        input_tokens=int(input_tokens or 0),
        cache_creation_input_tokens=int(cache_creation or 0),
        cache_read_input_tokens=int(cache_read or 0),
    )


def _client_prompt_usage(first_turn: TokenUsage | None, merged: TokenUsage) -> TokenUsage:
    """Prompt buckets the CLIENT sees = the FIRST turn's, never the multi-turn sum.

    Claude Code / Cowork / Codex read ``input + cache_creation + cache_read`` of the last
    response as "how full is my context window" and auto-compact near the limit. The first
    turn's prompt IS the client's conversation; every later turn re-sends it plus our search
    results, which the client never keeps. Summing N turns therefore reported N× the real
    context — 2026-09-16 US dev measurement: 26.7k real → 106.6k reported after 3 searches,
    and a 55k Cowork session reported 270k, crossing the 200k window so Cowork compacted on
    every search turn ("Autocompact is thrashing"). Billing is untouched: on_usage() still
    receives the merged multi-turn totals. Falls back to ``merged`` when no turn completed
    (nothing to snapshot) so the payload stays self-consistent.
    """
    return first_turn if first_turn is not None else merged


def _sse(event: str, data: dict) -> bytes:
    """Encode one server-sent event frame."""
    return f"event: {event}\ndata: {json.dumps(data)}\n\n".encode()


# ═══════════════════════════════════════════════════════════════════════════════
# Search execution (shared by the four loops)
# ═══════════════════════════════════════════════════════════════════════════════

def _truncate_result(text: str, max_chars: int) -> tuple[str, bool]:
    """Hard cap on one search result's text. Returns ``(text, truncated)``.

    Why: ``max_iterations`` bounds turns and ``total_deadline_sec`` bounds time, but neither
    bounds the two axes that decide the bill — bytes injected into the next turn's input and
    searches per turn. One search once injected ~17.4K tokens of raw result text; twenty
    parallel searches in a turn (which this loop deliberately supports) would inject 20× that,
    either as an uncapped cost or as a context overflow that 400s the continuation turn and
    throws away every billed turn before it.

    A visible marker is appended when the text is cut. Silent truncation is the worst case:
    the model reads JSON cut mid-record as "the full result set". Since the evidence set
    (``_build_evidence``) fits results to the cap exactly, this is a safety net only.
    ``max_chars <= 0`` disables the cap and returns the input unchanged.
    """
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    marker = "\n\n[truncated by gateway: result set exceeded the per-search size cap]"
    return text[:max_chars] + marker, True


def _cap_error(limit: int) -> str:
    """tool_result content for a search that exceeded the per-turn cap.

    The earlier wording ("answer from the results already provided") made the model give up
    on the rest of a fan-out (2026-09-16: 2 of 5 companies filled). Saying that the query was
    NOT run and can be requested again lets it finish within the round limit.
    """
    return json.dumps({"error": (f"per-turn web search limit reached ({limit} per turn); "
                                 "this query was NOT run — request it again in your next "
                                 "turn if it is still needed")})


def _exhausted_error() -> str:
    """tool_result content for a search requested after the request's budget was used up.

    Unlike ``_cap_error`` it must NOT invite a retry: the per-turn wording ("request it again
    in your next turn") would make the model spend the hard final turn asking again.
    """
    return json.dumps({"error": ("the web search budget for this request is used up; this "
                                 "query was NOT run and no further search will be run in "
                                 "this request — answer from the results already provided, "
                                 "or continue with your other tools")})


async def _run_turn_searches(
    mcp_client: AgentCoreMcpClient, inputs: list, allowance: int, deadline: float,
    default_max_results: int, max_result_chars: int, result_text_chars: int,
    exhausted: bool = False,
) -> list[tuple[str, bool, str, str | None, list[dict] | None]]:
    """Run one turn's searches CONCURRENTLY.

    Returns ``(result_text, ok, trace, reason, digest)`` per input, in input order.
    ``reason`` is None (ran), "capped" (over the per-turn limit), "exhausted" (the request's
    search budget is used up — nothing is run, see ``_exhausted_error``) or "deadline" (total
    deadline already passed) — the loops turn those into the dialect's error payload so
    every tool_use still gets exactly one result.

    Concurrency: waiting for two searches one after the other added ~5 s to every fan-out
    turn (2026-09-16: 4–9 s per search). Cost is identical; only wall-clock shrinks.
    ``_do_search`` never raises, so ``gather`` cannot be broken mid-way.
    """
    results: list = [None] * len(inputs)
    todo: list[tuple[int, Any]] = []
    past_deadline = time.monotonic() > deadline
    for i, inp in enumerate(inputs):
        q = inp.get("query", "") if isinstance(inp, dict) else ""
        if exhausted:
            results[i] = (_exhausted_error(), False,
                          _trace_line(q, _trace_words("exhausted")), "exhausted", None)
        elif i >= allowance:
            results[i] = (_cap_error(allowance), False, _trace_line(q, _trace_words("capped")),
                          "capped", None)
        elif past_deadline:
            results[i] = ("", False, _trace_line(q, _trace_words("deadline")), "deadline", None)
        else:
            todo.append((i, _do_search(mcp_client, inp if isinstance(inp, dict) else {},
                                       default_max_results, max_result_chars, result_text_chars)))
    if todo:
        done = await asyncio.gather(*(c for _, c in todo))
        for (i, _), (text, ok, trace, digest) in zip(todo, done):
            results[i] = (text, ok, trace, None, digest)
    return results


def _anthropic_tool_result(tool_use_id: Any, result_text: str, ok: bool,
                           reason: Optional[str]) -> dict:
    """Anthropic ``tool_result`` block for one search outcome."""
    if reason == "deadline":
        content = "web search deadline exceeded"
    else:
        content = result_text          # ran, or the JSON from _cap_error/_exhausted_error
    return {"type": "tool_result", "tool_use_id": tool_use_id, "content": content,
            **({"is_error": True} if not ok else {})}


def _responses_call_output(call_id: Any, result_text: str, ok: bool,
                           reason: Optional[str]) -> dict:
    """Responses ``function_call_output`` item for one search outcome."""
    if reason == "deadline":
        out = json.dumps({"error": "web search deadline exceeded"})
    else:
        out = result_text              # ran, or the capped JSON from _cap_error
    return {"type": "function_call_output", "call_id": call_id, "output": out}


def _turn_search_allowance(requested: int, max_per_turn: int) -> int:
    """How many of a turn's requested searches actually run. ``max_per_turn <= 0`` = all.

    The excess still gets a RESPONSE: Anthropic/Responses require exactly one
    tool_result / function_call_output per tool_use, or the next turn is a 400. "Not run"
    and "no result produced" are therefore different things (see ``_cap_error``).
    """
    if max_per_turn <= 0:
        return requested
    return min(requested, max_per_turn)


# ═══════════════════════════════════════════════════════════════════════════════
# Trace lines (text mode)
# ═══════════════════════════════════════════════════════════════════════════════
# Our web_search tool_use/tool_result blocks are removed from the client-facing response
# (the client never declared that tool). Without any trace, the model could not see in its
# own history that a search had happened when the client's tool loop (bash etc.) came back
# with the NEXT request, and it kept re-searching or "confessed" that it had never searched
# (2026-09-16, Cowork: 8 requests, 21 searches, $3.3 for one question). A one-line text trace
# is accepted by Bedrock and by every client and carries no result body.
# Native mode (next section) replaces the trace with real tool blocks for clients that
# replay them; the text line is still shown to the user there.

#: Prefix of the one-line trace. The tag lets users tell the gateway's server-side search
#: from the client's own search tool.
_TRACE_PREFIX = "🔎 [gateway web_search]"
#: Note appended after a search round in 1.0.62–1.0.64. No longer emitted (the same
#: information sits in the tool description) but kept as a filter marker: old transcripts
#: still contain it and the model may imitate it.
_TRACE_NOTE = ("(full search results were shown to the assistant in this turn and used for "
               "the answer; only this trace is kept in the transcript)")
#: Line prefixes that identify model-written imitations of our trace / note.
_TRACE_MARKERS = (_TRACE_PREFIX, _TRACE_NOTE[:40])


def _is_fake_trace_line(line: str) -> bool:
    t = line.strip()
    return bool(t) and any(t.startswith(m) for m in _TRACE_MARKERS)


def _could_become_trace_line(partial: str) -> bool:
    """True while a line-in-progress is still a prefix of (or starts with) a marker."""
    t = partial.lstrip()
    return any(m.startswith(t) or t.startswith(m) for m in _TRACE_MARKERS)


class _TraceLineFilter:
    """Drop MODEL-written lines that imitate our trace / note from client-facing text.

    2026-09-16: once the model saw our trace lines in its history it wrote lines in the same
    format without searching and claimed a search (1 of 3 requests, usage_logs ws=0). Only
    the gateway writes traces. Streaming adds almost no latency: at the start of a line only
    the fragment that could still become a marker is held back; the moment it diverges from
    every marker it is released.
    """

    def __init__(self) -> None:
        self.pending = ""        # held line-start fragment (could still become a marker)
        self.mid_line = False    # part of the current line was already emitted
        self.dropped = ""        # everything removed (for the never-empty fallback)

    def feed(self, text: str) -> str:
        out: list[str] = []
        buf = self.pending + text
        self.pending = ""
        while buf:
            nl = buf.find("\n")
            seg, buf = (buf, "") if nl < 0 else (buf[: nl + 1], buf[nl + 1:])
            complete = seg.endswith("\n")
            if self.mid_line:
                out.append(seg)
            elif complete:
                if _is_fake_trace_line(seg):
                    self.dropped += seg
                else:
                    out.append(seg)
            elif _could_become_trace_line(seg):
                self.pending = seg
            else:
                out.append(seg)
                self.mid_line = True
            if complete:
                self.mid_line = False
        return "".join(out)

    def flush(self) -> str:
        p, self.pending = self.pending, ""
        self.mid_line = False
        if p and _is_fake_trace_line(p):
            self.dropped += p
            return ""
        return p


def _strip_fake_trace_lines(text: str) -> str:
    """Non-streaming twin of _TraceLineFilter."""
    f = _TraceLineFilter()
    return f.feed(text) + f.flush()


def _trace_lang() -> str:
    """"en" | "ko" — settings.web_search_trace_lang; the prefix is language-neutral."""
    try:
        from app.config import get_settings
        return (get_settings().web_search_trace_lang or "en").lower()
    except Exception:  # settings unavailable (tests) → English
        return "en"


def _trace_words(kind: str, **kw: Any) -> str:
    """Human wording after the em dash.
    kind: results | failed | capped | exhausted | mixed | deadline."""
    ko = _trace_lang() == "ko"
    if kind == "results":
        n, hosts = kw["n"], kw.get("hosts") or []
        tail = f" ({', '.join(hosts)})" if hosts else ""
        if ko:
            return f"결과 {n}건{tail}"
        return f"{n} result{'' if n == 1 else 's'}{tail}"
    if kind == "failed":
        why = kw.get("why") or ""
        return (f"실패 ({why})" if why else "실패") if ko else (f"failed ({why})" if why else "failed")
    if kind == "capped":
        return "건너뜀 (턴당 상한)" if ko else "skipped (per-turn limit)"
    if kind == "exhausted":
        return ("실행 안 됨 (이 요청의 검색 한도 소진)" if ko
                else "not run (search budget of this request used up)")
    if kind == "mixed":
        if ko:
            return "실행 안 됨 (클라이언트 도구와 같은 턴 — 다음 턴에 web_search 단독 호출)"
        return ("not run (called in the same turn as a client tool — call web_search on its "
                "own next turn)")
    return "건너뜀 (마감)" if ko else "skipped (deadline)"


def _trace_line(query: str, outcome: str) -> str:
    """One user-facing line per search: ``🔎 [gateway web_search] "<query>" — <outcome>``.

    Titles and notes were dropped from the line for readability (user request, 2026-09-16).
    """
    q = (query or "").strip().replace("\n", " ")
    if len(q) > 80:
        q = q[:77] + "…"
    return f'{_TRACE_PREFIX} "{q}" — {outcome}'


# ═══════════════════════════════════════════════════════════════════════════════
# Native trace blocks (Cowork) and their inbound rewrite
# ═══════════════════════════════════════════════════════════════════════════════
# A text trace is, to the model, "something I wrote": when asked "did you really search?" it
# denied having searched even with 12 real trace lines in its history (2026-09-17, Cowork),
# and when a trace line shared an assistant message with a client tool call it copied the
# shape and wrote trace lines without searching (4× in one session). Anthropic's native
# search blocks — ``server_tool_use`` + ``web_search_tool_result`` — are tool records the
# model cannot fabricate. Cowork replays them verbatim in the next request's history
# (verified 2026-09-17), so:
#
#   outbound  : per search, a server_tool_use block + a web_search_tool_result block whose
#               items carry title/url/page_age and the result text base64-encoded in
#               ``encrypted_content`` (the field the API defines as opaque replay data).
#               The 🔎 text line is still emitted for the user; it is removed again on the
#               way back in.
#   inbound   : ``_rewrite_inbound_native_blocks`` turns the replayed blocks into
#               assistant[tool_use] / user[tool_result] history for loop turns (our tool is
#               defined there); ``_normalize_inbound_native_blocks`` turns them into text
#               trace lines for requests that go out WITHOUT our tool definition.
#
# Enabled by WEB_SEARCH_TRACE_MODE=native for the client classes listed in
# WEB_SEARCH_TRACE_NATIVE_CLIENTS (default: cowork). Other clients keep the text trace.

_NATIVE_TOOL_USE = "server_tool_use"
_NATIVE_TOOL_RESULT = "web_search_tool_result"
_DIGEST_RESULTS = 5
#: Per-result excerpt length in the replayed digest. The default follows the trimmed result
#: length the model actually saw (``result_text_chars``, 1500). Shorter excerpts (200, then
#: 600) cut sentences the model had grounded its answer on, and on the next turn it
#: retracted real facts as "not in my sources" (2026-09-17: Micron "since March",
#: TrendForce 20% — all present in the full results). The history must be what the model
#: saw. ``settings.web_search_digest_chars > 0`` overrides.
_DIGEST_SNIPPET_CHARS = 1500
#: Note placed in the replayed tool_result so the model knows the full results were shown in
#: the turn that ran the search.
_DIGEST_NOTE = ("digest of an earlier search: the full results were shown to you in the turn "
                "that ran it; only the leading excerpt of each result is kept here")


def _digest_chars(result_text_chars: int = 0) -> int:
    """Per-result digest length: WEB_SEARCH_DIGEST_CHARS if > 0, else the trimmed-result
    length the model actually saw (result_text_chars), else the code default."""
    try:
        from app.config import get_settings
        v = int(getattr(get_settings(), "web_search_digest_chars", 0) or 0)
    except Exception:  # settings unavailable (tests) → follow the trim length
        v = 0
    if v > 0:
        return v
    return result_text_chars if result_text_chars > 0 else _DIGEST_SNIPPET_CHARS


def _trace_mode() -> tuple[str, frozenset[str]]:
    """("text" | "native", allowed client classes — cowork/claude-code/codex; empty = all)."""
    try:
        from app.config import get_settings
        s = get_settings()
        mode = (getattr(s, "web_search_trace_mode", "text") or "text").strip().lower()
        raw = getattr(s, "web_search_trace_native_clients", "") or ""
        clients = frozenset(p.strip().lower() for p in raw.split(",") if p.strip())
        return mode, clients
    except Exception:  # settings unavailable (tests) → text
        return "text", frozenset()


def _request_client(request: Any) -> str:
    """The client class the ClientIdentificationMiddleware stored (scope.state.client), or
    the same classification from the headers when no middleware ran (tests, direct calls).

    Do not read the ``anthropic-client-platform`` header directly: the real Cowork client is
    identified by its User-Agent (claude-desktop-3p / local-agent), not by that header
    (2026-09-17: the first native build matched zero Cowork requests because of this). The
    classification rule lives in ``client_identifier`` only.
    """
    scope = getattr(request, "scope", None)
    if isinstance(scope, dict):
        client = (scope.get("state") or {}).get("client")
        if client:
            return str(client).lower()
    headers = getattr(request, "headers", None)
    if not headers:
        return ""
    try:
        from app.services.client_identifier import identify_client
        return str(identify_client(dict(headers)) or "").lower()
    except Exception:
        return ""


def _native_trace_enabled(request: Any) -> bool:
    """True when this request's client should receive native trace blocks."""
    mode, clients = _trace_mode()
    if mode != "native":
        return False
    if not clients:
        return True
    return _request_client(request) in clients


def _client_tool_flags() -> dict:
    """``{"mixed_turn_run": bool, "final_turn_soft": bool}`` from settings (both False when
    settings are unavailable, e.g. in unit tests that call the loops directly)."""
    try:
        from app.config import get_settings
        st = get_settings()
        return {"mixed_turn_run": bool(getattr(st, "web_search_mixed_turn_run", False)),
                "final_turn_soft": bool(getattr(st, "web_search_final_turn_soft", False))}
    except Exception:
        return {"mixed_turn_run": False, "final_turn_soft": False}


def _native_search_blocks(query: str, ok: bool, reason: str | None,
                          digest: list[dict] | None) -> list[dict]:
    """[server_tool_use, web_search_tool_result] for one search, in the API's shape."""
    srv_id = "srvtoolu_" + uuid.uuid4().hex[:24]
    use = {"type": _NATIVE_TOOL_USE, "id": srv_id, "name": GW_WEB_SEARCH_NAME,
           "input": {"query": query}}
    if ok and digest is not None:
        content: Any = [
            {"type": "web_search_result", "title": d.get("title") or "",
             "url": d.get("url") or "",
             "encrypted_content": base64.b64encode(json.dumps(
                 {"gw": 1, "snippet": d.get("snippet") or ""}, ensure_ascii=False,
             ).encode("utf-8")).decode("ascii"),
             "page_age": d.get("page_age")}
            for d in digest
        ]
    else:
        content = {"type": "web_search_tool_result_error",
                   "error_code": ("max_uses_exceeded" if reason in ("capped", "exhausted")
                                  else "unavailable")}
    return [use, {"type": _NATIVE_TOOL_RESULT, "tool_use_id": srv_id, "content": content}]


def _native_block_frames(gi: int, blk: dict) -> list[bytes]:
    """SSE frames for one native block at envelope index ``gi`` — server_tool_use streams its
    input as input_json_delta (like tool_use); the result block arrives whole in start."""
    if blk.get("type") == _NATIVE_TOOL_USE:
        partial = json.dumps(blk.get("input") or {}, ensure_ascii=False)
        return [
            _sse("content_block_start", {"type": "content_block_start", "index": gi,
                                         "content_block": {**blk, "input": {}}}),
            _sse("content_block_delta", {"type": "content_block_delta", "index": gi,
                                         "delta": {"type": "input_json_delta",
                                                   "partial_json": partial}}),
            _sse("content_block_stop", {"type": "content_block_stop", "index": gi}),
        ]
    return [
        _sse("content_block_start", {"type": "content_block_start", "index": gi,
                                     "content_block": blk}),
        _sse("content_block_stop", {"type": "content_block_stop", "index": gi}),
    ]


def _inbound_outcome(result: dict | None) -> str:
    """Trace wording for a replayed result block (used by the text fallback)."""
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list):
        class _R:  # _result_hosts reads .results
            results = content
        n, hosts = _result_hosts(_R())
        return _trace_words("results", n=n, hosts=hosts)
    if isinstance(content, dict):
        return _trace_words("failed", why=str(content.get("error_code") or "")[:60])
    return _trace_words("failed")


def _normalize_inbound_native_blocks(body: dict) -> dict:
    """Replayed native search blocks → text trace lines (for requests WITHOUT our tool).

    Bedrock knows neither block type, so leaving them in is a 400. Used where the request
    goes out without our tool definition: the router's fallback loop, Mantle, count_tokens,
    the F-7 pass-through and the MCP-init-failure fallback. Returns the SAME object when
    nothing was found (byte-identical path for everyone else) and logs how many blocks
    came back.
    """
    msgs = body.get("messages")
    if not isinstance(msgs, list):
        return body
    n_use = n_res = 0
    new_msgs: list = []
    for m in msgs:
        content = m.get("content") if isinstance(m, dict) else None
        if not isinstance(content, list) or not any(
            isinstance(b, dict) and b.get("type") in (_NATIVE_TOOL_USE, _NATIVE_TOOL_RESULT)
            for b in content
        ):
            new_msgs.append(m)
            continue
        results = {b.get("tool_use_id"): b for b in content
                   if isinstance(b, dict) and b.get("type") == _NATIVE_TOOL_RESULT}
        out: list = []
        for b in content:
            t = b.get("type") if isinstance(b, dict) else None
            if t == _NATIVE_TOOL_USE:
                n_use += 1
                q = str((b.get("input") or {}).get("query") or "")
                out.append({"type": "text",
                            "text": _trace_line(q, _inbound_outcome(results.get(b.get("id"))))})
            elif t == _NATIVE_TOOL_RESULT:
                n_res += 1
            else:
                out.append(b)
        if not out:   # result blocks without their tool_use — never leave an empty message
            out = [{"type": "text", "text": _trace_line("", _trace_words("failed"))}]
        # Mixed turn (mixed_turn_run): the stream sent our pairs and the 🔎 block AFTER the
        # client's tool_use, so text would now follow a tool_use. Keep the client tool calls
        # last, as a model turn has them (stable: the order inside each group is unchanged).
        first_tool = next((i for i, b in enumerate(out)
                           if isinstance(b, dict) and b.get("type") == "tool_use"), None)
        if first_tool is not None and any(
                not (isinstance(b, dict) and b.get("type") == "tool_use")
                for b in out[first_tool:]):
            out = ([b for b in out if not (isinstance(b, dict) and b.get("type") == "tool_use")]
                   + [b for b in out if isinstance(b, dict) and b.get("type") == "tool_use"])
        new_msgs.append({**m, "content": out})
    if not (n_use or n_res):
        return body
    logger.info("web_search.inbound_native_blocks", server_tool_use=n_use, tool_result=n_res)
    return {**body, "messages": new_msgs}


#: Public name for the router. Requests that do not enter the loop (client profile without
#: web search, MCP client not configured, count_tokens, helper-model requests) would also
#: 400 on replayed blocks, so the router calls this right after parsing the body.
normalize_inbound_native_blocks = _normalize_inbound_native_blocks


def _inbound_tool_id(srv_id: Any) -> str:
    """``srvtoolu_X`` → ``toolu_gw_X`` (the rebuilt tool_use and tool_result share it)."""
    sid = str(srv_id or "")
    if sid.startswith("srvtoolu_"):
        sid = sid[len("srvtoolu_"):]
    return "toolu_gw_" + (sid or uuid.uuid4().hex[:24])


def _decode_digest(enc: Any) -> str:
    """``encrypted_content`` (base64 JSON ``{"gw": 1, "snippet": …}``) → excerpt, or ""."""
    try:
        data = json.loads(base64.b64decode(str(enc or ""), validate=True).decode("utf-8"))
        return str(data.get("snippet") or "") if isinstance(data, dict) else ""
    except Exception:
        return ""


def _inbound_tool_result_block(tid: str, result: dict | None) -> tuple[dict, int]:
    """Replayed web_search_tool_result → our tool_result. Returns ``(block, decoded)`` where
    ``decoded`` is the number of results whose excerpt could be restored."""
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list):
        items: list[dict] = []
        decoded = 0
        for it in content:
            if not isinstance(it, dict):
                continue
            snippet = _decode_digest(it.get("encrypted_content"))
            decoded += 1 if snippet else 0
            row = {"title": str(it.get("title") or ""), "url": str(it.get("url") or ""),
                   "text": snippet}
            if it.get("page_age"):
                row["publishedDate"] = str(it["page_age"])
            items.append(row)
        text = json.dumps({"note": _DIGEST_NOTE, "results": items}, ensure_ascii=False,
                          separators=(",", ":"))
        return {"type": "tool_result", "tool_use_id": tid, "content": text}, decoded
    if isinstance(content, dict):
        why = str(content.get("error_code") or "error")[:60]
        return ({"type": "tool_result", "tool_use_id": tid, "is_error": True,
                 "content": json.dumps({"error": f"web search unavailable ({why})"})}, 0)
    return ({"type": "tool_result", "tool_use_id": tid, "is_error": True,
             "content": json.dumps({"error": "web search result missing"})}, 0)


def _rewrite_inbound_native_blocks(body: dict) -> dict:
    """Replayed native search blocks → real tool history (for loop turns).

    ::

        assistant [text0, server_tool_use, web_search_tool_result, text1]
          → assistant [text0, tool_use] / user [tool_result] / assistant [text1]

    The original turn structure (search → result → continue) is restored and the result
    excerpts come back as ``tool_result`` content — the form the model treats as a tool
    record rather than as its own prose. Consecutive searches become parallel tool_use blocks
    in one assistant turn; any other block (text, client tool call) closes the turn first.
    Display-only 🔎 lines are removed from text blocks in the same message: the tool record
    replaces them, and leaving them would re-create the imitation source.

    Mixed turn (``mixed_turn_run``): the message also holds a CLIENT tool_use, whose
    tool_result arrives in the client's next user message. An assistant turn cannot be split
    after that block (its result would no longer be in the immediately following user
    message), so from the client tool_use on everything stays in ONE assistant turn and our
    tool_results are merged into the FRONT of the client's user message::

        assistant [text, tool_use(client), server_tool_use, web_search_tool_result, 🔎]
        user      [tool_result(client)]
          → assistant [text, tool_use(client), tool_use(ours)]
            user      [tool_result(ours), tool_result(client)]

    That is the parallel-tool-call shape the model produced in the first place. Both block
    orders are accepted (the stream sends our pairs after the client tool_use, the
    non-streaming body before it).

    Only for turns that carry our ``web_search`` tool definition. A tool_use that names an
    undefined tool is a Bedrock 400, so requests that go out without our tool use
    ``_normalize_inbound_native_blocks`` instead.

    Caveat: this SYNTHESISES user[tool_result] messages the client never sent. From here on
    the loop's message list differs from the client's in count and indices — anything that
    relies on "the last user message", message indices or the number of user turns must be
    aware of it (``_place_cache_breakpoint`` is unaffected: synthesised messages carry no
    ``cache_control``, so the count of four still holds).

    Returns the same object when no native block is present.
    """
    msgs = body.get("messages")
    if not isinstance(msgs, list):
        return body
    n_use = n_res = n_decoded = n_merged = 0
    new_msgs: list = []
    #: our tool_results of a mixed turn, waiting for the client's user message (see docstring)
    carry: list = []
    for m in msgs:
        if carry:
            if isinstance(m, dict) and m.get("role") == "user":
                c = m.get("content")
                rest = c if isinstance(c, list) else [{"type": "text", "text": str(c or "")}]
                m = {**m, "content": carry + list(rest)}
            else:   # no client result message followed — keep the history pairable
                new_msgs.append({"role": "user", "content": carry})
            carry = []
        content = m.get("content") if isinstance(m, dict) else None
        if not isinstance(content, list) or not any(
            isinstance(b, dict) and b.get("type") in (_NATIVE_TOOL_USE, _NATIVE_TOOL_RESULT)
            for b in content
        ):
            new_msgs.append(m)
            continue
        results = {b.get("tool_use_id"): b for b in content
                   if isinstance(b, dict) and b.get("type") == _NATIVE_TOOL_RESULT}
        segment: list = []       # blocks of the assistant turn being rebuilt
        uses: list = []          # our tool_use blocks pending their results
        outs: list = []          # matching tool_result blocks
        closed = 0               # assistant/user pairs already emitted for this message
        client_pending = False   # a client tool_use is in `segment` → the turn cannot be split

        def close() -> None:
            nonlocal closed
            new_msgs.append({"role": "assistant", "content": segment + uses})
            new_msgs.append({"role": "user", "content": outs})
            closed += 1

        for b in content:
            t = b.get("type") if isinstance(b, dict) else None
            if t == _NATIVE_TOOL_USE:
                n_use += 1
                tid = _inbound_tool_id(b.get("id"))
                q = str((b.get("input") or {}).get("query") or "")
                uses.append({"type": "tool_use", "id": tid, "name": GW_WEB_SEARCH_NAME,
                             "input": {"query": q}})
                blk, decoded = _inbound_tool_result_block(tid, results.get(b.get("id")))
                outs.append(blk)
                n_decoded += decoded
            elif t == _NATIVE_TOOL_RESULT:
                n_res += 1          # consumed through `results` above
            else:
                if uses and not client_pending:
                    close()
                    segment, uses, outs = [], [], []
                if t == "tool_use":
                    client_pending = True
                if t == "text":
                    # Display-only 🔎 lines (shown to the user in native mode) are removed
                    # from the model's history: the tool record replaces them.
                    cleaned = _strip_fake_trace_lines(b.get("text") or "")
                    if not cleaned.strip():
                        continue
                    b = {**b, "text": cleaned}
                segment.append(b)
        if uses and client_pending:
            new_msgs.append({"role": "assistant", "content": segment + uses})
            carry = outs
            n_merged += len(outs)
        elif uses:
            close()
        elif segment:
            new_msgs.append({**m, "content": segment})
        elif closed:
            pass    # the message was fully emitted as assistant/user pairs above
        else:   # only orphan result blocks — never leave an empty assistant message
            new_msgs.append({**m, "content": [{"type": "text", "text": _trace_line(
                "", _trace_words("failed"))}]})
    if carry:   # the mixed assistant turn was the last message
        new_msgs.append({"role": "user", "content": carry})
    if not (n_use or n_res):
        return body
    logger.info("web_search.inbound_native_rewritten", server_tool_use=n_use,
                tool_result=n_res, digests=n_decoded, merged_into_client_results=n_merged)
    return {**body, "messages": new_msgs}


# ═══════════════════════════════════════════════════════════════════════════════
# Evidence set: one canonical result list per search
# ═══════════════════════════════════════════════════════════════════════════════
# The results of one search are normalised ONCE and the same list feeds three consumers:
# the model's tool_result, the replay digest (native mode), and the 🔎 line's count/hosts.
#
# Why (2026-09-17, from the Bedrock invocation bodies): the model asked for 15 results, the
# configured cap of 5 was treated as a mere default, and 15 × 1500 chars exceeded the 12k cap,
# so EVERY search reached the model as JSON cut mid-record (8 of 15 records visible, three of
# them mirrors of one article). The replay digest could not parse that JSON and rebuilt five
# results from the raw response (different lead text), and the 🔎 line counted 15. Three
# different views of one search — and the model, asked for its sources, retracted facts that
# were in the evidence with "not in the results I can see". Hence: the cap is a MAXIMUM,
# mirrors are removed by content fingerprint, and per-result length is fitted so the set
# always serialises within the cap (valid JSON, no truncation).

_FETCH_HEADROOM = 2          # ask the connector for 2× the cap so dedupe has candidates
_CONNECTOR_MAX_RESULTS = 25  # matches the limit in agentcore_mcp_client.search
_EVIDENCE_FLOOR = 120        # minimum body chars per result; below it, drop results instead
_MIRROR_JACCARD = 0.6        # 8-word shingle Jaccard: same article, different boilerplate
_MIRROR_CONTAIN = 0.9        # one page almost entirely inside the other (a truncated copy)
_MIRROR_MIN_SHINGLES = 60    # containment only counts for pages of substantial length


def _collapse(body: Any) -> str:
    """Whitespace-collapsed string form of a result body."""
    return " ".join(str(body or "").split())


#: Page badges that open many articles: "3 min read", "2:23 PM".
_PAGE_BADGES = re.compile(r"\b\d+\s*min(?:ute)?s?\s+read\b|\b\d{1,2}:\d{2}\s*[AaPp][Mm]\b")


def _wordy(tok: str) -> bool:
    """A token that looks like a word (mostly letters), as opposed to a number or symbol."""
    letters = sum(1 for ch in tok if ch.isalpha())
    return letters >= 2 and letters / max(len(tok), 1) >= 0.5


def _skip_noise(text: str) -> str:
    """Start at the first run of real words — skips ticker tables, timestamps and read-time
    badges that open many pages. Input returned when no such run exists."""
    text = " ".join(_PAGE_BADGES.sub(" ", text).split())
    toks = text.split(" ")
    for i in range(len(toks)):
        window = toks[i:i + 6]
        if (len(window) >= 3 and _wordy(window[0])
                and sum(1 for t in window if _wordy(t)) >= min(5, len(window))):
            return " ".join(toks[i:])
    return text


def _cut_sentence(text: str, limit: int) -> str:
    """Cut to ``limit`` at a sentence boundary (when one lies past 60% of it) and mark it."""
    if limit <= 0 or len(text) <= limit:
        return text
    cut = text[:limit]
    m = max(cut.rfind(". "), cut.rfind("다. "), cut.rfind("。"), cut.rfind("! "), cut.rfind("? "))
    if m > limit * 0.6:
        cut = cut[: m + 1]
    return cut.rstrip() + " …"


def _pick_snippet(body: Any, limit: int) -> str:
    """Leading excerpt: real words first (_skip_noise), then a sentence-boundary cut."""
    text = _collapse(body)
    if not text:
        return ""
    return _cut_sentence(_skip_noise(text), limit)


def _fingerprint(text: str, n: int = 8) -> set[str]:
    """Set of ``n``-word shingles over the first 400 words — the basis for mirror detection."""
    words = re.findall(r"\w+", text.lower())[:400]
    return {" ".join(words[i:i + n]) for i in range(max(0, len(words) - n + 1))}


def _is_mirror(fp: set[str], kept: list[set[str]]) -> bool:
    """Same article on another host? Jaccard catches copies that differ only in boilerplate;
    containment catches a truncated copy. A long quote shared by two DIFFERENT articles stays
    below both (Jaccard ≈ shared/(2−shared); containment needs ≥90% of a substantial page)."""
    if len(fp) < 4:
        return False
    for other in kept:
        if len(other) < 4:
            continue
        inter = len(fp & other)
        if inter / len(fp | other) >= _MIRROR_JACCARD:
            return True
        if (min(len(fp), len(other)) >= _MIRROR_MIN_SHINGLES
                and inter / min(len(fp), len(other)) >= _MIRROR_CONTAIN):
            return True
    return False


def _dumps(obj: Any) -> str:
    """Compact JSON, non-ASCII kept as-is (the form sent to the model)."""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _fit_to_cap(kept: list[dict], bodies: list[str], per_cap: int, max_chars: int,
                stats: dict) -> int:
    """Cut each body so the serialized result list fits ``max_chars`` (exactly, measured on
    the real JSON), dropping trailing results when the per-result share would fall below
    _EVIDENCE_FLOOR. Returns the per-result length used. Deterministic — no overhead estimate."""
    per = per_cap
    for _ in range(40):
        if not kept:
            return 0
        overhead = len(_dumps({"results": [{**it, "text": ""} for it in kept]}))
        share = (max_chars - overhead) // len(kept)
        if share < _EVIDENCE_FLOOR:
            kept.pop()
            bodies.pop()
            stats["dropped_budget"] += 1
            continue
        per = min(per_cap, share)
        for it, body in zip(kept, bodies):
            it["text"] = _cut_sentence(body, per)
        size = len(_dumps({"results": kept}))
        if size <= max_chars:
            return per
        per_cap = per - ((size - max_chars) // len(kept) + 1)   # shave the overshoot
        if per_cap < _EVIDENCE_FLOOR:
            kept.pop()
            bodies.pop()
            stats["dropped_budget"] += 1
            per_cap = per
    return per


def _build_evidence(raw_text: str, *, keep: int, per_result_chars: int,
                    max_chars: int) -> tuple[list[dict], dict] | None:
    """Canonical result list for one search, or None when the provider text is not JSON.

    URL duplicates and mirrors (same article body on another host) are dropped, the first
    ``keep`` distinct results are kept (``keep <= 0`` = all), each body starts at real words
    and is cut so that ``keep`` results fit ``max_chars`` (per-result cut disabled when
    ``per_result_chars <= 0`` — the old whole-JSON cap then applies downstream).

    Returns ``(items, stats)``; ``stats`` feeds the ``web_search.evidence_built`` log line.
    """
    try:
        data = json.loads(raw_text or "")
    except (ValueError, TypeError):
        return None
    items = data.get("results") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return None
    stats = {"fetched": len(items), "dup_url": 0, "dup_mirror": 0, "dropped_budget": 0}
    seen_urls: set[str] = set()
    kept_fps: list[set[str]] = []
    kept: list[dict] = []
    bodies: list[str] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        url = str(it.get("url") or "")
        key = url.split("#")[0].rstrip("/") if url else ""
        if key and key in seen_urls:
            stats["dup_url"] += 1
            continue
        raw_body = it.get("text") if it.get("text") is not None else it.get("content")
        collapsed = _collapse(raw_body)
        body = _skip_noise(collapsed)
        fp = _fingerprint(body)
        if _is_mirror(fp, kept_fps):
            stats["dup_mirror"] += 1
            continue
        if key:
            seen_urls.add(key)
        kept_fps.append(fp)
        if not kept:   # first result only: raw vs cleaned lead, to audit _skip_noise in logs
            stats["lead_raw"] = collapsed[:60]
            stats["lead_clean"] = body[:60]
        new = {k: v for k, v in it.items() if k not in ("text", "content", "raw_content")}
        new["text"] = body
        kept.append(new)
        bodies.append(body)
        if keep > 0 and len(kept) >= keep:
            break
    per = per_result_chars if per_result_chars > 0 else 0
    if per > 0 and max_chars > 0:
        per = _fit_to_cap(kept, bodies, per, max_chars, stats)
    elif per > 0:
        for it, body in zip(kept, bodies):
            it["text"] = _cut_sentence(body, per)
    stats["kept"] = len(kept)
    stats["per_result"] = per
    return kept, stats


def _digest_from_evidence(evidence: list[dict], snippet_chars: int) -> list[dict]:
    """Replay digest = the evidence itself (title/url/date + the same text, optionally
    shortened by WEB_SEARCH_DIGEST_CHARS)."""
    out: list[dict] = []
    for it in evidence:
        age = it.get("publishedDate") or it.get("published_date") or it.get("page_age")
        out.append({"title": str(it.get("title") or ""), "url": str(it.get("url") or ""),
                    "snippet": _cut_sentence(str(it.get("text") or ""), snippet_chars),
                    "page_age": str(age) if age else None})
    return out


def _result_hosts(resp) -> tuple[int, list[str]]:
    """(count, up to 3 distinct hosts) of a WebSearchResponse-like object; tolerant of fakes."""
    items = getattr(resp, "results", None)
    if items is None:
        try:
            data = json.loads(getattr(resp, "raw_text", "") or "")
            items = data.get("results", data) if isinstance(data, dict) else data
        except (ValueError, TypeError):
            items = []
    if not isinstance(items, list):
        items = []
    hosts: list[str] = []
    for it in items:
        url = it.get("url") if isinstance(it, dict) else getattr(it, "url", None)
        host = urlparse(str(url or "")).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        if host and host not in hosts and len(hosts) < 3:
            hosts.append(host)
    return len(items), hosts


def _result_digest(resp: Any, limit: int = _DIGEST_RESULTS,
                   snippet_chars: int = _DIGEST_SNIPPET_CHARS) -> list[dict]:
    """[{title, url, snippet, page_age}] × ≤limit from a WebSearchResponse-like object.

    Fallback digest for the non-JSON provider path (``_do_search``); the JSON path derives the
    digest from the evidence set instead.
    """
    items = getattr(resp, "results", None)
    if items is None:
        try:
            data = json.loads(getattr(resp, "raw_text", "") or "")
            items = data.get("results", data) if isinstance(data, dict) else data
        except (ValueError, TypeError):
            items = []
    if not isinstance(items, list):
        return []
    out: list[dict] = []
    for it in items:
        if isinstance(it, dict):
            def get(k, _it=it):
                return _it.get(k)
        else:
            def get(k, _it=it):
                return getattr(_it, k, None)
        body = get("text") if get("text") is not None else get("content")
        age = get("publishedDate") or get("published_date") or get("page_age")
        out.append({"title": str(get("title") or ""), "url": str(get("url") or ""),
                    "snippet": _pick_snippet(body, snippet_chars),
                    "page_age": str(age) if age else None})
        if len(out) >= limit:
            break
    return out


def _result_digest_from_text(text: str, resp: Any, limit: int = _DIGEST_RESULTS,
                             snippet_chars: int = _DIGEST_SNIPPET_CHARS) -> list[dict]:
    """Digest built from the TRIMMED result JSON the model actually saw (title/url/text/
    publishedDate, URL-deduped); falls back to the raw response when it is not JSON."""
    try:
        data = json.loads(text or "")
        items = data.get("results", data) if isinstance(data, dict) else data
    except (ValueError, TypeError):
        items = None
    if isinstance(items, list) and items:
        class _R:
            results = [it for it in items if isinstance(it, dict)]
        return _result_digest(_R(), limit, snippet_chars)
    return _result_digest(resp, limit, snippet_chars)


def _trim_results(text: str, per_result_chars: int) -> tuple[str, bool]:
    """Structured trim of the provider JSON: keep EVERY result, cut each body to
    ``per_result_chars`` at a sentence boundary, collapse whitespace, drop URL duplicates,
    re-serialize compactly. ``(text, changed)``. Disabled (<=0), non-JSON or empty → input
    untouched, so the byte-identical pre-trim path stays reachable.

    Predecessor of ``_build_evidence`` (which adds the count cap, mirror removal and the
    exact fit to the size cap); kept as a standalone helper.

    Why per-result trimming: a whole-JSON cap cut the trailing results mid-record while the
    leading results carried thousands of characters of body. Cutting each item keeps the
    title, URL and lead of every result and saves 40–50% of the tokens per search
    (2026-09-16: raw results were 13–24k chars).
    """
    if per_result_chars <= 0:
        return text, False
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return text, False
    items = data.get("results") if isinstance(data, dict) else data
    if not isinstance(items, list):
        return text, False
    seen: set[str] = set()
    out: list[dict] = []
    changed = False
    for it in items:
        if not isinstance(it, dict):
            continue
        url = str(it.get("url") or "")
        key = url.split("#")[0].rstrip("/") if url else ""
        if key and key in seen:
            changed = True
            continue
        if key:
            seen.add(key)
        raw_body = it.get("text") if it.get("text") is not None else it.get("content")
        body = " ".join(str(raw_body or "").split())
        if len(body) > per_result_chars:
            cut = body[:per_result_chars]
            m = max(cut.rfind(". "), cut.rfind("다. "), cut.rfind("。"), cut.rfind("! "),
                    cut.rfind("? "))
            if m > per_result_chars * 0.6:
                cut = cut[: m + 1]
            body = cut.rstrip() + " …"
        if body != raw_body:
            changed = True
        new = {k: v for k, v in it.items() if k not in ("text", "content", "raw_content")}
        new["text"] = body
        out.append(new)
    if not out:
        return text, False
    new_text = json.dumps({"results": out} if isinstance(data, dict) else out,
                          ensure_ascii=False, separators=(",", ":"))
    return new_text, changed


async def _do_search(
    mcp_client: AgentCoreMcpClient, tool_input: dict, default_max: int,
    max_result_chars: int = 0, result_text_chars: int = 0,
) -> tuple[str, bool, str, list[dict] | None]:
    """Run one web search. Returns ``(result_text_for_model, ok, trace_line, digest)``.

    Never raises — on failure the result text is an error JSON so the model can continue
    from its own knowledge. ``trace_line`` is the one-line evidence the loops leave in the
    client-facing output; ``digest`` is the replay digest for native mode.

    All caps are applied HERE: this is the only place where the four loops obtain result
    text, so they cannot diverge. A truncated search keeps ``ok=True`` — the query was billed
    and did ground the answer; flipping the flag would skew ``web_search_count``.
    """
    query = ""
    requested = default_max
    if isinstance(tool_input, dict):
        query = str(tool_input.get("query") or "")
        try:
            requested = int(tool_input.get("max_results") or default_max)
        except (TypeError, ValueError):
            requested = default_max
    if requested <= 0:
        requested = default_max
    # The configured cap is a MAXIMUM (the model cannot exceed it). The connector is asked for
    # up to 2× the cap so dedupe has candidates — the connector bills per call, so the number
    # of results does not affect cost (see the evidence-set header).
    keep = min(requested, default_max) if default_max > 0 else requested
    fetch_n = requested
    if default_max > 0:
        fetch_n = min(keep * _FETCH_HEADROOM, _CONNECTOR_MAX_RESULTS)
    try:
        resp = await mcp_client.search(query, fetch_n)
        built = _build_evidence(resp.raw_text, keep=keep, per_result_chars=result_text_chars,
                                max_chars=max_result_chars)
        if built is not None:
            evidence, stats = built
            text = json.dumps({"results": evidence}, ensure_ascii=False, separators=(",", ":"))
            logger.info("web_search.evidence_built", requested=requested, fetched=stats["fetched"],
                        kept=stats["kept"], dup_url=stats["dup_url"],
                        dup_mirror=stats["dup_mirror"], dropped_budget=stats["dropped_budget"],
                        per_result=stats["per_result"], chars=len(text),
                        lead_raw=stats.get("lead_raw"), lead_clean=stats.get("lead_clean"))
            text, truncated = _truncate_result(text, max_result_chars)   # safety net only
            if truncated:
                logger.warning("web_search.result_truncated", chars=len(text),
                               cap=max_result_chars)

            class _E:
                results = evidence
            n, hosts = _result_hosts(_E())
            digest = _digest_from_evidence(evidence, _digest_chars(result_text_chars))
        else:
            # provider text is not JSON — old path: whole-text cap, raw-response digest
            text, truncated = _truncate_result(resp.raw_text, max_result_chars)
            if truncated:
                logger.info("web_search.result_truncated", original_chars=len(resp.raw_text),
                            cap=max_result_chars)
            n, hosts = _result_hosts(resp)
            digest = _result_digest(resp, _DIGEST_RESULTS, _digest_chars(result_text_chars))
        return (text, True, _trace_line(query, _trace_words("results", n=n, hosts=hosts)), digest)
    except AgentCoreMcpError as e:
        logger.warning("web_search.failed", error=str(e)[:200])
        return (json.dumps({"error": f"web search unavailable: {str(e)[:160]}"}), False,
                _trace_line(query, _trace_words("failed", why=str(e)[:60])), None)
    except Exception as e:  # defensive — never kill the stream
        logger.exception("web_search.unexpected")
        return (json.dumps({"error": f"web search error: {str(e)[:160]}"}), False,
                _trace_line(query, _trace_words("failed")), None)


def _search_trace_frames(gi: int, native_blocks: list[dict],
                         traces: list[str]) -> tuple[list[bytes], int]:
    """SSE frames that leave one round of searches in the client envelope, starting at
    envelope index ``gi``: the native block pairs (native mode), then ONE text block with the
    🔎 lines. Returns ``(frames, next_index)``.

    The 🔎 line is emitted in both modes: in text mode it is the only trace, in native mode it
    is for the user (Cowork's UI does not render the blocks, 2026-09-17) and is removed from
    the history on the way back in (``_rewrite_inbound_native_blocks``).
    """
    frames: list[bytes] = []
    for blk in native_blocks:
        frames.extend(_native_block_frames(gi, blk))
        gi += 1
    if traces:
        frames += [
            _sse("content_block_start", {"type": "content_block_start", "index": gi,
                                         "content_block": {"type": "text", "text": ""}}),
            _sse("content_block_delta", {"type": "content_block_delta", "index": gi,
                                         "delta": {"type": "text_delta",
                                                   "text": "\n" + "\n".join(traces) + "\n"}}),
            _sse("content_block_stop", {"type": "content_block_stop", "index": gi}),
        ]
        gi += 1
    return frames, gi


# ═══════════════════════════════════════════════════════════════════════════════
# ANTHROPIC (Messages) — streaming stitcher
# ═══════════════════════════════════════════════════════════════════════════════
async def _anthropic_stream(
    *,
    invoke_stream: InvokeStreamFn,
    base_body: dict,
    mcp_client: AgentCoreMcpClient,
    request: Request,
    on_usage: Callable[[TokenUsage], Awaitable[None]],
    max_iterations: int,
    deadline: float,
    default_max_results: int,
    max_result_chars: int = 0,
    max_searches_per_turn: int = 0,
    cache_results: bool = True,
    result_text_chars: int = 0,
    native_trace: bool = False,
    mixed_turn_run: bool = False,
    final_turn_soft: bool = False,
) -> AsyncIterator[bytes]:
    """Stitch N Anthropic model turns into ONE message_start … message_stop stream.

    Forwards text/thinking blocks (re-indexed into one envelope); suppresses web_search
    tool_use/tool_result plumbing; runs the search between turns.

    Two flags cover web search next to the client's own tools (both default to the previous
    behaviour):

    - ``mixed_turn_run`` — a turn that calls a client tool AND web_search has to go to the
      client. With the flag (native mode only) its searches are run and their block pairs
      leave in the same message as the client's tool_use; without it they are not run. Text
      mode never runs them: the results could not come back with the client's next request.
    - ``final_turn_soft`` — see ``_final_turn_mode``.

    State that is reset PER TURN (``stop_reason_final``, ``saw_message_delta``, the block
    maps) must not leak across turns: a search turn ends with ``stop_reason: tool_use``, and
    if the next turn dies mid-stream that value would be emitted as the final frame — the
    client would then wait for a tool call it never saw (ours are all suppressed).
    """
    merged = TokenUsage()
    first_turn: TokenUsage | None = None   # turn-1 prompt buckets → client usage (context gauge)
    nudged = False           # empty force_final turn re-prompted once (_FINAL_TURN_TEXT_NUDGE)
    conversation: list[dict] = list(base_body.get("messages") or [])
    searches_done = 0        # successful searches → web_search_count (billing/attribution)
    search_attempts = 0      # ALL search rounds incl. failures → loop guard (F-5)
    refused_rounds = 0       # soft final turns whose searches were refused (bounds the loop)
    envelope_open = False
    global_index = 0  # next content_block index in the stitched envelope
    #: A provider error arrived inside a 200 stream. If so, never claim a normal end.
    error_seen = False
    #: content_block indices opened towards the client and not closed yet. If the upstream
    #: dies mid-block these are closed explicitly so the SDK's parser state is clean.
    open_global_blocks: set[int] = set()
    stop_reason_final = "end_turn"
    saw_message_delta = False
    #: 이번 턴에 Bedrock 이 보낸 message_delta.delta 의 사본. 마지막 프레임은 이것을
    #: 복사해 만든다(판정 safeguard_results 등 처음 보는 필드도 그대로 전달).
    #: English: copy of this turn's upstream message_delta.delta; the terminal frame is
    #: built from it (safeguard_results and any unknown field pass through).
    turn_delta: dict = {}
    #: All tool_use ids of our injected searches (used by the cache-breakpoint placement and
    #: by the plumbing strip if a caller removes the tool).
    our_tool_use_ids: set[str] = set()

    try:
        while True:
            final_mode = _final_turn_mode(search_attempts, max_iterations,
                                          time.monotonic() > deadline, final_turn_soft,
                                          refused_rounds)
            force_final = final_mode == "hard"
            soft_final = final_mode == "soft"
            if final_mode:
                logger.info("web_search.final_turn", mode=final_mode, attempts=search_attempts)
            turn_body = _with_web_search_tool(base_body, "anthropic", include=True,
                                              first_turn=search_attempts == 0,
                                              budget=(max_searches_per_turn, max_iterations),
                                              final=force_final)
            turn_body = dict(turn_body)
            # The hard final turn keeps the tool (tool_choice: none) so the history's tool
            # blocks stay valid; it only appends the answer-now instruction. The soft final
            # turn changes nothing but the instruction.
            turn_body["messages"] = (_with_answer_now(conversation) if force_final
                                     else _with_search_exhausted(conversation) if soft_final
                                     else conversation)
            turn_body["stream"] = True

            status, chunk_iter, _headers, _rid = await invoke_stream(turn_body)
            if status != 200:
                async for b in _drain_error(chunk_iter, envelope_open):
                    yield b
                return

            # Per-turn parse state (see the docstring on why these reset every turn).
            stop_reason_final = "end_turn"
            saw_message_delta = False
            turn_delta = {}
            assistant_content: list[dict] = []
            local_to_global: dict[int, int] = {}   # local block idx → emitted global idx
            suppressed: dict[int, dict] = {}        # local idx → {kind, buffer, block}
            text_buf: dict[int, str] = {}
            #: A text block is opened towards the client only when its first filtered
            #: character arrives — a block that contained nothing but imitated trace lines is
            #: never opened (an empty text block replayed by the client is a Bedrock 400).
            text_filters: dict[int, _TraceLineFilter] = {}
            text_start_ev: dict[int, dict] = {}
            turn_visible = False   # any client-visible text or client tool this turn
            thinking_buf: dict[int, dict] = {}
            pending_searches: list[dict] = []   # [{id, name, input}] — support MANY per turn (F-3)
            client_tool_present = False

            async for raw in chunk_iter:
                try:
                    ev = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                etype = ev.get("type")

                if etype == "message_start":
                    u = (ev.get("message") or {}).get("usage") or {}
                    merged.input_tokens += int(u.get("input_tokens", 0) or 0)
                    merged.cache_creation_input_tokens += int(u.get("cache_creation_input_tokens", 0) or 0)
                    merged.cache_read_input_tokens += int(u.get("cache_read_input_tokens", 0) or 0)
                    if first_turn is None:
                        first_turn = _prompt_snapshot(
                            u.get("input_tokens", 0),
                            u.get("cache_creation_input_tokens", 0),
                            u.get("cache_read_input_tokens", 0),
                        )
                    if not envelope_open:
                        envelope_open = True
                        yield _sse("message_start", ev)

                elif etype == "content_block_start":
                    idx = ev.get("index", 0)
                    block = ev.get("content_block") or {}
                    btype = block.get("type")
                    if btype == "tool_use" and block.get("name") == GW_WEB_SEARCH_NAME:
                        # OUR search — suppress, buffer input JSON. Exactly "tool_use" is
                        # matched: Bedrock never emits server_tool_use, and our native blocks
                        # are produced separately after the search (_native_block_frames).
                        suppressed[idx] = {"kind": "web_search", "buf": "",
                                           "id": block.get("id"), "name": block.get("name")}
                    elif _is_client_tool_use_block(block):
                        # CLIENT tool — terminal; forward re-indexed, buffer args to rebuild.
                        client_tool_present = True
                        turn_visible = True
                        gi = global_index
                        global_index += 1
                        local_to_global[idx] = gi
                        suppressed[idx] = {"kind": "client_tool", "buf": "",
                                           "id": block.get("id"), "name": block.get("name")}
                        ev2 = dict(ev); ev2["index"] = gi
                        open_global_blocks.add(gi)
                        yield _sse("content_block_start", ev2)
                    elif btype in ("thinking", "redacted_thinking"):
                        # Buffer thinking for the INTERNAL conversation (the next model turn's
                        # same-model replay needs it), but do NOT emit it to the client stream
                        # and do NOT advance global_index. Stitching merges N turns into one
                        # envelope; a thinking block replayed inside a stitched assistant message
                        # is rejected by Bedrock ("thinking blocks ... cannot be modified").
                        # Prior-turn thinking may be omitted on the client's next user turn, so
                        # suppressing it from client output is safe. (F-thinking)
                        thinking_buf[idx] = {"kind": btype, "thinking": "", "signature": "",
                                             "data": block.get("data")}
                    elif btype == "text":
                        text_buf[idx] = ""
                        text_filters[idx] = _TraceLineFilter()
                        text_start_ev[idx] = ev          # emitted lazily (see text_filters)
                    else:
                        gi = global_index
                        global_index += 1
                        local_to_global[idx] = gi
                        ev2 = dict(ev); ev2["index"] = gi
                        open_global_blocks.add(gi)
                        yield _sse("content_block_start", ev2)

                elif etype == "content_block_delta":
                    idx = ev.get("index", 0)
                    delta = ev.get("delta") or {}
                    dtype = delta.get("type")
                    if idx in suppressed and suppressed[idx]["kind"] == "web_search":
                        if dtype == "input_json_delta":
                            suppressed[idx]["buf"] += delta.get("partial_json", "") or ""
                        continue
                    if idx in suppressed and suppressed[idx]["kind"] == "client_tool":
                        if dtype == "input_json_delta":
                            suppressed[idx]["buf"] += delta.get("partial_json", "") or ""
                        gi = local_to_global.get(idx, idx)
                        ev2 = dict(ev); ev2["index"] = gi
                        yield _sse("content_block_delta", ev2)
                        continue
                    if idx in thinking_buf:
                        # Accumulate thinking/signature internally; never emit to client (see start).
                        if dtype == "thinking_delta":
                            thinking_buf[idx]["thinking"] += delta.get("thinking", "") or ""
                        elif dtype == "signature_delta":
                            thinking_buf[idx]["signature"] += delta.get("signature", "") or ""
                        continue
                    if dtype == "text_delta" and idx in text_buf:
                        raw = delta.get("text", "") or ""
                        text_buf[idx] += raw            # internal conversation keeps the original
                        emit = text_filters[idx].feed(raw)
                        if not emit:
                            continue
                        turn_visible = True
                        if idx not in local_to_global:   # first visible text → open the block now
                            gi = global_index
                            global_index += 1
                            local_to_global[idx] = gi
                            ev_start = dict(text_start_ev[idx])
                            ev_start["index"] = gi
                            open_global_blocks.add(gi)
                            yield _sse("content_block_start", ev_start)
                        gi = local_to_global[idx]
                        yield _sse("content_block_delta",
                                   {"type": "content_block_delta", "index": gi,
                                    "delta": {"type": "text_delta", "text": emit}})
                        continue
                    gi = local_to_global.get(idx, idx)
                    ev2 = dict(ev); ev2["index"] = gi
                    yield _sse("content_block_delta", ev2)

                elif etype == "content_block_stop":
                    idx = ev.get("index", 0)
                    if idx in suppressed and suppressed[idx]["kind"] == "web_search":
                        s = suppressed[idx]
                        try:
                            tool_input = json.loads(s["buf"]) if s["buf"] else {}
                        except (ValueError, TypeError):
                            tool_input = {}
                        pending_searches.append({"id": s["id"], "name": s["name"], "input": tool_input})
                        if s["id"]:
                            our_tool_use_ids.add(s["id"])
                        assistant_content.append(
                            {"type": "tool_use", "id": s["id"], "name": s["name"], "input": tool_input}
                        )
                        continue  # suppress
                    if idx in suppressed and suppressed[idx]["kind"] == "client_tool":
                        s = suppressed[idx]
                        try:
                            tool_input = json.loads(s["buf"]) if s["buf"] else {}
                        except (ValueError, TypeError):
                            tool_input = {}
                        assistant_content.append(
                            {"type": "tool_use", "id": s["id"], "name": s["name"], "input": tool_input}
                        )
                        gi = local_to_global.get(idx, idx)
                        ev2 = dict(ev); ev2["index"] = gi
                        open_global_blocks.discard(gi)
                        yield _sse("content_block_stop", ev2)
                        continue
                    if idx in thinking_buf:
                        # Keep the thinking block in the INTERNAL conversation so the next
                        # model turn's same-model replay is valid; do NOT emit its stop to
                        # the client (start/delta were already suppressed above).
                        tb = thinking_buf[idx]
                        if tb.get("kind") == "redacted_thinking":
                            blk = {"type": "redacted_thinking", "data": tb.get("data")}
                        else:
                            blk = {"type": "thinking", "thinking": tb["thinking"]}
                            if tb["signature"]:
                                blk["signature"] = tb["signature"]
                        assistant_content.append(blk)
                        continue  # suppress from client
                    if idx in text_buf:
                        assistant_content.append({"type": "text", "text": text_buf[idx]})
                        tail = text_filters[idx].flush()
                        if tail and idx not in local_to_global:
                            gi = global_index
                            global_index += 1
                            local_to_global[idx] = gi
                            ev_start = dict(text_start_ev[idx])
                            ev_start["index"] = gi
                            open_global_blocks.add(gi)
                            yield _sse("content_block_start", ev_start)
                        if idx not in local_to_global:
                            continue   # nothing visible survived — block never opened
                        if tail:
                            yield _sse("content_block_delta",
                                       {"type": "content_block_delta",
                                        "index": local_to_global[idx],
                                        "delta": {"type": "text_delta", "text": tail}})
                    gi = local_to_global.get(idx, idx)
                    ev2 = dict(ev); ev2["index"] = gi
                    open_global_blocks.discard(gi)
                    yield _sse("content_block_stop", ev2)

                elif etype == "message_delta":
                    saw_message_delta = True
                    d = ev.get("delta") or {}
                    turn_delta = dict(d)
                    if d.get("stop_reason"):
                        stop_reason_final = d["stop_reason"]
                    u = ev.get("usage") or {}
                    merged.output_tokens += int(u.get("output_tokens", 0) or 0)
                    # captured; do NOT emit here (emitted once at envelope close)

                elif etype == "message_stop":
                    pass  # end of this turn; do not emit

                elif etype == "ping":
                    yield _sse("ping", ev)
                elif etype == "error":
                    error_seen = True
                    yield _sse("error", ev)
                elif isinstance(ev.get("error"), dict):
                    # Every adapter in this repo emits error chunks with a NESTED type:
                    # {"error": {"type": "provider_error", ...}} and no top-level "type".
                    # Such a chunk misses the `etype == "error"` branch above and used to
                    # vanish silently — a truncated answer followed by a normal terminal
                    # frame, recorded as success by client and audit log alike (the Mantle
                    # adapter emits it when the stream breaks after a 200). Re-frame it with
                    # a top-level "type" — the only shape SDKs that dispatch on the SSE event
                    # name turn into an exception.
                    error_seen = True
                    inner = ev["error"]
                    yield _sse("error", {"type": "error", "error": inner})

            # ---- turn ended: decide terminal vs search ----
            # A turn that carried an error is never treated as a search turn; otherwise a
            # failed turn would be read as "the model asked for a search" and the loop
            # would continue.
            is_search_turn = (
                bool(pending_searches) and not client_tool_present and not error_seen
            )
            # A forced-final turn with thinking but no visible text is re-prompted once
            # (_FINAL_TURN_TEXT_NUDGE).
            if (final_mode and not turn_visible and not pending_searches and not nudged
                    and not error_seen and saw_message_delta and assistant_content):
                nudged = True
                logger.info("web_search.final_turn_empty_nudge")
                conversation = conversation + [
                    {"role": "assistant", "content": assistant_content},
                    {"role": "user", "content": [{"type": "text", "text": _FINAL_TURN_TEXT_NUDGE}]},
                ]
                continue
            if not is_search_turn or force_final:
                # Mixed turn (client tool + our search): the turn has to go to the client.
                mixed = (bool(pending_searches) and client_tool_present and not error_seen
                         and saw_message_delta and envelope_open)
                if mixed and native_trace and mixed_turn_run:
                    # Run the searches and send their block pairs in the same message as the
                    # client's tool_use — the shape a server tool has in the Messages API. The
                    # client replays the pairs with its next request, where
                    # _rewrite_inbound_native_blocks turns them into tool_use/tool_result next
                    # to the client's own results. 2026-09-18 (Cowork): without this, 5 of 10
                    # requested searches were dropped silently and re-issued one or two
                    # requests later. Past the budget they are refused, not run.
                    allowance = _turn_search_allowance(len(pending_searches),
                                                       max_searches_per_turn)
                    outcomes = await _run_turn_searches(
                        mcp_client, [ps.get("input") or {} for ps in pending_searches],
                        allowance, deadline, default_max_results, max_result_chars,
                        result_text_chars, exhausted=bool(final_mode))
                    mixed_traces: list[str] = []
                    mixed_blocks: list[dict] = []
                    ran = 0
                    for ps, (_text, ok, trace, reason, digest) in zip(pending_searches, outcomes):
                        mixed_traces.append(trace)
                        ran += 1 if ok else 0
                        mixed_blocks.extend(_native_search_blocks(
                            (ps.get("input") or {}).get("query", ""), ok, reason, digest))
                    searches_done += ran
                    logger.info("web_search.mixed_turn_ran", requested=len(pending_searches),
                                ran=ran, refused=bool(final_mode))
                    logger.info("web_search.native_blocks_emitted",
                                searches=len(mixed_blocks) // 2)
                    frames, global_index = _search_trace_frames(
                        global_index, mixed_blocks, mixed_traces)
                    for frame in frames:
                        yield frame
                elif mixed and not native_trace:
                    # Text mode: the results could not travel back with the client's next
                    # request, so the search is not run. If it vanished silently the model
                    # believed on the next request that it had run, or spent two requests
                    # re-issuing it (2026-09-17, Cowork) — leave a "not run" trace.
                    lines = [_trace_line((ps.get("input") or {}).get("query", ""),
                                         _trace_words("mixed")) for ps in pending_searches]
                    frames, global_index = _search_trace_frames(global_index, [], lines)
                    for frame in frames:
                        yield frame
                # Close blocks left open by an upstream that died mid-way — an envelope that
                # ends with an open block looks like a parse error on the SDK side, and the
                # cause (gateway vs upstream) becomes indistinguishable.
                for gi in sorted(open_global_blocks):
                    yield _sse("content_block_stop", {"type": "content_block_stop", "index": gi})
                open_global_blocks.clear()

                if error_seen or not saw_message_delta:
                    # Never claim a normal end. message_delta carries stop_reason — the
                    # frame that says "this is how it ended". Sending it after a truncated
                    # response makes client and audit log record a success. The error frame
                    # (already emitted above) is the terminal signal; message_stop only
                    # closes the stream. `saw_message_delta` False means the same: no
                    # terminal frame arrived (socket cut / timeout) and we have no basis to
                    # invent one.
                    if not error_seen:
                        yield _sse(
                            "error",
                            {"type": "error",
                             "error": {"type": "incomplete_stream",
                                       "message": "upstream ended without a terminal event"}},
                        )
                    # If the envelope was never opened (no message_start sent) do not send
                    # message_stop: the Anthropic SSE contract is message_start → … →
                    # message_stop, and a stop without a start is an SDK parse error that
                    # makes an upstream failure look like a gateway bug. The error frame
                    # above is the terminal signal in that case (_drain_error and the
                    # except clause below make the same distinction).
                    if envelope_open:
                        yield _sse("message_stop", {"type": "message_stop"})
                    break

                # Never say the message ended on a tool_use the client cannot see. Our search
                # tool_use blocks are all suppressed, so a `tool_use` stop_reason without a
                # client tool would leave the client waiting for a tool call that does not
                # exist.
                emitted_stop_reason = stop_reason_final
                if emitted_stop_reason == "tool_use" and not client_tool_present:
                    emitted_stop_reason = "end_turn"

                # Terminal: close the single envelope.
                #
                # Usage on the terminal frame: prompt buckets from the FIRST turn, output
                # summed over all turns. Clients read the last response's input+cache sum as
                # their context occupancy and auto-compact near the limit; the first turn's
                # prompt IS the client's conversation, later turns re-send it plus our search
                # results (which the client never receives). Reporting the N-turn sum made
                # the context look N× larger — the cause of "Autocompact is thrashing"
                # (2026-09-16, US). Billing is unchanged: on_usage(merged) gets the sum. The
                # cache buckets come from the same turn so they cannot contradict
                # input_tokens.
                if envelope_open and global_index == 0:
                    # The filter must not leave the response empty (an empty content list
                    # replayed by the client is a 400).
                    dropped = "".join(f.dropped for f in text_filters.values())
                    if dropped.strip():
                        yield _sse("content_block_start",
                                   {"type": "content_block_start", "index": 0,
                                    "content_block": {"type": "text", "text": ""}})
                        yield _sse("content_block_delta",
                                   {"type": "content_block_delta", "index": 0,
                                    "delta": {"type": "text_delta", "text": dropped}})
                        yield _sse("content_block_stop",
                                   {"type": "content_block_stop", "index": 0})
                        global_index = 1
                if envelope_open:
                    gauge = _client_prompt_usage(first_turn, merged)
                    # 마지막 턴의 delta 를 복사하고 stop_reason 만 우리 값으로 덮어쓴다.
                    # 판정(safeguard_results)은 이 턴 것만 실린다 — Claude Code 는 판정 항목이
                    # 정확히 하나여야 인식하고, 없으면 세션 내내 로컬 분류기로 바꾼다. 앞선
                    # 검색 턴의 판정은 우리 web_search 호출에 대한 것이라 넘기지 않는다.
                    # English: copy the last turn's delta and override only stop_reason. Only
                    # this turn's verdicts go out — Claude Code accepts exactly one entry and
                    # falls back to local classification for the session when none arrives;
                    # earlier search turns' verdicts cover our own web_search calls.
                    delta = {"stop_sequence": None, **turn_delta,
                             "stop_reason": emitted_stop_reason}
                    yield _sse(
                        "message_delta",
                        {"type": "message_delta",
                         "delta": delta,
                         "usage": {
                             "input_tokens": gauge.input_tokens,
                             "output_tokens": merged.output_tokens,
                             "cache_creation_input_tokens": gauge.cache_creation_input_tokens,
                             "cache_read_input_tokens": gauge.cache_read_input_tokens,
                         }},
                    )
                    yield _sse("message_stop", {"type": "message_stop"})
                break

            # Search turn: run ALL requested searches (F-3) → one tool_result per tool_use_id,
            # in order. search_attempts guards the loop even if every search fails (F-5).
            # Per-search deadline recheck so a large fan-out can't run uncapped (round2 High-2).
            search_attempts += 1
            tool_results = []
            # Per-turn cap. The excess still gets a RESPONSE: one tool_result per tool_use or
            # the next turn is a 400 — "not run" and "no result" are different things.
            allowance = _turn_search_allowance(len(pending_searches), max_searches_per_turn)
            if allowance < len(pending_searches):
                logger.info(
                    "web_search.turn_fanout_capped",
                    requested=len(pending_searches), allowed=allowance,
                )
            traces: list[str] = []   # one line per search → client-visible evidence
            native_blocks: list[dict] = []   # native mode: server_tool_use + result per search
            if soft_final:
                # Budget used up: every search of this turn is refused without running, and
                # the next turn is the hard final one (_final_turn_mode).
                refused_rounds += 1
                logger.info("web_search.search_refused_budget", requested=len(pending_searches))
            outcomes = await _run_turn_searches(
                mcp_client, [ps.get("input") or {} for ps in pending_searches], allowance,
                deadline, default_max_results, max_result_chars, result_text_chars,
                exhausted=soft_final)
            for ps, (result_text, ok, trace, reason, digest) in zip(pending_searches, outcomes):
                traces.append(trace)
                if ok:
                    searches_done += 1
                tool_results.append(_anthropic_tool_result(ps["id"], result_text, ok, reason))
                if native_trace:
                    native_blocks.extend(_native_search_blocks(
                        (ps.get("input") or {}).get("query", ""), ok, reason, digest))
            # Leave the search trace in the client envelope — block pairs in native mode,
            # then the text line in both modes. Nothing is added to the INTERNAL
            # conversation: it holds the real tool_use/tool_result.
            if envelope_open:
                if native_trace and native_blocks:
                    # Paired with inbound_native_rewritten on the next request for readouts.
                    logger.info("web_search.native_blocks_emitted",
                                searches=len(native_blocks) // 2)
                frames, global_index = _search_trace_frames(
                    global_index, native_blocks if native_trace else [], traces)
                for frame in frames:
                    yield frame
            # The forced-final turn sends the same prefix, so the marker is read there too.
            if cache_results:
                conversation = _place_cache_breakpoint(
                    base_body, conversation, tool_results, our_tool_use_ids)
            conversation = conversation + [
                {"role": "assistant", "content": assistant_content},
                {"role": "user", "content": tool_results},
            ]
    except Exception:
        logger.exception("web_search.anthropic_stream_failed")
        if envelope_open:
            yield _sse("error", {"type": "error",
                                 "error": {"type": "api_error", "message": "web search loop failed"}})
            yield _sse("message_stop", {"type": "message_stop"})
        return
    finally:
        merged.web_search_count = searches_done
        merged.total_tokens = merged.input_tokens + merged.output_tokens
        try:
            await on_usage(merged)
        except Exception:
            logger.warning("web_search.on_usage_failed")


async def _drain_error(chunk_iter: AsyncIterator[bytes], envelope_open: bool) -> AsyncIterator[bytes]:
    """Relay a provider error turn (non-200). If the envelope was already opened we
    close it cleanly; otherwise we surface the provider's error frames directly."""
    async for raw in chunk_iter:
        try:
            ev = json.loads(raw)
        except (ValueError, TypeError):
            continue
        yield _sse(ev.get("type", "error"), ev)
    if envelope_open:
        yield _sse("message_stop", {"type": "message_stop"})


# ═══════════════════════════════════════════════════════════════════════════════
# ANTHROPIC (Messages) — non-streaming loop
# ═══════════════════════════════════════════════════════════════════════════════
async def _anthropic_nonstream(
    *,
    invoke: InvokeFn,
    base_body: dict,
    mcp_client: AgentCoreMcpClient,
    on_usage: Callable[[TokenUsage], Awaitable[None]],
    max_iterations: int,
    deadline: float,
    default_max_results: int,
    max_result_chars: int = 0,
    max_searches_per_turn: int = 0,
    cache_results: bool = True,
    result_text_chars: int = 0,
    native_trace: bool = False,
    mixed_turn_run: bool = False,
    final_turn_soft: bool = False,
) -> JSONResponse:
    """Non-streaming twin of ``_anthropic_stream``: loop with ``invoke()`` and return the
    assembled final body (our plumbing removed, traces/native blocks prepended, usage rewritten
    to the client-facing gauge). ``mixed_turn_run`` / ``final_turn_soft``: same contract as
    the streaming path; here the block pairs of a mixed turn are prepended like every other
    trace, so they sit BEFORE the client's tool_use (the stream has them after it — the
    inbound rewrite accepts both orders)."""
    from app.providers.bedrock_adapter import _extract_bedrock_usage

    merged = TokenUsage()
    first_turn: TokenUsage | None = None   # turn-1 prompt buckets → client usage (context gauge)
    nudged = False           # empty force_final turn re-prompted once (_FINAL_TURN_TEXT_NUDGE)
    conversation: list[dict] = list(base_body.get("messages") or [])
    searches_done = 0
    search_attempts = 0      # loop guard incl. failures (F-5)
    refused_rounds = 0       # soft final turns whose searches were refused (bounds the loop)
    our_tool_use_ids: set[str] = set()
    traces: list[str] = []   # one line per search → client-visible evidence (_TRACE_PREFIX)
    native_blocks: list[dict] = []   # native mode: server_tool_use + result per search
    final_status = 200
    final_body: dict = {}

    try:
        while True:
            final_mode = _final_turn_mode(search_attempts, max_iterations,
                                          time.monotonic() > deadline, final_turn_soft,
                                          refused_rounds)
            force_final = final_mode == "hard"
            soft_final = final_mode == "soft"
            if final_mode:
                logger.info("web_search.final_turn", mode=final_mode, attempts=search_attempts)
            turn_body = _with_web_search_tool(base_body, "anthropic", include=True,
                                              first_turn=search_attempts == 0,
                                              budget=(max_searches_per_turn, max_iterations),
                                              final=force_final)
            turn_body = dict(turn_body)
            # Hard final turn: tool kept (tool_choice: none), answer-now instruction appended;
            # soft final turn: only the instruction — same contract as the streaming path.
            turn_body["messages"] = (_with_answer_now(conversation) if force_final
                                     else _with_search_exhausted(conversation) if soft_final
                                     else conversation)
            turn_body.pop("stream", None)
            status, body, _h, usage = await invoke(turn_body)
            final_status = status
            try:
                final_body = json.loads(body)
            except (ValueError, TypeError):
                final_body = {"error": {"type": "api_error", "message": "invalid provider response"}}
            if status != 200:
                break
            _merge_usage(merged, usage)
            if first_turn is None:
                first_turn = _prompt_snapshot(
                    usage.input_tokens, usage.cache_creation_input_tokens,
                    usage.cache_read_input_tokens,
                )

            content = final_body.get("content") or []
            our_calls = [
                b for b in content
                if isinstance(b, dict)
                and b.get("type") == "tool_use"
                and b.get("name") == GW_WEB_SEARCH_NAME
            ]
            client_calls = [b for b in content if _is_client_tool_use_block(b)]
            our_tool_use_ids.update(c.get("id") for c in our_calls if c.get("id"))

            has_text = any(
                isinstance(b, dict) and b.get("type") == "text" and (b.get("text") or "").strip()
                for b in content
            )
            if (final_mode and not nudged and not client_calls and not our_calls
                    and content and not has_text):
                nudged = True
                logger.info("web_search.final_turn_empty_nudge")
                conversation = conversation + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": [{"type": "text", "text": _FINAL_TURN_TEXT_NUDGE}]},
                ]
                continue
            if our_calls and client_calls and native_trace and mixed_turn_run:
                # Mixed turn, native mode: run the searches and hand their block pairs to the
                # client together with its tool_use (see the streaming path).
                allowance = _turn_search_allowance(len(our_calls), max_searches_per_turn)
                outcomes = await _run_turn_searches(
                    mcp_client, [c.get("input") or {} for c in our_calls], allowance,
                    deadline, default_max_results, max_result_chars, result_text_chars,
                    exhausted=bool(final_mode))
                ran = 0
                for call, (_text, ok, trace, reason, digest) in zip(our_calls, outcomes):
                    traces.append(trace)
                    ran += 1 if ok else 0
                    native_blocks.extend(_native_search_blocks(
                        (call.get("input") or {}).get("query", ""), ok, reason, digest))
                searches_done += ran
                logger.info("web_search.mixed_turn_ran", requested=len(our_calls), ran=ran,
                            refused=bool(final_mode))
            elif our_calls and client_calls and not native_trace:
                # Mixed turn, text mode: the search is not run — same "not run" trace as the
                # streaming path.
                traces.extend(_trace_line((c.get("input") or {}).get("query", ""),
                                          _trace_words("mixed")) for c in our_calls)
            if force_final or not our_calls or client_calls:
                break  # terminal — return this body

            search_attempts += 1
            tool_results = []
            assistant_content = content
            # Per-turn cap — see the same comment in the streaming stitcher.
            allowance = _turn_search_allowance(len(our_calls), max_searches_per_turn)
            if allowance < len(our_calls):
                logger.info("web_search.turn_fanout_capped",
                            requested=len(our_calls), allowed=allowance)
            if soft_final:
                # Budget used up: refuse without running; the next turn is the hard final one.
                refused_rounds += 1
                logger.info("web_search.search_refused_budget", requested=len(our_calls))
            outcomes = await _run_turn_searches(
                mcp_client, [c.get("input") or {} for c in our_calls], allowance,
                deadline, default_max_results, max_result_chars, result_text_chars,
                exhausted=soft_final)
            for call, (result_text, ok, trace, reason, digest) in zip(our_calls, outcomes):
                traces.append(trace)
                if ok:
                    searches_done += 1
                tool_results.append(
                    _anthropic_tool_result(call.get("id"), result_text, ok, reason))
                if native_trace:
                    native_blocks.extend(_native_search_blocks(
                        (call.get("input") or {}).get("query", ""), ok, reason, digest))
            # The forced-final turn sends the same prefix, so the marker is read there too.
            if cache_results:
                conversation = _place_cache_breakpoint(
                    base_body, conversation, tool_results, our_tool_use_ids)
            conversation = conversation + [
                {"role": "assistant", "content": assistant_content},
                {"role": "user", "content": tool_results},
            ]
    finally:
        # Fire on_usage whenever any tokens accrued — even if a LATER turn failed after
        # earlier turns succeeded (tokens were consumed and must be accounted) (F-9).
        merged.web_search_count = searches_done
        merged.total_tokens = merged.input_tokens + merged.output_tokens
        # Called even with zero tokens. A `> 0` guard used to skip on_usage when the first
        # turn died with an upstream 4xx/5xx — but that callback is cost_recorder.finalize,
        # whose zero-usage path is the ONLY place that releases the RPM/TPM/cost
        # reservations (this path never runs the fallback loop's release_reservations). A
        # request that got a 400 therefore held the user's per-minute/hour quota until the
        # window ended. With zero tokens finalize writes no usage_logs row and only releases
        # the reservation, so the unconditional call is safe.
        try:
            await on_usage(merged)
        except Exception:
            logger.warning("web_search.on_usage_failed")

    if final_status == 200 and isinstance(final_body.get("usage"), dict):
        # Client-facing usage: prompt buckets from the FIRST turn (the client's real
        # context — see _client_prompt_usage), output summed over all turns. The body's
        # own usage is the LAST turn's, which carries our search results the client
        # never keeps. Billing already went out via on_usage(merged).
        gauge = _client_prompt_usage(first_turn, merged)
        final_body["usage"]["input_tokens"] = gauge.input_tokens
        final_body["usage"]["cache_creation_input_tokens"] = gauge.cache_creation_input_tokens
        final_body["usage"]["cache_read_input_tokens"] = gauge.cache_read_input_tokens
        final_body["usage"]["output_tokens"] = merged.output_tokens
    # Strip thinking/redacted_thinking from the CLIENT-returned body: the client replays
    # this (possibly stitched) assistant message on its next turn, and Bedrock rejects a
    # modified thinking block. Prior-turn thinking may be omitted on a new user turn, so
    # this is safe. (Mirrors the streaming path's client-side suppression.) (F-thinking)
    if final_status == 200 and isinstance(final_body.get("content"), list):
        # Our web_search tool_use blocks are removed too. When a client tool call and our
        # search arrive in the SAME turn (both dialects support parallel tool calls) the
        # turn is terminal and this body goes out as-is, so the client would receive a
        # tool_use for a tool it never declared. The Anthropic contract requires a
        # tool_result for every tool_use, so the client would try to run an unknown tool
        # (Claude Code reports it as unknown) or get a 400 on its next turn. The streaming
        # path already suppresses these blocks; only the non-streaming path lacked it.
        final_body["content"] = [
            b for b in final_body["content"]
            if isinstance(b, dict)
            and b.get("type") not in ("thinking", "redacted_thinking")
            and not (b.get("type") == "tool_use" and b.get("name") == GW_WEB_SEARCH_NAME)
        ]
        # Remove model-imitated trace lines (see _TraceLineFilter). If everything would be
        # removed, keep the original — empty content is a 400 on the next turn.
        cleaned = []
        for b in final_body["content"]:
            if b.get("type") == "text":
                t = _strip_fake_trace_lines(b.get("text") or "")
                if not t.strip():
                    continue
                b = {**b, "text": t}
            cleaned.append(b)
        if cleaned or not final_body["content"]:
            final_body["content"] = cleaned
        # Search trace at the front of the body (same contract as the streaming path):
        # block pairs in native mode, then one text block with the 🔎 lines.
        lead: list[dict] = []
        if native_trace and native_blocks:
            logger.info("web_search.native_blocks_emitted",
                        searches=len(native_blocks) // 2)
            lead.extend(native_blocks)
        if traces:   # the 🔎 line is emitted in native mode too (see the streaming path)
            lead.append({"type": "text", "text": "\n".join(traces) + "\n\n"})
        if lead:
            final_body["content"][0:0] = lead
        # Only our blocks were removed, so a leftover `tool_use` stop_reason would make the
        # client wait for an invisible tool call — same decision as the streaming stitcher.
        if final_body.get("stop_reason") == "tool_use" and not any(
            _is_client_tool_use_block(b) for b in final_body["content"]
        ):
            final_body["stop_reason"] = "end_turn"
    # final_body 는 마지막 턴의 응답을 그대로 쓰고 필요한 키만 고친다. 그래서 최상위
    # safeguard_results(마지막 턴 판정)와 처음 보는 키가 그대로 전달된다 — 이 응답을
    # 새로 만들지 말 것(스트리밍 쪽 turn_delta 와 같은 이유).
    # English: final_body is the last turn's response with only the keys we must change
    # rewritten, so the top-level safeguard_results (the last turn's verdicts) and any
    # unknown key pass through — do not rebuild it (same reason as turn_delta above).
    return JSONResponse(status_code=final_status, content=final_body)


# ═══════════════════════════════════════════════════════════════════════════════
# RESPONSES (OpenAI) — helpers
# ═══════════════════════════════════════════════════════════════════════════════
def _normalize_responses_input(body: dict) -> list:
    """Responses `input` may be a string or an array of items — normalize to a list
    so we can append function_call / function_call_output items for continuation."""
    inp = body.get("input")
    if isinstance(inp, list):
        return list(inp)
    if isinstance(inp, str):
        return [{"role": "user", "content": inp}]
    return []


# ═══════════════════════════════════════════════════════════════════════════════
# RESPONSES (OpenAI) — streaming stitcher
# ═══════════════════════════════════════════════════════════════════════════════
async def _responses_stream(
    *,
    invoke_stream: InvokeStreamFn,
    base_body: dict,
    mcp_client: AgentCoreMcpClient,
    request: Request,
    on_usage: Callable[[TokenUsage], Awaitable[None]],
    max_iterations: int,
    deadline: float,
    default_max_results: int,
    max_result_chars: int = 0,
    max_searches_per_turn: int = 0,
    cache_results: bool = True,
    result_text_chars: int = 0,
) -> AsyncIterator[bytes]:
    """Stitch N Responses turns into ONE response.created … response.completed stream.

    Forwards message/text output items (re-indexed); suppresses function_call plumbing
    for our web_search; runs the search between turns.
    """
    merged = TokenUsage()
    first_turn: TokenUsage | None = None   # turn-1 prompt buckets → client usage (context gauge)
    conv_input: list = _normalize_responses_input(base_body)
    searches_done = 0        # successful → web_search_count
    search_attempts = 0      # all rounds incl. failures → loop guard (F-5)
    envelope_open = False
    global_out_index = 0
    #: The next two are PER-TURN state. Kept at loop scope, an answer turn that died without
    #: a terminal event would send the previous search turn's object and a
    #: "response.completed" as the final frame — a fabricated success on a truncated answer.
    #: Worse, that object's output is EMPTY (our web_search call was correctly removed), so
    #: clients that rebuild the answer from the final object (Codex family) get an empty
    #: answer marked completed. Dying on the first turn would send a completed without even
    #: an `id`.
    final_response_obj: Optional[dict] = None
    final_terminal_type = "response.completed"  # actual upstream terminal type (F-1)
    #: Whether THIS turn delivered a terminal event. Without one, no end is invented.
    saw_terminal_event = False
    #: The envelope id the client saw — from the FIRST turn's response.created. The terminal
    #: frame must carry the same id: with the last turn's id the client receives the end of
    #: a response it never opened (created(resp_1) … completed(resp_2)) and anything that
    #: correlates by id cannot find it.
    envelope_response_id: str | None = None
    our_call_ids: set[str] = set()  # our web_search call_ids to strip from final output (F-3 Responses)
    error_seen = False       # a 200-stream `error` event occurred (NEW round2 High-1)

    try:
        while True:
            force_final = search_attempts >= max_iterations or time.monotonic() > deadline
            turn_body = _with_web_search_tool(base_body, "responses", include=not force_final,
                                              first_turn=search_attempts == 0,
                                              budget=(max_searches_per_turn, max_iterations))
            turn_body = dict(turn_body)
            # Forced-final turn: the tool is removed, so our plumbing must be rewritten as
            # text — see _strip_anthropic_web_search_plumbing for the reasoning.
            turn_body["input"] = (
                _strip_responses_web_search_items(conv_input, our_call_ids)
                if force_final
                else conv_input
            )
            turn_body["stream"] = True
            status, chunk_iter, _h, _rid = await invoke_stream(turn_body)
            if status != 200:
                async for b in _drain_responses_error(chunk_iter, envelope_open):
                    yield b
                return

            # Per-turn state reset (see the declarations above).
            saw_terminal_event = False

            local_to_global: dict[int, int] = {}
            suppressed_out: dict[int, dict] = {}     # our web_search fn call by output_index
            fn_arg_buf: dict[int, str] = {}          # output_index → args buffer (our fn)
            turn_output_items: list[dict] = []       # completed output items (rebuild conv_input)
            pending_searches: list[dict] = []         # [{call_id, input}] — many per turn (F-3)
            client_tool_present = False

            async for raw in chunk_iter:
                try:
                    ev = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                etype = ev.get("type", "")

                if etype == "response.created":
                    if not envelope_open:
                        envelope_open = True
                        envelope_response_id = ((ev.get("response") or {}).get("id"))
                        yield _sse("response.created", ev)
                elif etype == "response.in_progress":
                    if not envelope_open:
                        yield _sse("response.in_progress", ev)

                elif etype == "response.output_item.added":
                    item = ev.get("item") or {}
                    oidx = ev.get("output_index", 0)
                    itype = item.get("type")
                    if itype == "function_call" and item.get("name") == GW_WEB_SEARCH_NAME:
                        suppressed_out[oidx] = {"kind": "web_search",
                                                "call_id": item.get("call_id"),
                                                "name": item.get("name")}
                        fn_arg_buf[oidx] = ""
                        if item.get("call_id"):
                            our_call_ids.add(item["call_id"])  # strip from final output (F-3 Responses)
                    elif _is_client_tool_call_item(item, our_call_ids):
                        client_tool_present = True
                        gi = global_out_index; global_out_index += 1
                        local_to_global[oidx] = gi
                        ev2 = dict(ev); ev2["output_index"] = gi
                        yield _sse(etype, ev2)
                    else:
                        gi = global_out_index; global_out_index += 1
                        local_to_global[oidx] = gi
                        ev2 = dict(ev); ev2["output_index"] = gi
                        yield _sse(etype, ev2)

                elif etype == "response.function_call_arguments.delta":
                    oidx = ev.get("output_index", 0)
                    if oidx in suppressed_out:
                        fn_arg_buf[oidx] += ev.get("delta", "") or ""
                    else:
                        gi = local_to_global.get(oidx, oidx)
                        ev2 = dict(ev); ev2["output_index"] = gi
                        yield _sse(etype, ev2)

                elif etype == "response.function_call_arguments.done":
                    oidx = ev.get("output_index", 0)
                    if oidx not in suppressed_out:
                        gi = local_to_global.get(oidx, oidx)
                        ev2 = dict(ev); ev2["output_index"] = gi
                        yield _sse(etype, ev2)

                elif etype == "response.output_item.done":
                    item = ev.get("item") or {}
                    oidx = ev.get("output_index", 0)
                    turn_output_items.append(item)
                    if oidx in suppressed_out and suppressed_out[oidx]["kind"] == "web_search":
                        try:
                            args = json.loads(fn_arg_buf.get(oidx, "") or "{}")
                        except (ValueError, TypeError):
                            args = {}
                        pending_searches.append({"call_id": suppressed_out[oidx]["call_id"], "input": args})
                        continue  # suppress
                    gi = local_to_global.get(oidx, oidx)
                    ev2 = dict(ev); ev2["output_index"] = gi
                    yield _sse(etype, ev2)

                elif etype in (
                    "response.output_text.delta", "response.output_text.done",
                    "response.content_part.added", "response.content_part.done",
                    "response.reasoning_summary_text.delta", "response.reasoning_summary_text.done",
                ):
                    oidx = ev.get("output_index", 0)
                    if oidx in suppressed_out:
                        continue
                    gi = local_to_global.get(oidx, oidx)
                    ev2 = dict(ev); ev2["output_index"] = gi
                    yield _sse(etype, ev2)

                elif etype in ("response.completed", "response.incomplete", "response.failed"):
                    resp_obj = ev.get("response") or {}
                    saw_terminal_event = True
                    final_response_obj = resp_obj
                    final_terminal_type = etype  # preserve incomplete/failed, don't fake completed (F-1)
                    # Responses `input_tokens` INCLUDES both cached_tokens and
                    # cache_write_tokens — parse per turn into exclusive buckets before
                    # accumulating, so merged.input_tokens stays the non-cached billable
                    # input (TokenUsage contract). Per TURN, not on the final sum: each
                    # turn caches a different amount, and typically exactly one turn of a
                    # search loop writes the cache while the rest read it.
                    turn_usage = extract_responses_usage(resp_obj)
                    _merge_usage(merged, turn_usage)
                    if first_turn is None:
                        first_turn = _prompt_snapshot(
                            turn_usage.input_tokens, turn_usage.cache_creation_input_tokens,
                            turn_usage.cache_read_input_tokens,
                        )
                    # captured; emit our own terminal event at envelope close
                elif etype == "error":
                    error_seen = True  # NEW round2 High-1: do not also emit a fake completed
                    yield _sse("error", ev)
                elif isinstance(ev.get("error"), dict):
                    # Adapters emit error chunks with a NESTED type:
                    # {"error": {"type": "provider_error", ...}} — no top-level "type", so
                    # the branch above misses it and it vanished silently. See the same
                    # branch in the Anthropic stitcher.
                    error_seen = True
                    yield _sse("error", {"type": "error", "error": ev["error"]})

            is_search_turn = bool(pending_searches) and not client_tool_present and not error_seen
            # An incomplete/failed upstream turn is terminal even if a search was requested —
            # never loop on a truncated/failed response (F-1).
            if (
                not is_search_turn
                or force_final
                or final_terminal_type != "response.completed"
                or error_seen
                # A turn without a terminal event (socket cut / timeout) is terminal too.
                # Without this clause pending_searches would be empty and the condition
                # above would fall through by accident — with final_* still holding the
                # PREVIOUS turn's values, i.e. a fabricated success.
                or not saw_terminal_event
            ):
                # Do not inherit the previous turn's object when this turn had no terminal
                # event.
                if not saw_terminal_event:
                    # The previous (search) turn's object has nothing to do with this
                    # answer — its output had our web_search removed and is empty, which
                    # gives final-object clients "empty answer, completed". The envelope id
                    # is restored by _finalize_responses_obj.
                    final_response_obj = {}
                    if error_seen:
                        # Ending on an error and simply being cut off must be DIFFERENT
                        # signals: failed = the upstream returned an error, incomplete =
                        # the stream broke without a terminal frame. Merged, an operator
                        # cannot tell a provider failure from a network cut.
                        final_terminal_type = "response.failed"
                    else:
                        final_terminal_type = "response.incomplete"
                        yield _sse(
                            "error",
                            {"type": "error",
                             "error": {"type": "incomplete_stream",
                                       "message": "upstream ended without a terminal event"}},
                        )
                # If a mid-stream `error` occurred, the error frame is the terminal signal —
                # do NOT also emit a synthetic response.completed (NEW round2 High-1). Emit
                # response.failed only if we never got a real terminal event.
                # If the envelope (response.created) was never sent, build no terminal
                # object either: a completed/failed without a created has nothing for the
                # Responses SDK to correlate with and fails to parse — an upstream error
                # would look like a gateway bug. The error frame already sent is the
                # terminal signal in that case.
                if not envelope_open:
                    break
                if error_seen and final_terminal_type == "response.completed":
                    yield _sse("response.failed",
                               {"type": "response.failed",
                                "response": _finalize_responses_obj(
                                    final_response_obj, merged, global_out_index,
                                    "response.failed", our_call_ids,
                                    envelope_id=envelope_response_id,
                                    first_turn=first_turn)})
                else:
                    yield _sse(
                        final_terminal_type,
                        {"type": final_terminal_type,
                         "response": _finalize_responses_obj(
                             final_response_obj, merged, global_out_index,
                             final_terminal_type, our_call_ids,
                             envelope_id=envelope_response_id,
                             first_turn=first_turn)},
                    )
                break

            # Search turn: run ALL requested searches (F-3) → one function_call_output per call_id,
            # in order. search_attempts guards the loop even if every search fails (F-5).
            # Per-search deadline recheck so a fan-out of many searches can't run uncapped
            # past the total deadline (NEW round2 High-2).
            search_attempts += 1
            outputs = []
            # Per-turn cap — see the same comment in the Anthropic stitcher.
            allowance = _turn_search_allowance(len(pending_searches), max_searches_per_turn)
            if allowance < len(pending_searches):
                logger.info(
                    "web_search.turn_fanout_capped",
                    requested=len(pending_searches), allowed=allowance,
                )
            outcomes = await _run_turn_searches(
                mcp_client, [ps.get("input") or {} for ps in pending_searches], allowance,
                deadline, default_max_results, max_result_chars, result_text_chars)
            for ps, (result_text, ok, _trace, reason, _digest) in zip(pending_searches, outcomes):
                if ok:
                    searches_done += 1
                outputs.append(_responses_call_output(ps["call_id"], result_text, ok, reason))
            conv_input = conv_input + turn_output_items + outputs
    except Exception:
        logger.exception("web_search.responses_stream_failed")
        if envelope_open:
            # Close the already-open envelope with a terminal response.failed so the client
            # never hangs on an open response (F-6).
            yield _sse("error", {"type": "error",
                                 "error": {"type": "api_error", "message": "web search loop failed"}})
            yield _sse("response.failed",
                       {"type": "response.failed",
                        "response": _finalize_responses_obj(
                            final_response_obj, merged, global_out_index, "response.failed", our_call_ids,
                            first_turn=first_turn)})
        return
    finally:
        merged.web_search_count = searches_done
        merged.total_tokens = merged.input_tokens + merged.output_tokens
        try:
            await on_usage(merged)
        except Exception:
            logger.warning("web_search.on_usage_failed")


def _finalize_responses_obj(
    resp_obj: Optional[dict], merged: TokenUsage, _n: int,
    terminal_type: str = "response.completed",
    our_call_ids: Optional[set] = None,
    envelope_id: str | None = None,
    first_turn: TokenUsage | None = None,
) -> dict:
    """Build the terminal response object: output summed over turns, prompt buckets
    from the FIRST turn (the client's real context — see _client_prompt_usage).

    Preserves the real upstream terminal status: response.completed → 'completed',
    response.incomplete → 'incomplete', response.failed → 'failed' (F-1).
    Strips OUR web_search function_call items from `output` so the client's final
    response never contains a function_call it was never streamed (F-3 Responses).
    """
    status_map = {"response.completed": "completed",
                  "response.incomplete": "incomplete",
                  "response.failed": "failed"}
    obj = dict(resp_obj or {})
    obj["status"] = status_map.get(terminal_type, "completed")
    # Pin the envelope id (the first response.created). Leaving the last turn's id gives
    # created(resp_1) … completed(resp_2) and clients/logs that correlate by id cannot find
    # the response. Merging several turns into one response is this stitcher's contract.
    if envelope_id:
        obj["id"] = envelope_id
    if our_call_ids and isinstance(obj.get("output"), list):
        obj["output"] = [
            it for it in obj["output"]
            if not (isinstance(it, dict) and it.get("type") == "function_call"
                    and it.get("call_id") in our_call_ids)
        ]
    # WIRE representation, not the billing one: Responses `input_tokens` must be the GRAND
    # TOTAL prompt count (cache reads AND cache writes included), because that is what the
    # OpenAI spec says and what Codex CLI reads to track context. merged.input_tokens is
    # the non-cached billing bucket, so add both cache buckets back on the way out.
    # Emitting the billing value here would produce cached_tokens > input_tokens — an
    # impossible payload. Both sub-counters are echoed for the same reason.
    # Prompt buckets = the FIRST turn's (what the client sent); later turns carry our
    # search results the client never keeps, and Codex reads this as its context gauge.
    gauge = _client_prompt_usage(first_turn, merged)
    wire_input = _wire_input(gauge)
    obj["usage"] = {
        "input_tokens": wire_input,
        "output_tokens": merged.output_tokens,
        "total_tokens": wire_input + merged.output_tokens,
        "input_tokens_details": {
            "cached_tokens": gauge.cache_read_input_tokens,
            "cache_write_tokens": gauge.cache_creation_input_tokens,
        },
        "output_tokens_details": {"reasoning_tokens": merged.reasoning_tokens},
    }
    return obj


async def _drain_responses_error(chunk_iter: AsyncIterator[bytes], envelope_open: bool) -> AsyncIterator[bytes]:
    """Relay a non-200 provider turn; close an already-open envelope with response.failed."""
    async for raw in chunk_iter:
        try:
            ev = json.loads(raw)
        except (ValueError, TypeError):
            continue
        yield _sse(ev.get("type", "error"), ev)
    # If a LATER turn returned non-200 after the envelope was already opened, close it with a
    # terminal response.failed so the client doesn't hang on an open response (F-6).
    if envelope_open:
        yield _sse("response.failed",
                   {"type": "response.failed", "response": {"status": "failed"}})


# ═══════════════════════════════════════════════════════════════════════════════
# RESPONSES (OpenAI) — non-streaming loop
# ═══════════════════════════════════════════════════════════════════════════════
async def _responses_nonstream(
    *,
    invoke: InvokeFn,
    base_body: dict,
    mcp_client: AgentCoreMcpClient,
    on_usage: Callable[[TokenUsage], Awaitable[None]],
    max_iterations: int,
    deadline: float,
    default_max_results: int,
    max_result_chars: int = 0,
    max_searches_per_turn: int = 0,
    cache_results: bool = True,
    result_text_chars: int = 0,
) -> JSONResponse:
    """Non-streaming twin of ``_responses_stream``."""
    merged = TokenUsage()
    first_turn: TokenUsage | None = None   # turn-1 prompt buckets → client usage (context gauge)
    conv_input: list = _normalize_responses_input(base_body)
    searches_done = 0
    search_attempts = 0      # loop guard incl. failures (F-5)
    our_call_ids: set[str] = set()
    final_status = 200
    final_body: dict = {}

    try:
        while True:
            force_final = search_attempts >= max_iterations or time.monotonic() > deadline
            turn_body = _with_web_search_tool(base_body, "responses", include=not force_final,
                                              first_turn=search_attempts == 0,
                                              budget=(max_searches_per_turn, max_iterations))
            turn_body = dict(turn_body)
            # Forced-final turn removes the tool, so our function_call/output items (leaked
            # ones included) are rewritten as text — see _anthropic_nonstream.
            turn_body["input"] = (
                _strip_responses_web_search_items(conv_input, our_call_ids)
                if force_final
                else conv_input
            )
            turn_body.pop("stream", None)
            status, body, _h, usage = await invoke(turn_body)
            final_status = status
            try:
                final_body = json.loads(body)
            except (ValueError, TypeError):
                final_body = {"error": {"type": "api_error", "message": "invalid provider response"}}
            if status != 200:
                break
            _merge_usage(merged, usage)
            if first_turn is None:
                first_turn = _prompt_snapshot(
                    usage.input_tokens, usage.cache_creation_input_tokens,
                    usage.cache_read_input_tokens,
                )

            output = final_body.get("output") or []
            our_calls = [
                o for o in output
                if isinstance(o, dict)
                and o.get("type") == "function_call"
                and o.get("name") == GW_WEB_SEARCH_NAME
            ]
            client_calls = [o for o in output if _is_client_tool_call_item(o)]
            our_call_ids.update(c.get("call_id") for c in our_calls if c.get("call_id"))

            if force_final or not our_calls or client_calls:
                break

            search_attempts += 1
            new_items = list(output)
            allowance = _turn_search_allowance(len(our_calls), max_searches_per_turn)
            if allowance < len(our_calls):
                logger.info("web_search.turn_fanout_capped",
                            requested=len(our_calls), allowed=allowance)
            inputs = []
            for call in our_calls:
                try:
                    inputs.append(json.loads(call.get("arguments") or "{}"))
                except (ValueError, TypeError):
                    inputs.append({})
            outcomes = await _run_turn_searches(
                mcp_client, inputs, allowance, deadline,
                default_max_results, max_result_chars, result_text_chars)
            for call, (result_text, ok, _trace, reason, _digest) in zip(our_calls, outcomes):
                if ok:
                    searches_done += 1
                new_items.append(
                    _responses_call_output(call.get("call_id"), result_text, ok, reason))
            conv_input = conv_input + new_items
    finally:
        merged.web_search_count = searches_done  # fire on any accrued usage even on late failure (F-9)
        merged.total_tokens = merged.input_tokens + merged.output_tokens
        # Called even with zero tokens — the reservation release depends on this callback;
        # see the same block in _anthropic_nonstream.
        try:
            await on_usage(merged)
        except Exception:
            logger.warning("web_search.on_usage_failed")

    if final_status == 200 and isinstance(final_body.get("usage"), dict):
        # Same wire-vs-billing split as _finalize_responses_obj: the client must see the
        # cache-INCLUSIVE prompt count that the Responses spec defines.
        gauge = _client_prompt_usage(first_turn, merged)   # first turn = the client's context
        wire_input = _wire_input(gauge)
        final_body["usage"]["input_tokens"] = wire_input
        final_body["usage"]["output_tokens"] = merged.output_tokens
        final_body["usage"]["total_tokens"] = wire_input + merged.output_tokens
        details = final_body["usage"].get("input_tokens_details")
        if isinstance(details, dict):
            details["cached_tokens"] = gauge.cache_read_input_tokens
            details["cache_write_tokens"] = gauge.cache_creation_input_tokens
    # Remove our web_search function_call items from the response output — same reasoning
    # as the block in _anthropic_nonstream, and the same thing _finalize_responses_obj
    # already does on the streaming side.
    if final_status == 200 and isinstance(final_body.get("output"), list):
        final_body["output"] = [
            it for it in final_body["output"]
            if not (
                isinstance(it, dict)
                and it.get("type") == "function_call"
                and it.get("name") == GW_WEB_SEARCH_NAME
            )
        ]
    return JSONResponse(status_code=final_status, content=final_body)


# ═══════════════════════════════════════════════════════════════════════════════
# Dispatcher (the router's entry point)
# ═══════════════════════════════════════════════════════════════════════════════
async def run_web_search_loop(
    *,
    dialect: str,
    invoke: InvokeFn,
    invoke_stream: InvokeStreamFn,
    initial_req_data: dict,
    is_stream: bool,
    mcp_client: AgentCoreMcpClient,
    request: Request,
    on_usage: Callable[[TokenUsage], Awaitable[None]],
    max_iterations: int = 5,
    total_deadline_sec: float = 90.0,
    default_max_results: int = 10,
    max_result_chars: int = 0,
    max_searches_per_turn: int = 0,
    cache_results: bool = True,
    result_text_chars: int = 0,
    handshake_timeout: float = 10.0,
    #: Token back-estimation hook (KI-08). The Responses dialect carries usage only in the
    #: terminal event; if a pass-through stream breaks before it, usage would be all zeros
    #: and no usage_logs row would be written (see the same comment in services/streaming.py).
    tokenizer_hook: Callable[[str], Awaitable[int | None]] | None = None,
    response_headers: Optional[dict] = None,
    on_stream_complete: Callable[[str, str], Awaitable[None]] | None = None,
    on_nonstream_complete: Callable[[int, bytes], Awaitable[None]] | None = None,
) -> StreamingResponse | JSONResponse:
    """Run the server-side web-search loop and return the client response.

    ``dialect`` is "anthropic" (/v1/messages) or "responses" (/v1/responses). The loop
    ensures the MCP client is initialized (discovers the WebSearch tool) before starting;
    if that fails, it degrades to a plain pass-through of the original request (no tool).

    Request flow: strip native web-search tools → convert replayed native trace blocks →
    F-7 pass-through if the client owns a ``web_search`` tool → MCP handshake (fallback to
    a no-search call on failure) → the dialect's stream/non-stream loop.

    ``on_stream_complete`` / ``on_nonstream_complete`` are the request/response **body**
    log hooks. Both are ``None`` when body logging is off, and the routers decide that
    before calling — passing a hook makes the streaming paths accumulate the full SSE
    text in memory, so the decision has to be made up front. They are parameters here
    because the routers wire body logging inside their ``if is_stream:`` block and this
    function returns BEFORE that block: without the hooks, requests of a web-search-enabled
    profile would never pass the router's logging code and go unrecorded — and Codex on
    Mantle, the main web-search user, leaves no AWS invocation log either, so the body would
    exist nowhere. All six return paths are wired and a test counts them.
    """
    deadline = time.monotonic() + total_deadline_sec

    def _log_stream(gen):
        """Wrap a streaming response with the body-log recorder (pass-through without a hook)."""
        if on_stream_complete is None:
            return gen
        from app.services.body_log_records import wrap_stream_for_body_log

        return wrap_stream_for_body_log(
            gen, dialect=dialect, on_complete=on_stream_complete
        )

    async def _log_nonstream(status: int, raw: bytes) -> None:
        """Record a non-streaming response body; never fails the request."""
        if on_nonstream_complete is None:
            return
        try:
            await on_nonstream_complete(status, raw)
        except Exception:
            logger.warning("web_search.body_log_failed")

    # Strip Anthropic/OpenAI NATIVE web_search tool(s) up front: Bedrock/Mantle reject
    # them, and we fulfill the intent via our own loop. Doing it here (before F-7) means
    # a native-only request no longer trips _client_declares_web_search, so the loop runs;
    # a genuine CUSTOM web_search tool (no native type) survives and is still respected
    # (F-7). This also covers the MCP-init-failure fallback below (both read initial_req_data).
    initial_req_data = _strip_native_web_search(initial_req_data)
    # Replayed native trace blocks (server_tool_use / web_search_tool_result — unknown to
    # Bedrock): loop turns carry our tool definition, so there they are REWRITTEN as
    # tool_use/tool_result history with the result excerpts; the F-7 pass-through and the
    # MCP-init-failure fallback go out without our tool, so there they become text trace
    # lines. Both helpers return the same object when no block is present.
    loop_req_data = initial_req_data
    if dialect == "anthropic":
        loop_req_data = _rewrite_inbound_native_blocks(initial_req_data)
        initial_req_data = _normalize_inbound_native_blocks(initial_req_data)
    native_trace = dialect == "anthropic" and _native_trace_enabled(request)
    # The Responses loops (Codex) take neither native blocks nor the client-tool flags.
    dialect_kw: dict = ({"native_trace": native_trace, **_client_tool_flags()}
                        if dialect == "anthropic" else {})

    # streaming.py sse helpers now call on_usage(usage, first_token_time) (2-arg TTFT
    # contract). The web-search loop's on_usage is 1-arg (multi-turn aggregate — per-turn
    # TTFT is not meaningful), so drop the first_token_time when threading the callback
    # into a pass-through sse helper.
    async def _stream_on_usage(usage: TokenUsage, _first_token_time: float | None = None) -> None:
        await on_usage(usage)

    # F-7: if the client already declared its OWN tool named `web_search`, do not hijack it.
    # Skip the loop entirely and pass the request through unmodified (client's tool loop runs).
    if _client_declares_web_search(initial_req_data):
        logger.info("web_search.client_owns_tool_skip")
        base = dict(initial_req_data)
        if is_stream:
            base["stream"] = True
            status, chunk_iter, _h, _ = await invoke_stream(base)
            from app.services.streaming import (
                bedrock_anthropic_sse_stream,
                responses_sse_stream,
            )
            gen = (bedrock_anthropic_sse_stream if dialect == "anthropic" else responses_sse_stream)(
                request, chunk_iter, on_usage=_stream_on_usage,
                tokenizer_hook=tokenizer_hook)
            return StreamingResponse(_log_stream(gen), status_code=status,
                                     media_type="text/event-stream", headers=response_headers)
        base.pop("stream", None)
        status, body, _h, usage = await invoke(base)
        if usage and (usage.input_tokens + usage.output_tokens) > 0:
            await on_usage(usage)
        await _log_nonstream(status, body)
        try:
            content = json.loads(body)
        except (ValueError, TypeError):
            content = {"error": {"type": "api_error", "message": "invalid provider response"}}
        return JSONResponse(status_code=status, content=content, headers=response_headers)

    # Ensure the AgentCore WebSearch tool is discoverable before we advertise it to the
    # model. If discovery fails, fall back to a normal (no-search) call so the request
    # still succeeds — the model simply lacks web search this time.
    try:
        # A timeout here states the worst case explicitly: while the handshake stalls this
        # request already holds its RPM/TPM/CPH reservations. asyncio.TimeoutError is an
        # Exception, so the except below catches it and falls back to a single no-search
        # turn.
        await asyncio.wait_for(
            mcp_client.ensure_initialized(),
            timeout=handshake_timeout,
        )
    except Exception:
        logger.warning("web_search.mcp_init_failed_fallback_no_search")
        # Degrade to a normal (no-search) single turn. Route the stream through the real
        # dialect SSE helper so usage is aggregated and on_usage still fires (F-4) — a
        # successful no-search response must NOT lose cost accounting.
        base = dict(initial_req_data)
        base.pop("stream", None)
        if is_stream:
            base["stream"] = True
            status, chunk_iter, headers, _ = await invoke_stream(base)
            from app.services.streaming import (
                bedrock_anthropic_sse_stream,
                responses_sse_stream,
            )
            if dialect == "anthropic":
                gen = bedrock_anthropic_sse_stream(
                    request, chunk_iter, on_usage=_stream_on_usage,
                    tokenizer_hook=tokenizer_hook)
            else:
                gen = responses_sse_stream(
                    request, chunk_iter, on_usage=_stream_on_usage,
                    tokenizer_hook=tokenizer_hook)
            return StreamingResponse(_log_stream(gen), status_code=status,
                                     media_type="text/event-stream", headers=response_headers)
        status, body, _h, usage = await invoke(base)
        if usage and (usage.input_tokens + usage.output_tokens) > 0:
            await on_usage(usage)
        await _log_nonstream(status, body)
        try:
            content = json.loads(body)
        except (ValueError, TypeError):
            content = {"error": {"type": "api_error", "message": "invalid provider response"}}
        return JSONResponse(status_code=status, content=content, headers=response_headers)

    if is_stream:
        stitcher = _anthropic_stream if dialect == "anthropic" else _responses_stream
        gen = stitcher(
            invoke_stream=invoke_stream,
            base_body=loop_req_data,
            mcp_client=mcp_client,
            request=request,
            on_usage=on_usage,
            max_iterations=max_iterations,
            deadline=deadline,
            default_max_results=default_max_results,
            max_result_chars=max_result_chars,
            max_searches_per_turn=max_searches_per_turn,
            cache_results=cache_results,
            result_text_chars=result_text_chars,
            **dialect_kw,
        )
        return StreamingResponse(
            _log_stream(gen), status_code=200,
            media_type="text/event-stream", headers=response_headers,
        )

    loop = _anthropic_nonstream if dialect == "anthropic" else _responses_nonstream
    resp = await loop(
        invoke=invoke,
        base_body=loop_req_data,
        mcp_client=mcp_client,
        on_usage=on_usage,
        max_iterations=max_iterations,
        deadline=deadline,
        default_max_results=default_max_results,
        max_result_chars=max_result_chars,
        max_searches_per_turn=max_searches_per_turn,
        cache_results=cache_results,
        result_text_chars=result_text_chars,
        **dialect_kw,
    )
    # Record the final body the loop assembled. `resp.body` is read here because the loop
    # merged several turns into it — no single provider response equals what the client
    # actually receives, and the bytes the client got are the record of truth.
    await _log_nonstream(resp.status_code, bytes(resp.body or b""))
    return resp
