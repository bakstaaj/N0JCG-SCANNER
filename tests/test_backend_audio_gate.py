from types import SimpleNamespace
from unittest.mock import patch

from pi_p25_scanner import backend


def test_audio_gate_rate_limits_same_key() -> None:
    manager = SimpleNamespace()

    assert backend._v04h5_audio_gate_allowed(
        manager,
        "100:blocked",
        1000.0,
    )
    assert not backend._v04h5_audio_gate_allowed(
        manager,
        "100:blocked",
        1000.1,
    )


def test_audio_gate_allows_key_after_rate_limit() -> None:
    manager = SimpleNamespace()

    assert backend._v04h5_audio_gate_allowed(
        manager,
        "100:blocked",
        1000.0,
    )
    assert backend._v04h5_audio_gate_allowed(
        manager,
        "100:blocked",
        1000.0 + backend._V04H5_AUDIO_GATE_RATE_LIMIT_SECONDS + 0.1,
    )


def test_audio_gate_cache_is_bounded() -> None:
    manager = SimpleNamespace()

    with patch.object(
        backend,
        "_V04H5_AUDIO_GATE_RATE_LIMIT_SECONDS",
        100000.0,
    ):
        for index in range(
            backend._V04H5_AUDIO_GATE_CACHE_LIMIT + 50
        ):
            assert backend._v04h5_audio_gate_allowed(
                manager,
                f"{index}:blocked",
                1000.0 + index,
            )

    assert len(manager._v04h5_audio_gate_cache) == (
        backend._V04H5_AUDIO_GATE_CACHE_LIMIT
    )


def test_audio_gate_prunes_expired_entries() -> None:
    manager = SimpleNamespace()
    manager._v04h5_audio_gate_cache = {
        "old:blocked": 1.0,
        "recent:blocked": 1000.0,
    }

    allowed = backend._v04h5_audio_gate_allowed(
        manager,
        "new:blocked",
        1000.1,
    )

    assert allowed
    assert "old:blocked" not in manager._v04h5_audio_gate_cache
    assert "recent:blocked" in manager._v04h5_audio_gate_cache
    assert "new:blocked" in manager._v04h5_audio_gate_cache


def test_gate_drops_request_when_queue_is_full() -> None:
    manager = SimpleNamespace()

    with (
        patch.object(
            backend,
            "_v04h5_audio_gate_allowed",
            return_value=True,
        ),
        patch.object(
            backend._V04H5_AUDIO_GATE_SLOTS,
            "acquire",
            return_value=False,
        ),
        patch.object(
            backend._V04H5_AUDIO_GATE_EXECUTOR,
            "submit",
        ) as submit,
    ):
        backend._v04h5_gate_audio_for_tgid(
            manager,
            1234,
            "encrypted",
        )

    submit.assert_not_called()
