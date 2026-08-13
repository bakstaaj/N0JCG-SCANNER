import json
from pathlib import Path

from pi_p25_scanner import analog_channels, config_store
from pi_p25_scanner.analog_channels import parse_csv_text
from pi_p25_scanner.csv_profile_tools import (
    analog_channels_to_chirp_csv,
    p25_config_to_csv,
)
from pi_p25_scanner.p25_csv_import import import_p25_csv_request, parse_p25_csv


ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"


def _chirp_csv(*rows: str) -> str:
    header = (WEB / "chirp_analog_template.csv").read_text(encoding="utf-8").splitlines()[0]
    return "\n".join((header, *rows, ""))


def test_standard_chirp_csv_assigns_vhf_and_uhf_from_frequency() -> None:
    payload = parse_csv_text(
        _chirp_csv(
            "1,VHF Dispatch,155.640000,off,0.000000,,88.5,88.5,023,NN,023,Tone->Tone,NFM,5.00,,25W,VHF comment,,,,,",
            "2,UHF Test,444.440000,off,0.000000,,88.5,88.5,023,NN,023,Tone->Tone,NFM,5.00,S,25W,UHF comment,,,,,",
        )
    )

    vhf = payload["channels_by_role"]["analog_2m"]
    uhf = payload["channels_by_role"]["analog_70cm"]
    assert [channel["frequency_hz"] for channel in vhf] == [155_640_000]
    assert [channel["frequency_hz"] for channel in uhf] == [444_440_000]
    assert vhf[0]["comment"] == "VHF comment"
    assert uhf[0]["enabled"] is False


def test_chirp_export_round_trips_channel_counts_and_skip_state() -> None:
    first = parse_csv_text(
        _chirp_csv(
            "1,VHF,146.600000,off,0.000000,,88.5,88.5,023,NN,023,Tone->Tone,NFM,5.00,,25W,,,,,,",
            "2,UHF,444.440000,off,0.000000,,88.5,88.5,023,NN,023,Tone->Tone,NFM,5.00,S,25W,,,,,,",
        )
    )
    exported = analog_channels_to_chirp_csv(first["channels_by_role"])
    second = parse_csv_text(exported)

    assert len(second["channels_by_role"]["analog_2m"]) == 1
    assert len(second["channels_by_role"]["analog_70cm"]) == 1
    assert second["channels_by_role"]["analog_70cm"][0]["enabled"] is False


def test_p25_export_round_trips_template_contract() -> None:
    source = (WEB / "p25_import_template.csv").read_text(encoding="utf-8")
    parsed = parse_p25_csv(source)
    config = {"schema_version": 1, "systems": parsed["systems"]}

    exported = p25_config_to_csv(config)
    round_trip = parse_p25_csv(exported)

    assert round_trip["row_count"] == 3
    assert round_trip["systems"][0]["control_channels_hz"] == [851_012_500]
    assert round_trip["systems"][0]["talkgroups"][0]["tgid"] == 1001


def test_p25_csv_translates_radio_reference_demodulation_terms() -> None:
    source = (WEB / "p25_import_template.csv").read_text(encoding="utf-8")
    parsed = parse_p25_csv(source.replace("C4FM", "4FSK"))
    assert parsed["systems"][0]["control_demod_type"] == "fsk4"
    cqpsk = parse_p25_csv(source.replace("C4FM", "CQPSK"))
    assert cqpsk["systems"][0]["control_demod_type"] == "cqpsk"


def test_p25_csv_import_updates_runtime_config_with_backup(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_p25 = tmp_path / "p25_systems.json"
    p25_payload = json.loads(
        (ROOT / "config" / "p25_systems.local.example.json").read_text(encoding="utf-8")
    )
    runtime_p25.write_text(json.dumps(p25_payload), encoding="utf-8")
    monkeypatch.setattr(config_store, "RUNTIME_CONFIG_PATH", runtime_p25)

    result = import_p25_csv_request(
        {
            "filename": "field.csv",
            "csv_text": (WEB / "p25_import_template.csv").read_text(encoding="utf-8"),
            "replace_mode": "systems_in_file",
        }
    )

    updated = json.loads(runtime_p25.read_text(encoding="utf-8"))
    assert result["imported_rows"] == 3
    assert any(system["name"] == "Example P25 System" for system in updated["systems"])
    backups = list((tmp_path / "backups").glob("p25_systems_*.json"))
    assert backups


def test_named_profile_snapshots_analog_channels_without_rebinding_radios(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runtime_p25 = tmp_path / "p25_systems.json"
    named_dir = tmp_path / "configs"
    analog_path = tmp_path / "analog_receivers.json"
    p25_payload = json.loads(
        (ROOT / "config" / "p25_systems.local.example.json").read_text(encoding="utf-8")
    )
    runtime_p25.write_text(json.dumps(p25_payload), encoding="utf-8")

    analog_payload = analog_channels.default_config()
    analog_payload["workers"]["analog_2m"]["rtl_serial"] = "00000144"
    analog_payload["workers"]["analog_70cm"]["rtl_serial"] = "00000440"
    analog_payload["workers"]["analog_2m"]["channels"] = [
        {"name": "VHF", "frequency_hz": 146_600_000, "mode": "nfm", "enabled": True}
    ]
    analog_path.write_text(json.dumps(analog_payload), encoding="utf-8")

    monkeypatch.setattr(config_store, "RUNTIME_CONFIG_PATH", runtime_p25)
    monkeypatch.setattr(config_store, "NAMED_CONFIG_DIR", named_dir)
    monkeypatch.setattr(analog_channels, "DEFAULT_CONFIG_PATH", analog_path)
    monkeypatch.setattr(analog_channels, "DEFAULT_TEMPLATE_PATH", tmp_path / "missing.json")

    saved = config_store.save_named_config("Field Profile", p25_payload)
    stored = json.loads(Path(saved["path"]).read_text(encoding="utf-8"))
    assert len(stored["analog_channels"]["analog_2m"]) == 1

    changed = json.loads(analog_path.read_text(encoding="utf-8"))
    changed["workers"]["analog_2m"]["rtl_serial"] = "00000144"
    changed["workers"]["analog_70cm"]["rtl_serial"] = "00000440"
    changed["workers"]["analog_2m"]["channels"] = []
    analog_path.write_text(json.dumps(changed), encoding="utf-8")

    loaded = config_store.load_named_config(saved["id"], apply_to_runtime=True)
    restored = json.loads(analog_path.read_text(encoding="utf-8"))
    assert loaded["analog"]["channel_counts"]["analog_2m"] == 1
    assert restored["workers"]["analog_2m"]["rtl_serial"] == "00000144"
    assert restored["workers"]["analog_70cm"]["rtl_serial"] == "00000440"
    assert restored["workers"]["analog_2m"]["channels"][0]["frequency_hz"] == 146_600_000

    resaved = config_store.save_named_config("Field Profile", p25_payload)
    assert Path(resaved["backup_path"]).exists()
    deleted = config_store.delete_named_config(saved["id"])
    assert not Path(saved["path"]).exists()
    assert Path(deleted["recoverable_path"]).exists()


def test_radio_profile_ui_contains_only_flat_file_workflow() -> None:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    assert "RadioReference" not in html
    assert "radioreference" not in html.lower()
    for element_id in (
        "profileSelect",
        "profileName",
        "analogCsvFile",
        "p25CsvFile",
        "exportAnalogCsvBtn",
        "exportP25CsvBtn",
    ):
        assert f'id="{element_id}"' in html
    assert "/chirp_analog_template.csv" in html
    assert "/p25_import_template.csv" in html
