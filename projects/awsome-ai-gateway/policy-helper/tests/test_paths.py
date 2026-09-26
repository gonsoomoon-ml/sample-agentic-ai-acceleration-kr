"""default_baseline_path: where the built-in baseline lives.

It must sit next to the executable in the admin-only install folder. A path a
user can influence (environment variable, user profile) would let them rewrite
their own managed settings, so there is deliberately no override.
"""
import sys

from policy_helper.main import default_baseline_path


def test_baseline_sits_next_to_frozen_executable(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "policy-helper.exe"))

    assert default_baseline_path() == tmp_path / "baseline.json"
