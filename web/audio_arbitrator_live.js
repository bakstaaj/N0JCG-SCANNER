(function installLowLatencyScannerAudio() {
  if (window.__lowLatencyScannerAudioInstalled) return;
  window.__lowLatencyScannerAudioInstalled = true;

  const SAMPLE_RATE = 8000;
  const FRAME_SAMPLES = 160;
  const MAX_QUEUED_SECONDS = 0.35;

  let context = null;
  let reader = null;
  let running = false;
  let stopping = false;
  let nextPlayTime = 0;
  let pending = new Uint8Array(0);

  function setStatus(message) {
    const node = document.getElementById('browserAudioLastEvent');
    if (node) node.textContent = message;
  }

  function stopNativePlayer() {
    const player = document.getElementById('browserAudioPlayer');
    if (!player) return;
    try {
      player.pause();
      player.removeAttribute('src');
      player.load();
    } catch (_error) {}
  }

  async function ensureContext() {
    if (!context) {
      context = new (window.AudioContext || window.webkitAudioContext)({
        sampleRate: SAMPLE_RATE,
        latencyHint: 'interactive'
      });
    }
    if (context.state === 'suspended') await context.resume();
  }

  function appendBytes(a, b) {
    const joined = new Uint8Array(a.length + b.length);
    joined.set(a, 0);
    joined.set(b, a.length);
    return joined;
  }

  function scheduleFrame(bytes) {
    const buffer = context.createBuffer(1, FRAME_SAMPLES, SAMPLE_RATE);
    const channel = buffer.getChannelData(0);
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);

    for (let i = 0; i < FRAME_SAMPLES; i += 1) {
      channel[i] = view.getInt16(i * 2, true) / 32768;
    }

    const now = context.currentTime;
    if (nextPlayTime < now + 0.02 || nextPlayTime - now > MAX_QUEUED_SECONDS) {
      nextPlayTime = now + 0.02;
    }

    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(context.destination);
    source.start(nextPlayTime);
    nextPlayTime += FRAME_SAMPLES / SAMPLE_RATE;
  }

  async function startLowLatencyAudio() {
    if (running) return;
    running = true;
    stopping = false;
    pending = new Uint8Array(0);
    nextPlayTime = 0;

    stopNativePlayer();
    await ensureContext();

    const response = await fetch(
      `http://${window.location.hostname}:8072/audio.pcm?_=${Date.now()}`,
      { cache: 'no-store', mode: 'cors' }
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
      while (pending.length >= FRAME_SAMPLES * 2) {
        const frame = pending.slice(0, FRAME_SAMPLES * 2);
        pending = pending.slice(FRAME_SAMPLES * 2);
        scheduleFrame(frame);
      }
    }

    running = false;
    reader = null;
  }

  async function stopLowLatencyAudio() {
    stopping = true;
    running = false;
    if (reader) {
      try { await reader.cancel(); } catch (_error) {}
    }
    reader = null;
    pending = new Uint8Array(0);
    nextPlayTime = 0;
    setStatus('Scanner audio stopped');
  }

  function wireControls() {
    const startButton = document.getElementById('startBtn');
    if (startButton && !startButton.dataset.lowLatencyWired) {
      startButton.dataset.lowLatencyWired = '1';
      startButton.addEventListener('click', () => {
        window.setTimeout(() => {
          startLowLatencyAudio().catch((error) => {
            running = false;
            setStatus(`Low-latency audio error: ${error.message}`);
          });
        }, 100);
      });
    }

    const stopButton = document.getElementById('stopBtn') || document.getElementById('stopScannerBtn');
    if (stopButton && !stopButton.dataset.lowLatencyWired) {
      stopButton.dataset.lowLatencyWired = '1';
      stopButton.addEventListener('click', stopLowLatencyAudio);
    }
  }

  wireControls();
  window.setInterval(wireControls, 1000);
})();
