# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""Codex CLI onboarding — write ``~/.codex/config.toml``, then launch with a fresh VK.

Codex CLI speaks the OpenAI **Responses** wire, which the gateway serves at
``POST /v1/responses``. Pointing Codex at it needs a provider block in Codex's own
``config.toml`` plus a Virtual Key in an environment variable. Two commands:

  ``gateway-cli codex setup``  — splice the provider block into ``config.toml``
  ``gateway-cli codex run``    — mint/reuse a VK, put it in the child env, exec codex

Why a text splice and not a TOML writer
---------------------------------------
This package has NO TOML-writing dependency, and cannot grow one: the build is
PyInstaller-packaged for an isolated network, so every dependency is a release-train
decision. Python 3.11's stdlib ``tomllib`` is read-only. So :func:`render_config`
splices text deterministically and ``tomllib`` then *validates* the result — we never
hand a file to Codex that we have not parsed back and checked key by key.

The splice obeys TOML's one structural rule that bites naive appenders: **top-level
keys must appear before the first ``[table]`` header.** Appending ``model = "..."`` to
the end of a file that already has tables silently makes it a key *of the last table*.
So ``model``/``model_provider`` go at the end of the preamble and the
``[model_providers.gateway]`` table goes at the end of the file.

Our ``model`` must be a GATEWAY ALIAS
-------------------------------------
The upstream AWS sample says the gateway ignores the client-sent ``model`` and always
routes to the profile's ``default_model``. Ours does not: ``/v1/responses`` honours the
requested model when it resolves to an ACTIVE alias on the Responses path (see
``gateway-proxy/src/app/routers/openai_compat.py``). That is a feature — it is how one
Codex install reaches Sol, Terra and Luna — but it means the value here must be one of
OUR aliases (:data:`KNOWN_CODEX_MODELS`), not an upstream OpenAI name. An unknown name
is NOT an error: the gateway logs
``responses_requested_model_unresolved_using_default`` and quietly falls back to the
profile default, so a copy-pasted ``gpt-5.6-sol`` looks like it worked while billing
and serving Terra. A ``provider_model_id`` fares worse still and is hard-rejected here
— see :func:`check_model`.

Two tracks, two rosters
-----------------------
:data:`MANTLE_CODEX_MODELS` (migration 0028) and :data:`RUNTIME_CODEX_MODELS` (0033) are
the same three models over two different endpoints, with different auth, different
pricing rows, and — only on the runtime one — AWS's own copy of every request and
response body. The alias is the ONLY discriminator: ``identify_client`` derives the
client from Codex's ``originator``/UA header, which is identical either way, and this
file writes a custom ``[model_providers.gateway]`` table, so Codex's own
``amazon-bedrock`` vs ``amazon-bedrock-runtime`` provider choice never reaches the
gateway at all. Which means the string in ``model`` is what decides the audit posture,
and typing it wrong cannot be caught downstream.

VK refresh — what is and is not possible
----------------------------------------
Claude Code re-runs ``apiKeyHelper`` whenever its key expires. **Codex has no such
hook**: it reads ``env_key`` from the environment when the process starts. So a VK
cannot be rotated inside a running Codex session, and any claim otherwise would be
false. What ``codex run`` does instead is guarantee every session STARTS with a VK
that has plenty of life left (it refreshes below
:data:`VK_REFRESH_THRESHOLD_SECONDS`), which turns "log in again" into "restart
codex". VKs from the OIDC exchange live ``OIDC_VK_TTL_HOURS`` (default 1h — kept
short so Cognito group changes propagate), so a session longer than that ends in a
401 and needs a restart; an operator who wants longer test sessions raises that
setting on admin-api.

