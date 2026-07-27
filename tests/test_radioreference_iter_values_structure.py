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


def test_iter_values_implementations_have_unique_names() -> None:
    tree = ast.parse(
        SOURCE_PATH.read_text(encoding="utf-8"),
        filename=str(SOURCE_PATH),
    )

    counts = Counter(
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )

    assert counts["_iter_values_v1"] == 1
    assert counts["_iter_values"] == 1


def test_iter_values_implementations_are_importable() -> None:
    assert callable(rr._iter_values_v1)
    assert callable(rr._iter_values)


def test_historical_iter_values_recurses_through_nested_values() -> None:
    value = {
        "outer": [
            {"inner": "target"},
            "other",
        ]
    }

    flattened = list(rr._iter_values_v1(value))

    assert value["outer"] in flattened
    assert {"inner": "target"} in flattened
    assert "target" in flattened
    assert "other" in flattened
    assert value not in flattened
