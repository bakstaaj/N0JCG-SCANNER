'use strict';

const ROLE_BY_SOURCE = { VHF: 'analog_2m', UHF: 'analog_70cm' };
const state = {
  backend: null,
  analog: null,
  audio: null,
  controls: null,
  activeRole: null,
  busy: false,
  polling: false,
};

function byId(id) { return document.getElementById(id); }
function setText(id, value) { const node = byId(id); if (node) node.textContent = value ?? '-'; }
function formatHz(value) { return value ? `${(Number(value) / 1e6).toFixed(5)} MHz` : 'Frequency unavailable'; }

async function fetchJson(url, options = {}) {
  const response = await fetch(url, { cache: 'no-store', ...options });
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
  const audioPlaying = !byId('scannerAudio').paused;
  const source = String(state.audio?.active_source || '').toUpperCase();
  setBadge('onlineBadge', state.backend ? 'Online' : 'Offline', state.backend ? 'online' : 'offline');
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
      fetchJson(`http://${window.location.hostname}:8072/api/audio/status`, { mode: 'cors' }),
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

async function attachAudio() {
  const audio = byId('scannerAudio');
  if (!audio.src) audio.src = `http://${window.location.hostname}:8072/audio.wav`;
  try {
    await audio.play();
    setText('message', 'Phone audio connected');
  } catch (error) {
    setText('message', `Audio needs another tap: ${error.message}`);
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
  const audio = byId('scannerAudio');
  audio.pause();
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
  const audio = byId('scannerAudio');
  audio.muted = !audio.muted;
  const button = byId('muteBtn');
  button.setAttribute('aria-pressed', String(audio.muted));
  button.textContent = audio.muted ? 'Unmute' : 'Mute';
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
byId('volumeSlider').addEventListener('input', (event) => {
  const audio = byId('scannerAudio');
  audio.volume = Number(event.target.value) / 100;
  if (audio.muted && audio.volume > 0) toggleMute();
});
byId('skipBtn').addEventListener('click', () => analogAction('skip'));
byId('blockBtn').addEventListener('click', () => analogAction('block'));
byId('clearLockBtn').addEventListener('click', () => analogAction('clear_lock'));
byId('clearBlocksBtn').addEventListener('click', () => analogAction('clear_blocks'));

byId('scannerAudio').volume = Number(byId('volumeSlider').value) / 100;
poll();
window.setInterval(poll, 1000);
