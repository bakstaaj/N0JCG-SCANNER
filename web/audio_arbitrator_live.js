(function installLowLatencyScannerAudio() {
  if (window.__lowLatencyScannerAudioInstalled) return;
  window.__lowLatencyScannerAudioInstalled = true;

  const SAMPLE_RATE = 8000;
  const FRAME_SAMPLES = 160;
  const FRAME_BYTES = FRAME_SAMPLES * 2;
  const PLAYBACK_LEAD_SECONDS = 0.06;
  const MAX_QUEUED_SECONDS = 0.45;

  let context = null;
  let gainNode = null;
  let reader = null;
  let running = false;
  let stopping = false;
  let audioWanted = false;
  let reconnectTimer = null;
  let nextPlayTime = 0;
  let pending = new Uint8Array(0);

  const field = (id) => document.getElementById(id);

  function setStatus(message) {
    const node = field('browserAudioLastEvent');
    if (node) node.textContent = message;
  }

  function stopNativeWavPlayer() {
    const player = field('browserAudioPlayer');
    if (!player) return;
    try {
      player.pause();
      player.removeAttribute('src');
      player.load();
    } catch (_error) {}
  }

  function applyVolume() {
    if (!gainNode) return;

    const slider =
      field('arbitratorAudioVolume') ||
      field('audioVolume');

    const value = slider ? Number(slider.value) : 100;
    gainNode.gain.value = Math.max(0, Math.min(1, value / 100));
  }

  async function ensureContext() {
    if (!context) {
      context = new (
        window.AudioContext ||
        window.webkitAudioContext
      )({
        sampleRate: SAMPLE_RATE,
        latencyHint: 'interactive'
      });

      gainNode = context.createGain();
      window.__scannerAudioGainNode = gainNode;
      gainNode.connect(context.destination);
      applyVolume();
    }

    if (context.state === 'suspended') {
      await context.resume();
    }
  }

  function appendBytes(a, b) {
    const joined = new Uint8Array(a.length + b.length);
    joined.set(a, 0);
    joined.set(b, a.length);
    return joined;
  }

  function scheduleFrame(bytes) {
    if (!context || !gainNode) return;

    const buffer = context.createBuffer(
      1,
      FRAME_SAMPLES,
      SAMPLE_RATE
    );

    const channel = buffer.getChannelData(0);
    const view = new DataView(
      bytes.buffer,
      bytes.byteOffset,
      bytes.byteLength
    );

    for (let i = 0; i < FRAME_SAMPLES; i += 1) {
      channel[i] = view.getInt16(i * 2, true) / 32768;
    }

    const now = context.currentTime;

    if (
      nextPlayTime < now + PLAYBACK_LEAD_SECONDS ||
      nextPlayTime - now > MAX_QUEUED_SECONDS
    ) {
      nextPlayTime = now + PLAYBACK_LEAD_SECONDS;
    }

    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(gainNode);
    source.start(nextPlayTime);

    nextPlayTime += FRAME_SAMPLES / SAMPLE_RATE;
  }

  async function startAudio() {
    if (running) return;

    audioWanted = true;
    running = true;
    stopping = false;
    pending = new Uint8Array(0);
    nextPlayTime = 0;

    stopNativeWavPlayer();
    await ensureContext();

    const response = await fetch(
      `http://${window.location.hostname}:8072/audio.pcm?_=${Date.now()}`,
      {
        cache: 'no-store',
        mode: 'cors'
      }
    );

    if (!response.ok || !response.body) {
      throw new Error(`PCM stream HTTP ${response.status}`);
    }

    reader = response.body.getReader();
    setStatus('Low-latency scanner audio connected');

    while (!stopping) {
      const result = await reader.read();
      if (result.done) break;

      pending = appendBytes(pending, result.value);

      while (pending.length >= FRAME_BYTES) {
        const frame = pending.slice(0, FRAME_BYTES);
        pending = pending.slice(FRAME_BYTES);
        scheduleFrame(frame);
      }
    }

    reader = null;
    running = false;
    if (!stopping && audioWanted) scheduleReconnect('Audio stream ended');
  }

  function scheduleReconnect(reason) {
    running = false;
    if (stopping || !audioWanted || reconnectTimer) return;
    setStatus(`${reason}; reconnecting audio`);
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null;
      startAudio().catch((error) => {
        scheduleReconnect(`Audio reconnect failed: ${error.message}`);
      });
    }, 500);
  }

  async function stopAudio() {
    stopping = true;
    audioWanted = false;
    running = false;

    if (reconnectTimer) {
      window.clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }

    if (reader) {
      try {
        await reader.cancel();
      } catch (_error) {}
    }

    reader = null;
    pending = new Uint8Array(0);
    nextPlayTime = 0;
    setStatus('Scanner audio stopped');
  }

  function wireControls() {
    const volume =
      field('arbitratorAudioVolume') ||
      field('audioVolume');

    if (volume && !volume.dataset.lowLatencyWired) {
      volume.dataset.lowLatencyWired = '1';
      volume.addEventListener('input', applyVolume);
    }

    const start = field('startBtn');

    if (start && !start.dataset.lowLatencyWired) {
      start.dataset.lowLatencyWired = '1';

      start.addEventListener('click', () => {
        window.setTimeout(() => {
          startAudio().catch((error) => {
            scheduleReconnect(`Low-latency audio error: ${error.message}`);
          });
        }, 100);
      });
    }

    const stop =
      field('stopBtn') ||
      field('stopScannerBtn');

    if (stop && !stop.dataset.lowLatencyWired) {
      stop.dataset.lowLatencyWired = '1';
      stop.addEventListener('click', stopAudio);
    }
  }

  wireControls();
  window.setInterval(wireControls, 1000);
})();


/* AUDIO_CONTROLS_ONLY_MUTE_V106 */
(function () {
  let muted = false;
  const field = (id) => document.getElementById(id);

  function volumeValue() {
    const slider = field('arbitratorAudioVolume');
    const value = slider ? Number(slider.value) : 80;
    return Math.max(0, Math.min(1, value / 100));
  }

  function apply() {
    const button = field('arbitratorMuteBtn');
    if (button) {
      button.textContent = muted ? 'Unmute' : 'Mute';
      button.setAttribute('aria-pressed', muted ? 'true' : 'false');
    }

    const gain = window.__scannerAudioGainNode;
    if (gain && gain.gain) {
      gain.gain.value = muted ? 0 : volumeValue();
    }
  }

  function wire() {
    const button = field('arbitratorMuteBtn');
    if (button && !button.dataset.v106Wired) {
      button.dataset.v106Wired = '1';
      button.addEventListener('click', function () {
        muted = !muted;
        apply();
      });
    }

    const slider = field('arbitratorAudioVolume');
    if (slider && !slider.dataset.v106Wired) {
      slider.dataset.v106Wired = '1';
      slider.addEventListener('input', function () {
        if (muted) muted = false;
        apply();
      });
    }

    apply();
  }

  wire();
  window.setInterval(wire, 1000);
})();
