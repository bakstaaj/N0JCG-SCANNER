from pathlib import Path

from pi_p25_scanner.receiver_inventory import (
    _arguments_claim_serial,
    scan_process_claims,
)


SERIAL = "00000144"
OTHER_SERIAL = "00000440"


def _write_process(root: Path, pid: int, arguments: list[str]) -> None:
    process = root / str(pid)
    process.mkdir()
    payload = b"\0".join(item.encode("utf-8") for item in arguments) + b"\0"
    (process / "cmdline").write_bytes(payload)


def test_argument_match_with_separate_device_option() -> None:
    assert _arguments_claim_serial(["rtl_fm", "-d", SERIAL], SERIAL)


def test_argument_match_with_long_device_option() -> None:
    assert _arguments_claim_serial(["rtl_power", "--device", SERIAL], SERIAL)


def test_argument_match_with_equals_option() -> None:
    assert _arguments_claim_serial(["scanner", f"--serial={SERIAL}"], SERIAL)


def test_argument_match_with_rtl_serial_option() -> None:
    assert _arguments_claim_serial(["scanner", "--rtl-serial", SERIAL], SERIAL)


def test_unrelated_substring_does_not_match() -> None:
    assert not _arguments_claim_serial(
        ["scanner", "--port", f"9{SERIAL}7"],
        SERIAL,
    )


def test_unrelated_exact_numeric_argument_does_not_match() -> None:
    assert not _arguments_claim_serial(
        ["scanner", "--frequency", SERIAL],
        SERIAL,
    )


def test_other_serial_does_not_match() -> None:
    assert not _arguments_claim_serial(
        ["rtl_fm", "-d", OTHER_SERIAL],
        SERIAL,
    )


def test_scan_process_claims_uses_argument_boundaries(tmp_path: Path) -> None:
    _write_process(tmp_path, 101, ["rtl_fm", "-d", SERIAL])
    _write_process(tmp_path, 102, ["rtl_power", f"--device={OTHER_SERIAL}"])
    _write_process(tmp_path, 103, ["other", "--port", f"9{SERIAL}7"])
    _write_process(tmp_path, 104, ["other", "--frequency", SERIAL])

    claims = scan_process_claims(
        [SERIAL, OTHER_SERIAL],
        process_root=tmp_path,
    )

    assert [item["pid"] for item in claims[SERIAL]] == [101]
    assert [item["pid"] for item in claims[OTHER_SERIAL]] == [102]
