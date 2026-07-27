import ast
from collections import Counter
from pathlib import Path

from pi_p25_scanner import radioreference_import as rr


SOURCE_PATH = (
    Path(__file__).resolve().parents[1]
    / "src"
    / "pi_p25_scanner"
    / "radioreference_import.py"
)


def test_plain_implementations_have_unique_names() -> None:
    tree = ast.parse(
        SOURCE_PATH.read_text(encoding="utf-8"),
        filename=str(SOURCE_PATH),
    )

    counts = Counter(
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )

    assert counts["_plain_v1"] == 1
    assert counts["_plain"] == 1


def test_plain_implementations_are_importable() -> None:
    assert callable(rr._plain_v1)
    assert callable(rr._plain)


def test_historical_plain_converts_nested_values() -> None:
    value = {
        "items": (
            {"id": 1},
            {"id": 2},
        )
    }

    result = rr._plain_v1(value)

    assert result == {
        "items": [
            {"id": 1},
            {"id": 2},
        ]
    }
