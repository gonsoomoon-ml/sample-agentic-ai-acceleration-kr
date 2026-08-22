# Copyright 2026 © Amazon.com and Affiliates
"""소스에 `func.timezone("<리터럴>", …)` 가 남아 있으면 실패.

REPORTING_TIMEZONE 설정화 이후 집계 타임존은 usage_filters.reporting_tz_sql() 한 곳에서
나와야 한다. 리터럴이 한 군데라도 남으면 그 화면만 KST 로 버킷돼 다른 화면과 어긋난다 —
upstream 리베이스 충돌을 파일 단위(--ours)로 풀다가 두 라우터에서 실제로 되살아난 적이 있다.
docstring·주석은 AST 로 걸러 검사하지 않는다.
"""

import ast
from pathlib import Path

import pytest

_SRC = Path(__file__).resolve().parents[2] / "src" / "app"


def _literal_timezone_calls(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    hits = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "timezone"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "func"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            hits.append(f"{path.relative_to(_SRC)}:{node.lineno} func.timezone({node.args[0].value!r}, …)")
    return hits


@pytest.mark.unit
def test_no_literal_timezone_in_sql_expressions():
    hits = [h for p in sorted(_SRC.rglob("*.py")) for h in _literal_timezone_calls(p)]
    assert not hits, "집계 타임존은 reporting_tz_sql() 로만 — 리터럴 발견:\n  " + "\n  ".join(hits)
