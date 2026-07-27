import os

from pi_p25_scanner.backend_launch import prepend_pythonpath


def test_prepend_pythonpath_preserves_existing_entries() -> None:
    required = os.pathsep.join(
        [
            "/opt/op25/apps",
            "/opt/op25",
        ]
    )
    existing = os.pathsep.join(
        [
            "/custom/python",
            "/vendor/python",
        ]
    )

    result = prepend_pythonpath(required, existing)

    assert result.split(os.pathsep) == [
        "/opt/op25/apps",
        "/opt/op25",
        "/custom/python",
        "/vendor/python",
    ]


def test_prepend_pythonpath_removes_duplicates() -> None:
    required = os.pathsep.join(
        [
            "/opt/op25/apps",
            "/opt/op25",
        ]
    )
    existing = os.pathsep.join(
        [
            "/opt/op25",
            "/custom/python",
            "/opt/op25/apps",
        ]
    )

    result = prepend_pythonpath(required, existing)

    assert result.split(os.pathsep) == [
        "/opt/op25/apps",
        "/opt/op25",
        "/custom/python",
    ]


def test_prepend_pythonpath_ignores_empty_entries() -> None:
    required = os.pathsep.join(
        [
            "",
            "/opt/op25",
            "",
        ]
    )

    assert prepend_pythonpath(required, "") == "/opt/op25"


def test_prepend_pythonpath_handles_empty_required_value() -> None:
    existing = os.pathsep.join(
        [
            "/custom/python",
            "/vendor/python",
        ]
    )

    result = prepend_pythonpath("", existing)

    assert result.split(os.pathsep) == [
        "/custom/python",
        "/vendor/python",
    ]