The VK itself is read from and written to the SAME cache
``api-key-helper`` uses (``gateway_cli_oidc.oidc_client``), so Claude Code and Codex
share one key and one identity — which is also why they share one budget.
"""

from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from pathlib import Path

import click
import structlog

from cli.site_defaults import resolve_config
from cli.utils.backup import backup_config

log = structlog.get_logger(component="cli-codex")

# ---------------------------------------------------------------------------
# What we write
# ---------------------------------------------------------------------------

#: The Codex provider table we own: ``[model_providers.gateway]``.
PROVIDER_KEY = "gateway"

#: ``name`` is NOT cosmetic — Codex CLI 0.147.0 exits with a config error when a
#: provider table has no ``name``, so the block must always carry one.
PROVIDER_NAME = "LLM Gateway"

#: Codex reads the VK from this environment variable (``env_key`` in the table).
#: ``codex run`` reads the name back OUT of the file rather than assuming it, so a
#: hand-edited ``env_key`` keeps working.
DEFAULT_ENV_KEY = "GATEWAY_VK"

#: OpenAI Responses wire. ``chat`` would target /v1/chat/completions, which this
#: gateway does not serve for the Mantle OpenAI provider.
WIRE_API = "responses"

#: Balanced tier, matching migration 0028's ``routing_profiles.default_model`` for
#: the codex client. Sol (agentic) and Luna (high volume) are the other two on that track.
#: Deliberately a MANTLE alias: a user who passes no ``--model`` must not be moved onto the
#: runtime track, whose rows arrive in a later migration and under an operational go/no-go.
DEFAULT_CODEX_MODEL = "codex-gpt-5.6-terra"

#: Aliases migration 0028 seeds on the **mantle** track: ``bedrock-mantle`` endpoint,
#: bearer auth, no AWS-side invocation-log record at all (``bedrock-mantle`` traffic is
#: not captured by ``put-model-invocation-logging-configuration``; that is why the gateway
#: keeps its own body log). ``provider_model_id`` is the bare ``openai.gpt-5.6-*``.
MANTLE_CODEX_MODELS = (
    "codex-gpt-5.6-sol",
    "codex-gpt-5.6-terra",
    "codex-gpt-5.6-luna",
)

#: Aliases migration 0033 seeds on the **bedrock-runtime** track: SigV4 (pod IRSA), an
#: AWS-side ``ModelInvocationLog`` record per call, and its own pricing rows.
#: ``provider_model_id`` is CRIS-prefixed (``global.openai.gpt-5.6-*``), which is what
#: :func:`alias_for_provider_model_id` keys the two tracks apart on.
#:
#: THAT RECORD DOES **NOT** CONTAIN THE BODIES FOR CODEX. MEASURED 2026-09-02 (dev
#: <account-id>, us-east-2, native invocation logging enabled, n=9): the endpoint appends a
#: spurious ``internal_server_error`` frame to every successful STREAM, AWS's logging layer
#: reads that as a failure, and the resulting record has ``errorCode:
#: internal_server_error`` with no ``input``/``output`` sections at all. Non-streamed calls
#: to the same model in the same minute log both bodies in full — but codex only ever
#: streams. So the audit trail for codex bodies is the GATEWAY's own record
#: (``gateway-proxy/src/app/schemas/body_log.py`` -> Firehose/S3) on BOTH tracks, and the
#: runtime track's remaining advantages over mantle are the SigV4/IRSA auth posture, the
#: per-call AWS-side metadata record, and separate pricing — not bodies. Do not restate the
#: bodies claim anywhere without re-measuring it; see
#: ``providers/runtime_openai_adapter.py::invoke_stream``.
#:
#: Listed here BEFORE 0033 is applied anywhere, deliberately. This CLI is
#: PyInstaller-packaged and shipped through an installer to user machines, so its roster
#: can only change on a release train, while the alias rows change with a migration —
#: the constant must therefore lead the database, never trail it. The cost of leading is
#: that during the window between this build and 0033, ``--model codex-rt-…`` is a known
#: name that the gateway cannot yet resolve, so it is served by the codex profile's
#: default (mantle Terra) with only an INFO line. Nothing here can detect that — the
#: roster is a build-time constant and resolvability is a database fact — so the
#: verification advice stays where it already is: ``gateway-cli codex status`` against
#: the admin console.
RUNTIME_CODEX_MODELS = (
    "codex-rt-gpt-5.6-sol",
    "codex-rt-gpt-5.6-terra",
    "codex-rt-gpt-5.6-luna",
)

#: Every alias this build knows, across both tracks. Used only to WARN — never to
#: reject — because the roster is a database row, and a new alias must not need a CLI
#: rebuild. It IS used affirmatively for one thing: filtering the alias suggested in a
#: rejection, so the CLI can never tell a user to type a name it does not ship.
KNOWN_CODEX_MODELS = (*MANTLE_CODEX_MODELS, *RUNTIME_CODEX_MODELS)

#: Refresh the VK when it has less than this left. Deliberately far larger than
#: api-key-helper's 300s: that helper is re-invoked per request, so a nearly-expired
#: key costs it one extra round trip, while a Codex session inherits the key it
#: started with for its whole lifetime. 30 minutes is api-key-helper's own daemon
#: threshold — the same reasoning (a long-lived consumer) applies here.
VK_REFRESH_THRESHOLD_SECONDS = 1800

#: Name Codex ships as. Overridable for installs that are not on PATH.
CODEX_BIN_ENV = "GATEWAY_CLI_CODEX_BIN"

#: Backup namespace (see cli.utils.backup). Distinct from ``claude-code`` so a
#: snapshot's tool of origin stays readable in the shared backups dir.
BACKUP_TOOL = "codex"


class CodexStepError(Exception):
    """A codex step failed in a way the user must fix. Message is user-facing."""


# ---------------------------------------------------------------------------
# Config path
# ---------------------------------------------------------------------------

def codex_home() -> Path:
    """Codex's own config directory.

    ``CODEX_HOME`` is Codex CLI's variable, not ours — we honour it so a user who
    relocated their Codex config is not silently configured in the wrong place.
    """
    override = os.environ.get("CODEX_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".codex"


def codex_config_path() -> Path:
    """Path to ``config.toml`` inside :func:`codex_home`."""
    return codex_home() / "config.toml"


#: Encoding used to READ config.toml. ``utf-8-sig`` strips a leading BOM when there is
#: one and is byte-for-byte identical to ``utf-8`` when there is not, so this only ever
#: helps a file somebody else saved — we always WRITE bom-less utf-8.
#:
#: This matters far more than it looks on the Windows box this ships to. PowerShell 5.1's
#: ``Set-Content -Encoding UTF8`` and pre-2019 Notepad both emit a UTF-8 BOM, and a BOM
#: broke `setup` twice over: ``tomllib`` rejects it at line 1 column 1, AND ``_top_key_re``
#: cannot see the user's own ``model =`` line behind it (the BOM is not whitespace —
#: ``re.match(r"\s", "﻿")`` is None), so the splice emitted a SECOND top-level
#: ``model`` key and then blamed a ``model_providers.gateway`` declaration the file did
#: not contain. The user's editor shows nothing wrong on line 1.
CONFIG_READ_ENCODING = "utf-8-sig"


def not_utf8_message(path: Path, exc: UnicodeDecodeError) -> str:
    """The "re-save it as UTF-8" remedy, in one place for every command that needs it.

    ``setup`` owned this text alone, so ``revert`` and ``status`` printed the bare codec
    error — and ``verify --post-teardown``, which sends the user to ``codex revert``, sent
    them at the command that dies with it. One decode failure, one answer, wherever it
    surfaces.

    The remedy has to be per-PowerShell-version, because no single one-liner is valid on
    both:

    * ``-Encoding Default`` (ANSI, i.e. CP949 on the ko-KR boxes where this actually
      happens) exists only in 5.1; 7 dropped it. 7 takes a numeric code page instead
      (6.2+), so ``949`` is the explicit equivalent.
    * ``utf8NoBOM`` exists only in 6+; 5.1's ``utf8`` writes a BOM. That is survivable
      *now* — ``setup`` rewrites a BOM'd file instead of calling it up to date, and every
      read here is ``utf-8-sig`` — but it is still worth not asking for.
    * Both forms read the file into a variable FIRST. ``Get-Content x | Set-Content x``
      truncates x: Set-Content opens the path for writing while Get-Content is still
      streaming it lazily.
    """
    return (
        f"{path} is not UTF-8 ({exc.reason} at byte {exc.start}). Re-save it as UTF-8 and "
        "re-run.\n"
        "  Notepad:  File > Save As > Encoding: UTF-8   (a UTF-8 byte-order mark is fine "
        "— setup removes it)\n"
        f"  PS 5.1:   $p='{path}'; $t=Get-Content -Raw -Encoding Default $p; "
        "Set-Content -Encoding utf8 $p -Value $t\n"
        f"  PS 7:     $p='{path}'; $t=Get-Content -Raw -Encoding 949 $p; "
        "Set-Content -Encoding utf8NoBOM $p -Value $t\n"
        "  ($PSVersionTable.PSVersion.Major says which. Read into $t first — piping "
        "Get-Content into Set-Content on the same path truncates it.)\n"
        "Moving the file aside also works, but loses your Codex settings."
    )


# ---------------------------------------------------------------------------
# TOML text splice
# ---------------------------------------------------------------------------

#: A table header line: ``[table]`` or ``[[array.of.tables]]``, trailing comment ok.
_HEADER_RE = re.compile(r"^\s*\[\[?\s*(?P<key>[^\[\]]+?)\s*\]\]?\s*(?:#.*)?$")


def _header_key(line: str) -> str | None:
    """Return a table header's normalised dotted key, or None if not a header.

    Normalisation drops quoting and inner spaces so ``[ model_providers."gateway" ]``
    and ``[model_providers.gateway]`` compare equal. A quoted key that *contains* a
    dot would normalise wrong, but the keys involved here are bare identifiers, and
    anything we get wrong is caught by the post-render validation rather than written.
    """
    match = _HEADER_RE.match(line)
    if match is None:
        return None
    return match.group("key").replace('"', "").replace("'", "").replace(" ", "")


def _top_key_re(name: str) -> re.Pattern[str]:
    """Match an assignment to top-level key ``name`` (bare or quoted)."""
    return re.compile(rf"""^\s*(?:{re.escape(name)}|"{re.escape(name)}"|'{re.escape(name)}')\s*=""")


def _multiline_string_lines(text: str) -> list[bool]:
    """For each physical line, does it BEGIN inside a TOML multi-line string?

    Every scanner below is line-based, and a line-based scanner cannot tell our own
    ``[model_providers.gateway]`` from the same characters sitting inside somebody's
    ``notes = \"\"\" … \"\"\"`` block. That confusion is not cosmetic:

    * :func:`config_has_gateway_block` reported residue for a config we had never
      touched — permanently, since no ``codex revert`` can remove text that is not ours.
      ``clear`` then held a snapshot back forever and ``verify --post-teardown`` failed
      with a remedy that could not work.
    * :func:`strip_gateway_config` latched ``in_ours`` on the quoted header and deleted
      every line to the next real table header — including the string's own closing
      delimiter, so the result did not parse. Both writers re-parse before writing, so
      nothing reached disk, but ``revert`` refused for good and blamed the user's file.

    A full TOML tokenizer is out of scope (and this package has no TOML *writer* to lean
    on), so this tracks exactly the states that can hide a line from us: comments,
    single-line basic/literal strings — where the delimiters of a multi-line string may
    legitimately appear, as in ``x = '\"\"\"'`` — and the two multi-line forms. Basic
    strings honour ``\\`` escapes; literal strings, by TOML's definition, have none.

    A single-line string cannot cross a newline, so one left open at end of line means
    the document is malformed; it is closed here rather than allowed to swallow the rest
    of the file, because a wrong answer on one line beats a wrong answer on all of them.
    """
    inside: list[bool] = []
    state: str | None = None  # None | '"' | "'" | '\"\"\"' | "'''"
    for line in text.splitlines():
        inside.append(state in ('"""', "'''"))
        index, end = 0, len(line)
        while index < end:
            char = line[index]
            if state is None:
                if char == "#":
                    break  # comment runs to end of line
                if line.startswith('"""', index) or line.startswith("'''", index):
                    state, index = line[index : index + 3], index + 3
                    continue
                if char in "\"'":
                    state = char
                index += 1
            elif state in ('"', '"""') and char == "\\":
                index += 2  # escape; at end of line in a """ string this is a continuation
            elif state in ('"""', "'''"):
                if line.startswith(state, index):
                    state, index = None, index + 3
                    continue
                index += 1
            else:  # single-line basic or literal string
                if char == state:
                    state = None
                index += 1
        if state in ('"', "'"):
            state = None
    return inside


def _toml_basic_string(value: str) -> str:
    """Encode ``value`` as a TOML basic string.

    Values reach us from flags and env vars, so a stray quote or backslash (a Windows
    path in ``base_url``, say) must escape rather than produce a file Codex cannot
    parse. Control characters are escaped too — TOML forbids them raw.
    """
    out = value.replace("\\", "\\\\").replace('"', '\\"')
    out = out.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
    return f'"{out}"'


def _dotted_our_key_re(provider_key: str) -> re.Pattern[str]:
    """Match the dotted-key spelling of our provider table.

    The key must END at ``provider_key``, so require what can legally follow it: a
    further dot (``model_providers.gateway.base_url = …``) or the assignment itself
    (``model_providers.gateway = {…}``).

    NOT ``\\b``. A word boundary fires between ``gateway`` and ``-``, so
    ``model_providers.gateway-prod.base_url`` looked like our table and the line was
    deleted — an unrelated provider silently removed from a file we only meant to
    splice, while the result stayed valid TOML and validate_config (which re-checks
    only our own five keys) passed. The header path in :func:`strip_gateway_config`
    already gets this right: it preserves ``[model_providers.gateway-prod]``. The two
    branches disagreeing is what proves the deletion was a bug and not a policy.

    Shared by the stripper and :func:`config_has_gateway_block` on purpose: the
    detector answering "is our block still there?" must use the same rule as the
    remover, or teardown ends up deleting a recovery snapshot for a block that
    ``revert`` would not in fact have removed.
    """
    return re.compile(
        rf"^\s*(?:model_providers|\"model_providers\")\s*\.\s*{re.escape(provider_key)}\s*(?:\.|=)"
    )


def strip_gateway_config(text: str, *, provider_key: str = PROVIDER_KEY) -> str:
    """Remove everything :func:`render_config` writes, leaving the rest untouched.

    Three things go:

    * the ``[model_providers.<key>]`` table and any of its sub-tables (a header is
      "ours" while it is that key or a child of it),
    * top-level ``model`` / ``model_provider`` assignments — **only in the preamble**,
      because the same key inside ``[profiles.foo]`` belongs to that profile and
      deleting it would silently reconfigure an unrelated Codex profile, and
    * the ``model_providers.<key>.…`` dotted-key form of our table.

    Lines inside a multi-line string are text, not structure, so they are copied through
    untouched and cannot flip any of the state below — see
    :func:`_multiline_string_lines` for what went wrong when they could.

    Used by ``render`` (making a re-run idempotent instead of appending a second
    block) and by ``revert``.
    """
    ours = f"model_providers.{provider_key}"
    model_re = _top_key_re("model")
    provider_re = _top_key_re("model_provider")
    dotted_re = _dotted_our_key_re(provider_key)

    kept: list[str] = []
    in_preamble = True
    in_ours = False
    lines = text.splitlines()
    for line, in_string in zip(lines, _multiline_string_lines(text)):
        if in_string:
            # Inside our own table the string is part of the body the user put there and
            # goes with it; anywhere else it is somebody's prose and is not ours to read.
            if not in_ours:
                kept.append(line)
            continue
        header = _header_key(line)
        if header is not None:
            in_preamble = False
            in_ours = header == ours or header.startswith(f"{ours}.")
            if in_ours:
                continue
        elif in_ours:
            continue  # body line of our table
        elif in_preamble and (
            model_re.match(line) or provider_re.match(line) or dotted_re.match(line)
        ):
            continue
        kept.append(line)

    # Collapse the blank-line run our removal may have left behind, so repeated
    # setup/revert cycles do not grow the file by a line each time.
    out: list[str] = []
    for line in kept:
        if not line.strip() and out and not out[-1].strip():
            continue
        out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return ("\n".join(out) + "\n") if out else ""


