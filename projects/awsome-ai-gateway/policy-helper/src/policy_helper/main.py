"""Claude Code policyHelper for the LLM Gateway.

Claude Code runs this program with no arguments at launch (and every
refreshIntervalMs) and uses its stdout — one JSON envelope,
`{"managedSettings": {...}}` — as the session's only managed settings.

Contract this module keeps: stdout is always exactly one JSON object and the
exit status is always 0. A non-zero exit, a timeout or unparsable stdout makes
Claude Code refuse to start, so anything that goes wrong becomes `{}` — which
contributes nothing, leaving the static managed settings in effect — plus the
reason on stderr (visible with `claude --debug`).
"""
from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from policy_helper.policy import (
    cache_path_for,
    http_fetch,
    merge,
    resolve_fragment,
    vk_path_for,
)

# Claude Code rejects helper stdout over 1 MiB (and then refuses to start).
MAX_STDOUT_BYTES = 1024 * 1024


class BaselineError(Exception):
    """The built-in baseline cannot be used; the message says why."""


def build_envelope(baseline_path: Path) -> dict:
    """Wrap the built-in baseline as the policyHelper envelope."""
    try:
        text = baseline_path.read_text(encoding="utf-8-sig")  # tolerate a Windows BOM
    except FileNotFoundError:
        raise BaselineError(f"baseline not found: {baseline_path}") from None
    try:
        baseline = json.loads(text)
    except ValueError as exc:
        raise BaselineError(f"baseline is not valid JSON: {exc}") from None
    if not isinstance(baseline, dict):
        raise BaselineError("baseline is not a JSON object")
    private = sorted(k for k in baseline if k.startswith("_"))
    if private:
        # e.g. gateway-cli's `_gatewayCli` marker, copied from managed-settings.json.
        raise BaselineError(f"baseline has non-settings keys {private}; remove them")
    envelope = {"managedSettings": baseline}
    if not _fits(envelope):
        raise BaselineError("envelope exceeds the 1 MiB stdout limit")
    return envelope


def _note(stderr: TextIO, message: str) -> None:
    """Best-effort diagnostic. On Windows stderr is a pipe in the locale code page,
    so non-ASCII (a user name in a path) could raise on write — and an exception
    here would turn into a non-zero exit. Escape to ASCII and swallow errors."""
    safe = message.encode("ascii", "backslashreplace").decode("ascii")
    try:
        stderr.write(f"policy-helper: {safe}\n")
    except Exception:  # noqa: BLE001, S110 — diagnostics must never break the contract
        pass


def _fits(envelope: dict) -> bool:
    return len(json.dumps(envelope).encode("utf-8")) <= MAX_STDOUT_BYTES


def run(
    baseline_path: Path,
    stdout: TextIO,
    stderr: TextIO,
    *,
    vk_path: Path | None = None,
    cache_path: Path | None = None,
    fetch: Callable[[str, str, float], dict] | None = None,
    now: float | None = None,
) -> int:
    """Write the envelope to stdout. Always returns 0.

    Without vk_path/cache_path/fetch only the baseline is used (PoC stage 1).
    With them, the server fragment is laid over the baseline; if that fails or the
    result would not fit, the baseline alone still goes out.
    """
    try:
        envelope = build_envelope(baseline_path)
    except Exception as exc:  # noqa: BLE001 — never let the helper fail
        _note(stderr, str(exc))
        envelope = {}

    if envelope and vk_path is not None and cache_path is not None and fetch is not None:
        try:
            fragment = resolve_fragment(
                envelope["managedSettings"],
                vk_path=vk_path,
                cache_path=cache_path,
                fetch=fetch,
                now=time.time() if now is None else now,
                note=lambda message: _note(stderr, message),
            )
            if fragment:
                merged = {"managedSettings": merge(envelope["managedSettings"], fragment)}
                if _fits(merged):
                    envelope = merged
                else:
                    _note(stderr, "server fragment would exceed the 1 MiB stdout limit; baseline only")
        except Exception as exc:  # noqa: BLE001 — the baseline must still go out
            _note(stderr, f"server policy skipped: {type(exc).__name__}: {exc}")

    # json.dumps escapes to ASCII by default, so stdout is safe in any code page.
    stdout.write(json.dumps(envelope))
    return 0


def default_baseline_path() -> Path:
    """`baseline.json` next to the program, i.e. in the admin-only install folder.

    No environment-variable override on purpose: Claude Code passes its own
    environment to the helper, so a user-set variable could point the helper at
    a baseline the user wrote.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "baseline.json"
    return Path(__file__).resolve().parent / "baseline.json"


def main() -> None:
    env, home = os.environ, Path.home()
    sys.exit(run(
        default_baseline_path(), sys.stdout, sys.stderr,
        vk_path=vk_path_for(sys.platform, env, home),
        cache_path=cache_path_for(sys.platform, env, home),
        fetch=http_fetch,
    ))
