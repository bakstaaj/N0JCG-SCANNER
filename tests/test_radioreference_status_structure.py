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


def test_status_functions_have_explicit_unique_names() -> None:
    tree = ast.parse(
        SOURCE_PATH.read_text(encoding="utf-8"),
        filename=str(SOURCE_PATH),
    )

    counts = Counter(
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )

    assert counts["radioreference_status"] == 1
    assert counts["_radioreference_status_base"] == 1


def test_status_wrapper_keeps_base_callable() -> None:
    assert callable(rr.radioreference_status)
    assert callable(rr._radioreference_status_base)
    assert callable(rr._rr_v0_4d2_base_status)

    assert (
        rr._rr_v0_4d2_base_status
        is rr._radioreference_status_base
    )
