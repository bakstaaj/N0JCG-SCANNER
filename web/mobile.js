'use strict';

const ROLE_BY_SOURCE = { VHF: 'analog_2m', UHF: 'analog_70cm' };
const SAMPLE_RATE = 8000;
const FRAME_SAMPLES = 160;
const FRAME_BYTES = FRAME_SAMPLES * 2;
const PLAYBACK_LEAD_SECONDS = 0.06;
const MAX_QUEUED_SECONDS = 0.45;
const state = {
  backend: null,
  analog: null,
  audio: null,
  controls: null,
  activeRole: null,
  busy: false,
  polling: false,
  audioAttached: false,
  audioWanted: false,
  muted: false,
};
const pcm = {
  context: null,
  gain: null,
  worklet: null,
  workletStats: null,
  ringPlayer: null,
  reader: null,
  running: false,
  stopping: false,
  nextPlayTime: 0,
  pending: new Uint8Array(0),
  droppedFrames: 0,
  reconnectTimer: null,
};

function byId(id) { return document.getElementById(id); }
function setText(id, value) { const node = byId(id); if (node) node.textContent = value ?? '-'; }
function formatHz(value) { return value ? `${(Number(value) / 1e6).toFixed(5)} MHz` : 'Frequency unavailable'; }

const PI_SCANNER_BASE_PATH = window.location.pathname === '/pi-scanner'
  || window.location.pathname.startsWith('/pi-scanner/')
  ? '/pi-scanner'
  : '';

