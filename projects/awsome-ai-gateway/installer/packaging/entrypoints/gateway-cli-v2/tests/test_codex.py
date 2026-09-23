# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""Tests for cli.codex — the Codex ``config.toml`` splice and the VK launcher.

Two things here can break a user's machine rather than just fail, so they carry most
of the weight:

  * the splice writes a file this installer does NOT own. A wrong splice can make
    Codex refuse to start (duplicate/misplaced table), silently reconfigure an
    unrelated ``[profiles.*]``, or drop settings the user cares about. Hence the
    "existing content survives" and "idempotent" cases, plus the TOML-structure case
    (top-level keys must land BEFORE the first table header — appending them to a
    file that already has tables makes them keys of the LAST table, which Codex reads
    as provider settings and we would never notice).
  * the launcher handles the Virtual Key. It must reach the child process and nothing
    else: not stdout, not the parent env, not a log line.
  * ``check_model`` is the only thing standing between a copy-paste and a wrong TRACK.
    The value it lets through picks the endpoint, the auth, the price row, and whether
    AWS keeps its own copy of every request and response body — and the gateway answers
    a value it cannot resolve with HTTP 200 off the profile default, never an error. So
    the cases below assert that a ``provider_model_id`` is refused *at the point of the
    mistake* and that the refusal names the alias the user meant, since the alternative
    is a working Codex on a track the user did not choose and cannot tell apart.

The Responses wire itself (base_url + ``/responses``, ``wire_api``) is asserted as
literal values because they are a contract with the gateway route
(``/v1/responses``) and with Codex CLI's own config parser, not internal choices.
"""

from __future__ import annotations

import os
import signal
import time
import tomllib
from pathlib import Path

import pytest
from click.testing import CliRunner

from cli import codex
from cli.codex import (
    DEFAULT_CODEX_MODEL,
    DEFAULT_ENV_KEY,
    PROVIDER_KEY,
    CodexStepError,
    codex_config_path,
    codex_group,
    codex_home,
    read_env_key,
    render_config,
    strip_gateway_config,
    validate_config,
)

BASE_URL = "https://gw.example.com/v1"


@pytest.fixture()
def codex_dir(tmp_path, monkeypatch):
    """Point CODEX_HOME at a temp dir so no test can touch a real ~/.codex."""
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("GATEWAY_CLI_BACKUP_DIR", str(tmp_path / "backups"))
    return home


def _rendered(existing: str = "", *, model: str = DEFAULT_CODEX_MODEL) -> str:
    return render_config(existing, model=model, base_url=BASE_URL)


# ---------------------------------------------------------------------------
# path resolution
# ---------------------------------------------------------------------------

def test_codex_home_honours_codex_home_env(codex_dir):
    """CODEX_HOME is Codex's OWN variable — a user who moved their config must not
    be configured into a directory Codex never reads."""
    assert codex_home() == codex_dir
    assert codex_config_path() == codex_dir / "config.toml"


def test_codex_home_defaults_under_the_user_home(monkeypatch, tmp_path):
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(codex.Path, "home", staticmethod(lambda: tmp_path))
    assert codex_home() == tmp_path / ".codex"


def test_blank_codex_home_is_ignored(monkeypatch, tmp_path):
    """An exported-but-empty CODEX_HOME must not resolve to the filesystem root."""
    monkeypatch.setenv("CODEX_HOME", "   ")
    monkeypatch.setattr(codex.Path, "home", staticmethod(lambda: tmp_path))
    assert codex_home() == tmp_path / ".codex"


# ---------------------------------------------------------------------------
# render — the wire contract
# ---------------------------------------------------------------------------

def test_render_produces_the_provider_block_codex_needs():
    data = tomllib.loads(_rendered())
    assert data["model"] == DEFAULT_CODEX_MODEL
    assert data["model_provider"] == PROVIDER_KEY
    provider = data["model_providers"][PROVIDER_KEY]
    # `name` is load-bearing: Codex CLI 0.147.0 errors out on a provider table
    # without it, so its absence is a CLI that will not start at all.
    assert provider["name"]
    # base_url ends at /v1 because Codex appends /responses and the gateway serves
    # /v1/responses. Anything else is a 404 that reads as "the gateway is down".
    assert provider["base_url"] == BASE_URL
    # `chat` would target /v1/chat/completions, which the Mantle OpenAI provider
    # does not serve.
    assert provider["wire_api"] == "responses"
    assert provider["env_key"] == DEFAULT_ENV_KEY


def test_default_model_matches_the_seeded_routing_profile_default():
    """Migration 0028 seeds routing_profiles.default_model = codex-gpt-5.6-terra.

    Writing a different default here would make `codex setup` and the gateway
    disagree about which model an unconfigured user gets — the same request billed
    at a different rate depending on whether `model` reached the resolver.
    """
    assert DEFAULT_CODEX_MODEL == "codex-gpt-5.6-terra"
    assert DEFAULT_CODEX_MODEL in codex.KNOWN_CODEX_MODELS


def test_top_level_keys_land_before_the_first_table_header():
    """TOML's one structural trap for appenders.

    A `model = "..."` line written after `[model_providers.gateway]` is a key OF that
    table, not a top-level default — so Codex would keep using its own default model
    while our file looks correct to a human reader.
    """
    text = _rendered()
    lines = text.splitlines()
    first_header = next(i for i, line in enumerate(lines) if line.startswith("["))
    model_line = next(i for i, line in enumerate(lines) if line.startswith("model ="))
    provider_line = next(i for i, line in enumerate(lines) if line.startswith("model_provider ="))
    assert model_line < first_header
    assert provider_line < first_header


def test_existing_content_survives_the_splice():
    """We are editing another tool's file; unrelated settings must be untouched."""
    existing = (
        "# my codex notes\n"
        "approval_policy = \"on-request\"\n"
        "\n"
        "[shell_environment_policy]\n"
        "inherit = \"all\"\n"
        "\n"
        "[profiles.work]\n"
        "model = \"some-other-model\"\n"
    )
    out = _rendered(existing)
    data = tomllib.loads(out)
    assert data["approval_policy"] == "on-request"
    assert data["shell_environment_policy"] == {"inherit": "all"}
    # A `model` key inside another table belongs to THAT table — stripping it would
    # silently reconfigure a profile the user set up by hand.
    assert data["profiles"]["work"]["model"] == "some-other-model"
    assert "# my codex notes" in out
    # And ours is still top-level, not swallowed by [profiles.work].
    assert data["model"] == DEFAULT_CODEX_MODEL


def test_render_is_idempotent():
    """A second `codex setup` must not append a second gateway table.

    Two `[model_providers.gateway]` headers is a TOML parse error, i.e. a Codex that
    refuses to start — the worst possible outcome of re-running a setup command.
    """
    once = _rendered()
    twice = render_config(once, model=DEFAULT_CODEX_MODEL, base_url=BASE_URL)
    assert twice == once
    assert twice.count("[model_providers.gateway]") == 1


def test_re_running_with_a_different_model_replaces_rather_than_duplicates():
    first = _rendered(model="codex-gpt-5.6-terra")
    second = render_config(first, model="codex-gpt-5.6-sol", base_url=BASE_URL)
    data = tomllib.loads(second)
    assert data["model"] == "codex-gpt-5.6-sol"
    assert second.count("model =") == 1
    assert second.count("[model_providers.gateway]") == 1


def test_pre_existing_gateway_block_is_replaced_not_nested():
    """The realistic case: the user followed the guide by hand, then ran setup."""
    existing = (
        'model = "gpt-5.5"\n'
        'model_provider = "gateway"\n'
        "\n"
        "[model_providers.gateway]\n"
        'name = "Old Name"\n'
        'base_url = "https://old.example.com/v1"\n'
        'wire_api = "chat"\n'
        'env_key = "GATEWAY_VK"\n'
        "\n"
        "[history]\n"
        'persistence = "save-all"\n'
    )
    out = _rendered(existing)
    data = tomllib.loads(out)
    assert data["model_providers"][PROVIDER_KEY]["base_url"] == BASE_URL
    assert data["model_providers"][PROVIDER_KEY]["wire_api"] == "responses"
    assert "old.example.com" not in out
    assert data["history"] == {"persistence": "save-all"}  # untouched


def test_sub_tables_of_our_provider_are_removed_too():
    """`[model_providers.gateway.headers]` is part of OUR block.

    Leaving an orphaned sub-table behind would re-create the parent table implicitly,
    so the file would end up with the stale sub-table attached to our new block.
    """
    existing = (
        "[model_providers.gateway]\n"
        'base_url = "https://old/v1"\n'
        "\n"
        "[model_providers.gateway.http_headers]\n"
        'X-Stale = "yes"\n'
        "\n"
        "[model_providers.other]\n"
        'base_url = "https://other/v1"\n'
    )
    out = _rendered(existing)
    data = tomllib.loads(out)
    assert "http_headers" not in data["model_providers"][PROVIDER_KEY]
    assert "X-Stale" not in out
    # A neighbouring provider is none of our business.
    assert data["model_providers"]["other"]["base_url"] == "https://other/v1"


def test_quoted_and_spaced_header_forms_are_recognised():
    """`[ model_providers."gateway" ]` is the same table to TOML, so also to us."""
    existing = '[ model_providers."gateway" ]\nbase_url = "https://old/v1"\n'
    out = _rendered(existing)
    assert out.count("base_url") == 1
    assert tomllib.loads(out)["model_providers"][PROVIDER_KEY]["base_url"] == BASE_URL


def test_values_are_toml_escaped():
    """Flags/env vars are free text; a quote or backslash must not break the file."""
    out = render_config("", model='we"ird', base_url="https://x/v1\\path")
    data = tomllib.loads(out)
    assert data["model"] == 'we"ird'
    assert data["model_providers"][PROVIDER_KEY]["base_url"] == "https://x/v1\\path"


def test_render_rejects_a_provider_key_that_is_not_a_bare_toml_key():
    with pytest.raises(CodexStepError, match="bare TOML key"):
        render_config("", model="m", base_url=BASE_URL, provider_key="not a key")


def test_render_tolerates_crlf_input():
    """A Windows-authored config.toml round-trips through the splice."""
    existing = 'approval_policy = "never"\r\n\r\n[history]\r\npersistence = "none"\r\n'
    out = render_config(
        existing.replace("\r\n", "\n"), model=DEFAULT_CODEX_MODEL, base_url=BASE_URL
    )
    data = tomllib.loads(out)
    assert data["approval_policy"] == "never"
    assert data["model"] == DEFAULT_CODEX_MODEL


# ---------------------------------------------------------------------------
# strip / revert
# ---------------------------------------------------------------------------

def test_strip_removes_exactly_what_render_added():
    existing = 'approval_policy = "never"\n\n[history]\npersistence = "none"\n'
    out = _rendered(existing)
    assert strip_gateway_config(out) == existing


def test_strip_leaves_a_file_with_no_gateway_block_alone():
    existing = '[history]\npersistence = "none"\n'
    assert strip_gateway_config(existing) == existing


def test_strip_does_not_touch_model_keys_inside_other_tables():
    existing = '[profiles.work]\nmodel = "keep-me"\nmodel_provider = "elsewhere"\n'
    assert strip_gateway_config(existing) == existing


