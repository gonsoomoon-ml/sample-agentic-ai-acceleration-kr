# Copyright 2026 © Amazon.com and Affiliates: This deliverable is considered Developed Content as defined in the AWS Service Terms.

"""Tests for cli.teardown — the `clear` engine (settings revert, OS-env restore,
backup sweep, post-teardown checks).

Windows-registry paths are exercised only for their POSIX-reachable logic
(snapshot selection, sweep, settings revert); the winreg calls themselves are
covered by the on-box E2E (plan T9).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cli import teardown
from cli.teardown import (
    owned_user_settings_keys,
    post_teardown_checks,
    revert_user_settings,
    sweep_backups,
)


@pytest.fixture()
def backup_dir(tmp_path, monkeypatch):
    d = tmp_path / "backups"
    d.mkdir()
    monkeypatch.setenv("GATEWAY_CLI_BACKUP_DIR", str(d))
    return d


# ---------------------------------------------------------------------------
# owned-key derivation
# ---------------------------------------------------------------------------

def test_owned_keys_match_manifest_pins():
    """The eraser's key set mirrors what setup emits at the user tier."""
    top, env = owned_user_settings_keys()
    assert {"apiKeyHelper", "model", "availableModels"} <= top
    assert {
        "ANTHROPIC_BASE_URL",
        "ADMIN_API_URL",
        "OIDC_ISSUER_URL",
        "OIDC_CLIENT_ID",
        "GATEWAY_CLI_GATEWAY_URL",
    } <= env
    # Never touch keys we don't own.
    assert "ANTHROPIC_API_KEY" not in env
    assert "CLAUDE_CODE_USE_BEDROCK" not in env


# ---------------------------------------------------------------------------
# settings.json revert
# ---------------------------------------------------------------------------