function applicationUrl(value) {
  const url = String(value || '');
  if (!PI_SCANNER_BASE_PATH || !url.startsWith('/')) return url;
  if (url.startsWith('/api/')) return `${PI_SCANNER_BASE_PATH}${url}`;
  if (url.startsWith('/radio/')) {
    return `${PI_SCANNER_BASE_PATH}/audio-api${url.slice('/radio'.length)}`;
  }
  return url;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(applicationUrl(url), { cache: 'no-store', ...options });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

function postJson(url, payload = {}) {
  return fetchJson(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
}

function setBadge(id, label, style) {
  const badge = byId(id);
  if (!badge) return;
  badge.textContent = label;
  badge.className = `badge ${style}`;
}

function renderRegistration(registration = {}) {
  const badge = byId('registrationBadge');
  if (!badge) return;
  setText('registrationSerial', registration.serial_number || '-');
  setText(
    'registrationStatusText',
    registration.registered
      ? `Registered ${registration.license_suffix || ''} for ${registration.serial_number || ''}`
      : `Installation S/N: ${registration.serial_number || '-'} · Five-minute trial`,
  );
  badge.title = registration.serial_number
    ? `Scanner S/N ${registration.serial_number}`
    : 'Scanner registration unavailable';
  if (registration.registered) {
    setBadge('registrationBadge', 'Registered', 'online');
  } else if (registration.trial_expired) {
    setBadge('registrationBadge', 'Trial ended', 'offline');
  } else if (registration.trial_active) {
    const seconds = Math.max(0, Number(registration.trial_remaining_seconds || 0));
    const label = `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`;
    setBadge('registrationBadge', `Trial ${label}`, 'scanning');
  } else {
    setBadge('registrationBadge', 'Unregistered', 'pending');
  }
}

async function activateLicense() {
  if (state.busy) return;
  state.busy = true;
  render();
  try {
    await postJson('/api/license/activate', {
      license_serial: String(byId('licenseSerialInput')?.value || '').trim(),
      email: String(byId('licenseEmailInput')?.value || '').trim(),
    });
    if (byId('licenseSerialInput')) byId('licenseSerialInput').value = '';
    setText('message', 'License activated');
  } catch (error) {
    setText('message', `Activation failed: ${error.message}`);
  } finally {
    state.busy = false;
    await poll();
  }
}

function applyAudioLevel() {
  if (!pcm.gain) return;
  const volume = Number(byId('volumeSlider').value) / 100;
  pcm.gain.gain.value = state.muted ? 0 : Math.max(0, Math.min(1, volume));
}

async function ensureAudioContext() {
  if (!pcm.context) {
    const AudioContextClass = window.AudioContext || window.webkitAudioContext;
    if (!AudioContextClass) throw new Error('Web Audio is not supported by this browser');
    // Let the phone choose its hardware output rate. AudioBuffer resampling
    // handles the 8 kHz scanner frames, and avoids mobile devices that reject
    // a forced 8 kHz AudioContext.
    pcm.context = new AudioContextClass({ latencyHint: 'interactive' });
    pcm.gain = pcm.context.createGain();
    pcm.gain.connect(pcm.context.destination);
    applyAudioLevel();
    if (pcm.context.audioWorklet && window.AudioWorkletNode) {
      try {
        await pcm.context.audioWorklet.addModule(
          new URL('pcm-player-worklet.js?v=1.0.0', window.location.href).href,
        );
        pcm.worklet = new AudioWorkletNode(pcm.context, 'scanner-pcm-player', {
          numberOfInputs: 0,
          numberOfOutputs: 1,
          outputChannelCount: [1],
          processorOptions: { inputRate: SAMPLE_RATE, startSamples: 640, maxSamples: 3600 },
        });
        pcm.worklet.port.onmessage = (event) => {
          if (event.data?.type === 'stats') pcm.workletStats = event.data;
        };
        pcm.worklet.connect(pcm.gain);
      } catch (_error) {
        pcm.worklet = null;
      }
    }
    if (!pcm.worklet && window.ScannerPcmRingPlayer) {
      pcm.ringPlayer = new window.ScannerPcmRingPlayer(pcm.context, pcm.gain, {
        inputRate: SAMPLE_RATE,
        startSamples: 640,
        maxSamples: 3600,
      });
    }
  }
  if (pcm.context.state === 'suspended') await pcm.context.resume();
}

function appendBytes(first, second) {
  const joined = new Uint8Array(first.length + second.length);
  joined.set(first, 0);
  joined.set(second, first.length);
  return joined;
}

function scheduleFrame(bytes) {
  if (!pcm.context || !pcm.gain) return;
  if (pcm.worklet) {
    const samples = new Int16Array(FRAME_SAMPLES);
    const input = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    for (let index = 0; index < FRAME_SAMPLES; index += 1) {
      samples[index] = input.getInt16(index * 2, true);
    }
    pcm.worklet.port.postMessage({ type: 'pcm', samples: samples.buffer }, [samples.buffer]);
    return;
  }
  if (pcm.ringPlayer) {
    const samples = new Int16Array(FRAME_SAMPLES);
    const input = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    for (let index = 0; index < FRAME_SAMPLES; index += 1) {
      samples[index] = input.getInt16(index * 2, true);
    }
    pcm.ringPlayer.enqueue(samples);
    return;
  }
  const buffer = pcm.context.createBuffer(1, FRAME_SAMPLES, SAMPLE_RATE);
  const channel = buffer.getChannelData(0);
  const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
  for (let index = 0; index < FRAME_SAMPLES; index += 1) {
    channel[index] = view.getInt16(index * 2, true) / 32768;
  }
  const now = pcm.context.currentTime;
  if (pcm.nextPlayTime < now + PLAYBACK_LEAD_SECONDS) {
    pcm.nextPlayTime = now + PLAYBACK_LEAD_SECONDS;
  }
  // Do not rewind into already-scheduled sources. That overlaps audio and
  // sounds like a buffer overrun; discard excess input while the queue drains.
  if (pcm.nextPlayTime - now > MAX_QUEUED_SECONDS) {
    pcm.droppedFrames += 1;
    return;
  }
  const source = pcm.context.createBufferSource();
  source.buffer = buffer;
  source.connect(pcm.gain);
  source.start(pcm.nextPlayTime);
  pcm.nextPlayTime += FRAME_SAMPLES / SAMPLE_RATE;
}

async function pumpPcmStream() {
  try {
    while (!pcm.stopping && pcm.reader) {
      const result = await pcm.reader.read();
      if (result.done) break;
      pcm.pending = appendBytes(pcm.pending, result.value);
      while (pcm.pending.length >= FRAME_BYTES) {
        scheduleFrame(pcm.pending.slice(0, FRAME_BYTES));
        pcm.pending = pcm.pending.slice(FRAME_BYTES);
      }
    }
  } catch (error) {
    if (!pcm.stopping) setText('message', `Audio stream error: ${error.message}`);
  } finally {
    pcm.reader = null;
    pcm.running = false;
    if (!pcm.stopping && state.audioWanted) {
      state.audioAttached = false;
      scheduleMobileReconnect('Audio stream ended');
      render();
    }
  }
}

function scheduleMobileReconnect(reason) {
  if (pcm.stopping || !state.audioWanted || pcm.reconnectTimer) return;
  setText('message', `${reason}; reconnecting...`);
  pcm.reconnectTimer = window.setTimeout(() => {
    pcm.reconnectTimer = null;
    attachAudio().catch((error) => {
      scheduleMobileReconnect(`Audio reconnect failed: ${error.message}`);
    });
  }, 500);
}

async function attachAudio() {
  state.audioWanted = true;
  await ensureAudioContext();
  if (pcm.running) {
    state.audioAttached = true;
    setText('message', 'Phone audio connected');
    return;
  }
  pcm.stopping = false;
  pcm.pending = new Uint8Array(0);
  pcm.nextPlayTime = 0;
  pcm.droppedFrames = 0;
  pcm.workletStats = null;
  pcm.worklet?.port.postMessage({ type: 'reset' });
  pcm.ringPlayer?.reset();
  const response = await fetch(
    applicationUrl(`/radio/audio.pcm?_=${Date.now()}`),
    { cache: 'no-store', credentials: 'same-origin' },
  );
  if (!response.ok || !response.body) throw new Error(`PCM stream HTTP ${response.status}`);
  pcm.reader = response.body.getReader();
  pcm.running = true;
  state.audioAttached = true;
  setText('message', 'Phone audio connected');
  pumpPcmStream();
}

async function stopAudio() {
  pcm.stopping = true;
  state.audioWanted = false;
  state.audioAttached = false;
  if (pcm.reconnectTimer) {
    window.clearTimeout(pcm.reconnectTimer);
    pcm.reconnectTimer = null;
  }
  if (pcm.reader) {
    try { await pcm.reader.cancel(); } catch (_error) {}
  }
  pcm.reader = null;
  pcm.running = false;
  pcm.pending = new Uint8Array(0);
  pcm.nextPlayTime = 0;
}

function scannersRunning() {
  const coordinated = state.backend?.coordinated_scanners || {};
  return Boolean(state.backend?.decoder_process?.running)
    || ['running', 'active'].includes(String(coordinated.vhf || '').toLowerCase())
    || ['running', 'active'].includes(String(coordinated.uhf || '').toLowerCase());
}

function startReady() {
  const process = state.backend?.decoder_process || {};
  const marker = process.validated_marker || {};
  return Boolean(process.start_enabled || marker.start_ready || (marker.exists && marker.validated));
}

function latestTalkgroup() {
  const backend = state.backend || {};
  const events = backend.activity_summary?.recent_events || [];
  const fallback = [...events].reverse().find((item) => item?.tgid || item?.talkgroup_label) || {};
  const tgid = backend.active_tgid || backend.last_active_tgid || fallback.tgid;
  const labels = backend.talkgroup_catalog?.labels || {};
  const label = labels[String(tgid)]
    || backend.active_talkgroup_label
    || backend.last_active_talkgroup_label
    || fallback.talkgroup_label
    || (tgid ? 'Unmapped talkgroup' : 'Waiting for activity');
  return {
    label,
    detail: tgid ? `TGID ${tgid} · ${formatHz(backend.active_voice_frequency_hz || backend.last_active_voice_frequency_hz || fallback.voice_frequency_hz)}` : 'No P25 call heard yet',
  };
}

function roleChannel(role) {
  const roleStatus = state.analog?.roles?.[role] || {};
  const current = roleStatus.current_channel || {};
  const last = roleStatus.last_lock || {};
  const live = String(roleStatus.state || '').toLowerCase() === 'locked';
  return {
    name: (live ? current.name : last.name) || current.name || last.name || (role === 'analog_2m' ? 'VHF channel' : 'UHF channel'),
    frequencyHz: (live ? current.frequency_hz : last.frequency_hz) || current.frequency_hz || last.frequency_hz,
  };
}

function hasSuppressions() {
  return ['analog_2m', 'analog_70cm'].some((role) => {
    const item = state.controls?.roles?.[role] || {};
    return (item.blocked_frequencies_hz || []).length > 0
      || Object.values(item.skip_until_epoch || {}).some((until) => Number(until) > Date.now() / 1000);
  });
}

function renderNowPlaying() {
  const source = String(state.audio?.active_source || '').toUpperCase();
  state.activeRole = ROLE_BY_SOURCE[source] || null;
  setText('activeSource', source || 'Idle');

  if (state.activeRole) {
    const channel = roleChannel(state.activeRole);
    setText('activeLabel', channel.name);
    setText('activeDetail', formatHz(channel.frequencyHz));
    setText('analogControlTarget', `${source}: ${channel.name}`);
    return;
  }

  const talkgroup = latestTalkgroup();
  setText('activeLabel', talkgroup.label);
  setText('activeDetail', talkgroup.detail);
  setText('analogControlTarget', 'No active lock');
}

function render() {
  const running = scannersRunning();
  const audioPlaying = state.audioAttached;
  const source = String(state.audio?.active_source || '').toUpperCase();
  setBadge('onlineBadge', state.backend ? 'Connected' : 'Offline', state.backend ? 'online' : 'offline');
  if (source) setBadge('scannerBadge', `${source} On Air`, 'on-air');
  else if (running) setBadge('scannerBadge', 'Scanning', 'scanning');
  else setBadge('scannerBadge', 'Stopped', 'idle');

  const coordinated = state.backend?.coordinated_scanners || {};
  setText('p25State', coordinated.p25 || (state.backend?.decoder_process?.running ? 'running' : 'stopped'));
  setText('vhfState', coordinated.vhf || '-');
  setText('uhfState', coordinated.uhf || '-');
  setText('voiceCalls', state.backend?.activity_summary?.distinct_voice_calls || 0);
  setText('vhfLocks', state.analog?.roles?.analog_2m?.lock_count || 0);
  setText('uhfLocks', state.analog?.roles?.analog_70cm?.lock_count || 0);
  renderRegistration(state.backend?.registration || {});
  renderNowPlaying();

  byId('startBtn').textContent = running
    ? (audioPlaying ? 'Listening' : 'Listen')
    : 'Start + Listen';
  byId('startBtn').disabled = state.busy
    || (running ? audioPlaying : !startReady());
  byId('stopBtn').disabled = state.busy || !running;
  for (const id of ['skipBtn', 'blockBtn', 'clearLockBtn']) {
    byId(id).disabled = state.busy || !state.activeRole;
  }
  byId('clearBlocksBtn').disabled = state.busy || !hasSuppressions();
}

async function poll() {
  if (state.polling) return;
  state.polling = true;
  try {
    const [backend, analog, audio, controls] = await Promise.all([
      fetchJson('/api/status'),
      fetchJson('/api/analog/status'),
      fetchJson('/radio/api/audio/status'),
      fetchJson('/api/analog/controls'),
    ]);
    Object.assign(state, { backend, analog, audio, controls });
    render();
  } catch (error) {
    state.backend = null;
    setBadge('onlineBadge', 'Offline', 'offline');
    setText('message', `Connection error: ${error.message}`);
  } finally {
    state.polling = false;
  }
}

async function startScanning(event) {
  if (event.isTrusted === false || state.busy) return;
  state.busy = true;
  render();
  const alreadyRunning = scannersRunning();
  setText('message', alreadyRunning
    ? 'Connecting this phone to audio...'
    : 'Starting P25, VHF, and UHF...');
  try {
    const audioPromise = attachAudio();
    if (!alreadyRunning) {
      state.backend = await postJson('/api/scanner/start');
    }
    await audioPromise;
  } catch (error) {
    setText('message', `${alreadyRunning ? 'Listen' : 'Start'} failed: ${error.message}`);
  } finally {
    state.busy = false;
    await poll();
  }
}

async function stopScanning() {
  if (state.busy) return;
  state.busy = true;
  render();
  setText('message', 'Stopping all scanners...');
  await stopAudio();
  try {
    state.backend = await postJson('/api/scanner/stop');
    setText('message', 'Stopped; all call counters reset');
  } catch (error) {
    setText('message', `Stop failed: ${error.message}`);
  } finally {
    state.busy = false;
    await poll();
  }
}

function toggleMute() {
  state.muted = !state.muted;
  const button = byId('muteBtn');
  button.setAttribute('aria-pressed', String(state.muted));
  button.textContent = state.muted ? 'Unmute' : 'Mute';
  applyAudioLevel();
}

async function analogAction(action) {
  if (state.busy) return;
  state.busy = true;
  render();
  try {
    let result;
    if (action === 'clear_blocks') {
      await Promise.all(['analog_2m', 'analog_70cm'].map((role) => postJson('/api/analog/control', { role, action })));
      result = { message: 'Cleared all skips and blocks' };
    } else if (state.activeRole) {
      result = await postJson('/api/analog/control', { role: state.activeRole, action });
    }
    setText('message', result?.message || 'Analog control updated');
  } catch (error) {
    setText('message', `Control failed: ${error.message}`);
  } finally {
    state.busy = false;
    await poll();
  }
}

byId('startBtn').addEventListener('click', startScanning);
byId('stopBtn').addEventListener('click', stopScanning);
byId('muteBtn').addEventListener('click', toggleMute);
byId('activateLicenseBtn').addEventListener('click', activateLicense);
byId('volumeSlider').addEventListener('input', (event) => {
  if (state.muted && Number(event.target.value) > 0) toggleMute();
  applyAudioLevel();
});
byId('skipBtn').addEventListener('click', () => analogAction('skip'));
byId('blockBtn').addEventListener('click', () => analogAction('block'));
byId('clearLockBtn').addEventListener('click', () => analogAction('clear_lock'));
byId('clearBlocksBtn').addEventListener('click', () => analogAction('clear_blocks'));

poll();
window.setInterval(poll, 1000);
