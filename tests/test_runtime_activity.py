from concurrent.futures import ThreadPoolExecutor

from pi_p25_scanner.runtime_activity import (
    RECENT_EVENT_LIMIT,
    UNIQUE_TGID_LIMIT,
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
    assert snapshot["unique_tgid_count"] == 0
    assert snapshot["unique_tgids"] == []
    assert snapshot["recent_events"] == []