def _write_settings(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def test_revert_removes_owned_keys_preserves_others(tmp_path, backup_dir):
    settings = tmp_path / "settings.json"
    _write_settings(settings, {
        "apiKeyHelper": "C:/x/api-key-helper.exe",
        "model": "claude-sonnet-4-6",
        "userOwnedKey": "keep-me",
        "env": {
            "OIDC_ISSUER_URL": "https://idp",
            "ANTHROPIC_BASE_URL": "http://gw",
            "USER_OWNED_ENV": "keep-me-too",
        },
    })

    result = revert_user_settings(settings)

    assert result.changed
    data = json.loads(settings.read_text())
    assert "apiKeyHelper" not in data
    assert "model" not in data
    assert data["userOwnedKey"] == "keep-me"
    assert data["env"] == {"USER_OWNED_ENV": "keep-me-too"}
    assert "env.OIDC_ISSUER_URL" in result.removed


def test_revert_restores_pre_setup_value_from_earliest_snapshot(tmp_path, backup_dir):
    """A key the user had BEFORE setup comes back with its old value."""
    settings = tmp_path / "settings.json"
    _write_settings(settings, {"model": "claude-sonnet-4-6", "env": {"ANTHROPIC_BASE_URL": "http://gw"}})
    # Earliest snapshot = pre-setup state; a later one holds gateway's own values
    # and must NOT win.
    (backup_dir / "claude-code.settings.json.20260101T000000.bak").write_text(
        json.dumps({"model": "user-model", "env": {}}), encoding="utf-8"
    )
    (backup_dir / "claude-code.settings.json.20260601T000000.bak").write_text(
        json.dumps({"model": "claude-sonnet-4-6", "env": {"ANTHROPIC_BASE_URL": "http://gw"}}),
        encoding="utf-8",
    )

    result = revert_user_settings(settings)

    data = json.loads(settings.read_text())
    assert data["model"] == "user-model"          # restored, not removed
    assert "model" in result.restored
    assert "env" not in data                       # our env key removed, block emptied
    assert "env.ANTHROPIC_BASE_URL" in result.removed


def test_revert_empty_env_block_dropped(tmp_path, backup_dir):
    settings = tmp_path / "settings.json"
    _write_settings(settings, {"env": {"OIDC_CLIENT_ID": "cid"}})
    revert_user_settings(settings)
    assert "env" not in json.loads(settings.read_text())


def test_revert_missing_file_is_noop(tmp_path, backup_dir):
    result = revert_user_settings(tmp_path / "absent.json")
    assert not result.changed
    assert result.skipped_reason


def test_revert_unparseable_file_left_alone(tmp_path, backup_dir):
    settings = tmp_path / "settings.json"
    settings.write_text("{not json", encoding="utf-8")
    result = revert_user_settings(settings)
    assert not result.changed
    assert settings.read_text() == "{not json"  # refused to guess


def test_revert_idempotent(tmp_path, backup_dir):
    settings = tmp_path / "settings.json"
    _write_settings(settings, {"model": "claude-sonnet-4-6"})
    assert revert_user_settings(settings).changed
    second = revert_user_settings(settings)
    assert not second.changed  # Q3: idempotency = absence of owned keys


# ---------------------------------------------------------------------------
# POSIX rc-line removal
# ---------------------------------------------------------------------------

def test_strip_posix_rc_removes_only_our_block_lines(tmp_path):
    rc = tmp_path / ".zshrc"
    rc.write_text(
        "export PATH=$PATH:/usr/local/bin\n"
        "\n"
        "# LLM Gateway — added by gateway-cli env --persist\n"
        'export OIDC_ISSUER_URL="https://idp"\n'
        'export ANTHROPIC_BASE_URL="http://gw"\n'
        'export MY_OWN_VAR="untouched"\n',
        encoding="utf-8",
    )
    changed = teardown._strip_posix_rc(rc)
    assert changed
    text = rc.read_text()
    assert "OIDC_ISSUER_URL" not in text
    assert "ANTHROPIC_BASE_URL" not in text
    assert "MY_OWN_VAR" in text                    # not ours — kept
    assert "gateway-cli env --persist" not in text  # marker gone
    assert "export PATH=$PATH:/usr/local/bin" in text


def test_strip_posix_rc_noop_when_absent(tmp_path):
    rc = tmp_path / ".zshrc"
    rc.write_text("export FOO=bar\n", encoding="utf-8")
    assert not teardown._strip_posix_rc(rc)
    assert rc.read_text() == "export FOO=bar\n"


def test_restore_os_env_posix_cleans_both_rc_files(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / ".zshrc").write_text(
        '# LLM Gateway — added by gateway-cli env --persist\nexport OIDC_CLIENT_ID="x"\n',
        encoding="utf-8",
    )
    result = teardown.restore_os_env()
    assert result.changed
    assert str(tmp_path / ".zshrc") in result.rc_files


# ---------------------------------------------------------------------------
# backup sweep
# ---------------------------------------------------------------------------

def test_sweep_removes_only_owned_prefixes(backup_dir):
    ours = [
        backup_dir / "claude-code.settings.json.20260101T000000.bak",
        backup_dir / "claude-code-managed.managed-settings.json.20260101T000000.bak",
        backup_dir / "gateway-cli-hkcu-env.Environment.20260101T000000.json.bak",
    ]
    theirs = [
        backup_dir / "cowork.config.20260101T000000.bak",   # sibling tool's snapshot
        backup_dir / "random-notes.txt",
    ]
    for p in ours + theirs:
        p.write_text("{}", encoding="utf-8")

    removed = sweep_backups()

    assert sorted(removed) == sorted(ours)
    for p in ours:
        assert not p.exists()
    for p in theirs:
        assert p.exists()  # never touch another tenant's files


def test_sweep_refuses_symlink_escape(backup_dir, tmp_path):
    outside = tmp_path / "outside.bak"
    outside.write_text("precious", encoding="utf-8")
    link = backup_dir / "claude-code.evil.20260101T000000.bak"
    link.symlink_to(outside)

    sweep_backups()

    assert outside.exists()  # the escape guard kept the target


def test_sweep_missing_dir_is_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("GATEWAY_CLI_BACKUP_DIR", str(tmp_path / "nope"))
    assert sweep_backups() == []


# ---------------------------------------------------------------------------
# post-teardown checks
# ---------------------------------------------------------------------------

def test_post_teardown_clean_home(tmp_path, monkeypatch, backup_dir):
    monkeypatch.setenv("HOME", str(tmp_path))
    # The developer's own CODEX_HOME must not decide the codex-config check.
    monkeypatch.delenv("CODEX_HOME", raising=False)
    # Point every stateful surface at the empty tmp home.
    monkeypatch.setattr(teardown, "_user_settings_path", lambda: tmp_path / ".claude" / "settings.json")
    monkeypatch.setattr("cli.managed.is_gateway_enabled", lambda: False)
    monkeypatch.setattr("cli.login.load_tokens", lambda: None)
    monkeypatch.setattr("cli.login.load_vk_cache", lambda: None)
    monkeypatch.setattr(teardown, "oidc_tokens_path", lambda: tmp_path / "t.json")
    monkeypatch.setattr(teardown, "vk_cache_path", lambda: tmp_path / "v.json")

    checks = post_teardown_checks()

    assert set(checks) == {
        "managed-settings",
        "user-settings",
        "os-env",
        "tokens",
        "backups",
        "codex-config",
    }
    assert all(v == "ok" for v in checks.values()), checks


def test_post_teardown_flags_residue(tmp_path, monkeypatch, backup_dir):
    monkeypatch.setenv("HOME", str(tmp_path))
    claude_dir = tmp_path / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text(json.dumps({"apiKeyHelper": "x"}), encoding="utf-8")
    (backup_dir / "claude-code.settings.json.20260101T000000.bak").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(teardown, "_user_settings_path", lambda: claude_dir / "settings.json")
    monkeypatch.setattr("cli.managed.is_gateway_enabled", lambda: True)
    monkeypatch.setattr("cli.login.load_tokens", lambda: None)
    monkeypatch.setattr("cli.login.load_vk_cache", lambda: None)
    monkeypatch.setattr(teardown, "oidc_tokens_path", lambda: tmp_path / "t.json")
    monkeypatch.setattr(teardown, "vk_cache_path", lambda: tmp_path / "v.json")

    checks = post_teardown_checks()

    assert checks["managed-settings"] == "residue"
    assert checks["user-settings"] == "residue"
    assert checks["backups"] == "residue"
    assert checks["tokens"] == "ok"


# ---------------------------------------------------------------------------
# Codex snapshot: swept only once Codex's own file no longer needs it
# ---------------------------------------------------------------------------
# `clear` deliberately does not revert config.toml (it is Codex's file — `codex revert`
# owns it), so the snapshot is the user's only way back to their pre-gateway settings.
# Deleting it while our block is still live strands them, and via
# `uninstall --clear-first` it strands them with no `codex revert` command either.

CODEX_BAK = "codex.config.toml.20260101T000000.bak"


@pytest.fixture(autouse=True)
def codex_home(tmp_path, monkeypatch):
    """Isolate CODEX_HOME for EVERY test in this module.

    autouse because the sweep and the post-teardown checks now consult Codex's config,
    so a developer's real ~/.codex/config.toml (or an exported CODEX_HOME) would
    otherwise decide what the sweep deletes. Starts empty — "Codex never configured".
    """
    home = tmp_path / "codex-home"
    home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(home))
    return home


