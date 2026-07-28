from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from pi_p25_scanner.runtime_activity import (
    RECENT_EVENT_LIMIT,
    UNIQUE_TGID_LIMIT,
    VOICE_CALL_DEDUP_SECONDS,
    RuntimeActivityTracker,
)
from pi_p25_scanner.runtime_status import RuntimeStatusUpdate


def _update(tgid: int) -> RuntimeStatusUpdate:
    return RuntimeStatusUpdate(
        line=f"voice tgid={tgid}",
        tgid=tgid,
        voice_call=True,
    )


def test_unique_tgids_are_bounded() -> None:
    tracker = RuntimeActivityTracker()

    for tgid in range(UNIQUE_TGID_LIMIT + 25):
        tracker.record(_update(tgid))

    snapshot = tracker.snapshot()

    assert snapshot["unique_tgid_count"] == UNIQUE_TGID_LIMIT
    assert 0 not in snapshot["unique_tgids"]
    assert 24 not in snapshot["unique_tgids"]
    assert 25 in snapshot["unique_tgids"]
    assert UNIQUE_TGID_LIMIT + 24 in snapshot["unique_tgids"]


def test_duplicate_tgid_does_not_consume_capacity() -> None:
    tracker = RuntimeActivityTracker()

    tracker.record(_update(100))
    tracker.record(_update(100))
    tracker.record(_update(100))

    snapshot = tracker.snapshot()

    assert snapshot["talkgroup_updates"] == 3
    assert snapshot["unique_tgid_count"] == 1
    assert snapshot["unique_tgids"] == [100]


def test_repeated_voice_updates_count_as_one_distinct_call() -> None:
    tracker = RuntimeActivityTracker()
    update = RuntimeStatusUpdate(
        line="voice update: tg(4540), freq(853.300000)",
        tgid=4540,
        voice_frequency_hz=853_300_000,
        voice_call=True,
    )

    with patch(
        "pi_p25_scanner.runtime_activity.time.time",
        side_effect=[100.0, 100.2, 101.9],
    ):
        tracker.record(update)
        tracker.record(update)
        tracker.record(update)

    snapshot = tracker.snapshot()
    assert snapshot["voice_call_events"] == 3
    assert snapshot["distinct_voice_calls"] == 1


def test_voice_call_counts_again_after_quiet_gap() -> None:
    tracker = RuntimeActivityTracker()
    update = RuntimeStatusUpdate(
        line="voice update: tg(4540), freq(853.300000)",
        tgid=4540,
        voice_frequency_hz=853_300_000,
        voice_call=True,
    )

    with patch(
        "pi_p25_scanner.runtime_activity.time.time",
        side_effect=[100.0, 100.5, 100.5 + VOICE_CALL_DEDUP_SECONDS + 0.1],
    ):
        tracker.record(update)
        tracker.record(update)
        tracker.record(update)

    assert tracker.snapshot()["distinct_voice_calls"] == 2


def test_different_voice_channel_counts_immediately() -> None:
    tracker = RuntimeActivityTracker()
    first = RuntimeStatusUpdate(
        line="voice update: tg(4540), freq(853.300000)",
        tgid=4540,
        voice_frequency_hz=853_300_000,
        voice_call=True,
    )
    second = RuntimeStatusUpdate(
        line="voice update: tg(2678), freq(853.775000)",
        tgid=2678,
        voice_frequency_hz=853_775_000,
        voice_call=True,
    )

    with patch(
        "pi_p25_scanner.runtime_activity.time.time",
        side_effect=[100.0, 100.1],
    ):
        tracker.record(first)
        tracker.record(second)

    assert tracker.snapshot()["distinct_voice_calls"] == 2


def test_recent_events_remain_bounded() -> None:
    tracker = RuntimeActivityTracker()

    for tgid in range(RECENT_EVENT_LIMIT + 10):
        tracker.record(_update(tgid))

    snapshot = tracker.snapshot()

    assert len(snapshot["recent_events"]) == RECENT_EVENT_LIMIT
    assert snapshot["recent_events"][0]["tgid"] == 10


def test_concurrent_record_and_snapshot_are_consistent() -> None:
    tracker = RuntimeActivityTracker()
    updates_per_worker = 250
    worker_count = 8

    def writer(worker: int) -> None:
        start = worker * updates_per_worker
        for value in range(start, start + updates_per_worker):
            tracker.record(_update(value))

    def reader() -> None:
        for _ in range(500):
            snapshot = tracker.snapshot()
            assert snapshot["unique_tgid_count"] == len(snapshot["unique_tgids"])
            assert len(snapshot["recent_events"]) <= RECENT_EVENT_LIMIT

    with ThreadPoolExecutor(max_workers=worker_count + 2) as executor:
        futures = [
            executor.submit(writer, worker)
            for worker in range(worker_count)
        ]
        futures.extend(executor.submit(reader) for _ in range(2))

        for future in futures:
            future.result()

    snapshot = tracker.snapshot()

    assert snapshot["parsed_status_lines"] == updates_per_worker * worker_count
    assert snapshot["talkgroup_updates"] == updates_per_worker * worker_count
    assert snapshot["unique_tgid_count"] == UNIQUE_TGID_LIMIT


def test_reset_clears_bounded_state() -> None:
    tracker = RuntimeActivityTracker()
    tracker.record(_update(1234))

    snapshot = tracker.reset()

    assert snapshot["parsed_status_lines"] == 0
    assert snapshot["distinct_voice_calls"] == 0
    assert snapshot["unique_tgid_count"] == 0
    assert snapshot["unique_tgids"] == []
    assert snapshot["recent_events"] == []