def render_config(
    existing: str,
    *,
    model: str,
    base_url: str,
    provider_key: str = PROVIDER_KEY,
    provider_name: str = PROVIDER_NAME,
    env_key: str = DEFAULT_ENV_KEY,
) -> str:
    """Return ``existing`` with our provider block spliced in. Idempotent.

    Strips any previous block first, so running setup twice yields the same file
    rather than two conflicting ``[model_providers.gateway]`` tables (which is a
    hard TOML parse error, i.e. a Codex that refuses to start).
    """
    if not re.fullmatch(r"[A-Za-z0-9_-]+", provider_key):
        raise CodexStepError(
            f"provider key {provider_key!r} is not a bare TOML key "
            "(letters, digits, '_' and '-' only)."
        )

    stripped = strip_gateway_config(existing, provider_key=provider_key)
    lines = stripped.splitlines()

    # TOML: top-level keys must precede the first table header, so ours go at the
    # end of the preamble — after any leading comments the user wrote, which we keep.
    # A ``[table]``-looking line inside a multi-line string is not a header: splitting
    # the preamble there would insert our two keys INTO that string, which swallows them
    # (validate_config then fails on a `model` it cannot find, blaming the splice).
    in_string = _multiline_string_lines(stripped)
    first_header = next(
        (
            i
            for i, line in enumerate(lines)
            if not in_string[i] and _header_key(line) is not None
        ),
        len(lines),
    )
    preamble = lines[:first_header]
    rest = lines[first_header:]
    while preamble and not preamble[-1].strip():
        preamble.pop()

    out = [
        *preamble,
        f"model = {_toml_basic_string(model)}",
        f"model_provider = {_toml_basic_string(provider_key)}",
    ]
    if rest:
        out += ["", *rest]
    while out and not out[-1].strip():
        out.pop()
    out += [
        "",
        f"[model_providers.{provider_key}]",
        f"name = {_toml_basic_string(provider_name)}",
        f"base_url = {_toml_basic_string(base_url)}",
        f"wire_api = {_toml_basic_string(WIRE_API)}",
        f"env_key = {_toml_basic_string(env_key)}",
    ]
    return "\n".join(out) + "\n"


def validate_config(
    text: str,
    *,
    model: str,
    base_url: str,
    provider_key: str = PROVIDER_KEY,
    env_key: str = DEFAULT_ENV_KEY,
) -> dict:
    """Parse ``text`` and assert it says what we meant it to say.

    The splice is string surgery on a file we do not own, so "it looks right" is not
    enough: a stray dotted key or an unbalanced quote elsewhere in the user's file can
    make our block land somewhere else entirely. Parsing back and checking each value
    is what makes the write safe without a TOML writer.
    """
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise CodexStepError(
            f"the spliced config is not valid TOML ({exc}). Nothing was written. "
            "This usually means the existing file already declares "
            f"'model_providers.{provider_key}' in a form we could not remove "
            "(e.g. a dotted key inside another table) — remove it by hand and re-run."
        ) from exc

    if data.get("model") != model:
        raise CodexStepError(f"post-write check failed: top-level model is {data.get('model')!r}")
    if data.get("model_provider") != provider_key:
        raise CodexStepError(
            f"post-write check failed: model_provider is {data.get('model_provider')!r}"
        )
    providers = data.get("model_providers")
    provider = providers.get(provider_key) if isinstance(providers, dict) else None
    if not isinstance(provider, dict):
        raise CodexStepError(
            f"post-write check failed: [model_providers.{provider_key}] is missing"
        )
    for key, expected in (
        ("base_url", base_url),
        ("wire_api", WIRE_API),
        ("env_key", env_key),
        ("name", PROVIDER_NAME),
    ):
        if provider.get(key) != expected:
            raise CodexStepError(
                f"post-write check failed: {key} is {provider.get(key)!r}, expected {expected!r}"
            )
    return data


# ---------------------------------------------------------------------------
# Value resolution
# ---------------------------------------------------------------------------

def default_base_url() -> str:
    """Codex ``base_url`` derived from the baked gateway (proxy) URL.

    Codex appends ``/responses`` to ``base_url``, and the gateway serves
    ``/v1/responses`` — so the ``/v1`` suffix belongs here. Without it every request
    is a 404 against the proxy root, which reads to the user as "the gateway is down".
    """
    gateway_url = (resolve_config().gateway_url or "").strip().rstrip("/")
    if not gateway_url:
        raise CodexStepError(
            "no gateway URL available — normally baked into the build. Pass --base-url "
            "(ending in /v1), or set GATEWAY_CLI_GATEWAY_PROXY_URL."
        )
    return gateway_url if gateway_url.endswith("/v1") else f"{gateway_url}/v1"


def check_base_url(base_url: str) -> None:
    """Fail on a base_url Codex cannot use; the /v1 suffix is the usual mistake."""
    from cli import validators

    try:
        validators.validate_url(base_url)
    except validators.ValidationError as exc:
        raise CodexStepError(f"--base-url: {exc}") from exc
    if not base_url.rstrip("/").endswith("/v1"):
        raise CodexStepError(
            f"--base-url must end with /v1 (got {base_url!r}). Codex appends "
            "'/responses' to it, and the gateway serves the Responses API at "
            "/v1/responses."
        )


#: Vendor segment of a Bedrock model id. Every id we can be handed is
#: ``[<geo>.]<vendor>.<family>`` — bare ``openai.gpt-5.6-terra`` upstream of the mantle
#: track, CRIS ``global.openai.gpt-5.6-sol`` upstream of the runtime track (where CRIS is
#: mandatory: In-Region inference does not exist for these models on ``bedrock-runtime``,
#: so ``global.``/``us.`` is the ONLY shape that track ever sends).
_PROVIDER_ID_VENDORS = frozenset({"openai", "anthropic"})


def _split_provider_model_id(value: str) -> tuple[str, str] | None:
    """``(geo_prefix, model_family)`` when ``value`` is a Bedrock model id, else None.

    The test is grammatical, not a prefix list: a vendor token in dotted segment 0 or 1
    with at least one segment after it. Segment 0 is a bare id, segment 1 is the CRIS
    form, and that is the whole grammar — anything deeper is not an id AWS issues.

    **Why not the enumerated ``^(global|us|eu|apac|in)\\.`` prefix alternation.** AWS adds
    geographies (``global.`` itself post-dates ``us.``), and a prefix this build has never
    heard of would fall back to a WARNING — i.e. the exact silent-substitution mode the
    reject exists to close, re-opening itself the day AWS ships a region. Reading the
    vendor token instead makes the rule prefix-agnostic. It also subsumes the old
    ``".anthropic." in lowered`` substring test for every real id shape, and it picks up a
    whole inference-profile ARN pasted from the console
    (``arn:…:inference-profile/global.openai.gpt-5.6-sol``): the ARN lands in segment 0
    and is read as a geo prefix, which is wrong as a label and right as an answer — that
    paste is a provider id and must be rejected.

    The cost, recorded on purpose: an operator who names a gateway alias
    ``something.openai.whatever`` gets it refused by this CLI. That is a false rejection
    the user can see and work around (hand-edit ``config.toml``, or rename the alias); the
    inverse — a false accept — is a wrong price row and a missing audit log that nobody
    sees. Our own aliases do carry a dot (the ``5.6`` version), but it splits them into
    ``codex-gpt-5`` / ``6-sol``, and neither segment is a vendor token — so no shipped
    roster entry can trip this. Pinned by a test, because "the roster survives its own
    validator" is not obvious from either side alone.
    """
    parts = value.strip().lower().split(".")
    for index in (0, 1):
        if len(parts) > index + 1 and parts[index] in _PROVIDER_ID_VENDORS:
            return ".".join(parts[:index]), ".".join(parts[index + 1 :])
    return None


def alias_for_provider_model_id(value: str) -> str | None:
    """The alias that CARRIES ``value`` as its ``provider_model_id``, when we ship one.

    Derived, not tabulated, from the one convention both seeding migrations follow: the
    mantle alias for ``openai.<family>`` is ``codex-<family>`` (0028) and the runtime
    alias for ``<geo>.openai.<family>`` is ``codex-rt-<family>`` (0033). **The geo prefix
    is the discriminator**, because CRIS is mandatory on ``bedrock-runtime`` and absent on
    mantle — so its presence decides which TRACK the id belongs to, and therefore which
    price row, which endpoint, which auth, and whether an AWS-side invocation-log record
    exists at all. (That record is metadata-only for codex; bodies live in the gateway's
    own log on both tracks — see :data:`RUNTIME_CODEX_MODELS`.) Suggesting the wrong side
    of that line would answer a user's mistake with a different one.

    **The candidate is filtered through :data:`KNOWN_CODEX_MODELS`, so this can only ever
    name an alias this build ships.** That is what keeps a derived string from becoming a
    fabricated one: if a migration ever renames its aliases, or the id names a model we
    have no alias for at all (any ``anthropic.…`` — those are Claude Code's, reached over
    ``/v1/messages``, never Codex's), the derivation misses, this returns None, and
    :func:`check_model` falls back to listing the roster rather than telling the user to
    type a name that does not exist.
    """
    parts = _split_provider_model_id(value)
    if parts is None:
        return None
    geo, family = parts
    candidate = f"codex-rt-{family}" if geo else f"codex-{family}"
    return candidate if candidate in KNOWN_CODEX_MODELS else None


