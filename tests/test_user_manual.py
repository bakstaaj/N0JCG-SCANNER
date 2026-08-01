import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANUAL = ROOT / "docs" / "USER_MANUAL.md"


def test_user_manual_links_and_tool_references_exist() -> None:
    text = MANUAL.read_text(encoding="utf-8")
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "docs/USER_MANUAL.md" in readme
    for target in re.findall(r"\]\(([^)]+)\)", text):
        if target.startswith(("#", "http://", "https://")):
            continue
        assert (MANUAL.parent / target).resolve().exists(), target

    for target in set(re.findall(r"\./(tools/[A-Za-z0-9_.-]+)", text)):
        assert (ROOT / target).exists(), target


def test_manual_and_role_template_use_canonical_serial_map() -> None:
    expected = {
        "p25_control": "00000251",
        "p25_voice": "00000252",
        "analog_2m": "00000144",
        "analog_70cm": "00000440",
    }
    manual = MANUAL.read_text(encoding="utf-8")
    template = json.loads(
        (ROOT / "config" / "receiver_roles.example.json").read_text(
            encoding="utf-8"
        )
    )

    for role, serial in expected.items():
        assert template["roles"][role]["rtl_serial"] == serial
        assert serial in manual
    assert template["roles"]["analog_2m"]["enabled"] is True
    assert template["roles"]["analog_70cm"]["enabled"] is True
    for unrelated in (
        "NOAA",
        "Airband",
        "airband",
        "ADS-B",
        "UAT",
        "00000162",
        "00000978",
        "00001090",
    ):
        assert unrelated not in manual