def _ours(codex_home):
    (codex_home / "config.toml").write_text(
        'model = "codex-gpt-5.6-terra"\n'
        'model_provider = "gateway"\n'
        "\n"
        "[model_providers.gateway]\n"
        'name = "LLM Gateway"\n'
        'base_url = "https://gw.example.com/v1"\n'
        'wire_api = "responses"\n'
        'env_key = "GATEWAY_VK"\n',
        encoding="utf-8",
    )


def test_sweep_keeps_the_codex_snapshot_while_config_is_still_ours(backup_dir, codex_home):
    _ours(codex_home)
    codex_bak = backup_dir / CODEX_BAK
    codex_bak.write_text("# pre-gateway codex config\n", encoding="utf-8")
    other = backup_dir / "claude-code.settings.json.20260101T000000.bak"
    other.write_text("{}", encoding="utf-8")

    removed = sweep_backups()

    assert codex_bak.exists(), "deleting this strands a config that still points at us"
    assert not other.exists()  # everything else still goes
    assert codex_bak not in removed
    assert teardown.retained_codex_snapshots() == [codex_bak]


def test_sweep_removes_the_codex_snapshot_once_the_block_is_gone(backup_dir, codex_home):
    """After `codex revert` the snapshot is ordinary residue — and must go."""
    (codex_home / "config.toml").write_text('approval_policy = "never"\n', encoding="utf-8")
    codex_bak = backup_dir / CODEX_BAK
    codex_bak.write_text("# pre-gateway codex config\n", encoding="utf-8")

    removed = sweep_backups()

    assert not codex_bak.exists()
    assert codex_bak in removed
    assert teardown.retained_codex_snapshots() == []


