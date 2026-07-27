(function installLowLatencyScannerAudio() {
  if (window.__lowLatencyScannerAudioInstalled) return;
  window.__lowLatencyScannerAudioInstalled = true;

  const SAMPLE_RATE = 8000;
  const FRAME_SAMPLES = 160;
  const FRAME_BYTES = FRAME_SAMPLES * 2;
  const MAX_QUEUED_SECONDS = 0.35;

  let context = null;
  let gainNode = null;
  let reader = null;
  let running = false;
  let stopping = false;
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
      nextPlayTime < now + 0.02 ||
      nextPlayTime - now > MAX_QUEUED_SECONDS
    ) {
      nextPlayTime = now + 0.02;
    }

    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(gainNode);
    source.start(nextPlayTime);

    nextPlayTime += FRAME_SAMPLES / SAMPLE_RATE;
  }

  async function startAudio() {
    if (running) return;

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
  }

  async function stopAudio() {
    stopping = true;
    running = false;

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
            running = false;
            setStatus(
              `Low-latency audio error: ${error.message}`
            );
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
