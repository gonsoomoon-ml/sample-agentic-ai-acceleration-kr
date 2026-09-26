"""End to end: run the helper the way Claude Code does — no arguments,
CLAUDE_CODE_VERSION in the environment — and check exit status and stdout."""
import json
import os
import pathlib
import subprocess
import sys

SRC = pathlib.Path(__file__).resolve().parents[1] / "src"


def test_process_without_baseline_prints_empty_object_and_exits_zero():
    env = {**os.environ, "PYTHONPATH": str(SRC), "CLAUDE_CODE_VERSION": "2.1.281"}

    proc = subprocess.run(
        [sys.executable, "-m", "policy_helper"],
        capture_output=True, text=True, env=env, timeout=30, check=False,
    )

    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {}