def test_sweep_removes_the_codex_snapshot_when_codex_was_never_configured(
    backup_dir, codex_home
):
    """No config.toml at all (Claude-Code-only user) — nothing to protect."""
    codex_bak = backup_dir / CODEX_BAK
    codex_bak.write_text("{}", encoding="utf-8")

    assert codex_bak in sweep_backups()


def test_post_teardown_reports_codex_config_and_not_the_snapshot_it_kept(
    tmp_path, monkeypatch, backup_dir, codex_home
):
    """The two halves of the honest report.

    codex-config is residue (true, and `codex revert` fixes it); the snapshot held back
    for it is NOT reported as backup residue, because `clear` kept it on purpose and
    telling the user to re-run `clear` would be a loop.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    _ours(codex_home)
    (backup_dir / CODEX_BAK).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(teardown, "_user_settings_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr("cli.managed.is_gateway_enabled", lambda: False)
    monkeypatch.setattr("cli.login.load_tokens", lambda: None)
    monkeypatch.setattr("cli.login.load_vk_cache", lambda: None)
    monkeypatch.setattr(teardown, "oidc_tokens_path", lambda: tmp_path / "t.json")
    monkeypatch.setattr(teardown, "vk_cache_path", lambda: tmp_path / "v.json")

    checks = post_teardown_checks()

    assert checks["codex-config"] == "residue"
    assert checks["backups"] == "ok"


# ---------------------------------------------------------------------------
# `codex setup --config-path <elsewhere>`: the block is not in the default file
# ---------------------------------------------------------------------------
# Everything above asks about $CODEX_HOME/config.toml. A custom target broke that: the
# snapshot is named after the BASENAME (codex.config.toml.<ts>.bak), so it looked exactly
# like the default file's snapshot, while codex_config_pending() asked the default file,
# got False, and swept the only pre-setup copy of a file our block was still in. Worse via
# `uninstall --clear-first`, which then deletes the `codex revert` that could have fixed
# it. cli.codex.record_custom_config_target leaves a marker so the question can be asked
# about the file we actually wrote.

def _ours_at(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'model = "codex-gpt-5.6-terra"\n'
        'model_provider = "gateway"\n'
        "\n"
        "[model_providers.gateway]\n"
        'name = "LLM Gateway"\n'
        'base_url = "https://gw.example.com/v1"\n'
        'wire_api = "responses"\n'
        'env_key = "GATEWAY_VK"\n',
        encoding="utf-8",
    )


def _record(target: Path):
    from cli.codex import record_custom_config_target

    return record_custom_config_target(target)


def test_sweep_keeps_the_snapshot_when_the_block_lives_in_a_custom_config_path(
    tmp_path, backup_dir, codex_home
):
    """The regression itself. Default file clean, custom file ours → snapshot must stay."""
    custom = tmp_path / "work" / "config.toml"
    _ours_at(custom)
    marker = _record(custom)
    assert marker is not None and marker.exists()
    codex_bak = backup_dir / CODEX_BAK
    codex_bak.write_text("# pre-gateway codex config\n", encoding="utf-8")

    removed = sweep_backups()

    assert teardown.codex_config_pending() is True
    assert codex_bak.exists(), "swept the only undo for a file still routing through us"
    assert codex_bak not in removed
    # The marker is what keeps that answer True — sweeping it would make the snapshot
    # sweepable on the very next `clear`, block still live.
    assert marker.exists()
    assert marker not in removed


def test_the_custom_target_marker_is_swept_once_that_file_is_reverted(
    tmp_path, backup_dir, codex_home
):
    """`codex revert --config-path <file>` then `clear`: both the snapshot and the marker."""
    custom = tmp_path / "work" / "config.toml"
    custom.parent.mkdir(parents=True)
    custom.write_text('approval_policy = "never"\n', encoding="utf-8")  # our block gone
    marker = _record(custom)
    codex_bak = backup_dir / CODEX_BAK
    codex_bak.write_text("# pre-gateway codex config\n", encoding="utf-8")

    removed = sweep_backups()

    assert teardown.codex_config_pending() is False
    assert codex_bak in removed and not codex_bak.exists()
    assert marker in removed and not marker.exists()


def test_a_recorded_target_that_no_longer_exists_does_not_hold_the_sweep(
    tmp_path, backup_dir, codex_home
):
    """A deleted custom config carries no block — "gone" must not read as "pending"."""
    marker = _record(tmp_path / "work" / "config.toml")  # never created
    codex_bak = backup_dir / CODEX_BAK
    codex_bak.write_text("{}", encoding="utf-8")

    removed = sweep_backups()

    assert teardown.codex_config_pending() is False
    assert codex_bak in removed
    assert marker in removed


def test_post_teardown_reports_a_custom_target_as_codex_residue(
    tmp_path, monkeypatch, backup_dir, codex_home
):
    """The report has to agree with the sweep, or `clear` claims a clean surface."""
    monkeypatch.setenv("HOME", str(tmp_path))
    custom = tmp_path / "work" / "config.toml"
    _ours_at(custom)
    _record(custom)
    (backup_dir / CODEX_BAK).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(teardown, "_user_settings_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr("cli.managed.is_gateway_enabled", lambda: False)
    monkeypatch.setattr("cli.login.load_tokens", lambda: None)
    monkeypatch.setattr("cli.login.load_vk_cache", lambda: None)
    monkeypatch.setattr(teardown, "oidc_tokens_path", lambda: tmp_path / "t.json")
    monkeypatch.setattr(teardown, "vk_cache_path", lambda: tmp_path / "v.json")

    checks = post_teardown_checks()

    assert checks["codex-config"] == "residue"
    # Kept on purpose (snapshot AND marker) → not reported as backup residue, or the
    # advice would be "run clear again", forever.
    assert checks["backups"] == "ok"


def test_pending_paths_name_the_custom_file_so_revert_is_actionable(
    tmp_path, backup_dir, codex_home
):
    """`codex revert` defaults to $CODEX_HOME/config.toml and would clear nothing here."""
    from cli.codex import codex_config_path

    custom = tmp_path / "work" / "config.toml"
    _ours_at(custom)
    _record(custom)

    pending = teardown.pending_codex_config_paths()

    assert pending == [custom]
    assert codex_config_path() not in pending


def test_pending_paths_list_the_default_file_first_when_both_are_ours(
    tmp_path, backup_dir, codex_home
):
    """Both live → both listed, default first (main.py prints the bare command for it)."""
    from cli.codex import codex_config_path

    _ours(codex_home)
    custom = tmp_path / "work" / "config.toml"
    _ours_at(custom)
    _record(custom)

    assert teardown.pending_codex_config_paths() == [codex_config_path(), custom]


def test_clear_prints_the_revert_command_that_matches_each_pending_file(
    tmp_path, backup_dir, codex_home
):
    """The bare `codex revert` would not touch a custom target — so don't print it.

    main.py._codex_revert_hints pairs each file with the command that clears *that* file.
    The trap it guards: "the default file is the first entry" is only true when the default
    file is itself pending.
    """
    from cli.codex import codex_config_path
    from cli.main import _codex_revert_hints

    custom = tmp_path / "work" / "config.toml"
    _ours_at(custom)
    _record(custom)

    only_custom = _codex_revert_hints()
    assert len(only_custom) == 1
    assert f"--config-path {custom}" in only_custom[0]

    _ours(codex_home)  # now the default file carries the block too
    both = _codex_revert_hints()
    assert len(both) == 2
    assert both[0].startswith(str(codex_config_path()))
    assert "--config-path" not in both[0]  # the bare command is right for this one
    assert f"--config-path {custom}" in both[1]


def test_verify_post_teardown_puts_codex_revert_before_clear(tmp_path, monkeypatch):
    """`codex revert` first, then `clear` — the order `_do_clear` already printed.

    Backwards, it costs a round trip: `clear` holds the Codex snapshot back while the block
    is live, so clearing first means clearing twice. And `uninstall` deletes the `codex
    revert` command, which turns "later" into "never".
    """
    from click.testing import CliRunner

    from cli.main import cli

    monkeypatch.setattr(
        teardown,
        "post_teardown_checks",
        lambda: {"backups": "residue", "codex-config": "residue", "tokens": "ok"},
    )
    monkeypatch.setattr(teardown, "pending_codex_config_paths", lambda: [tmp_path / "c.toml"])

    result = CliRunner().invoke(cli, ["verify", "--post-teardown"])

    assert result.exit_code == 1
    revert_at = result.output.index("codex revert")
    clear_at = result.output.index("gateway-cli clear")
    assert revert_at < clear_at, result.output
    # The step that `clear` cannot do is not listed as something `clear` will do.
    assert "for: backups" in result.output
    assert "codex-config." not in result.output


def test_verify_post_teardown_names_only_clear_when_codex_is_clean(monkeypatch):
    """No Codex residue → no Codex step, and no numbering that implies one."""
    from click.testing import CliRunner

    from cli.main import cli

    monkeypatch.setattr(
        teardown, "post_teardown_checks", lambda: {"backups": "residue", "codex-config": "ok"}
    )

    result = CliRunner().invoke(cli, ["verify", "--post-teardown"])

    assert result.exit_code == 1
    assert "codex revert" not in result.output
    assert "gateway-cli clear" in result.output


def test_retained_snapshots_counts_snapshots_only_not_the_markers(
    tmp_path, backup_dir, codex_home
):
    """"kept N snapshot(s)" must not count marker files, which restore nothing."""
    custom = tmp_path / "work" / "config.toml"
    _ours_at(custom)
    marker = _record(custom)
    codex_bak = backup_dir / CODEX_BAK
    codex_bak.write_text("{}", encoding="utf-8")

    retained = teardown.retained_codex_snapshots()

    assert retained == [codex_bak]
    assert marker not in retained


def test_an_unreadable_default_config_keeps_everything(tmp_path, backup_dir, codex_home):
    """"cannot tell" is not "clean".

    config_has_gateway_block used to answer False for any OSError, which made
    codex_config_pending's `except OSError: return True` unreachable — so a config.toml we
    could not read (ACL denial, a sharing violation while Codex holds it, a OneDrive
    placeholder) got its snapshot swept with our block possibly still inside.
    """
    from cli.codex import codex_config_path

    config = codex_config_path()
    config.parent.mkdir(parents=True, exist_ok=True)
    config.mkdir()  # a directory where a file belongs → OSError, not FileNotFoundError
    codex_bak = backup_dir / CODEX_BAK
    codex_bak.write_text("# pre-gateway codex config\n", encoding="utf-8")

    removed = sweep_backups()

    assert teardown.codex_config_pending() is True
    assert codex_bak.exists() and codex_bak not in removed


def test_post_teardown_codex_config_ok_for_an_unrelated_codex_install(
    tmp_path, monkeypatch, backup_dir, codex_home
):
    """A Codex the user configured themselves must not read as our leftovers."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (codex_home / "config.toml").write_text(
        'model = "gpt-5.6-sol"\nmodel_provider = "openai"\n', encoding="utf-8"
    )
    monkeypatch.setattr(teardown, "_user_settings_path", lambda: tmp_path / "settings.json")
    monkeypatch.setattr("cli.managed.is_gateway_enabled", lambda: False)
    monkeypatch.setattr("cli.login.load_tokens", lambda: None)
    monkeypatch.setattr("cli.login.load_vk_cache", lambda: None)
    monkeypatch.setattr(teardown, "oidc_tokens_path", lambda: tmp_path / "t.json")
    monkeypatch.setattr(teardown, "vk_cache_path", lambda: tmp_path / "v.json")

    assert post_teardown_checks()["codex-config"] == "ok"