def test_strip_removes_the_dotted_key_form():
    """`model_providers.gateway.base_url = …` at top level is the same table.

    Left behind it collides with the table we append, and TOML rejects the file —
    so it must be stripped, not merely appended around.
    """
    existing = 'model_providers.gateway.base_url = "https://old/v1"\napproval_policy = "never"\n'
    out = _rendered(existing)
    data = tomllib.loads(out)  # would raise on a duplicate declaration
    assert data["model_providers"][PROVIDER_KEY]["base_url"] == BASE_URL


def test_repeated_setup_revert_cycles_do_not_grow_the_file():
    """Blank-line accumulation is cosmetic but compounding — pin it."""
    base = 'approval_policy = "never"\n'
    text = base
    for _ in range(3):
        text = render_config(text, model=DEFAULT_CODEX_MODEL, base_url=BASE_URL)
        text = strip_gateway_config(text)
    assert text == base


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

def test_validate_accepts_what_render_produces():
    data = validate_config(_rendered(), model=DEFAULT_CODEX_MODEL, base_url=BASE_URL)
    assert data["model_providers"][PROVIDER_KEY]["wire_api"] == "responses"


def test_validate_rejects_a_model_that_landed_in_the_wrong_place():
    """The exact failure the top-level-keys rule prevents, asserted from the other side.

    A splice bug that puts `model` inside the provider table leaves valid TOML, so
    only a value-level check catches it.
    """
    bad = (
        "[model_providers.gateway]\n"
        'name = "LLM Gateway"\n'
        f'base_url = "{BASE_URL}"\n'
        'wire_api = "responses"\n'
        f'env_key = "{DEFAULT_ENV_KEY}"\n'
        f'model = "{DEFAULT_CODEX_MODEL}"\n'
    )
    with pytest.raises(CodexStepError, match="top-level model"):
        validate_config(bad, model=DEFAULT_CODEX_MODEL, base_url=BASE_URL)


def test_validate_reports_unparseable_toml_with_a_recovery_hint():
    with pytest.raises(CodexStepError, match="not valid TOML"):
        validate_config("model = = =\n", model="m", base_url=BASE_URL)


def test_validate_catches_a_wire_api_mismatch():
    text = _rendered().replace('wire_api = "responses"', 'wire_api = "chat"')
    with pytest.raises(CodexStepError, match="wire_api"):
        validate_config(text, model=DEFAULT_CODEX_MODEL, base_url=BASE_URL)


# ---------------------------------------------------------------------------
# value checks
# ---------------------------------------------------------------------------

def test_base_url_must_end_in_v1():
    with pytest.raises(CodexStepError, match="/v1"):
        codex.check_base_url("https://gw.example.com")


def test_base_url_must_be_a_url():
    with pytest.raises(CodexStepError, match="--base-url"):
        codex.check_base_url("gw.example.com/v1")


def test_default_base_url_appends_v1_once(monkeypatch):
    class _Cfg:
        gateway_url = "https://gw.example.com/"

    monkeypatch.setattr(codex, "resolve_config", lambda *a, **k: _Cfg())
    assert codex.default_base_url() == "https://gw.example.com/v1"
    _Cfg.gateway_url = "https://gw.example.com/v1"
    assert codex.default_base_url() == "https://gw.example.com/v1"


def test_provider_model_id_is_rejected_as_a_model():
    """`openai.gpt-5.6-terra` is what the gateway sends UPSTREAM, not an alias.

    Pasting it here does not simply fail: the gateway falls back to looking a
    `provider_model_id` up with `limit(1)`, so the user gets a working Codex on an
    arbitrary alias — an arbitrary price row and arbitrary per-alias permissions.
    """
    with pytest.raises(CodexStepError, match="provider model id"):
        codex.check_model("openai.gpt-5.6-terra")
    with pytest.raises(CodexStepError, match="provider model id"):
        codex.check_model("us.anthropic.claude-sonnet-4-6-v1:0")


@pytest.mark.parametrize(
    "pasted",
    [
        "global.openai.gpt-5.6-sol",          # the runtime track's own upstream id
        "us.openai.gpt-5.6-terra",            # the other CRIS geography (us-east-1/2 only)
        "GLOBAL.OpenAI.GPT-5.6-Luna",         # console copy with the case mangled
        "jp.openai.gpt-5.6-sol",              # a geography AWS has not shipped yet
        "eu.anthropic.claude-sonnet-4-6-v1:0",  # a geo this build never enumerated
        "arn:aws:bedrock:us-east-2:123456789012:inference-profile/global.openai.gpt-5.6-sol",
    ],
)
def test_a_cris_prefixed_provider_model_id_is_hard_rejected_not_warned(pasted):
    """This used to WARN, which made it the worst input in the set.

    `global.openai.gpt-5.6-sol` is real (it is what the runtime track sends upstream, and
    CRIS is mandatory there), authoritative-looking, and exactly what `aws bedrock
    list-inference-profiles` and the console print — so it is the string a user copies.
    Warned-and-accepted, it is not a name the gateway answers to: the resolver finds no
    alias, and either misses the `provider_model_id` fallback too (serving the codex
    profile's MANTLE default at HTTP 200) or hits it and takes `limit(1)` of whichever
    alias rows carry that id. Both are a 200 on a track the user did not choose — mantle
    price, bearer auth, no AWS-side invocation-log record — with nothing in the output to
    tell them apart.

    A warning is not enough BECAUSE the flow continues: `codex setup` writes the file and
    the next `codex run` succeeds. Only a raise stops the wrong value reaching config.toml.

    `jp.openai.…` is in the table on purpose and is not hypothetical padding: it is the
    case an enumerated `^(global|us|eu|apac|in)\\.` prefix list would silently let through
    the day AWS ships another geography, which is why the detector reads the vendor token
    instead of the prefix.
    """
    with pytest.raises(CodexStepError, match="provider model id"):
        codex.check_model(pasted)


def test_the_rejection_names_the_alias_that_carries_the_id_on_the_SAME_track():
    """The remedy must not answer one mistake with a different one.

    The geo prefix is the only thing that distinguishes the two tracks' upstream ids, and
    the tracks differ in endpoint, auth, price row and whether AWS keeps a per-call
    invocation-log record. So
    `openai.gpt-5.6-sol` must be answered with the mantle alias and
    `global.openai.gpt-5.6-sol` with the runtime one — pointing a user at the other side
    of that line silently moves their spend and their AWS-side record.
    """
    with pytest.raises(CodexStepError) as bare:
        codex.check_model("openai.gpt-5.6-sol")
    assert "codex-gpt-5.6-sol" in str(bare.value)
    assert "codex-rt-" not in str(bare.value), (
        "a bare (mantle) pmid was answered with a runtime alias: the user would be moved "
        "to a different endpoint, price row and audit posture by the fix message itself"
    )

    with pytest.raises(CodexStepError) as cris:
        codex.check_model("global.openai.gpt-5.6-sol")
    assert "codex-rt-gpt-5.6-sol" in str(cris.value)
    # And it says WHY the string looked like a model name, so the user learns the shape
    # rather than just being told no — plus which track the suggested alias is on, so the
    # remedy does not read as a downgrade to "some other model".
    assert "CRIS" in str(cris.value)
    assert "bedrock-runtime" in str(cris.value)


def test_the_rejection_never_invents_an_alias_this_build_does_not_ship():
    """A suggestion is derived by string convention, so it is filtered by the roster.

    `us.anthropic.claude-…` is a real Bedrock id with no Codex alias at all (Claude Code
    reaches those over /v1/messages). Deriving `codex-rt-claude-sonnet-4-6-v1:0` and
    printing it would be a fabricated remedy: the user would type it, the gateway would
    fail to resolve it, and the profile default would answer 200 — the exact substitution
    this whole function exists to prevent, caused by the error message.
    """
    with pytest.raises(CodexStepError) as exc:
        codex.check_model("us.anthropic.claude-sonnet-4-6-v1:0")
    message = str(exc.value)
    assert "codex-rt-claude" not in message and "codex-claude" not in message
    # With nothing to suggest it falls back to the roster, so the user still has an answer.
    for known in codex.KNOWN_CODEX_MODELS:
        assert known in message

    assert codex.alias_for_provider_model_id("us.anthropic.claude-sonnet-4-6-v1:0") is None
    # A model family we have no alias for, on a track we do have aliases for.
    assert codex.alias_for_provider_model_id("global.openai.gpt-9-nova") is None
    # A non-id is not an id: the suggester must not answer for an alias.
    assert codex.alias_for_provider_model_id("codex-gpt-5.6-sol") is None


def test_alias_for_provider_model_id_maps_each_track_to_its_own_alias():
    """Pins the 0028/0033 naming convention this CLI derives suggestions from.

    If a migration renames its aliases, this fails here rather than by printing a name
    the gateway has never heard of.
    """
    assert codex.alias_for_provider_model_id("openai.gpt-5.6-terra") == "codex-gpt-5.6-terra"
    assert (
        codex.alias_for_provider_model_id("global.openai.gpt-5.6-terra")
        == "codex-rt-gpt-5.6-terra"
    )
    assert codex.alias_for_provider_model_id("us.openai.gpt-5.6-luna") == "codex-rt-gpt-5.6-luna"


def test_no_alias_this_build_ships_is_mistaken_for_a_provider_model_id():
    """Fail-closed both ways: the reject and the roster cannot be allowed to disagree.

    The detector reads a vendor token out of dotted segment 0 or 1, which is broader than
    a fixed `global.|us.|…` prefix list on purpose. Broader means it could in principle
    swallow a legitimate alias — so every alias we actually ship is asserted to pass
    `check_model` cleanly. A roster entry that its own validator rejects would be a
    `--model` value the CLI advertises in `--help` and then refuses.
    """
    for alias in codex.KNOWN_CODEX_MODELS:
        assert codex._split_provider_model_id(alias) is None, alias
        assert codex.check_model(alias) == [], f"{alias} is advertised but not accepted"


def test_runtime_aliases_are_in_the_roster_so_they_do_not_warn():
    """Migration 0033's aliases ship in the roster BEFORE 0033 is applied, deliberately.

    This CLI is PyInstaller-packaged and reaches user machines through an installer, so
    its roster only changes on a release train while the alias rows change with a
    migration — the constant has to lead the database. Warning on a name the operator was
    told to use would train users to ignore the warning that carries the real signal
    (an unknown alias is silently substituted).
    """
    assert codex.RUNTIME_CODEX_MODELS == (
        "codex-rt-gpt-5.6-sol",
        "codex-rt-gpt-5.6-terra",
        "codex-rt-gpt-5.6-luna",
    )
    assert codex.KNOWN_CODEX_MODELS == (*codex.MANTLE_CODEX_MODELS, *codex.RUNTIME_CODEX_MODELS)
    for alias in codex.RUNTIME_CODEX_MODELS:
        assert codex.check_model(alias) == []
    # The default stays on the mantle track: 0033 is gated on operational go/no-go, so a
    # user who passes no --model must not be moved onto a track that may not be seeded.
    assert DEFAULT_CODEX_MODEL in codex.MANTLE_CODEX_MODELS


def test_unknown_alias_warns_but_is_allowed():
    """The alias roster is a DB row — a new alias must not need a CLI rebuild.

    But silence would hide the gateway's quiet fallback, so it warns.
    """
    warnings = codex.check_model("codex-gpt-6-nova")
    assert warnings and "not one of the aliases" in warnings[0]
    assert codex.check_model("codex-gpt-5.6-sol") == []


def test_empty_model_is_rejected():
    with pytest.raises(CodexStepError, match="empty"):
        codex.check_model("   ")


