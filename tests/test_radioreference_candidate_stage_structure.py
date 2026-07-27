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


def test_named_candidate_stages_exist_once() -> None:
    tree = ast.parse(
        SOURCE_PATH.read_text(encoding="utf-8"),
        filename=str(SOURCE_PATH),
    )

    counts = Counter(
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )

    assert counts["_discover_trs_candidates_v05p"] == 1
    assert counts["_discover_trs_candidates_v05q"] == 1


def test_candidate_stage_aliases_reference_named_functions() -> None:
    assert (
        rr._rr_v05p_base_discover_trs_candidates
        is rr._discover_trs_candidates_v05p
    )

    assert (
        rr._rr_v05q_base_discover_trs_candidates
        is rr._discover_trs_candidates_v05q
    )


def test_final_candidate_function_remains_public_callable() -> None:
    assert callable(rr._discover_trs_candidates)
