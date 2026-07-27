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


def test_site_discovery_stages_have_unique_names() -> None:
    counts = _function_counts()

    assert counts["_discover_radioreference_sites_base"] == 1
    assert counts["_discover_radioreference_sites_v05t"] == 1
    assert counts["discover_radioreference_sites"] == 1


def test_site_stage_aliases_reference_named_functions() -> None:
    assert (
        rr._rr_v05t_base_discover_sites
        is rr._discover_radioreference_sites_base
    )

    assert (
        rr._rr_v05u_base_discover_sites
        is rr._discover_radioreference_sites_v05t
    )


def test_final_site_discovery_aliases_use_enriched_function() -> None:
    final = rr.discover_radioreference_sites

    assert callable(final)
    assert rr.radioreference_sites is final
    assert rr.find_radioreference_sites is final
    assert rr.list_radioreference_sites is final
    assert rr.rr_picker_find_sites is final