def check_model(model: str) -> list[str]:
    """Validate a codex model value. Returns advisory warnings (never fatal).

    Two shapes are hard-rejected: a blank value, and a provider model id pasted where an
    alias belongs — bare (``openai.gpt-5.6-terra``), CRIS-prefixed
    (``global.openai.gpt-5.6-sol``, ``us.anthropic.…``), or as a whole
    ``inference-profile`` ARN.

    Note the provider-id rejection is NOT "it cannot work" — the gateway looks a
    ``model`` up by alias first and then falls back to ``provider_model_id`` within
    the same provider (``router_service.resolve_mantle_model``), so ``openai.…`` resolves.
    It is rejected because it
    resolves to the WRONG THING quietly: several aliases may share one
    ``provider_model_id`` and the fallback takes ``limit(1)``, so which alias (and
    therefore which price row and which per-alias permission) you actually get is
    arbitrary. A user who writes ``openai.gpt-5.6-sol`` because they saw it in a Bedrock
    console also silently buys Sol's rate — about 2x Terra: 2.0x on input, cache-write and
    cache-read, 1.67x (5/3) on output, the same ratio per 1K or per 1M tokens.

    **A CRIS-prefixed id used to be a WARNING only, and that was the worst case of the
    set.** ``global.openai.gpt-5.6-sol`` is simultaneously (a) the runtime track's
    mandatory upstream id, so it is a real string that reads as authoritative, (b) exactly
    what the Bedrock console and ``list-inference-profiles`` print, so it is the value a
    user is most likely to copy, and (c) never a name this gateway answers to. Warned and
    accepted, it reached the resolver, which finds no alias of that name and — for as long
    as the runtime aliases are unseeded — no ``provider_model_id`` either, so
    ``openai_compat.py`` serves the codex profile's MANTLE default at HTTP 200: mantle
    price, mantle SigV4-less bearer auth, and no AWS-side invocation-log record at all,
    for a user who explicitly asked for the runtime track. Bodies are audited either way
    (the gateway's own ``body_log`` covers both tracks), but the price and the AWS-side
    per-call record are not. Once those rows exist the pmid fallback starts matching instead, and
    then its ``limit(1)`` picks which alias's price row and per-alias permissions apply.
    Both outcomes are a 200 on a track the user did not choose, which is why this is a
    raise and not a warning: a warning does not stop ``setup`` writing the file, and the
    next ``codex run`` then succeeds.

    This is the client-side half of "one ``provider_model_id`` may not span two
    providers". The server-side half belongs at registration time in admin-api (refuse a
    pmid already registered under another provider), and this half is not redundant with
    it: the id a user pastes here need not be registered anywhere at all.

    An unrecognised-but-plausible alias is only a WARNING, because the alias table is
    a database row an operator can add without rebuilding this CLI. That case really
    does fail to resolve, and the gateway then serves the profile default instead of
    erroring — staying silent about it would hide the substitution.
    """
    alias = model.strip()
    if not alias:
        raise CodexStepError("--model is empty")
    provider_id = _split_provider_model_id(alias)
    if provider_id is not None:
        geo, _family = provider_id
        # The prefix is deliberately NOT quoted back: for an ARN paste `geo` is the whole
        # ARN head, and a sentence built around it reads as nonsense on the one input
        # where the remedy matters most. Kept vendor-neutral for the same reason — an
        # `eu.anthropic.…` paste is a CRIS id too, and naming the Codex runtime track at
        # it would be a confident non-sequitur. The track belongs on the remedy, which is
        # the only part that knows which alias it is pointing at.
        cris = (
            " Its region/geo prefix makes it a CRIS inference-profile id — an UPSTREAM id "
            "(which is why the Bedrock console prints it), never an alias this gateway "
            "resolves."
            if geo
            else ""
        )
        suggestion = alias_for_provider_model_id(alias)
        if suggestion:
            # Say the track out loud when the answer is a runtime alias. The user pasted
            # the runtime track's own upstream id, so they are already thinking about that
            # track; confirming which side of the line the alias sits on is what stops the
            # remedy from looking like a downgrade to "some other model".
            track = (
                " on the bedrock-runtime track"
                if suggestion in RUNTIME_CODEX_MODELS
                else ""
            )
            remedy = f" Use --model {suggestion!r}, the alias that carries it{track}."
        else:
            remedy = f" Use one of: {', '.join(KNOWN_CODEX_MODELS)}."
        raise CodexStepError(
            f"--model {model!r} is a provider model id (what the gateway sends UPSTREAM), "
            f"not a gateway alias.{cris}{remedy}"
        )
    warnings: list[str] = []
    if alias not in KNOWN_CODEX_MODELS:
        warnings.append(
            f"'{alias}' is not one of the aliases this build knows "
            f"({', '.join(KNOWN_CODEX_MODELS)}). If the gateway cannot resolve it, the "
            "request is served by the codex profile's default model instead of failing "
            "— check `gateway-cli codex status` output against the admin console."
        )
    return warnings


def _load_config(path: Path | None = None) -> dict:
    """Parse the Codex config, or ``{}`` if it is missing/unreadable/invalid.

    Every caller here treats "cannot read it" the same as "not configured", so the
    error is swallowed deliberately; the commands that need to *report* the parse
    failure (``status``) parse it themselves.
    """
    config_path = path or codex_config_path()
    try:
        return tomllib.loads(config_path.read_text(encoding=CONFIG_READ_ENCODING))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return {}


def routes_to_gateway(data: dict, *, provider_key: str = PROVIDER_KEY) -> bool:
    """True when this config actually sends Codex's traffic to the gateway.

    Both conditions matter: ``model_provider`` must SELECT our table (otherwise our
    block is inert dead config), and that table must be on the Responses wire with a
    base_url (otherwise it is half-written).
    """
    if data.get("model_provider") != provider_key:
        return False
    providers = data.get("model_providers")
    provider = providers.get(provider_key) if isinstance(providers, dict) else None
    if not isinstance(provider, dict):
        return False
    return provider.get("wire_api") == WIRE_API and bool(provider.get("base_url"))


def config_has_gateway_block(
    path: Path | None = None, *, provider_key: str = PROVIDER_KEY
) -> bool:
    """True when Codex's config file still contains the block ``revert`` would remove.

    Answers a narrower question than :func:`routes_to_gateway`, for a different
    caller. :mod:`cli.teardown` needs to know whether our text is still *in the user's
    file* before it deletes the pre-setup snapshot of that file — not whether the block
    is currently selected. An inert block (ours present, ``model_provider`` pointing
    elsewhere) is still our residue and still what ``codex revert`` cleans up.

    Textual, and tolerant of a file that does not parse:

    * ``tomllib`` refuses the whole document over one bad line, and a config Codex
      itself chokes on is exactly when the user most needs the snapshot kept — so a
      parse is the wrong instrument here.
    * a decode failure falls back to ``errors="replace"``. Our markers
      (``[model_providers.gateway]``, ``model_providers.gateway.…``) are pure ASCII, so
      they survive intact; only the CP949 comment or path that broke the decode turns
      into replacement characters. Detection stays exact where it matters.

    Textual does NOT mean naive: lines inside a multi-line string are skipped
    (:func:`_multiline_string_lines`), because a False here is recoverable and a True is
    not — nothing the user can run removes a header that only exists inside their own
    prose, so a false positive is residue reported forever.

    Only the provider table counts, in either spelling. The top-level ``model`` /
    ``model_provider`` keys ``strip_gateway_config`` also removes are deliberately NOT
    treated as evidence: a user who set their own ``model`` before ever meeting this
    tool would otherwise look like leftover gateway state forever.

    **"absent" and "unreadable" are different answers.** Only a missing file means
    False. Anything else (ACL denial, a sharing violation while Codex holds the file, a
    OneDrive Files-On-Demand placeholder, ``CODEX_HOME`` pointing at a plain file)
    propagates, because the caller's safe default is not ours to pick: teardown treats
    "cannot tell" as pending and KEEPS the snapshot. Returning False here instead made
    ``codex_config_pending``'s ``except OSError: return True`` unreachable, so an
    unreadable config.toml got its snapshot swept — deleting the user's only pre-setup
    copy while our block was still in the file.
    """
    config_path = path or codex_config_path()
    try:
        text = config_path.read_text(encoding=CONFIG_READ_ENCODING)
    except UnicodeDecodeError:
        text = config_path.read_bytes().decode(CONFIG_READ_ENCODING, errors="replace")
    except FileNotFoundError:
        return False  # never written → nothing of ours to find

    ours = f"model_providers.{provider_key}"
    dotted_re = _dotted_our_key_re(provider_key)
    in_preamble = True
    for line, in_string in zip(text.splitlines(), _multiline_string_lines(text)):
        if in_string:
            continue  # prose that happens to quote our header is not our residue
        header = _header_key(line)
        if header is not None:
            if header == ours or header.startswith(f"{ours}."):
                return True
            in_preamble = False
        # Preamble-only, mirroring the stripper: the same dotted key under some other
        # table is that table's business, so revert leaves it — and a detector that
        # counted it would report residue no `codex revert` can ever clear.
        elif in_preamble and dotted_re.match(line):
            return True
    return False


def read_env_key(path: Path | None = None, *, provider_key: str = PROVIDER_KEY) -> str:
    """Return the ``env_key`` **our own** provider table declares.

    Scoped to our table on purpose, NOT to whatever ``model_provider`` currently
    selects. Following the selection instead leaks the gateway VK: on a config that
    still points at another provider, ``setup`` would copy THAT provider's env_key
    (say ``OPENAI_API_KEY``) into our block, and ``codex run`` would then export the
    gateway's Virtual Key under that name — into Codex's environment and into every
    tool and MCP server Codex spawns, and, if the selection is still foreign, straight
    to that third party's endpoint as its API key.

    Reading our own table still honours the case the flexibility was for: a user who
    hand-edited ``model_providers.gateway.env_key`` keeps their variable name across a
    re-run of ``setup``, because ``render_config`` writes back what we read here.
    """
    provider = _load_config(path).get("model_providers")
    provider = provider.get(provider_key) if isinstance(provider, dict) else None
    if isinstance(provider, dict):
        declared = provider.get("env_key")
        if isinstance(declared, str) and declared.strip():
            return declared.strip()
    return DEFAULT_ENV_KEY


