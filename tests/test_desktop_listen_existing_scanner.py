from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_desktop_running_scanner_offers_enabled_listen_without_restart() -> None:
    app = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert "function desktopScannersRunning(status)" in app
    assert "? (listening ? 'Listening' : 'Listen')" in app
    assert "startBtn.disabled = running ? listening : !canStart" in app
    assert "const alreadyRunning = desktopScannersRunning(latestStatus)" in app
    assert "if (!alreadyRunning)" in app
    assert app.count("postJson('/api/scanner/start')") == 1
    assert "Browser audio attached; scanners were left running" in app
    assert "await window.__scannerBrowserAudio?.stop?.()" in app


def test_desktop_pcm_controller_exposes_per_browser_attachment_state() -> None:
    script = (ROOT / "web" / "audio_arbitrator_live.js").read_text(
        encoding="utf-8"
    )

    assert "window.__scannerBrowserAudio =" in script
    assert "start: startAudio" in script
    assert "isAttached: () => attached" in script
    assert "async function pumpAudioStream()" in script
    assert "pumpAudioStream().catch" in script
    assert "scanner-browser-audio-state" in script
    assert "/audio.pcm?_=" in script
    assert "if (nextPlayTime - now > MAX_QUEUED_SECONDS)" in script
    assert "droppedFrames += 1" in script
    assert "diagnostics: () =>" in script
    assert "audio-worklet-ring-buffer" in script
    assert "new AudioWorkletNode" in script
    assert "pcm-player-worklet.js?v=1.0.0" in script
    assert "script-processor-ring-buffer" in script
    assert "new window.ScannerPcmRingPlayer" in script


def test_desktop_listen_fix_has_a_shared_cache_buster() -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")

    assert "2.2.2-clock-recovered-ring-player" in html
    assert 'src="pcm-ring-player.js?v=1.0.1-clock-recovery"' in html
    assert 'src="audio_arbitrator_live.js?v=2.2.2-clock-recovered-ring-player-3.0.1-roc-subpath"' in html


def test_audio_worklet_uses_a_bounded_continuous_ring_buffer() -> None:
    script = (ROOT / "web" / "pcm-player-worklet.js").read_text(encoding="utf-8")

    assert "class ScannerPcmPlayer extends AudioWorkletProcessor" in script
    assert "this.maxSamples" in script
    assert "this.droppedSamples += 1" in script
    assert "this.underruns += 1" in script
    assert "registerProcessor('scanner-pcm-player'" in script
    assert "rateCorrection" in script


def test_http_compatible_player_uses_one_bounded_audio_node() -> None:
    script = (ROOT / "web" / "pcm-ring-player.js").read_text(encoding="utf-8")

    assert "class ScannerPcmRingPlayer" in script
    assert "createScriptProcessor(2048, 0, 1)" in script
    assert "this.maxSamples" in script
    assert "this.droppedSamples += 1" in script
    assert "this.underruns += 1" in script
    assert "rateCorrection" in script
