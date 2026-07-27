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


def _function_counts() -> Counter[str]:
    tree = ast.parse(
        SOURCE_PATH.read_text(encoding="utf-8"),
        filename=str(SOURCE_PATH),
    )

    return Counter(
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )


def test_call_variant_implementations_have_unique_names() -> None:
    counts = _function_counts()

    assert counts["_call_variants_v1"] == 1
    assert counts["_call_variants_v2"] == 1
    assert counts["_call_variants"] == 1


def test_call_variant_implementations_are_importable() -> None:
    assert callable(rr._call_variants_v1)
    assert callable(rr._call_variants_v2)
    assert callable(rr._call_variants)


def test_call_variants_returns_first_successful_result() -> None:
    calls: list[str] = []

    class Service:
        def lookup(self, value: str) -> str:
            calls.append(value)
            if value == "first":
                raise TypeError("unsupported test signature")
            return value

    class Client:
        service = Service()

    result = rr._call_variants(
        Client(),
        "lookup",
        [
            ("first",),
            ("second",),
        ],
    )

    assert result == "second"
    assert calls == ["first", "second"]
