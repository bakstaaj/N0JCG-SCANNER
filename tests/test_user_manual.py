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


def test_manual_uses_public_placeholders_and_role_template_keeps_defaults() -> None:
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

    placeholders = {
        "p25_control": "<P25_CONTROL_SERIAL>",
        "p25_voice": "<P25_VOICE_SERIAL>",
        "analog_2m": "<VHF_SERIAL>",
        "analog_70cm": "<UHF_SERIAL>",
    }
    for role, serial in expected.items():
        assert template["roles"][role]["rtl_serial"] == serial
        assert serial not in manual
        assert placeholders[role] in manual
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


def test_public_front_door_docs_do_not_publish_private_station_data() -> None:
    public_docs = (
        ROOT / "README.md",
        ROOT / ".env.example",
        ROOT / "docs" / "README.md",
        ROOT / "docs" / "USER_MANUAL.md",
        ROOT / "docs" / "ADMINISTRATOR_GUIDE.md",
        ROOT / "docs" / "DEVELOPER_GUIDE.md",
        ROOT / "docs" / "API_REFERENCE.md",
        ROOT / "docs" / "HARDWARE_GUIDE.md",
        ROOT / "docs" / "ARCHITECTURE.md",
        ROOT / "docs" / "SPLIT_HOST_DEPLOYMENT.md",
    )
    forbidden = (
        "192.168.68.",
        "00000144",
        "00000440",
        "00000251",
        "00000252",
    )
    for path in public_docs:
        text = path.read_text(encoding="utf-8")
        for value in forbidden:
            assert value not in text, f"{path.relative_to(ROOT)} exposes {value}"