# ---------------------------------------------------------------------------
# env_key round-trip (setup writes it, run reads it)
# ---------------------------------------------------------------------------

def test_read_env_key_round_trips_what_setup_wrote(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(_rendered(), encoding="utf-8")
    assert read_env_key(path) == DEFAULT_ENV_KEY


def test_read_env_key_honours_a_hand_edited_value(tmp_path):
    """`run` must export the variable the FILE names.

    Otherwise a user who renamed env_key gets the VK in a variable Codex never
    reads, and the symptom is an auth error that points at the wrong cause.
    """
    path = tmp_path / "config.toml"
    path.write_text(_rendered().replace(DEFAULT_ENV_KEY, "MY_KEY"), encoding="utf-8")
    assert read_env_key(path) == "MY_KEY"


def test_read_env_key_reads_our_table_not_whatever_is_selected(tmp_path):
    """Never inherit ANOTHER provider's env_key — that leaks the gateway VK.

    This test previously asserted the opposite ("follow `model_provider` to the right
    table"), which was wrong: on a config that still selects another provider, `setup`
    copied THAT provider's variable name into our block, and `codex run` then exported
    a gateway Virtual Key as e.g. `OPENAI_API_KEY` — into Codex's environment, into
    every tool and MCP server Codex spawns, and, while the selection is still foreign,
    to that third party's endpoint as its API key.

    The flexibility this was reaching for is preserved by the test above: a user's
    hand-edited `model_providers.gateway.env_key` still round-trips.
    """
    path = tmp_path / "config.toml"
    path.write_text(
        'model_provider = "other"\n'
        "\n"
        "[model_providers.gateway]\n"
        'env_key = "OURS"\n'
        "\n"
        "[model_providers.other]\n"
        'env_key = "THEIRS"\n',
        encoding="utf-8",
    )
    assert read_env_key(path) == "OURS"


def test_read_env_key_falls_back_when_the_file_is_missing_or_broken(tmp_path):
    assert read_env_key(tmp_path / "nope.toml") == DEFAULT_ENV_KEY
    broken = tmp_path / "broken.toml"
    broken.write_text("not = = toml\n", encoding="utf-8")
    assert read_env_key(broken) == DEFAULT_ENV_KEY


# ---------------------------------------------------------------------------
# `codex setup` command
# ---------------------------------------------------------------------------

def test_setup_writes_the_file_and_creates_the_directory(codex_dir, monkeypatch):
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    result = CliRunner().invoke(codex_group, ["setup"])
    assert result.exit_code == 0, result.output
    written = tomllib.loads((codex_dir / "config.toml").read_text(encoding="utf-8"))
    assert written["model"] == DEFAULT_CODEX_MODEL
    assert written["model_providers"][PROVIDER_KEY]["base_url"] == BASE_URL


def test_setup_backs_the_file_up_before_touching_it(codex_dir, monkeypatch, tmp_path):
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    codex_dir.mkdir(parents=True)
    original = 'approval_policy = "never"\n'
    (codex_dir / "config.toml").write_text(original, encoding="utf-8")

    result = CliRunner().invoke(codex_group, ["setup"])
    assert result.exit_code == 0, result.output
    backups = list((tmp_path / "backups").glob("codex.config.toml.*.bak"))
    assert len(backups) == 1
    # The snapshot is the PRE-write content — that is the whole point of taking it.
    assert backups[0].read_text(encoding="utf-8") == original


def test_setup_dry_run_writes_nothing(codex_dir, monkeypatch):
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    result = CliRunner().invoke(codex_group, ["setup", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert not (codex_dir / "config.toml").exists()
    # The plan shows the actual file, so a reviewer can eyeball it before writing.
    assert "[model_providers.gateway]" in result.output
    assert 'wire_api = "responses"' in result.output


def test_setup_rejects_a_base_url_without_v1(codex_dir):
    result = CliRunner().invoke(
        codex_group, ["setup", "--base-url", "https://gw.example.com"]
    )
    assert result.exit_code != 0
    assert "/v1" in result.output
    assert not (codex_dir / "config.toml").exists()


def test_setup_refuses_a_cris_provider_model_id_and_writes_nothing(codex_dir, monkeypatch):
    """The reject has to land before the write, or it is only advice.

    `check_model` runs inside `setup`'s try block ahead of the render, so a rejected
    value cannot reach config.toml — and the exit code is non-zero, which is what a
    scripted/MDM rollout keys off. If this ever regressed to a warning, the file would be
    written and the next `codex run` would succeed on the wrong track.
    """
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    result = CliRunner().invoke(
        codex_group, ["setup", "--model", "global.openai.gpt-5.6-sol"]
    )
    assert result.exit_code != 0
    assert "provider model id" in result.output
    # The remedy the user can act on, printed with the refusal.
    assert "codex-rt-gpt-5.6-sol" in result.output
    assert not (codex_dir / "config.toml").exists()


def test_setup_help_lists_both_tracks_rosters():
    """The roster is only useful if the user can see it before they guess.

    Split by track in the help text because the six aliases are not interchangeable —
    same three models, different endpoint, auth, price row, and whether AWS keeps its own
    copy of the bodies. A flat list of six invites a coin flip on all four.
    """
    result = CliRunner().invoke(codex_group, ["setup", "--help"])
    assert result.exit_code == 0, result.output
    # Whitespace is squeezed out before matching because click's textwrap breaks on
    # hyphens (`codex-\n  gpt-5.6-luna`), which is a rendering artefact of the terminal
    # width, not content. Asserting on the raw output would make this test a hostage to
    # the help text's line lengths.
    shown = "".join(result.output.split())
    for alias in codex.KNOWN_CODEX_MODELS:
        assert alias in shown, f"{alias} is shipped but never shown to the user"


def test_setup_second_run_reports_no_change_and_takes_no_backup(codex_dir, monkeypatch, tmp_path):
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    runner = CliRunner()
    assert runner.invoke(codex_group, ["setup"]).exit_code == 0
    result = runner.invoke(codex_group, ["setup"])
    assert result.exit_code == 0, result.output
    assert "already up to date" in result.output
    # No write ⇒ no new snapshot; otherwise every re-run litters the backups dir.
    assert len(list((tmp_path / "backups").glob("codex.config.toml.*.bak"))) == 0


def test_setup_honours_config_path(tmp_path, monkeypatch):
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    monkeypatch.setenv("GATEWAY_CLI_BACKUP_DIR", str(tmp_path / "b"))
    target = tmp_path / "elsewhere" / "config.toml"
    result = CliRunner().invoke(codex_group, ["setup", "--config-path", str(target)])
    assert result.exit_code == 0, result.output
    assert target.is_file()


# ---------------------------------------------------------------------------
# `--config-path` target record — so teardown does not sweep a live file's only undo
# ---------------------------------------------------------------------------
# The snapshot is named after the BASENAME (codex.config.toml.<ts>.bak), so a block written
# to D:\work\config.toml left a snapshot indistinguishable from the default file's. Teardown
# asked whether $CODEX_HOME/config.toml carried the block, got False, and swept that
# snapshot — the sole pre-setup copy of a file still routing through the gateway. With
# `uninstall --clear-first` the `codex revert` that could undo it goes in the same breath.
# So `setup` records every non-default target it writes to.

def _markers(backup_dir: Path) -> list[Path]:
    return sorted(backup_dir.glob(f"{codex.CUSTOM_TARGET_PREFIX}.*.origin"))


def test_setup_records_a_non_default_config_path(tmp_path, monkeypatch):
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    backups = tmp_path / "b"
    monkeypatch.setenv("GATEWAY_CLI_BACKUP_DIR", str(backups))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    target = tmp_path / "elsewhere" / "config.toml"

    result = CliRunner().invoke(codex_group, ["setup", "--config-path", str(target)])
    assert result.exit_code == 0, result.output

    assert codex.recorded_custom_config_targets() == [target]
    assert len(_markers(backups)) == 1
    # Absolute path, one per line — teardown re-reads this to ask config_has_gateway_block.
    assert _markers(backups)[0].read_text(encoding="utf-8").strip() == str(target)


def test_setup_records_nothing_for_the_default_config_path(codex_dir, monkeypatch, tmp_path):
    """No marker for the file teardown already knows about — it would be pure noise."""
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    assert CliRunner().invoke(codex_group, ["setup"]).exit_code == 0
    assert _markers(tmp_path / "backups") == []
    assert codex.recorded_custom_config_targets() == []


def test_a_spelled_out_default_path_is_still_the_default(codex_dir, monkeypatch, tmp_path):
    """`--config-path ~/.codex/../.codex/config.toml` is the default file, not a custom one.

    Paths are compared resolved for exactly this: a false "custom" would file a marker for
    the file teardown already checks (harmless), but the same comparison decides the
    reverse case, and a false "default" skips the marker teardown depends on.
    """
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    codex_dir.mkdir(parents=True)
    dotted = codex_dir / ".." / codex_dir.name / "config.toml"

    result = CliRunner().invoke(codex_group, ["setup", "--config-path", str(dotted)])
    assert result.exit_code == 0, result.output
    assert _markers(tmp_path / "backups") == []


def test_re_running_setup_on_the_same_custom_path_does_not_pile_up_markers(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    backups = tmp_path / "b"
    monkeypatch.setenv("GATEWAY_CLI_BACKUP_DIR", str(backups))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    target = tmp_path / "elsewhere" / "config.toml"
    runner = CliRunner()

    assert runner.invoke(codex_group, ["setup", "--config-path", str(target)]).exit_code == 0
    second = runner.invoke(codex_group, ["setup", "--config-path", str(target)])

    assert second.exit_code == 0, second.output
    assert "already up to date" in second.output
    assert len(_markers(backups)) == 1


def test_the_already_up_to_date_branch_still_records_the_target(tmp_path, monkeypatch):
    """That branch means the block IS in the file — precisely what teardown must know.

    Recording only on the write branch left an upgraded/re-run install with a live block
    and no marker, which is the original bug with an extra step.
    """
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    backups = tmp_path / "b"
    monkeypatch.setenv("GATEWAY_CLI_BACKUP_DIR", str(backups))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    target = tmp_path / "elsewhere" / "config.toml"
    runner = CliRunner()
    assert runner.invoke(codex_group, ["setup", "--config-path", str(target)]).exit_code == 0
    for marker in _markers(backups):
        marker.unlink()  # e.g. a `clear` that ran while the block was briefly absent

    result = runner.invoke(codex_group, ["setup", "--config-path", str(target)])

    assert "already up to date" in result.output
    assert codex.recorded_custom_config_targets() == [target]


def test_setup_dry_run_records_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    backups = tmp_path / "b"
    monkeypatch.setenv("GATEWAY_CLI_BACKUP_DIR", str(backups))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    target = tmp_path / "elsewhere" / "config.toml"

    result = CliRunner().invoke(
        codex_group, ["setup", "--config-path", str(target), "--dry-run"]
    )

    assert result.exit_code == 0, result.output
    assert not target.exists()
    assert _markers(backups) == []


def test_recording_survives_an_unwritable_backup_dir(tmp_path, monkeypatch):
    """A safety net must never be the thing that fails `setup`."""
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    monkeypatch.setenv("GATEWAY_CLI_BACKUP_DIR", str(tmp_path / "b"))
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.setattr(
        codex, "_target_record_dir", lambda: (_ for _ in ()).throw(OSError("read-only"))
    )
    target = tmp_path / "elsewhere" / "config.toml"

    result = CliRunner().invoke(codex_group, ["setup", "--config-path", str(target)])

    assert result.exit_code == 0, result.output
    assert target.is_file()  # the config still got written


def test_unreadable_or_empty_markers_are_skipped(tmp_path, monkeypatch):
    """One bad marker must not make teardown throw — it runs during `clear`."""
    backups = tmp_path / "b"
    backups.mkdir()
    monkeypatch.setenv("GATEWAY_CLI_BACKUP_DIR", str(backups))
    good = tmp_path / "elsewhere" / "config.toml"
    (backups / f"{codex.CUSTOM_TARGET_PREFIX}.20260101T000001.origin").write_text(
        f"{good}\n", encoding="utf-8"
    )
    (backups / f"{codex.CUSTOM_TARGET_PREFIX}.20260101T000002.origin").write_text(
        "   \n", encoding="utf-8"
    )
    (backups / f"{codex.CUSTOM_TARGET_PREFIX}.20260101T000003.origin").write_bytes(b"\xff\xfe\x00")
    (backups / f"{codex.CUSTOM_TARGET_PREFIX}.20260101T000004.origin").mkdir()

    assert codex.recorded_custom_config_targets() == [good]


def test_the_marker_name_cannot_be_mistaken_for_a_snapshot(tmp_path, monkeypatch):
    """Drift guard on the two glob patterns teardown keys off.

    `codex.*.bak` must not match a marker (it holds no config and must never be offered
    as one to restore), and `codex.custom-target.*.origin` must not match a snapshot.
    """
    from fnmatch import fnmatch

    marker = f"{codex.CUSTOM_TARGET_PREFIX}.20260101T000000.origin"
    snapshot = "codex.config.toml.20260101T000000.bak"
    assert not fnmatch(marker, "codex.*.bak")
    assert not fnmatch(snapshot, f"{codex.CUSTOM_TARGET_PREFIX}.*.origin")
    assert fnmatch(marker, f"{codex.CUSTOM_TARGET_PREFIX}.*.origin")
    assert fnmatch(snapshot, "codex.*.bak")


def test_setup_warns_but_proceeds_on_an_unknown_alias(codex_dir, monkeypatch):
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    result = CliRunner().invoke(codex_group, ["setup", "--model", "codex-gpt-7"])
    assert result.exit_code == 0, result.output
    assert "not one of the aliases" in result.output
    assert tomllib.loads((codex_dir / "config.toml").read_text())["model"] == "codex-gpt-7"


# ---------------------------------------------------------------------------
# `codex revert` command
# ---------------------------------------------------------------------------

def test_revert_removes_only_our_block(codex_dir, monkeypatch):
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    codex_dir.mkdir(parents=True)
    original = 'approval_policy = "never"\n'
    (codex_dir / "config.toml").write_text(original, encoding="utf-8")
    runner = CliRunner()
    assert runner.invoke(codex_group, ["setup"]).exit_code == 0

    result = runner.invoke(codex_group, ["revert"])
    assert result.exit_code == 0, result.output
    assert (codex_dir / "config.toml").read_text(encoding="utf-8") == original


def test_revert_on_a_clean_file_changes_nothing(codex_dir):
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_text('approval_policy = "never"\n', encoding="utf-8")
    result = CliRunner().invoke(codex_group, ["revert"])
    assert result.exit_code == 0
    assert "nothing to revert" in result.output.lower()


def test_revert_without_a_file_is_not_an_error(codex_dir):
    result = CliRunner().invoke(codex_group, ["revert"])
    assert result.exit_code == 0
    assert "nothing to revert" in result.output.lower()


# ---------------------------------------------------------------------------
# A line-based scanner on a file it does not own (MEDIUM-6)
# ---------------------------------------------------------------------------
# Two ways the scanners used to misread a file: our header quoted inside somebody's
# multi-line string (structure that is really prose), and a syntax error the user
# introduced after setup (a real block, an unparseable document). The first produced
# residue no command could clear; the second produced a refusal that blamed the wrong
# thing and pointed at a snapshot predating every edit since.

NOTES_QUOTING_OUR_HEADER = '''\
approval_policy = "never"
notes = """
paste this to go through the gateway:
[model_providers.gateway]
name = "LLM Gateway"
"""
'''


def test_a_quoted_header_inside_a_multiline_string_is_not_our_residue(codex_dir):
    """The detector must say False, or teardown reports residue forever.

    Nothing the user can run removes a header that exists only inside their own prose,
    so a false positive here means `clear` holds a snapshot back and
    `verify --post-teardown` fails with a remedy that cannot work.
    """
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_text(NOTES_QUOTING_OUR_HEADER, encoding="utf-8")

    assert codex.config_has_gateway_block() is False


def test_the_stripper_leaves_a_multiline_string_alone(codex_dir):
    """It used to latch onto the quoted header and delete to the next real header —
    taking the string's own closing delimiter with it, so the result did not parse."""
    out = strip_gateway_config(NOTES_QUOTING_OUR_HEADER)

    assert out == NOTES_QUOTING_OUR_HEADER
    assert tomllib.loads(out)["notes"].count("[model_providers.gateway]") == 1


def test_revert_says_nothing_to_do_for_a_quoted_header(codex_dir):
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_text(NOTES_QUOTING_OUR_HEADER, encoding="utf-8")

    result = CliRunner().invoke(codex_group, ["revert"])

    assert result.exit_code == 0, result.output
    assert "nothing to revert" in result.output.lower()
    assert (codex_dir / "config.toml").read_text(encoding="utf-8") == NOTES_QUOTING_OUR_HEADER


def test_setup_splices_around_a_multiline_string_that_looks_like_a_table(codex_dir, monkeypatch):
    """Our two top-level keys must not land INSIDE the string.

    `render_config` splits the preamble at the first table header; treating the quoted
    header as real put `model =` and `model_provider =` inside the string, which swallowed
    them — and then validate_config failed on a `model` it could not find, blaming the
    splice for the user's formatting.
    """
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_text(NOTES_QUOTING_OUR_HEADER, encoding="utf-8")

    result = CliRunner().invoke(codex_group, ["setup"])

    assert result.exit_code == 0, result.output
    data = tomllib.loads((codex_dir / "config.toml").read_text(encoding="utf-8"))
    assert data["model"] == DEFAULT_CODEX_MODEL
    assert data["model_provider"] == PROVIDER_KEY
    assert "model =" not in data["notes"]          # the prose is untouched
    assert data["approval_policy"] == "never"


def test_a_delimiter_inside_a_single_line_string_does_not_open_a_block(codex_dir):
    """`x = '\"\"\"'` is three characters of data, not the start of a multi-line string.

    Were it read as one, everything after it would be "inside a string" and our real
    block below would become invisible — the mirror-image failure, and the dangerous
    direction: teardown would sweep the snapshot of a file still routing to the gateway.
    """
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_text(
        'quirk = \'"""\'\n\n[model_providers.gateway]\nname = "LLM Gateway"\n',
        encoding="utf-8",
    )

    assert codex.config_has_gateway_block() is True


def test_an_escaped_quote_does_not_close_a_multiline_string_early(codex_dir):
    r"""A backslash-escaped quote followed by two more is content, not a closing delimiter.

    Inside a multi-line basic string, `\` + three quotes is one escaped quote plus two
    literal ones. Read without honouring the backslash the string appears to close on that
    line, everything after it becomes structure again, and the header on the NEXT line
    reads as a real table — back to residue no command can clear. tomllib agrees with the
    escape reading: this document has a `notes` key and no provider table at all.
    """
    codex_dir.mkdir(parents=True)
    doc = 'notes = """\nliteral quotes: \\"""\n[model_providers.gateway]\n"""\n'
    (codex_dir / "config.toml").write_text(doc, encoding="utf-8")
    parsed = tomllib.loads(doc)
    assert "model_providers" not in parsed          # the premise, stated by the parser
    assert "[model_providers.gateway]" in parsed["notes"]

    assert codex.config_has_gateway_block() is False
    assert strip_gateway_config(doc) == doc


def test_an_escaped_quote_run_in_a_single_line_string_is_data(codex_dir):
    r"""And `x = "\"\"\""` on one line stays one line — no block opened, ours still seen."""
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_text(
        'quirk = "\\"\\"\\""\n\n[model_providers.gateway]\nname = "LLM Gateway"\n',
        encoding="utf-8",
    )

    assert codex.config_has_gateway_block() is True


def test_a_comment_mentioning_our_header_is_still_not_residue(codex_dir):
    """A `#` comment is not structure — `_header_key` never matched one, and must not
    start being read as one now that the scanner tracks comment state."""
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_text(
        'approval_policy = "never"  # was [model_providers.gateway]\n', encoding="utf-8"
    )

    assert codex.config_has_gateway_block() is False


def test_revert_blames_the_users_own_syntax_error_not_its_own_removal(codex_dir, monkeypatch):
    """A line the user broke AFTER setup: refuse, but for the true reason.

    The old message — "reverting would leave invalid TOML … restore a snapshot from the
    backups dir" — was wrong twice: the breakage predates the removal, and that snapshot
    predates every edit the user has made since setup.
    """
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    codex_dir.mkdir(parents=True)
    runner = CliRunner()
    assert runner.invoke(codex_group, ["setup"]).exit_code == 0
    config = codex_dir / "config.toml"
    before = config.read_text(encoding="utf-8")
    config.write_text(before + 'oops = [1, 2\n', encoding="utf-8")
    broken = config.read_text(encoding="utf-8")

    result = runner.invoke(codex_group, ["revert"])

    assert result.exit_code != 0
    assert "not valid TOML as it stands" in result.output
    assert "reverting would leave" not in result.output   # not our removal's fault
    assert "snapshot" not in result.output.lower()        # and not the remedy
    assert config.read_text(encoding="utf-8") == broken   # nothing written


def test_setup_names_a_pre_existing_syntax_error_as_such(codex_dir):
    """Same misdiagnosis on the way in: validate_config used to blame a
    `model_providers.gateway` declaration "we could not remove" for a stray bracket."""
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_text('oops = [1, 2\n', encoding="utf-8")

    result = CliRunner().invoke(codex_group, ["setup", "--base-url", BASE_URL])

    assert result.exit_code != 0
    assert "is not valid TOML" in result.output
    assert "could not remove" not in result.output
    assert (codex_dir / "config.toml").read_text(encoding="utf-8") == 'oops = [1, 2\n'


def test_a_pre_existing_syntax_error_alone_is_still_nothing_to_revert(codex_dir):
    """Order matters: with no block of ours, a broken line is none of our business.

    Refusing here would turn every malformed config.toml on the fleet into a `revert`
    that exits non-zero — and `verify --post-teardown` only sends users here when there
    IS residue.
    """
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_text('oops = [1, 2\n', encoding="utf-8")

    result = CliRunner().invoke(codex_group, ["revert"])

    assert result.exit_code == 0, result.output
    assert "nothing to revert" in result.output.lower()


# ---------------------------------------------------------------------------
# Virtual Key resolution
# ---------------------------------------------------------------------------

class _FakeVK:
    def __init__(self, key="vk-cached", ttl=3600, issuer="https://idp", admin="https://admin"):
        self.virtual_key = key
        self.expires_at = time.time() + ttl
        self.issuer_url = issuer
        self.admin_api_url = admin
        self.user_id = "u"
        self.team_id = "t"

    def is_expiring(self, threshold_seconds=300):
        return time.time() + threshold_seconds >= self.expires_at


@pytest.fixture()
def oidc(monkeypatch):
    """Stub gateway_cli_oidc.oidc_client — cli.codex imports it lazily, by name."""
    import gateway_cli_oidc.oidc_client as mod

    state = {"saved": [], "exchanges": 0, "id_token": "id-token"}

    class _Cfg:
        issuer_url = "https://idp"
        client_id = "client"
        admin_api_url = "https://admin"

    monkeypatch.setattr(mod, "load_oidc_config_from_env", lambda: _Cfg())
    monkeypatch.setattr(mod, "load_vk_cache", lambda: state.get("cached"))
    monkeypatch.setattr(mod, "save_vk_cache", lambda vk: state["saved"].append(vk))
    monkeypatch.setattr(mod, "get_valid_id_token", lambda cfg: state["id_token"])

    class _Resp:
        virtual_key = "vk-fresh"
        user_id = "u"
        team_id = "t"

        class expires_at:  # noqa: N801 — stands in for a datetime
            @staticmethod
            def timestamp():
                return time.time() + 3600

    def _exchange(admin_api_url, token, device):
        state["exchanges"] += 1
        state["last_exchange"] = (admin_api_url, token, device)
        return _Resp()

    monkeypatch.setattr(mod, "exchange_jwt_for_vk", _exchange)
    return state


def test_a_healthy_cached_vk_is_reused(oidc):
    """Claude Code and Codex share one cached VK — and therefore one identity.

    Minting a second key per launch would be extra load and, worse, would make the
    two tools' keys diverge while both spend the same user's budget.
    """
    oidc["cached"] = _FakeVK()
    key, _expires, minted = codex.resolve_virtual_key()
    assert (key, minted) == ("vk-cached", False)
    assert oidc["exchanges"] == 0


def test_an_expiring_vk_is_re_minted_and_cached(oidc):
    # 10 minutes left, threshold is 30: a Codex session inherits the key it starts
    # with for its whole life, so "still valid" is not good enough.
    oidc["cached"] = _FakeVK(ttl=600)
    key, _expires, minted = codex.resolve_virtual_key()
    assert (key, minted) == ("vk-fresh", True)
    assert oidc["exchanges"] == 1
    assert oidc["saved"] and oidc["saved"][0].virtual_key == "vk-fresh"


def test_the_refresh_threshold_is_far_wider_than_the_helpers(oidc):
    """Pin the value, not just the behaviour: api-key-helper uses 300s because it is
    re-invoked per request. A wrapper that execs once cannot refresh later, so a
    5-minute margin would routinely hand Codex a key that dies mid-session."""
    assert codex.VK_REFRESH_THRESHOLD_SECONDS == 1800


def test_a_vk_from_a_different_idp_is_not_reused(oidc):
    """A cache entry from another issuer/admin-api is not ours to spend."""
    oidc["cached"] = _FakeVK(issuer="https://other-idp")
    _key, _expires, minted = codex.resolve_virtual_key()
    assert minted is True


def test_missing_id_token_tells_the_user_to_log_in(oidc, monkeypatch):
    import gateway_cli_oidc.oidc_client as mod

    monkeypatch.setattr(mod, "get_valid_id_token", lambda cfg: "")
    with pytest.raises(CodexStepError, match="gateway-cli login"):
        codex.resolve_virtual_key()


def test_the_exchange_uses_the_id_token_not_the_access_token(oidc):
    """Cognito puts email/name/groups in the id_token only, and admin-api provisions
    the user from those claims — an access_token exchange 4xxs or mis-provisions."""
    oidc["cached"] = None
    codex.resolve_virtual_key()
    admin_api_url, token, _device = oidc["last_exchange"]
    assert token == "id-token"
    assert admin_api_url == "https://admin"


# ---------------------------------------------------------------------------
# `codex run`
# ---------------------------------------------------------------------------

@pytest.fixture()
def fake_codex_bin(monkeypatch):
    monkeypatch.setattr(codex, "resolve_codex_bin", lambda: "/usr/bin/codex")


@pytest.fixture()
def routed_config(codex_dir):
    """A config.toml that actually selects the gateway — i.e. what `setup` writes.

    `run` refuses to mint a VK without one, so every run test needs it except the one
    that asserts the refusal. Before that check existed these tests passed with NO
    config file at all, which is exactly the state `run` must not serve a key into.
    """
    codex_dir.mkdir(parents=True, exist_ok=True)
    path = codex_dir / "config.toml"
    path.write_text(_rendered(), encoding="utf-8")
    return path


def test_run_passes_the_vk_to_the_child_only(routed_config, oidc, fake_codex_bin, monkeypatch):
    oidc["cached"] = _FakeVK()
    captured = {}

    def _exec(argv, env):
        captured["argv"] = argv
        captured["env"] = env
        return 0

    monkeypatch.setattr(codex, "exec_codex", _exec)
    result = CliRunner().invoke(codex_group, ["run", "--", "exec", "hello"])

    assert result.exit_code == 0, result.output
    assert captured["argv"] == ["/usr/bin/codex", "exec", "hello"]
    assert captured["env"][DEFAULT_ENV_KEY] == "vk-cached"
    # The key must never reach the terminal — a VK in scrollback or CI logs is a
    # credential leak, and this wrapper is the only thing between it and stdout.
    assert "vk-cached" not in result.output
    # …nor leak into the parent process, which keeps running after codex exits.
    assert DEFAULT_ENV_KEY not in __import__("os").environ


def test_run_passes_unknown_options_through_to_codex(routed_config, oidc, fake_codex_bin, monkeypatch):
    """`--model` and friends belong to codex here, not to us: ignore_unknown_options
    is what stops `gateway-cli codex run --help-ish-codex-flag` being eaten."""
    oidc["cached"] = _FakeVK()
    captured = {}
    monkeypatch.setattr(codex, "exec_codex", lambda argv, env: captured.setdefault("argv", argv) and 0)
    result = CliRunner().invoke(codex_group, ["run", "--sandbox", "read-only", "hi"])
    assert result.exit_code == 0, result.output
    assert captured["argv"][1:] == ["--sandbox", "read-only", "hi"]


def test_run_propagates_the_child_exit_code(routed_config, oidc, fake_codex_bin, monkeypatch):
    """The wrapper must be transparent to scripts and CI, or a failed `codex exec`
    reads as success."""
    oidc["cached"] = _FakeVK()
    monkeypatch.setattr(codex, "exec_codex", lambda argv, env: 3)
    result = CliRunner().invoke(codex_group, ["run"])
    assert result.exit_code == 3


def test_run_uses_the_env_key_the_config_declares(codex_dir, oidc, fake_codex_bin, monkeypatch):
    oidc["cached"] = _FakeVK()
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_text(
        _rendered().replace(DEFAULT_ENV_KEY, "CUSTOM_VK"), encoding="utf-8"
    )
    captured = {}
    monkeypatch.setattr(codex, "exec_codex", lambda argv, env: captured.setdefault("env", env) and 0)
    result = CliRunner().invoke(codex_group, ["run"])
    assert result.exit_code == 0, result.output
    assert captured["env"]["CUSTOM_VK"] == "vk-cached"
    assert DEFAULT_ENV_KEY not in captured["env"]


def test_run_dry_run_launches_nothing(routed_config, oidc, fake_codex_bin, monkeypatch):
    oidc["cached"] = _FakeVK()
    monkeypatch.setattr(
        codex, "exec_codex", lambda argv, env: pytest.fail("dry run must not exec")
    )
    result = CliRunner().invoke(codex_group, ["run", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would exec" in result.output
    assert "vk-cached" not in result.output


def test_the_dry_run_verb_does_not_promise_exec_on_windows(
    routed_config, oidc, fake_codex_bin, monkeypatch
):
    """A dry run has to describe what will really happen on THIS platform.

    Windows has no exec: `run` stays in the process tree and waits (see exec_codex), so
    "would exec" was false exactly where a reader is most likely to be checking whether
    a wrapper process survives — the platform whose Ctrl-C and %ERRORLEVEL% behaviour
    depends on that answer.
    """
    monkeypatch.setattr(
        codex, "exec_codex", lambda argv, env: pytest.fail("dry run must not launch")
    )
    oidc["cached"] = _FakeVK()
    monkeypatch.setattr(codex.sys, "platform", "win32")

    result = CliRunner().invoke(codex_group, ["run", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would run:" in result.output
    assert "exec" not in result.output


def test_run_reports_a_missing_codex_binary_as_a_clean_error(routed_config, oidc, monkeypatch):
    oidc["cached"] = _FakeVK()
    monkeypatch.setattr(codex, "shutil", type("S", (), {"which": staticmethod(lambda c: None)}))
    result = CliRunner().invoke(codex_group, ["run"])
    assert result.exit_code != 0
    assert "GATEWAY_CLI_CODEX_BIN" in result.output


def test_run_refuses_when_no_config_exists(codex_dir, oidc, fake_codex_bin, monkeypatch):
    """No config = setup was never run. Do not mint a key for a file that routes nowhere.

    The old behaviour was to mint one and print "Virtual Key: cached … Passed as:
    $GATEWAY_VK", which reads as success on a machine that is not configured at all.
    """
    oidc["cached"] = _FakeVK()
    monkeypatch.setattr(codex, "exec_codex", lambda argv, env: pytest.fail("must not exec"))
    result = CliRunner().invoke(codex_group, ["run"])
    assert result.exit_code != 0
    assert "does not route Codex to the gateway" in result.output
    assert oidc["exchanges"] == 0, "no VK may be minted before the routing check"


def test_run_refuses_when_the_config_selects_another_provider(
    codex_dir, oidc, fake_codex_bin, monkeypatch
):
    """The security case: a foreign selection would RECEIVE the gateway's Virtual Key.

    Our block can sit in the file while `model_provider` points elsewhere (the user
    switched back, or hand-edited). Exporting the VK then hands a gateway credential to
    whatever endpoint that provider names, as its API key — so refuse, and mint nothing.
    """
    oidc["cached"] = _FakeVK()
    codex_dir.mkdir(parents=True, exist_ok=True)
    (codex_dir / "config.toml").write_text(
        _rendered().replace('model_provider = "gateway"', 'model_provider = "openai"')
        + '\n[model_providers.openai]\nname = "OpenAI"\nenv_key = "OPENAI_API_KEY"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(codex, "exec_codex", lambda argv, env: pytest.fail("must not exec"))
    result = CliRunner().invoke(codex_group, ["run"])
    assert result.exit_code != 0
    assert "does not route Codex to the gateway" in result.output
    assert oidc["exchanges"] == 0
    assert "vk-cached" not in result.output


def test_setup_does_not_inherit_another_providers_env_key(codex_dir, monkeypatch):
    """`setup` on a config that selects another provider must keep OUR variable name.

    Inheriting it wrote e.g. `env_key = "OPENAI_API_KEY"` into our block, and `run`
    then exported the gateway VK under that name — inherited by every tool and MCP
    server Codex spawns.
    """
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    codex_dir.mkdir(parents=True, exist_ok=True)
    (codex_dir / "config.toml").write_text(
        'model_provider = "openai"\n\n[model_providers.openai]\n'
        'name = "OpenAI"\nenv_key = "OPENAI_API_KEY"\n',
        encoding="utf-8",
    )
    result = CliRunner().invoke(codex_group, ["setup"])
    assert result.exit_code == 0, result.output
    written = (codex_dir / "config.toml").read_text(encoding="utf-8")
    assert f"env_key = \"{DEFAULT_ENV_KEY}\"" in written
    assert "OPENAI_API_KEY" not in written.split("[model_providers.gateway]", 1)[1]
    assert f"  env_key:      {DEFAULT_ENV_KEY}" in result.output


def test_strip_preserves_an_unrelated_hyphenated_sibling_provider(tmp_path):
    """`model_providers.gateway-prod.…` is NOT our table — a `\\b` regex ate it.

    The dotted-key branch used a word boundary, which fires between `gateway` and `-`,
    so an unrelated provider declared with top-level dotted keys was deleted outright.
    The result stayed valid TOML and `validate_config` only re-checks our own five keys,
    so `setup`/`revert` reported success while silently removing someone else's config.
    The header branch always got this right (it preserves `[model_providers.gateway-prod]`),
    and the two disagreeing is what proved it was a bug rather than a policy.
    """
    text = (
        'model_providers.gateway-prod.name = "Prod GW"\n'
        'model_providers.gateway-prod.base_url = "https://prod.example.com/v1"\n'
        'model_providers.gateway_dev.env_key = "DEV_VK"\n'
        'model_providers.gateway.base_url = "https://ours.example.com/v1"\n'
        'approval_policy = "never"\n'
    )
    stripped = strip_gateway_config(text)
    assert "gateway-prod" in stripped
    assert "gateway_dev" in stripped
    assert 'approval_policy = "never"' in stripped
    # ...while OUR dotted-key form is still removed, which is what the branch is for.
    assert "https://ours.example.com/v1" not in stripped


def test_strip_still_removes_our_dotted_inline_table(tmp_path):
    """The replacement predicate must accept `=` too, not only a further `.`."""
    stripped = strip_gateway_config(
        'model_providers.gateway = { name = "x", base_url = "y" }\n'
        'model_providers.gateway-prod = { name = "keep" }\n'
    )
    assert 'base_url = "y"' not in stripped
    assert "keep" in stripped


def test_codex_bin_override_is_honoured(tmp_path, monkeypatch):
    """An override with a directory component is taken as the answer, made absolute.

    Checked against a real file rather than a stubbed `shutil.which`: for a command that
    already has a dirname, 3.11's `which` only access-checks that exact name, so a stub
    returning a *different* path was asserting behaviour the stdlib never had.
    """
    real = tmp_path / "opt" / "codex" / "bin" / "codex"
    real.parent.mkdir(parents=True)
    real.write_text("#!/bin/sh\n", encoding="utf-8")
    real.chmod(0o755)
    monkeypatch.setenv("GATEWAY_CLI_CODEX_BIN", str(real))

    assert codex.resolve_codex_bin() == str(real)


def test_a_relative_override_is_resolved_to_an_absolute_path(tmp_path, monkeypatch):
    """So what we resolved is what we launch, even if something chdirs in between."""
    real = tmp_path / "tools" / "codex"
    real.parent.mkdir(parents=True)
    real.write_text("#!/bin/sh\n", encoding="utf-8")
    real.chmod(0o755)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("GATEWAY_CLI_CODEX_BIN", "tools/codex")

    resolved = codex.resolve_codex_bin()
    assert os.path.isabs(resolved)
    assert os.path.samefile(resolved, real)


def test_a_codex_in_the_current_directory_is_never_the_answer(tmp_path, monkeypatch):
    """The fallback that used to do this handed a live Virtual Key to whatever file was
    sitting in the directory the user happened to be standing in.

    Asserted on POSIX as well as Windows: the removed fallback was platform-independent,
    so `_which_windows` closed the front door while this left the back one open.
    """
    monkeypatch.chdir(tmp_path)
    plant = tmp_path / "codex"
    plant.write_text("#!/bin/sh\necho pwned\n", encoding="utf-8")
    plant.chmod(0o755)
    monkeypatch.delenv("GATEWAY_CLI_CODEX_BIN", raising=False)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("PATHEXT", WIN_PATHEXT)

    for platform in ("linux", "win32"):
        monkeypatch.setattr(codex.sys, "platform", platform)
        with pytest.raises(CodexStepError) as excinfo:
            codex.resolve_codex_bin()
        assert "on PATH" in str(excinfo.value), platform


def test_an_extensionless_override_finds_the_cmd_shim_on_windows(tmp_path, monkeypatch):
    """`where codex` prints `codex.cmd`, but a path typed from memory omits the extension.

    3.11's `shutil.which` gives a dirname'd command no PATHEXT help, so the override
    failed with "could not find the 'codex' executable" while pointing right at it.
    """
    # _plant, not a bare write: the probe asks for the PATHEXT spelling (`codex.CMD`), so
    # on a case-sensitive filesystem that name has to exist. See its docstring.
    shim = _plant(tmp_path / "npm", "codex.cmd")
    monkeypatch.setattr(codex.sys, "platform", "win32")
    monkeypatch.setenv("PATHEXT", WIN_PATHEXT)
    monkeypatch.setenv("GATEWAY_CLI_CODEX_BIN", str(tmp_path / "npm" / "codex"))

    assert _same_path(codex.resolve_codex_bin(), shim)


def test_an_extensionless_override_is_not_tried_on_posix(tmp_path, monkeypatch):
    """PATHEXT is a Windows concept — appending `.EXE` on Linux would be a wrong guess."""
    shim = tmp_path / "npm" / "codex.cmd"
    shim.parent.mkdir(parents=True)
    shim.write_text("@echo off\n", encoding="utf-8")
    shim.chmod(0o755)
    monkeypatch.setattr(codex.sys, "platform", "linux")
    monkeypatch.setenv("PATHEXT", WIN_PATHEXT)
    monkeypatch.setenv("GATEWAY_CLI_CODEX_BIN", str(tmp_path / "npm" / "codex"))

    with pytest.raises(CodexStepError) as excinfo:
        codex.resolve_codex_bin()
    assert "not an executable file" in str(excinfo.value)
    assert ".cmd" not in str(excinfo.value)   # no Windows hint on a POSIX box


def test_a_bad_override_names_itself_in_the_error(tmp_path, monkeypatch):
    """The message must point at the variable, not at "install Codex CLI" — the user
    already did, and mistyped where."""
    monkeypatch.setattr(codex.sys, "platform", "linux")
    monkeypatch.setenv("GATEWAY_CLI_CODEX_BIN", str(tmp_path / "nope" / "codex"))

    with pytest.raises(CodexStepError) as excinfo:
        codex.resolve_codex_bin()
    assert codex.CODEX_BIN_ENV in str(excinfo.value)
    assert "not an executable file" in str(excinfo.value)


# ---------------------------------------------------------------------------
# `codex status`
# ---------------------------------------------------------------------------

def test_status_reports_not_configured_without_a_file(codex_dir, oidc):
    result = CliRunner().invoke(codex_group, ["status"])
    assert result.exit_code == 0
    assert "NOT CONFIGURED" in result.output


def test_status_reports_the_routed_state(codex_dir, oidc, monkeypatch):
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    oidc["cached"] = _FakeVK()
    runner = CliRunner()
    assert runner.invoke(codex_group, ["setup"]).exit_code == 0
    result = runner.invoke(codex_group, ["status"])
    assert result.exit_code == 0, result.output
    assert "ON gateway" in result.output
    assert BASE_URL in result.output
    # Read-only: status must be safe offline, so it never mints.
    assert oidc["exchanges"] == 0
    # And it never prints the key it found in the cache.
    assert "vk-cached" not in result.output


def test_status_flags_a_config_that_is_not_on_the_gateway(codex_dir, oidc):
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_text('model = "gpt-5.5"\n', encoding="utf-8")
    result = CliRunner().invoke(codex_group, ["status"])
    assert result.exit_code == 0
    assert "NOT on the gateway" in result.output


def test_status_survives_an_unreadable_config(codex_dir, oidc):
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_text("broken = = =\n", encoding="utf-8")
    result = CliRunner().invoke(codex_group, ["status"])
    assert result.exit_code == 0
    assert "UNREADABLE" in result.output


# ---------------------------------------------------------------------------
# Windows: config encoding (WIN-1)
# ---------------------------------------------------------------------------
# The target box is a ko-KR Windows machine. Two encodings reach config.toml there
# that never reach a mac: a UTF-8 BOM (PowerShell 5.1 `Set-Content -Encoding UTF8`,
# pre-2019 Notepad) and CP949 "ANSI" (Notepad's default on that locale, easy to hit
# because the hand-edit snippet in docs/guides/codex.md carries Korean comments).

def test_setup_survives_a_utf8_bom_and_does_not_duplicate_the_model_key(
    codex_dir, oidc, monkeypatch
):
    r"""A BOM must not hide the user's own `model =` line from the splice.

    Read as strict utf-8 the BOM is a hard failure; read as utf-8 with the BOM left in
    place it is worse than that, because the BOM is not whitespace (`re.match(r"\s",
    "\ufeff")` is None) so `_top_key_re` cannot match the line behind it — the splice
    then emits a SECOND top-level `model` key, which is a TOML duplicate-key error and
    a Codex that refuses to start.
    """
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    codex_dir.mkdir(parents=True)
    cfg = codex_dir / "config.toml"
    cfg.write_bytes(b"\xef\xbb\xbf" + b'model = "gpt-5.6-sol"\napproval_policy = "never"\n')

    result = CliRunner().invoke(codex_group, ["setup"])

    assert result.exit_code == 0, result.output
    text = cfg.read_text(encoding="utf-8-sig")
    data = tomllib.loads(text)  # would raise on a duplicate `model`
    assert data["model"] == DEFAULT_CODEX_MODEL
    assert len([ln for ln in text.splitlines() if ln.startswith("model =")]) == 1
    assert data["approval_policy"] == "never"  # unrelated setting kept
    assert not text.startswith("\ufeff")  # we write bom-less


def test_setup_names_the_encoding_and_the_fix_for_a_cp949_config(
    codex_dir, oidc, monkeypatch
):
    """A non-UTF-8 file gets its own diagnosis, not the OSError "move it aside" advice."""
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_bytes("# 한글 주석\nmodel = \"x\"\n".encode("cp949"))

    result = CliRunner().invoke(codex_group, ["setup"])

    assert result.exit_code != 0
    assert "not UTF-8" in result.output
    assert "Set-Content -Encoding utf8" in result.output  # runnable remedy
    assert "byte" in result.output                        # says where it broke


def test_setup_repairs_a_bom_on_an_otherwise_identical_config(codex_dir, oidc, monkeypatch):
    """"already up to date" must not be said about a file Codex cannot parse.

    The comparison behind that branch is on DECODED text, and CONFIG_READ_ENCODING strips
    the BOM — so a config carrying our block plus a BOM matched, `setup` declared success,
    and the three bytes that make tomllib fail at line 1 column 1 stayed on disk. Nothing
    the user could do fixed it: re-running setup kept agreeing everything was fine.
    """
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    codex_dir.mkdir(parents=True)
    cfg = codex_dir / "config.toml"
    runner = CliRunner()
    assert runner.invoke(codex_group, ["setup"]).exit_code == 0
    cfg.write_bytes(b"\xef\xbb\xbf" + cfg.read_bytes())  # PS 5.1 `Set-Content -Encoding utf8`
    assert cfg.read_bytes().startswith(b"\xef\xbb\xbf")

    result = runner.invoke(codex_group, ["setup"])

    assert result.exit_code == 0, result.output
    assert "already up to date" not in result.output
    assert "byte-order mark" in result.output  # says WHY it rewrote a file that looked same
    assert not cfg.read_bytes().startswith(b"\xef\xbb\xbf")
    tomllib.loads(cfg.read_text(encoding="utf-8"))  # strict utf-8: a BOM would raise


def test_setup_still_reports_no_change_for_a_crlf_config(codex_dir, oidc, monkeypatch):
    """The BOM check must not turn into a byte comparison.

    Path.write_text translates \\n to \\r\\n on Windows, so the file we wrote never matches
    the text we rendered byte-for-byte there. Comparing bytes would make every single
    re-run "changed" — rewriting the file and taking a fresh snapshot forever, on Windows
    only, where none of these tests run.
    """
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    codex_dir.mkdir(parents=True)
    cfg = codex_dir / "config.toml"
    runner = CliRunner()
    assert runner.invoke(codex_group, ["setup"]).exit_code == 0
    cfg.write_bytes(cfg.read_text(encoding="utf-8").replace("\n", "\r\n").encode("utf-8"))

    result = runner.invoke(codex_group, ["setup"])

    assert result.exit_code == 0, result.output
    assert "already up to date" in result.output
    assert cfg.read_bytes().count(b"\r\n") > 0  # left exactly as the user's editor had it


def test_the_cp949_remedy_is_runnable_on_both_powershell_versions(tmp_path):
    """The remedy is a command a user pastes — it has to survive contact with PowerShell.

    Three separate ways the old one-liner
    (`Get-Content -Encoding Default <f> | Set-Content -Encoding utf8 <f>`) was wrong:
    a same-file pipe truncates the file; `-Encoding Default` does not exist on PS 7; and
    PS 5.1's `-Encoding utf8` writes a BOM, i.e. it produced the very state the branch
    above had to learn to repair.
    """
    path = tmp_path / "config.toml"
    try:
        "# 한글\n".encode("cp949").decode("utf-8")
    except UnicodeDecodeError as exc:
        message = codex.not_utf8_message(path, exc)

    assert str(path) in message
    assert "not UTF-8" in message and "byte" in message
    # Version-split, and each line valid only for the version it is labelled with.
    ps51 = next(ln for ln in message.splitlines() if "PS 5.1:" in ln)
    ps7 = next(ln for ln in message.splitlines() if "PS 7:" in ln)
    assert "-Encoding Default" in ps51 and "Default" not in ps7
    assert "-Encoding 949" in ps7 and "utf8NoBOM" in ps7
    # Both read the whole file into a variable before writing it back.
    assert "-Raw" in ps51 and "-Raw" in ps7
    for line in (ps51, ps7):
        assert "|" not in line, "a same-file Get-Content|Set-Content pipe truncates it"
    assert "Notepad" in message  # the fix that needs no version reasoning


def test_revert_diagnoses_a_cp949_config_instead_of_the_raw_codec_error(
    codex_dir, oidc, monkeypatch
):
    """`verify --post-teardown` sends the user here, so a dead end here strands teardown."""
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_bytes("# 한글 주석\nmodel = \"x\"\n".encode("cp949"))

    result = CliRunner().invoke(codex_group, ["revert"])

    assert result.exit_code != 0
    assert "not UTF-8" in result.output
    assert "Set-Content" in result.output
    assert "'utf-8' codec can't decode" not in result.output  # not the bare exception


def test_status_diagnoses_a_cp949_config_instead_of_the_raw_codec_error(
    codex_dir, oidc, monkeypatch
):
    """status is where a user lands when something is wrong — it must not dead-end."""
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").write_bytes("# 한글 주석\nmodel = \"x\"\n".encode("cp949"))

    result = CliRunner().invoke(codex_group, ["status"])

    assert result.exit_code == 0  # read-only command: report, don't crash
    assert "UNREADABLE" in result.output
    assert "not UTF-8" in result.output
    assert "Set-Content" in result.output


def test_status_and_revert_read_a_bom_config(codex_dir, oidc, monkeypatch):
    """Every read path takes the same encoding — not just setup's."""
    monkeypatch.setattr(codex, "default_base_url", lambda: BASE_URL)
    codex_dir.mkdir(parents=True)
    cfg = codex_dir / "config.toml"
    runner = CliRunner()
    assert runner.invoke(codex_group, ["setup"]).exit_code == 0
    cfg.write_bytes(b"\xef\xbb\xbf" + cfg.read_bytes())  # user re-saves it with a BOM

    status = runner.invoke(codex_group, ["status"])
    assert status.exit_code == 0
    assert "ON gateway" in status.output
    assert "UNREADABLE" not in status.output

    revert = runner.invoke(codex_group, ["revert"])
    assert revert.exit_code == 0, revert.output
    assert f"[model_providers.{PROVIDER_KEY}]" not in cfg.read_text(encoding="utf-8-sig")


# ---------------------------------------------------------------------------
# config_has_gateway_block — the teardown-facing detector
# ---------------------------------------------------------------------------

def _write_cfg(codex_dir, text, *, encoding="utf-8"):
    codex_dir.mkdir(parents=True, exist_ok=True)
    path = codex_dir / "config.toml"
    path.write_bytes(text.encode(encoding))
    return path


def test_block_detector_finds_the_table_in_either_spelling(codex_dir):
    _write_cfg(codex_dir, f"[model_providers.{PROVIDER_KEY}]\nname = \"x\"\n")
    assert codex.config_has_gateway_block() is True

    _write_cfg(codex_dir, f'[ model_providers."{PROVIDER_KEY}" ]\nname = "x"\n')
    assert codex.config_has_gateway_block() is True

    _write_cfg(codex_dir, f'model_providers.{PROVIDER_KEY}.base_url = "https://x/v1"\n')
    assert codex.config_has_gateway_block() is True


def test_block_detector_ignores_a_file_that_is_not_ours(codex_dir):
    """No false positives: a user's own model key or a similarly-named provider."""
    _write_cfg(codex_dir, 'model = "gpt-5.6-sol"\nmodel_provider = "openai"\n')
    assert codex.config_has_gateway_block() is False

    # gateway-prod is somebody else's provider — the same trap the stripper guards.
    _write_cfg(codex_dir, f"[model_providers.{PROVIDER_KEY}-prod]\nname = \"x\"\n")
    assert codex.config_has_gateway_block() is False


def test_block_detector_matches_the_strippers_preamble_scope(codex_dir):
    """Dotted form under another table is not ours — revert leaves it, so must we.

    A detector that counted it would report residue that no `codex revert` can clear.
    """
    text = f'[profiles.work]\nmodel_providers.{PROVIDER_KEY}.base_url = "https://x/v1"\n'
    _write_cfg(codex_dir, text)
    assert codex.config_has_gateway_block() is False
    assert strip_gateway_config(text) == text  # the remover agrees


def test_block_detector_sees_through_an_undecodable_file(codex_dir):
    """A config that breaks tomllib/UTF-8 is when the snapshot matters most."""
    _write_cfg(
        codex_dir,
        f"# 한글 주석\n[model_providers.{PROVIDER_KEY}]\nname = \"x\"\n",
        encoding="cp949",
    )
    assert codex.config_has_gateway_block() is True

    _write_cfg(codex_dir, f"[model_providers.{PROVIDER_KEY}]\nbroken = = =\n")
    assert codex.config_has_gateway_block() is True  # invalid TOML, still our text


def test_block_detector_is_false_without_a_file(codex_dir):
    assert codex.config_has_gateway_block() is False


def test_block_detector_propagates_an_unreadable_file(codex_dir):
    """"absent" and "unreadable" are different answers.

    Returning False for *any* OSError made teardown's `except OSError: return True`
    unreachable — so an unreadable config.toml (an ACL denial, a sharing violation while
    Codex holds the file, a OneDrive Files-On-Demand placeholder) got its snapshot swept
    with our block quite possibly still inside. Only a missing file may answer False; the
    caller's safe default is the caller's to choose.
    """
    codex_dir.mkdir(parents=True)
    (codex_dir / "config.toml").mkdir()  # a directory where a file belongs
    with pytest.raises(OSError):
        codex.config_has_gateway_block()


# ---------------------------------------------------------------------------
# Windows: PATH resolution (WIN-2)
# ---------------------------------------------------------------------------
# shutil.which searches os.curdir FIRST on Windows, unconditionally on 3.11
# (NoDefaultCurrentDirectoryInExePath only gates it from 3.12, and passing path=
# does not suppress the insert). `codex run` puts a live Virtual Key in the resolved
# process's environment, so a codex.cmd in whatever directory the user happened to
# cd into would receive a working, budget-spending gateway credential.

WIN_PATHEXT = ".COM;.EXE;.BAT;.CMD"


def _same_path(found, expected):
    """Compare paths the way Windows would — case-insensitively.

    `_which_windows` appends the extension exactly as PATHEXT spells it (".CMD"), which
    is what `shutil.which` does too, so on the real box the resolved path is
    `...\\npm\\codex.CMD` for an on-disk `codex.cmd`. Harmless there (the filesystem is
    case-insensitive), but these tests run on a POSIX box where `os.path.normcase` is a
    no-op, so the folding has to be explicit.
    """
    return found is not None and str(found).lower() == str(expected).lower()


def _pathext_cased(path):
    """``codex.cmd`` → ``codex.CMD`` — the spelling `_which_windows` actually probes for."""
    stem, dot, ext = path.name.rpartition(".")
    return path.with_name(f"{stem}{dot}{ext.upper()}") if dot else path


def _plant(directory, name):
    """Create an executable stub at ``directory/name``, plus its PATHEXT-cased twin.

    `_which_windows` builds candidate names by appending each PATHEXT entry exactly as
    the variable spells it — upper case, as on a real box — so for a `codex.cmd` from
    npm it asks the filesystem about `codex.CMD`. Windows answers yes, and so does the
    case-insensitive APFS volume these tests usually run on (verified: `os.path.isfile`
    finds `codex.CMD` for a planted `codex.cmd`). ext4 and a case-sensitive APFS volume
    answer no — and then every assertion below fails for a reason that has nothing to do
    with cwd precedence, PATHEXT order, or explicit extensions, which is what they are
    actually about. Planting both spellings makes the probe hit on either filesystem.

    The production code needs no matching change: on Windows the OS folds the case, and
    the one place our own code compares extensions (`cmd.lower().endswith(ext.lower())`)
    already folds explicitly — see the two tests at the end of this section.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    for variant in (path, _pathext_cased(path)):
        variant.write_text("@echo off\n", encoding="utf-8")
        variant.chmod(0o755)
    return path


def test_which_windows_never_picks_the_current_directory(tmp_path, monkeypatch):
    cwd, path_dir = tmp_path / "project", tmp_path / "npm"
    plant = _plant(cwd, "codex.cmd")
    real = _plant(path_dir, "codex.cmd")
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("PATH", str(path_dir))
    monkeypatch.setenv("PATHEXT", WIN_PATHEXT)

    # normcase: the ext is appended exactly as PATHEXT spells it (".CMD"), which is
    # what shutil.which does too — Windows paths compare case-insensitively.
    assert _same_path(codex._which_windows("codex"), real)
    assert str(plant) != str(real)  # the plant really was there to be found


def test_which_windows_returns_none_when_only_the_cwd_has_it(tmp_path, monkeypatch):
    """The dangerous case in isolation: nothing on PATH, a plant in the cwd."""
    cwd = tmp_path / "project"
    _plant(cwd, "codex.cmd")
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    monkeypatch.setenv("PATHEXT", WIN_PATHEXT)

    assert codex._which_windows("codex") is None


def test_which_windows_follows_pathext_order(tmp_path, monkeypatch):
    """A native .exe wins over the .cmd shim, as the OS would resolve it."""
    path_dir = tmp_path / "npm"
    _plant(path_dir, "codex.cmd")
    exe = _plant(path_dir, "codex.exe")
    monkeypatch.setenv("PATH", str(path_dir))
    monkeypatch.setenv("PATHEXT", WIN_PATHEXT)

    assert _same_path(codex._which_windows("codex"), exe)


def test_which_windows_honours_an_explicit_extension(tmp_path, monkeypatch):
    """`codex.cmd` asks for that name only — which's rule when the ext is known."""
    path_dir = tmp_path / "npm"
    _plant(path_dir, "codex.exe")
    monkeypatch.setenv("PATH", str(path_dir))
    monkeypatch.setenv("PATHEXT", WIN_PATHEXT)

    assert codex._which_windows("codex.cmd") is None
    assert _same_path(codex._which_windows("codex.exe"), path_dir / "codex.exe")


def test_which_windows_folds_case_when_matching_an_explicit_extension(tmp_path, monkeypatch):
    """`codex.CMD` counts as already-extended against a lower-case PATHEXT entry.

    This is the one case decision our own code makes rather than delegating to the
    filesystem, so it is testable on any filesystem: were the fold dropped, `.CMD` would
    look unknown and the probe would ask for `codex.CMD.cmd`.
    """
    path_dir = tmp_path / "npm"
    path_dir.mkdir(parents=True)
    target = path_dir / "codex.CMD"
    target.write_text("@echo off\n", encoding="utf-8")
    target.chmod(0o755)
    monkeypatch.setenv("PATH", str(path_dir))
    monkeypatch.setenv("PATHEXT", ".com;.exe;.cmd")  # lower case, unlike the real one

    assert codex._which_windows("codex.CMD") == str(target)


def test_which_windows_probes_the_pathext_spelling(tmp_path, monkeypatch):
    """Names are built with PATHEXT's own casing — the assumption `_plant` compensates for.

    Pinned deliberately: it is why a case-sensitive filesystem needs both spellings on
    disk, and if the lookup ever lower-cased its candidates this test would say so
    instead of leaving the rest of the section mysteriously green only on macOS.
    """
    path_dir = tmp_path / "npm"
    path_dir.mkdir(parents=True)
    upper = path_dir / "codex.CMD"
    upper.write_text("@echo off\n", encoding="utf-8")
    upper.chmod(0o755)
    monkeypatch.setenv("PATH", str(path_dir))
    monkeypatch.setenv("PATHEXT", ".CMD")

    assert codex._which_windows("codex") == str(upper)


def test_resolve_codex_bin_uses_the_windows_scan_on_win32(tmp_path, monkeypatch):
    """The end-to-end wiring: the plant loses even though which would prefer it."""
    cwd, path_dir = tmp_path / "project", tmp_path / "npm"
    _plant(cwd, "codex.cmd")
    real = _plant(path_dir, "codex.cmd")
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("PATH", str(path_dir))
    monkeypatch.setenv("PATHEXT", WIN_PATHEXT)
    monkeypatch.delenv("GATEWAY_CLI_CODEX_BIN", raising=False)
    monkeypatch.setattr(codex.sys, "platform", "win32")

    assert _same_path(codex.resolve_codex_bin(), real)


def test_resolve_codex_bin_still_honours_a_path_override_on_win32(tmp_path, monkeypatch):
    """The escape hatch survives: an override WITH a dirname is taken literally."""
    override = _plant(tmp_path / "custom", "codex.exe")
    monkeypatch.setenv("GATEWAY_CLI_CODEX_BIN", str(override))
    monkeypatch.setattr(codex.sys, "platform", "win32")

    assert codex.resolve_codex_bin() == str(override)


# ---------------------------------------------------------------------------
# Windows: cmd.exe re-parse warning (WIN-3)
# ---------------------------------------------------------------------------

def test_reparse_risk_is_silent_on_posix():
    assert codex.cmd_reparse_risk("/usr/bin/codex", ["exec", "why is %HOME% empty"]) is None


def test_reparse_risk_warns_for_a_batch_shim_with_a_percent_arg(monkeypatch):
    monkeypatch.setattr(codex.sys, "platform", "win32")
    warning = codex.cmd_reparse_risk(
        r"C:\Users\me\AppData\Roaming\npm\codex.cmd", ["exec", "why is %APPDATA% empty"]
    )
    assert warning is not None
    assert "%APPDATA%" in warning              # names the affected argument
    assert "codex.cmd" in warning
    assert codex.CODEX_BIN_ENV in warning      # and the way out


def test_reparse_risk_is_silent_for_a_native_exe_or_clean_args(monkeypatch):
    monkeypatch.setattr(codex.sys, "platform", "win32")
    assert codex.cmd_reparse_risk(r"C:\codex\codex.exe", ["exec", "100%% sure"]) is None
    assert codex.cmd_reparse_risk(r"C:\npm\codex.cmd", ["exec", "fix the test"]) is None


def test_run_prints_the_reparse_warning_before_launching(
    routed_config, oidc, monkeypatch
):
    oidc["cached"] = _FakeVK()
    monkeypatch.setattr(codex, "resolve_codex_bin", lambda: r"C:\npm\codex.cmd")
    monkeypatch.setattr(codex.sys, "platform", "win32")

    result = CliRunner().invoke(
        codex_group, ["run", "--dry-run", "--", "exec", "why is %APPDATA% empty"]
    )

    assert result.exit_code == 0, result.output
    assert "%APPDATA%" in result.output
    assert "Dry run" in result.output


def test_reparse_risk_flags_an_unquoted_cmd_operator(monkeypatch):
    """`fix&whoami` is one word, so list2cmdline does NOT quote it — cmd.exe splits it.

    This is the severe half of the re-parse problem: not a mangled prompt but a second
    command running with the Virtual Key still in the environment we just built.
    """
    monkeypatch.setattr(codex.sys, "platform", "win32")
    warning = codex.cmd_reparse_risk(r"C:\npm\codex.cmd", ["exec", "fix&whoami"])

    assert warning is not None
    assert "fix&whoami" in warning
    assert "operator" in warning
    assert "run the rest as its own" in warning


def test_reparse_risk_covers_every_cmd_operator(monkeypatch):
    """Each of &|<>^ on its own is enough — redirection and ^-escapes count too."""
    monkeypatch.setattr(codex.sys, "platform", "win32")
    for char in "&|<>^":
        warning = codex.cmd_reparse_risk(r"C:\npm\codex.cmd", ["exec", f"a{char}b"])
        assert warning is not None, char
        assert "operator" in warning, char


def test_reparse_risk_leaves_a_quoted_operator_alone(monkeypatch):
    """An operator inside a whitespace-quoted argument is protected — no operator scare.

    list2cmdline wraps `read a & b` in quotes, and cmd.exe honours quotes for operators,
    so warning about it here would be crying wolf on ordinary English prompts.
    """
    monkeypatch.setattr(codex.sys, "platform", "win32")

    assert codex.cmd_reparse_risk(r"C:\npm\codex.cmd", ["exec", "read a & b"]) is None


def test_a_quote_anywhere_exposes_an_operator_that_looked_safe(monkeypatch):
    """cmd.exe tracks quote state across the WHOLE line, so the risk is not per-argument.

    list2cmdline escapes the inner quote as \\" — an escape the MS C runtime honours and
    cmd.exe does not — so from that point cmd.exe's idea of "inside quotes" no longer
    matches ours, and the quoted operator in a later argument may be live after all.
    """
    monkeypatch.setattr(codex.sys, "platform", "win32")
    warning = codex.cmd_reparse_risk(
        r"C:\npm\codex.cmd", ["exec", 'say "hi"', "read a & b"]
    )

    assert warning is not None
    assert "read a & b" in warning
    assert "operator" in warning


def test_the_operator_sentence_is_absent_when_only_percent_is_at_stake(monkeypatch):
    """A %VAR% prompt gets the mangling warning, not the "second command" one."""
    monkeypatch.setattr(codex.sys, "platform", "win32")
    warning = codex.cmd_reparse_risk(r"C:\npm\codex.cmd", ["exec", "why is %APPDATA% empty"])

    assert warning is not None
    assert "operator" not in warning


def test_quote_prediction_matches_list2cmdline(monkeypatch):
    """_list2cmdline_quotes mirrors CPython's rule; if that drifts, this fails loudly."""
    import subprocess

    for arg in ("a&b", "a & b", "", "a\tb", "plain", "why is %APPDATA% empty"):
        rendered = subprocess.list2cmdline([arg])
        assert codex._list2cmdline_quotes(arg) is rendered.startswith('"'), arg


# ---------------------------------------------------------------------------
# Windows: Ctrl-C must not eat Codex's exit code (WIN-4)
# ---------------------------------------------------------------------------
# There is no exec on Windows, so a wrapper process really is in the middle. The
# console delivers CTRL_C_EVENT to every attached process, so with the default
# disposition CPython raises KeyboardInterrupt in the wrapper the moment the wait
# returns; click's standalone mode turns that into "Aborted!" + exit 1 and Codex's
# own exit code is lost.

class _FakePopen:
    """Records the SIGINT disposition in force while the wrapper is waiting."""

    def __init__(self, argv, env=None, code=7, raises=None):
        self.argv, self.env, self._code, self._raises = argv, env, code, raises
        self.seen_disposition = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def wait(self):
        self.seen_disposition = signal.getsignal(signal.SIGINT)
        if self._raises:
            raise self._raises
        return self._code


def test_exec_codex_deafens_the_wrapper_to_ctrl_c_while_waiting(monkeypatch):
    monkeypatch.setattr(codex.sys, "platform", "win32")
    spawned = {}

    def _popen(argv, env=None, **kw):
        spawned["proc"] = _FakePopen(argv, env)
        return spawned["proc"]

    monkeypatch.setattr(codex.subprocess, "Popen", _popen)
    before = signal.getsignal(signal.SIGINT)

    code = codex.exec_codex([r"C:\npm\codex.cmd", "exec", "x"], {"GATEWAY_VK": "vk-1"})

    assert code == 7  # Codex's code, not click's 1
    assert spawned["proc"].seen_disposition is signal.SIG_IGN
    assert signal.getsignal(signal.SIGINT) is before  # and put back afterwards
    assert spawned["proc"].env == {"GATEWAY_VK": "vk-1"}


def test_exec_codex_restores_the_handler_when_the_child_fails(monkeypatch):
    """The restore is in a finally — an OSError must not leave SIGINT ignored."""
    monkeypatch.setattr(codex.sys, "platform", "win32")
    monkeypatch.setattr(
        codex.subprocess,
        "Popen",
        lambda argv, env=None, **kw: _FakePopen(argv, env, raises=OSError("boom")),
    )
    before = signal.getsignal(signal.SIGINT)

    with pytest.raises(OSError, match="boom"):
        codex.exec_codex([r"C:\npm\codex.cmd"], {})

    assert signal.getsignal(signal.SIGINT) is before
