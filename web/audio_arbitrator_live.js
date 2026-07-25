(function installUnifiedArbitratorAudio() {
  if (window.__unifiedArbitratorAudioInstalled) return;
  window.__unifiedArbitratorAudioInstalled = true;
  const SAMPLE_RATE = 8000;
  const FRAME_SAMPLES = 160;
  const MAX_QUEUED_SECONDS = 0.35;
  let context = null;
  let gainNode = null;
  let reader = null;
  let running = false;
  let stopping = false;
  let nextPlayTime = 0;
  let pending = new Uint8Array(0);

    let muted = false;
const field = (id) => document.getElementById(id);
  function setStatus(message) { const node = field('browserAudioLastEvent'); if (node) node.textContent = message; }
  function setSource(source) { const node = field('arbitratorAudioSource'); if (node) node.textContent = source || 'Idle'; }
  function applyVolume() {
    const slider = field('arbitratorAudioVolume');
    const value = slider ? Number(slider.value) : 80;
    if (gainNode) {
      gainNode.gain.value = muted ? 0 : Math.max(0, Math.min(1, value / 100));
    }
  }

  function updateMuteButton() {
    const button = field('arbitratorMuteBtn');
    if (!button) return;
    button.textContent = muted ? 'Unmute' : 'Mute';
    button.setAttribute('aria-pressed', muted ? 'true' : 'false');
    button.classList.toggle('active', muted);
  }

  function toggleMute() {
    muted = !muted;
    applyVolume();
    updateMuteButton();
    setStatus(muted ? 'Unified audio muted' : 'Unified audio unmuted');
  }
  async function ensureContext() {
    if (!context) {
      context = new (window.AudioContext || window.webkitAudioContext)({ sampleRate: SAMPLE_RATE, latencyHint: 'interactive' });
      gainNode = context.createGain();
      gainNode.connect(context.destination);
      applyVolume();
    }
    if (context.state === 'suspended') await context.resume();
  }
  function appendBytes(a, b) { const joined = new Uint8Array(a.length + b.length); joined.set(a); joined.set(b, a.length); return joined; }
  function scheduleFrame(bytes) {
    if (!context || !gainNode) return;
    const buffer = context.createBuffer(1, FRAME_SAMPLES, SAMPLE_RATE);
    const channel = buffer.getChannelData(0);
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    for (let i = 0; i < FRAME_SAMPLES; i += 1) channel[i] = view.getInt16(i * 2, true) / 32768;
    const now = context.currentTime;
    if (nextPlayTime < now + 0.02 || nextPlayTime - now > MAX_QUEUED_SECONDS) nextPlayTime = now + 0.02;
    const source = context.createBufferSource();
    source.buffer = buffer;
    source.connect(gainNode);
    source.start(nextPlayTime);
    nextPlayTime += FRAME_SAMPLES / SAMPLE_RATE;
  }
  async function startAudio() {
    if (running) return;
    running = true; stopping = false; nextPlayTime = 0; pending = new Uint8Array(0);
    await ensureContext();
    const response = await fetch(`http://${window.location.hostname}:8072/audio.pcm?_=${Date.now()}`, { cache: 'no-store', mode: 'cors' });
    if (!response.ok || !response.body) throw new Error(`PCM stream HTTP ${response.status}`);
    reader = response.body.getReader();
    setStatus('Unified audio connected');
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
    reader = null; running = false;
  }
  async function stopAudio() {
    stopping = true; running = false;
    if (reader) { try { await reader.cancel(); } catch (_error) {} }
    reader = null; pending = new Uint8Array(0); nextPlayTime = 0;
    setSource('Idle'); setStatus('Scanner audio stopped');
  }
  async function pollArbitrator() {
    try {
      const response = await fetch(`http://${window.location.hostname}:8072/api/audio/status?_=${Date.now()}`, { cache: 'no-store', mode: 'cors' });
      if (!response.ok) return;
      const status = await response.json();
      setSource(status.active_source || 'Idle');
    } catch (_error) { setSource('Offline'); }
  }
  function wireControls() {
    const volume = field('arbitratorAudioVolume');
    if (volume && !volume.dataset.arbitratorWired) { volume.dataset.arbitratorWired = '1'; volume.addEventListener('input', applyVolume); }
    const mute = field('arbitratorMuteBtn');
    if (mute && !mute.dataset.arbitratorWired) {
      mute.dataset.arbitratorWired = '1';
      mute.addEventListener('click', toggleMute);
      updateMuteButton();
    }

    const start = field('startBtn');
    if (start && !start.dataset.arbitratorWired) {
      start.dataset.arbitratorWired = '1';
      start.addEventListener('click', () => window.setTimeout(() => startAudio().catch((error) => { running = false; setStatus(`Unified audio error: ${error.message}`); }), 100));
    }
    const stop = field('stopBtn');
    if (stop && !stop.dataset.arbitratorWired) { stop.dataset.arbitratorWired = '1'; stop.addEventListener('click', stopAudio); }
  }
  wireControls(); pollArbitrator();
  window.setInterval(wireControls, 1000);
  window.setInterval(pollArbitrator, 250);
})();
