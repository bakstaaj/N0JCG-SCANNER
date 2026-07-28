import json
from pathlib import Path


TGID = 2678
LABEL = "Mueller State Park Ops"
CONFIG_PATHS = (
    Path("config/templates/topaz_trwc_mesa_discovery_2500_4500.json"),
    Path("web/system_catalog.local.topaz_trwc_mesa.json"),
)


def test_mueller_state_park_ops_label_is_preserved() -> None:
    for path in CONFIG_PATHS:
        payload = json.loads(path.read_text(encoding="utf-8"))
        matches = []
        for system in payload.get("systems", []):
            matches.extend(
                item
                for item in system.get("talkgroups", [])
                if int(item.get("tgid", -1)) == TGID
            )

        assert len(matches) == 1, path
        assert matches[0]["label"] == LABEL, path
        assert matches[0]["enabled"] is True, path
