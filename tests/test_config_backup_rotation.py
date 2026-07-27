import os
from pathlib import Path

from pi_p25_scanner.config_store import rotate_config_backups


def _create_backup(
    directory: Path,
    index: int,
) -> Path:
    path = directory / f"p25_systems_20260726T120{index:02d}Z.json"
    path.write_text(f'{{"index": {index}}}\n', encoding="utf-8")

    timestamp = 1_700_000_000 + index
    os.utime(path, (timestamp, timestamp))
    return path


def test_backup_rotation_keeps_newest_files(tmp_path: Path) -> None:
    backups = [
        _create_backup(tmp_path, index)
        for index in range(10)
    ]

    removed = rotate_config_backups(
        tmp_path,
        limit=4,
    )

    remaining = sorted(
        tmp_path.glob("p25_systems_*.json"),
        key=lambda path: path.name,
    )

    assert len(removed) == 6
    assert remaining == backups[-4:]
    assert all(not path.exists() for path in backups[:-4])


def test_backup_rotation_ignores_unrelated_files(tmp_path: Path) -> None:
    for index in range(5):
        _create_backup(tmp_path, index)

    unrelated = tmp_path / "notes.txt"
    unrelated.write_text("keep me\n", encoding="utf-8")

    rotate_config_backups(
        tmp_path,
        limit=2,
    )

    assert unrelated.exists()
    assert len(list(tmp_path.glob("p25_systems_*.json"))) == 2


def test_backup_rotation_does_nothing_below_limit(tmp_path: Path) -> None:
    backups = [
        _create_backup(tmp_path, index)
        for index in range(3)
    ]

    removed = rotate_config_backups(
        tmp_path,
        limit=5,
    )

    assert removed == []
    assert all(path.exists() for path in backups)


def test_backup_rotation_handles_missing_directory(tmp_path: Path) -> None:
    missing = tmp_path / "missing"

    assert rotate_config_backups(
        missing,
        limit=5,
    ) == []


def test_backup_rotation_rejects_zero_retention_without_deleting(
    tmp_path: Path,
) -> None:
    backups = [
        _create_backup(tmp_path, index)
        for index in range(3)
    ]

    removed = rotate_config_backups(
        tmp_path,
        limit=0,
    )

    assert removed == []
    assert all(path.exists() for path in backups)