# ---------------------------------------------------------------------------
# Custom target record (--config-path)
# ---------------------------------------------------------------------------
#: Basename prefix of the marker files that record a NON-default ``--config-path``.
#: Read by :func:`cli.teardown.codex_config_pending`, swept by the same conditional gate
#: as the snapshots themselves — see :data:`cli.teardown._CODEX_TARGET_PATTERN`.
CUSTOM_TARGET_PREFIX = "codex.custom-target"


def _target_record_dir() -> Path:
    """The backups dir — same override, same 0700 creation as the snapshots.

    Deliberately the same directory: the marker has to live and die with the snapshot it
    describes, and ``GATEWAY_CLI_BACKUP_DIR`` has to move both together or teardown's two
    halves would disagree about which run they are looking at.
    """
    from cli.utils.backup import _backup_dir  # noqa: PLC0415 — one owner of the path

    return _backup_dir()


def _paths_equal(left: Path, right: Path) -> bool:
    """Do these two paths name the same file?

    Compared after ``resolve()`` so ``~/.codex/../.codex/config.toml``, a relative
    ``./config.toml`` and a symlinked ``CODEX_HOME`` all collapse onto one target, and
    case-insensitively on Windows only — NTFS folds case, POSIX does not, and
    ``os.path.normcase`` is a no-op on POSIX so it cannot express the difference by
    itself. Getting this wrong is not cosmetic: a false "different" writes a marker for
    the default file (teardown then asks about a path it already covers, harmless) and a
    false "same" skips the marker for a real custom target (teardown goes blind again,
    which is the whole bug this record exists to close).
    """
    try:
        left_r, right_r = left.resolve(), right.resolve()
    except OSError:  # unresolvable (symlink loop, denied parent) — compare as given
        left_r, right_r = left, right
    if sys.platform == "win32":
        return str(left_r).lower() == str(right_r).lower()
    return left_r == right_r


def _is_default_target(path: Path) -> bool:
    """True when ``path`` is the file :func:`codex_config_path` already covers."""
    return _paths_equal(path, codex_config_path())


def record_custom_config_target(path: Path) -> Path | None:
    """Remember that we wrote our block to ``path``, when that is not the default file.

    Without this, teardown's "is our block still live?" question was asked about
    ``$CODEX_HOME/config.toml`` only, while ``codex setup --config-path D:\\work\\
    config.toml`` put the block somewhere else and named the snapshot identically
    (``codex.config.toml.<ts>.bak``). ``gateway-cli clear`` then swept that snapshot and
    reported the surface clean, with the gateway block still in the user's file and the
    only pre-setup copy of it gone. ``uninstall --clear-first`` compounded it: the
    ``codex revert`` that could have fixed the file goes away in the same breath.

    Returns the marker path, or None for the default target (nothing to remember), for a
    path already on record, and on any write failure — this is a safety net, never a
    reason to fail ``setup``.
    """
    if _is_default_target(path):
        return None
    try:
        from cli.utils.backup import _timestamp  # noqa: PLC0415 — one owner of the format

        # Re-running `setup --config-path <same file>` must not pile up markers: one file,
        # one record. Compared resolved, so ./config.toml and the absolute form are one
        # target. Inside the try because reading the records creates the dir, and on a
        # read-only or ACL-locked backups dir that raises — the same OSError the write
        # below can raise, and neither may fail `setup`. (For a first-ever write to a new
        # path nothing else has touched the dir yet, so this is where it surfaces.)
        if any(_paths_equal(known, path) for known in recorded_custom_config_targets()):
            return None
        record_dir = _target_record_dir()
        base = f"{CUSTOM_TARGET_PREFIX}.{_timestamp()}"
        marker = record_dir / f"{base}.origin"
        counter = 1
        while marker.exists():  # two custom targets in the same second
            marker = record_dir / f"{base}.{counter}.origin"
            counter += 1
        marker.write_text(f"{path}\n", encoding="utf-8")
    except OSError as exc:
        log.debug("codex_custom_target_record_failed", path=str(path), error=str(exc))
        return None
    log.debug("codex_custom_target_recorded", path=str(path), marker=str(marker))
    return marker


def recorded_custom_config_targets() -> list[Path]:
    """Every non-default config.toml a past ``setup`` wrote our block into.

    Unreadable/garbage markers are skipped: a marker we cannot parse must not make
    teardown throw. A marker whose file is long gone is harmless — the caller asks
    :func:`config_has_gateway_block` about each path, and a missing file answers False.
    """
    record_dir = _target_record_dir()
    if not record_dir.is_dir():
        return []
    targets: list[Path] = []
    for marker in sorted(record_dir.glob(f"{CUSTOM_TARGET_PREFIX}.*.origin")):
        try:
            recorded = marker.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            continue
        if recorded:
            targets.append(Path(recorded))
    return targets


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def write_config(
    path: Path,
    text: str,
) -> str | None:
    """Back up then write ``text`` to ``path``. Returns the backup path, if any.

    The backup runs first and is timestamped/non-overwriting (cli.utils.backup), so
    the user's pre-gateway Codex config is always recoverable — this is another
    tool's file and `gateway-cli clear` deliberately does not revert it (see
    ``codex revert``).
    """
    entry = backup_config(BACKUP_TOOL, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return entry.backup_path if entry else None


# ---------------------------------------------------------------------------
# Virtual Key
# ---------------------------------------------------------------------------

def _device_name() -> str:
    """Hostname label recorded on the VK, matching api_key_helper.sso."""
    import socket

    try:
        return socket.gethostname()
    except OSError:
        return "unknown-device"


def resolve_virtual_key(
    *, threshold_seconds: int = VK_REFRESH_THRESHOLD_SECONDS
) -> tuple[str, float, bool]:
    """Return ``(virtual_key, expires_at_epoch, minted)`` — cache first, then mint.

    Deliberately the same code path ``api-key-helper`` runs in OIDC mode
    (``gateway_cli_oidc.oidc_client``), imported lazily as it does. That is what makes
    Claude Code and Codex share ONE cached VK: two implementations reading the same
    file would eventually disagree about when it is stale and fight over it, and the
    shared key is also what puts both tools on one identity — and therefore one budget.

    Cache location comes from ``gateway_cli_oidc``, which honours the same three
    overrides ``cli.paths`` does — ``GATEWAY_CLI_VK_CACHE`` / ``GATEWAY_CLI_OIDC_CACHE``
    for a single file, ``GATEWAY_CLI_DATA_DIR`` for the directory holding both — so a
    relocated data dir moves the files `login` writes and the files this reads together.
    """
    from gateway_cli_oidc.oidc_client import (
        CachedVK,
        OIDCConfig,
        OIDCExchangeError,
        OIDCLoginError,
        exchange_jwt_for_vk,
        get_valid_id_token,
        load_oidc_config_from_env,
        load_vk_cache,
        save_vk_cache,
    )

    # Prefer the env config (identical to api-key-helper); fall back to the values
    # baked into this build so `codex run` works in a shell where `setup
    # --no-persist-env` skipped exporting them.
    oidc_cfg = load_oidc_config_from_env()
    if oidc_cfg is None:
        cfg = resolve_config()
        if not cfg.oidc_issuer_url or not cfg.oidc_client_id:
            raise CodexStepError(
                "OIDC is not configured — normally baked into the build. Set "
                "OIDC_ISSUER_URL and OIDC_CLIENT_ID (or run `gateway-cli setup`, "
                "which persists them), then retry."
            )
        oidc_cfg = OIDCConfig(
            issuer_url=cfg.oidc_issuer_url.rstrip("/"),
            client_id=cfg.oidc_client_id,
            admin_api_url=(cfg.admin_api_url or "").rstrip("/"),
        )

    admin_api_url = oidc_cfg.admin_api_url or (resolve_config().admin_api_url or "").rstrip("/")
    if not admin_api_url:
        raise CodexStepError(
            "no Admin API URL available — set ADMIN_API_URL (or GATEWAY_CLI_ADMIN_API_URL)."
        )

    cached = load_vk_cache()
    if (
        cached
        and cached.issuer_url == oidc_cfg.issuer_url
        and cached.admin_api_url == admin_api_url
        and not cached.is_expiring(threshold_seconds=threshold_seconds)
    ):
        return cached.virtual_key, cached.expires_at, False

    try:
        # id_token, not access_token: Cognito puts email/name/groups only there, and
        # admin-api provisions the user from those claims.
        id_token = get_valid_id_token(oidc_cfg)
    except OIDCLoginError as exc:
        raise CodexStepError(f"{exc}\nRun: gateway-cli login") from exc
    if not id_token:
        raise CodexStepError("no OIDC id_token cached. Run: gateway-cli login")

    try:
        vk_resp = exchange_jwt_for_vk(admin_api_url, id_token, _device_name())
    except OIDCExchangeError as exc:
        raise CodexStepError(f"Admin API key exchange failed: {exc}") from exc

    save_vk_cache(
        CachedVK(
            virtual_key=vk_resp.virtual_key,
            expires_at=vk_resp.expires_at.timestamp(),
            issuer_url=oidc_cfg.issuer_url,
            admin_api_url=admin_api_url,
            user_id=vk_resp.user_id,
            team_id=vk_resp.team_id,
        )
    )
    return vk_resp.virtual_key, vk_resp.expires_at.timestamp(), True


#: Windows default, from CPython's ``shutil._WIN_DEFAULT_PATHEXT``. Only used when the
#: environment has no PATHEXT at all.
_WIN_DEFAULT_PATHEXT = ".COM;.EXE;.BAT;.CMD;.VBS;.JS;.WS;.MSC"

#: Suffixes Windows cannot load as a program image: CreateProcess runs these through
#: cmd.exe instead, which re-parses the whole command line. See :func:`cmd_reparse_risk`.
_WIN_BATCH_SUFFIXES = (".bat", ".cmd")

#: cmd.exe operators. Unlike ``%``, these do not merely mangle an argument — outside
#: quotes ``&`` and ``|`` END the current command and start another one, and ``<``/``>``
#: redirect its I/O to a file. ``^`` is cmd.exe's escape character, so it eats the byte
#: after it. ``list2cmdline`` quotes an argument only when it contains a space, a tab, or
#: is empty (verified: ``['a&b']`` → ``a&b``, ``['a & b']`` → ``"a & b"``), which is
#: exactly the wrong rule here — a one-word ``fix&whoami`` reaches cmd.exe bare.
_CMD_OPERATOR_CHARS = "&|<>^"


def _list2cmdline_quotes(arg: str) -> bool:
    """Will :func:`subprocess.list2cmdline` wrap ``arg`` in double quotes?

    Mirrors CPython's rule (``needquote = (" " in arg) or ("\\t" in arg) or not arg``)
    rather than calling ``list2cmdline`` and looking for quotes, because the escaping it
    applies to inner quotes would make that test ambiguous.
    """
    return not arg or " " in arg or "\t" in arg


def _pathext() -> list[str]:
    """PATHEXT's entries, or Windows' own default when the variable is unset.

    PATHEXT is semicolon-separated by Windows' own definition — identical to
    ``os.pathsep`` on win32, but spelled literally so this stays parseable (and testable)
    when the interpreter is not on Windows. PATH itself does use ``os.pathsep``, which is
    the right separator for it on either platform.
    """
    return [
        ext for ext in (os.environ.get("PATHEXT") or _WIN_DEFAULT_PATHEXT).split(";") if ext
    ]


def _win_spellings(path: str) -> tuple[str, ...]:
    """``C:\\tools\\codex`` → itself, then ``…\\codex.COM``, ``…\\codex.EXE``, … per PATHEXT.

    Windows resolves a bare command name against PATHEXT; a command given as a *path*
    gets no such help from ``shutil.which`` (3.11 checks the name exactly as written and
    stops). But ``npm install -g @openai/codex`` installs ``codex.cmd`` and no
    ``codex.exe``, and ``where codex`` is what a user reads the location out of — so a
    ``GATEWAY_CLI_CODEX_BIN`` typed from memory routinely omits the extension, and the
    override then failed with "could not find the 'codex' executable" while pointing
    squarely at it. As-given comes first, so an extensionless binary still wins.
    """
    if any(path.lower().endswith(ext.lower()) for ext in _pathext()):
        return (path,)
    return (path, *(path + ext for ext in _pathext()))


def _which_windows(cmd: str) -> str | None:
    """PATH lookup for Windows that does NOT search the current directory.

    Why this is hand-rolled instead of ``shutil.which``: on Windows ``which`` mirrors
    the OS by searching ``os.curdir`` FIRST (CPython ``shutil.which``: "The current
    directory takes precedence on Windows" → ``path.insert(0, curdir)``). That is right
    for a shell and wrong here, because :func:`codex_run_cmd` puts a live Virtual Key
    into the resolved process's environment — a ``codex.cmd`` sitting in whatever
    directory the user happened to ``cd`` into would receive a working gateway
    credential that spends against their budget.

    ``NoDefaultCurrentDirectoryInExePath`` is NOT a usable fix: it only gates the insert
    from CPython 3.12 on, and this package supports 3.11 (``pyproject.toml``), where the
    insert is unconditional. Passing ``path=`` does not help either — the insert happens
    after the list is built, whatever its source. So the scan is done here, mirroring
    ``which``'s PATHEXT rules (match ``cmd`` as given when it already ends in a known
    extension, else try each extension in PATHEXT order) and nothing else.

    An absolute-path override is still honoured by the caller, so a user whose codex
    really does live in the working directory has ``GATEWAY_CLI_CODEX_BIN``.
    """
    pathext = _pathext()
    if any(cmd.lower().endswith(ext.lower()) for ext in pathext):
        names = [cmd]
    else:
        names = [cmd + ext for ext in pathext]
    seen: set[str] = set()
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory.strip():
            continue
        normalised = os.path.normcase(directory)
        if normalised in seen:
            continue
        seen.add(normalised)
        for name in names:
            full = os.path.join(directory, name)
            if os.path.isfile(full) and os.access(full, os.X_OK):
                return full
    return None


def resolve_codex_bin() -> str:
    """Absolute path to the codex executable.

    ``GATEWAY_CLI_CODEX_BIN`` overrides the PATH lookup for installs that are not on
    PATH (a common state right after a per-user npm/brew install on Windows). An override
    that includes a directory component is taken literally — that is the escape hatch for
    anything this resolver deliberately will not pick up, including a codex in the current
    directory (see :func:`_which_windows`) — and is returned absolute, so what we resolve
    is what we launch even if something changes directory in between.

    **A bare name is only ever resolved through PATH.** There used to be a fallback that
    tried ``os.path.isfile(candidate)`` after the PATH scan missed, which for the default
    candidate ``"codex"`` means "is there a file called codex in the current directory" —
    reopening by hand the exact hole :func:`_which_windows` exists to close, on POSIX as
    well as Windows. ``codex run`` puts a live Virtual Key in the resolved process's
    environment, so that file would have received a working, budget-spending credential
    because of where the user happened to be standing.

    A ``.`` entry in PATH on POSIX is still honoured, deliberately: that is the user's own
    explicit choice about their own PATH, where the Windows curdir search this guards
    against is CPython's insert, made whatever the user's PATH says.
    """
    override = os.environ.get(CODEX_BIN_ENV, "").strip()
    candidate = override or "codex"

    if os.path.dirname(candidate):
        spellings = _win_spellings(candidate) if sys.platform == "win32" else (candidate,)
        for spelling in spellings:
            if os.path.isfile(spelling) and os.access(spelling, os.X_OK):
                return os.path.abspath(spelling)
        hint = ""
        if sys.platform == "win32":
            hint = (
                " On Windows the npm install is a batch shim, so the name usually ends in "
                ".cmd — `where codex` prints it exactly."
            )
        raise CodexStepError(
            f"{CODEX_BIN_ENV} points at {candidate!r}, which is not an executable "
            f"file.{hint}"
        )

    resolved = _which_windows(candidate) if sys.platform == "win32" else shutil.which(candidate)
    if resolved:
        return resolved
    raise CodexStepError(
        f"could not find the 'codex' executable ({candidate!r}) on PATH. Install Codex "
        f"CLI, or point {CODEX_BIN_ENV} at its full path."
    )


def cmd_reparse_risk(codex_bin: str, args: list[str]) -> str | None:
    """Warn text when Windows will hand our arguments to cmd.exe for a second parse.

    ``npm install -g @openai/codex`` installs ``codex.cmd`` (a batch shim) and no
    ``codex.exe``, so :func:`resolve_codex_bin` legitimately returns a ``.cmd``.
    CreateProcess cannot load a batch file as an image — it runs it through cmd.exe —
    and cmd.exe applies its OWN parse to the command line that
    ``subprocess.list2cmdline`` built for the MS C runtime. The two disagree in two
    places that matter to a prompt:

      ``%NAME%``  cmd.exe substitutes environment variables even inside double quotes,
                  so ``codex exec "why is %APPDATA% empty"`` reaches Codex expanded.
      ``"``       list2cmdline escapes an inner quote as ``\\"``, which cmd.exe does not
                  treat as escaped — it just toggles quote state, so an operator that
                  looked quoted can end up live.
      ``&|<>^``   cmd.exe operators (:data:`_CMD_OPERATOR_CHARS`). These are worse than
                  mangling: an unquoted ``&`` or ``|`` ends our command and RUNS WHAT
                  FOLLOWS as a separate one, with the Virtual Key still in the
                  environment we just built. list2cmdline quotes only on whitespace, so
                  ``codex exec fix&whoami`` — one word, no space — arrives bare.

    An operator only bites when nothing protects it, so it counts as risky when
    list2cmdline will not quote its argument, or when some argument carries a ``"`` that
    can desynchronise cmd.exe's quote tracking for everything after it. ``%`` and ``"``
    count wherever they appear, since quoting does not stop either one.

    This is not a regression the wrapper introduces (typing ``codex`` in cmd.exe takes
    the same path), so it is a warning, not a refusal: blocking a prompt that merely
    contains a ``%`` would be worse than telling the user what will happen. Returns
    None on POSIX, for a non-batch target, or when no argument carries a risky
    character.
    """
    if sys.platform != "win32":
        return None
    if not codex_bin.lower().endswith(_WIN_BATCH_SUFFIXES):
        return None
    # One unbalanced-quote risk for the whole line, not per argument: cmd.exe tracks
    # quote state across the command line, so a `"` in argv[1] can expose an operator
    # in argv[3] that list2cmdline did quote.
    quoting_unreliable = any('"' in a for a in args)
    risky: list[str] = []
    operators: list[str] = []
    for arg in args:
        exposed = any(c in arg for c in _CMD_OPERATOR_CHARS) and (
            quoting_unreliable or not _list2cmdline_quotes(arg)
        )
        if exposed:
            operators.append(arg)
        if exposed or "%" in arg or '"' in arg:
            risky.append(arg)
    if not risky:
        return None
    message = (
        f"{os.path.basename(codex_bin)} is a batch shim, so Windows runs it through "
        "cmd.exe and your arguments get parsed a second time: %NAME% is replaced with "
        'that variable\'s value (even inside quotes) and an embedded " can break quoting. '
        f"Affected: {', '.join(repr(a) for a in risky)}."
    )
    if operators:
        named = ", ".join(repr(a) for a in operators)
        verb = "carries" if len(operators) == 1 else "carry"
        message += (
            f" Worse, {named} {verb} an unquoted cmd.exe operator "
            f"({_CMD_OPERATOR_CHARS}) — cmd.exe will end this command there and run the "
            "rest as its own, with the Virtual Key still set. Check that text is what "
            "you meant before continuing."
        )
    return message + (
        " To avoid it, install a native "
        f"codex.exe and point {CODEX_BIN_ENV} at it, or type the prompt inside the "
        "interactive Codex session instead of passing it on the command line."
    )


def exec_codex(argv: list[str], env: dict) -> int:
    """Run codex with ``env``. Returns its exit code (POSIX: does not return).

    POSIX uses ``execvpe`` so there is no wrapper process left in the middle: Codex
    owns the terminal, Ctrl-C and job control behave exactly as an unwrapped ``codex``,
    and the shell sees Codex's own exit code. Windows has no real exec — ``execvpe``
    there spawns and immediately returns, which would hand the console back to the
    shell while Codex is still drawing on it — so we wait on a child and propagate its
    code instead. On Windows, therefore, the "no wrapper in the middle" property does
    NOT hold, and Ctrl-C needs the handling below.
    """
    if sys.platform == "win32":
        # Ignore SIGINT in THIS process while waiting. The Windows console delivers
        # CTRL_C_EVENT to every process attached to it, so Ctrl-C already reaches Codex
        # directly — that part we want. The problem is that it also reaches us: with the
        # default handler, CPython raises KeyboardInterrupt as soon as the main thread
        # runs bytecode again (i.e. right after the wait returns), click's standalone
        # mode turns it into ``Aborted!`` + ``sys.exit(1)``, and Codex's real exit code
        # is lost — the caller's ``raise SystemExit(code)`` never runs. Only observable
        # outside Codex's raw mode (``codex exec``, plus the TUI's startup/teardown
        # windows): raw mode clears ENABLE_PROCESSED_INPUT on the console input buffer,
        # which is shared by every attached process, so no CTRL_C_EVENT is generated for
        # anyone.
        #
        # Deliberately NOT CREATE_NEW_PROCESS_GROUP: that would stop Ctrl-C reaching
        # Codex at all, which is worse than the bug it fixes. A signal disposition is
        # per-process on Windows and is not inherited, so SIG_IGN here cannot deafen
        # Codex.
        previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            with subprocess.Popen(argv, env=env) as proc:
                return proc.wait()
        finally:
            signal.signal(signal.SIGINT, previous)
    os.execvpe(argv[0], argv, env)  # noqa: S606 — resolved path, caller-supplied args
    raise CodexStepError("exec failed")  # pragma: no cover - execvpe raises or replaces


def _fmt_ttl(seconds: float) -> str:
    total = int(seconds)
    if total <= 0:
        return "expired"
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h{minutes:02d}m"
    if minutes:
        return f"{minutes}m{secs:02d}s"
    return f"{secs}s"


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

@click.group("codex")
def codex_group() -> None:
    """Configure and launch Codex CLI against the gateway."""


@codex_group.command("setup")
@click.option(
    "--model",
    default=DEFAULT_CODEX_MODEL,
    envvar="GATEWAY_CLI_CODEX_MODEL",
    show_default=True,
    # Split by track, because the two rosters are not interchangeable: same models, but
    # different endpoint, auth, price row, and whether AWS keeps a per-call record of its
    # own. A flat list of six invites a coin flip on all four. Body auditing is NOT a
    # discriminator — the gateway logs bodies on both tracks, and AWS logs none for
    # codex's streaming traffic even on runtime (see RUNTIME_CODEX_MODELS).
    help="Gateway model alias Codex should request. Mantle track: "
    + ", ".join(MANTLE_CODEX_MODELS)
    # "must already be seeded" is not padding: this build's roster leads the migration
    # that creates the rows (see RUNTIME_CODEX_MODELS), and an alias the gateway cannot
    # resolve is answered from the profile default at HTTP 200, not with an error.
    + ". bedrock-runtime track (SigV4/IRSA, AWS-side per-call log record, separate "
    "pricing; "
    "must already be seeded on your gateway): "
    + ", ".join(RUNTIME_CODEX_MODELS)
    + ". NOT an upstream OpenAI name and NOT a provider model id — an unresolvable value "
    "is silently served by the codex profile's default model.",
)
@click.option(
    "--base-url",
    default=None,
    envvar="GATEWAY_CLI_CODEX_BASE_URL",
    help="Codex provider base_url; must end in /v1. Defaults to the baked gateway "
    "proxy URL + /v1.",
)
@click.option(
    "--config-path",
    default=None,
    type=click.Path(dir_okay=False, path_type=Path),
    help="config.toml to write. Defaults to $CODEX_HOME/config.toml, else "
    "~/.codex/config.toml.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Print the file that would be written; change nothing.",
)
def codex_setup_cmd(
    model: str, base_url: str | None, config_path: Path | None, dry_run: bool
) -> None:
    """Write the gateway provider block into Codex's config.toml.

    Existing content is preserved: the file is snapshotted first, our two top-level
    keys and the [model_providers.gateway] table are replaced (not duplicated), and
    the result is parsed back and checked before it is written.
    """
    path = config_path or codex_config_path()
    try:
        resolved_base_url = (base_url or default_base_url()).strip()
        check_base_url(resolved_base_url)
        warnings = check_model(model)
        env_key = read_env_key(path)

        existing = ""
        had_bom = False
        if path.is_file():
            try:
                raw = path.read_bytes()
            except OSError as exc:
                raise CodexStepError(
                    f"cannot read {path}: {exc}. Move it aside and re-run to start clean."
                ) from exc
            # Read as bytes so the BOM is visible: CONFIG_READ_ENCODING strips it, which is
            # what every other reader here wants but makes `existing == rendered` below
            # answer True for a file whose first three bytes still break Codex's own TOML
            # parser. Newlines are normalised by hand because read_text() used to do it
            # (universal newlines) and write_config writes through Path.write_text, which
            # translates \n to \r\n on Windows — comparing raw bytes instead would make
            # every re-run on Windows "changed", re-writing and re-snapshotting forever.
            had_bom = raw.startswith(b"\xef\xbb\xbf")
            try:
                existing = raw.decode(CONFIG_READ_ENCODING)
            except UnicodeDecodeError as exc:
                # Named separately from OSError because the remedy is completely
                # different — and because "move it aside" is bad advice for a file the
                # user can simply re-save. The likely cause on the Windows fleet this
                # ships to is Notepad's "ANSI" on a ko-KR machine (CP949), which is
                # easy to hit since the hand-edit snippet in docs/guides/codex.md
                # carries Korean comments.
                raise CodexStepError(not_utf8_message(path, exc)) from exc
            existing = existing.replace("\r\n", "\n").replace("\r", "\n")
            # Name a pre-existing syntax error as one. Left to fail later, this surfaces
            # out of validate_config as "the spliced config is not valid TOML … this
            # usually means the existing file already declares 'model_providers.gateway'
            # in a form we could not remove", sending the user to hunt for a gateway block
            # that has nothing to do with it. Runs after the utf-8-sig decode, so a BOM —
            # which tomllib also rejects — still reaches the repair below instead of this.
            try:
                tomllib.loads(existing)
            except tomllib.TOMLDecodeError as exc:
                raise CodexStepError(
                    f"{path} is not valid TOML ({exc}), so it cannot be edited safely — "
                    "nothing was written. Codex cannot load it in this state either, so "
                    "fix that line and re-run. 'gateway-cli codex status' reports the "
                    "same error with its position."
                ) from exc

        rendered = render_config(
            existing, model=model.strip(), base_url=resolved_base_url, env_key=env_key
        )
        validate_config(
            rendered, model=model.strip(), base_url=resolved_base_url, env_key=env_key
        )
    except CodexStepError as exc:
        raise click.ClickException(str(exc)) from exc

    click.echo(f"  Config file:  {path}")
    click.echo(f"  Model:        {model}")
    click.echo(f"  base_url:     {resolved_base_url}")
    click.echo(f"  wire_api:     {WIRE_API}")
    click.echo(f"  env_key:      {env_key}")
    click.echo("")
    for warning in warnings:
        click.secho(f"  ! {warning}", fg="yellow")
    if warnings:
        click.echo("")

    if dry_run:
        click.secho("Dry run — nothing written. The file would be:", fg="cyan", bold=True)
        click.echo("")
        click.echo(rendered.rstrip("\n"))
        return

    # `and not had_bom`: with the block already in place, the decoded text matches and this
    # branch used to declare success while the BOM stayed on disk — the one thing that made
    # the file unreadable to Codex in the first place. tomllib rejects a BOM at line 1
    # column 1, so `setup` reported "already up to date" about a config Codex refuses to
    # load, forever (the user's editor shows nothing wrong on line 1). We always write
    # bom-less UTF-8, so re-writing IS the repair.
    if existing == rendered and not had_bom:
        click.secho("Codex config already up to date.", fg="green")
    else:
        if had_bom and existing == rendered:
            click.secho(
                "  Removing a UTF-8 byte-order mark — Codex's TOML parser rejects it.",
                fg="yellow",
            )
        try:
            backup = write_config(path, rendered)
        except OSError as exc:
            raise click.ClickException(f"could not write {path}: {exc}") from exc
        click.secho("Codex config written.", fg="green", bold=True)
        if backup:
            click.echo(f"  Backup:       {backup}")

    # Our block is now in `path` (the "already up to date" branch means it was there
    # before — which is exactly the state teardown must know about, so record it too).
    # When `path` is not the default file, teardown has no way to guess it: the snapshot
    # is named after the basename only, so `clear` would sweep the sole pre-setup copy of
    # a file still routing through the gateway. No-op for the default path.
    marker = record_custom_config_target(path)
    if marker:
        click.echo(f"  Recorded:     {marker}")
        click.echo("                (so `clear` keeps the backup until `codex revert`)")

    click.echo("")
    click.echo(f"Codex reads the key from ${env_key} at startup, so launch it with:")
    click.echo("    gateway-cli codex run")
    # "launches", not "execs": on Windows there is no exec — `run` stays as the parent
    # process and waits (see exec_codex). Same text on every platform, true on all of them.
    click.echo("(which mints/reuses a Virtual Key and launches codex for you).")


@codex_group.command(
    "run",
    context_settings={"ignore_unknown_options": True, "allow_interspersed_args": False},
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Resolve the Virtual Key and print the command; launch nothing. The key is "
    "never printed.",
)
@click.argument("codex_args", nargs=-1, type=click.UNPROCESSED)
def codex_run_cmd(dry_run: bool, codex_args: tuple[str, ...]) -> None:
    """Launch Codex with a fresh Virtual Key in its environment.

    Everything after the command is passed through to codex, e.g.
    `gateway-cli codex run -- exec "fix the failing test"`.

    Codex reads its key from the environment ONCE at startup and has no
    apiKeyHelper-style refresh hook, so this is where the refresh happens: each
    launch reuses the cached VK, or mints a new one when it is close to expiry. A
    session that outlives the key ends in a 401 — restart it to get a new one.
    """
    try:
        # Refuse before minting anything. If this config does not select our provider,
        # the key we are about to export is a gateway credential going to whatever
        # endpoint the selected provider names — a third party, for a config that still
        # points at one. `status` reports the same state read-only; this is the write.
        config_path = codex_config_path()
        if not routes_to_gateway(_load_config(config_path)):
            raise CodexStepError(
                f"{config_path} does not route Codex to the gateway, so no Virtual Key "
                "was issued. Run 'gateway-cli codex setup' first, or "
                "'gateway-cli codex status' to see what the file currently selects."
            )
        env_key = read_env_key(config_path)
        virtual_key, expires_at, minted = resolve_virtual_key()
        codex_bin = resolve_codex_bin()
    except CodexStepError as exc:
        raise click.ClickException(str(exc)) from exc

    ttl = _fmt_ttl(expires_at - time.time())
    click.echo(
        f"  Virtual Key:  {'issued' if minted else 'cached'}, expires in {ttl}", err=True
    )
    click.echo(f"  Passed as:    ${env_key}", err=True)

    argv = [codex_bin, *codex_args]
    # The VK goes into the CHILD's environment only — never printed, never persisted.
    env = {**os.environ, env_key: virtual_key}

    # Printed before the launch, and before the dry-run return, so it is visible in both
    # modes — once Codex's TUI paints, anything above it is gone.
    reparse = cmd_reparse_risk(codex_bin, list(codex_args))
    if reparse:
        click.secho(f"  ! {reparse}", fg="yellow", err=True)

    if dry_run:
        click.echo("", err=True)
        # The verb tracks what actually happens: POSIX replaces this process, Windows
        # spawns a child and waits (exec_codex). Saying "exec" on Windows would describe
        # the one platform where a wrapper process demonstrably stays in the tree —
        # exactly what someone reading a dry run is trying to find out.
        verb = "run" if sys.platform == "win32" else "exec"
        click.secho(
            f"Dry run — would {verb}: {' '.join(argv)}  (with ${env_key} set)",
            fg="cyan",
            err=True,
        )
        return

    log.info("codex_launch", bin=codex_bin, args=len(codex_args), minted=minted)
    try:
        code = exec_codex(argv, env)
    except OSError as exc:
        raise click.ClickException(f"could not launch codex: {exc}") from exc
    if code:
        raise SystemExit(code)


@codex_group.command("status")
def codex_status_cmd() -> None:
    """Show the Codex config and Virtual Key state — read-only, mints nothing."""
    path = codex_config_path()
    click.echo("")
    click.echo("Codex + LLM Gateway")
    click.echo("=" * 50)
    click.echo(f"  Config file: {path}")

    if not path.is_file():
        click.secho("  Codex: [NOT CONFIGURED]", fg="red", bold=True)
        click.echo("    Run 'gateway-cli codex setup'.")
        click.echo("=" * 50)
        return

    try:
        data = tomllib.loads(path.read_text(encoding=CONFIG_READ_ENCODING))
    except UnicodeDecodeError as exc:
        # `status` is where a user lands when something is wrong, so it is the last place
        # that should answer with a bare codec error and no way forward.
        click.secho("  Codex: [UNREADABLE]", fg="red", bold=True)
        for line in not_utf8_message(path, exc).splitlines():
            click.echo(f"    {line}")
        click.echo("=" * 50)
        return
    except (OSError, tomllib.TOMLDecodeError) as exc:
        click.secho(f"  Codex: [UNREADABLE] {exc}", fg="red", bold=True)
        click.echo("=" * 50)
        return

    provider_key = data.get("model_provider")
    providers = data.get("model_providers")
    provider = providers.get(provider_key) if isinstance(providers, dict) else None
    provider = provider if isinstance(provider, dict) else {}
    # Same predicate `run` gates on, so the two can never disagree about whether this
    # machine is on the gateway.
    routed = routes_to_gateway(data)

    if routed:
        click.secho("  Codex: [ON gateway]", fg="green", bold=True)
    else:
        click.secho("  Codex: [NOT on the gateway]", fg="yellow", bold=True)
        click.echo("    Run 'gateway-cli codex setup'.")
    click.echo(f"    model:          {data.get('model', '-')}")
    click.echo(f"    model_provider: {provider_key or '-'}")
    click.echo(f"    base_url:       {provider.get('base_url', '-')}")
    click.echo(f"    wire_api:       {provider.get('wire_api', '-')}")
    env_key = provider.get("env_key") or DEFAULT_ENV_KEY
    click.echo(f"    env_key:        {env_key}")
    click.echo("")

    # Cache only — status must never mint, so it stays safe to run while offline.
    from gateway_cli_oidc.oidc_client import load_vk_cache

    cached = load_vk_cache()
    if cached:
        click.echo(f"  Virtual Key:   cached, expires in {_fmt_ttl(cached.expires_at - time.time())}")
    else:
        click.echo("  Virtual Key:   none cached — 'gateway-cli codex run' will mint one")
    # A VK exported by hand shadows what `codex run` would pass: Codex reads the
    # variable from its own environment, so a stale value here wins silently.
    if os.environ.get(env_key):
        click.secho(
            f"  ! ${env_key} is already set in this shell; codex run overrides it "
            "for the child process only.",
            fg="yellow",
        )
    click.echo("=" * 50)


@codex_group.command("revert")
@click.option(
    "--config-path",
    default=None,
    type=click.Path(dir_okay=False, path_type=Path),
    help="config.toml to revert. Defaults to $CODEX_HOME/config.toml.",
)
@click.option("--dry-run", is_flag=True, help="Print the result; change nothing.")
def codex_revert_cmd(config_path: Path | None, dry_run: bool) -> None:
    """Remove the gateway provider block from Codex's config.toml.

    `gateway-cli clear` does NOT do this: config.toml belongs to Codex, not to this
    installer, and a blanket revert of another tool's file would be overreach. So the
    undo is explicit — and, like setup, it keys off exactly the block we wrote and
    leaves everything else in place.
    """
    path = config_path or codex_config_path()
    if not path.is_file():
        click.echo(f"Nothing to revert — {path} does not exist.")
        return
    try:
        existing = path.read_text(encoding=CONFIG_READ_ENCODING)
    except UnicodeDecodeError as exc:
        # Was folded into the OSError clause, which printed the bare codec error. This is
        # the command `verify --post-teardown` sends the user to, so a dead end here is a
        # dead end for teardown too.
        raise click.ClickException(not_utf8_message(path, exc)) from exc
    except OSError as exc:
        raise click.ClickException(f"cannot read {path}: {exc}") from exc

    # Does the file parse AS IT STANDS? Asked before the strip, because otherwise the two
    # causes of an unparseable result are indistinguishable and the message blamed the
    # wrong one: a config that was already broken (a half-typed line the user added after
    # setup) got "reverting would leave invalid TOML … restore a snapshot", which is both
    # untrue — the breakage predates us — and bad advice, since the snapshot predates
    # every edit they have made since.
    pre_existing = None
    try:
        tomllib.loads(existing)
    except tomllib.TOMLDecodeError as exc:
        pre_existing = exc

    stripped = strip_gateway_config(existing)
    if stripped == existing:
        click.echo("No gateway provider block found — nothing to revert.")
        return

    if pre_existing is not None:
        # Refuse, but for the real reason. Line surgery on a document that does not parse
        # cannot be checked afterwards — the post-strip parse would fail whatever we did,
        # so there is no way to tell a good removal from a destructive one.
        raise click.ClickException(
            f"{path} is not valid TOML as it stands ({pre_existing}), so the gateway "
            "block cannot be removed safely — a check on the result would fail whatever "
            "this command did. Fix that line first (Codex cannot load the file either "
            "way), then re-run 'gateway-cli codex revert'."
        )

    try:
        tomllib.loads(stripped)
    except tomllib.TOMLDecodeError as exc:
        raise click.ClickException(
            f"reverting would leave invalid TOML ({exc}); nothing was written. "
            f"Restore a snapshot from the backups dir instead."
        ) from exc

    if dry_run:
        click.secho("Dry run — nothing written. The file would be:", fg="cyan", bold=True)
        click.echo("")
        click.echo(stripped.rstrip("\n") or "(empty)")
        return

    try:
        backup = write_config(path, stripped)
    except OSError as exc:
        raise click.ClickException(f"could not write {path}: {exc}") from exc
    click.secho("Gateway provider block removed from the Codex config.", fg="yellow")
    if backup:
        click.echo(f"  Backup: {backup}")
    click.echo("Codex will fall back to whatever provider its config now names.")
