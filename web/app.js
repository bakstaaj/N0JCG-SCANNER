'use strict';

let currentConfig = null;
let latestStatus = null;
let browserAudioLastEvent = 'Enable browser audio to unlock playback.';
let browserAudioStatus = null;

function formatHz(value) {
  if (!value) return '-';
  return `${(Number(value) / 1000000).toFixed(6)} MHz`;
}

function formatBool(value) {
  return value ? 'yes' : 'no';
}

function formatList(values) {
  if (!Array.isArray(values) || values.length === 0) return '-';
  return values.join('\n');
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value ?? '-';
}

function field(id) {
  return document.getElementById(id);
}

function setBadge(id, text, kind) {
  const el = document.getElementById(id);
  if (!el) return;
  el.textContent = text;
  el.className = `badge ${kind || ''}`.trim();
}

function commandText(command) {
  if (Array.isArray(command)) return command.join(' ');
  if (typeof command === 'string') return command;
  return '';
}

function browserAudioStreamUrl(payload) {
  if (payload?.stream_url) return payload.stream_url;
  return `http://${window.location.hostname}:8072/audio.wav`;
}

function browserAudioToneUrl(payload) {
  if (payload?.test_tone_url) return payload.test_tone_url;
  return `http://${window.location.hostname}:8072/test-tone.wav`;
}

function extractOp25HttpListener(status) {
  const process = status?.decoder_process || {};
  const marker = process.validated_marker || {};
  const combined = `${commandText(process.command)} ${JSON.stringify(marker)}`;
  const match = combined.match(/http:(?:\[[^\]]+\]|[^:\s]+):(\d{1,5})/);
  if (!match) return null;
  const port = Number(match[1]);
  if (!Number.isInteger(port) || port <= 0 || port > 65535) return null;
  return { port, piLocalUrl: `http://127.0.0.1:${port}/` };
}

function markerIsReady(marker) {
  return Boolean(marker?.exists && marker?.validated);
}

function setButtonsForState(status) {
  const startBtn = field('startBtn');
  const stopBtn = field('stopBtn');
  const process = status?.decoder_process || {};
  const marker = process.validated_marker || {};
  const running = Boolean(process.running);
  const canStart = markerIsReady(marker) || Boolean(process.start_enabled);
  if (startBtn) startBtn.disabled = running || !canStart;
  if (stopBtn) stopBtn.disabled = !running;
}

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    cache: 'no-store',
    ...options,
  });
  const text = await response.text();
  let payload = {};
  try {
    payload = text ? JSON.parse(text) : {};
  } catch (error) {
    payload = { ok: false, error: `Invalid JSON: ${error.message}`, raw: text };
  }
  if (!response.ok) {
    throw new Error(payload.error || `HTTP ${response.status}`);
  }
  return payload;
}

function renderBrowserAudioState(payload = browserAudioStatus) {
  browserAudioStatus = payload || browserAudioStatus;
  const status = browserAudioStatus || {};
  const bridge = status.bridge_status || {};
  const running = Boolean(status.running);
  const badgeText = running ? 'Raw Audio Ready' : 'Stopped';
  const badgeKind = running ? 'badge-ok' : 'badge-warn';
  setBadge('browserAudioBadge', badgeText, badgeKind);
  setText('browserAudioBridgeState', running ? `running pid ${status.pid || '-'}` : 'stopped');
  setText('browserAudioStreamSource', browserAudioStreamUrl(status));
  setText('browserAudioPackets', bridge.audio_packets ?? '-');
  setText('browserAudioFlags', bridge.flag_packets ?? '-');
  setText('browserAudioClients', bridge.stream_clients ?? '-');
  setText('browserAudioLastAudio', bridge.last_audio_age_seconds == null ? '-' : `${bridge.last_audio_age_seconds}s ago`);
  setText('browserAudioDevice', 'Browser default');
  setText('browserAudioLastEvent', browserAudioLastEvent);
  const audio = field('browserAudioPlayer');
  if (audio && running && !audio.src) {
    audio.src = browserAudioStreamUrl(status);
  }
}

async function refreshAudioStatus() {
  try {
    const status = await fetchJson('/api/audio/status');
    renderBrowserAudioState(status);
    return status;
  } catch (error) {
    browserAudioLastEvent = `Audio status error: ${error.message}`;
    renderBrowserAudioState({ running: false, bridge_status: {} });
    return null;
  }
}

async function startBrowserAudioBridge() {
  try {
    const status = await fetchJson('/api/audio/start', { method: 'POST' });
    browserAudioLastEvent = 'Raw browser audio bridge started.';
    renderBrowserAudioState(status);
    return status;
  } catch (error) {
    browserAudioLastEvent = `Audio bridge start failed: ${error.message}`;
    renderBrowserAudioState();
    return null;
  }
}

async function stopBrowserAudioBridge() {
  try {
    const audio = field('browserAudioPlayer');
    if (audio) {
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
    }
    const status = await fetchJson('/api/audio/stop', { method: 'POST' });
    browserAudioLastEvent = 'Raw browser audio bridge stopped.';
    renderBrowserAudioState(status);
  } catch (error) {
    browserAudioLastEvent = `Audio bridge stop failed: ${error.message}`;
    renderBrowserAudioState();
  }
}

async function enableBrowserAudio() {
  const status = browserAudioStatus?.running ? browserAudioStatus : await startBrowserAudioBridge();
  const audio = field('browserAudioPlayer');
  if (!audio || !status?.running) {
    browserAudioLastEvent = 'Audio bridge is not running yet.';
    renderBrowserAudioState(status);
    return;
  }
  audio.src = browserAudioStreamUrl(status);
  try {
    await audio.play();
    browserAudioLastEvent = 'Browser audio stream playing.';
  } catch (error) {
    browserAudioLastEvent = `Press play on the audio control if autoplay was blocked: ${error.message}`;
  }
  renderBrowserAudioState(status);
}

async function playBrowserTestTone() {
  const status = browserAudioStatus?.running ? browserAudioStatus : await startBrowserAudioBridge();
  const audio = field('browserAudioPlayer');
  if (!audio || !status?.running) {
    browserAudioLastEvent = 'Audio bridge is not running yet.';
    renderBrowserAudioState(status);
    return;
  }
  audio.src = browserAudioToneUrl(status);
  try {
    await audio.play();
    browserAudioLastEvent = 'Playing bridge-provided test tone.';
  } catch (error) {
    browserAudioLastEvent = `Test tone play failed: ${error.message}`;
  }
  renderBrowserAudioState(status);
}

function formatActivityEvent(event) {
  const pieces = [];
  if (event.tgid) pieces.push(`TGID ${event.tgid}`);
  if (event.talkgroup_label) pieces.push(event.talkgroup_label);
  if (event.voice_frequency_hz) pieces.push(formatHz(event.voice_frequency_hz));
  if (event.control_frequency_hz) pieces.push(`control ${formatHz(event.control_frequency_hz)}`);
  if (event.p25_phase) pieces.push(event.p25_phase);
  if (event.encrypted === true) pieces.push('encrypted');
  if (event.encrypted === false) pieces.push('clear');
  if (event.muted === true) pieces.push('muted');
  return pieces.length ? pieces.join(' | ') : (event.line || '-');
}

function renderActivitySummary(activity) {
  const parsed = Number(activity?.parsed_status_lines || 0);
  setText('activityParsedLines', parsed);
  setText('activityControlUpdates', activity?.control_frequency_updates ?? 0);
  setText('activityVoiceUpdates', activity?.voice_frequency_updates ?? 0);
  setText('activityTalkgroupUpdates', activity?.talkgroup_updates ?? 0);
  setText('activityUniqueTgids', activity?.unique_tgid_count ?? 0);
  setText('activityClearEvents', activity?.clear_voice_events ?? 0);
  setText('activityEncryptedEvents', activity?.encrypted_events ?? 0);
  setText('activityMutedEvents', activity?.muted_events ?? 0);
  const recent = Array.isArray(activity?.recent_events) ? activity.recent_events : [];
  setText('activityRecentEvents', recent.length ? recent.slice(-10).map(formatActivityEvent).join('\n') : 'No parsed activity yet.');
  setBadge('activityBadge', parsed > 0 ? `${parsed} parsed` : 'No activity', parsed > 0 ? 'badge-ok' : 'badge-warn');
}

function renderDashboard(status) {
  const process = status.decoder_process || {};
  const marker = process.validated_marker || {};
  const running = Boolean(process.running);
  const ready = markerIsReady(marker) || Boolean(process.start_enabled);
  const state = status.scanner_state || '-';
  const listener = extractOp25HttpListener(status);
  const warnings = Array.isArray(status.warnings) ? status.warnings : [];

  setText('scannerStateCard', state);
  setText('scannerStateDetail', running ? `PID ${process.pid || '-'}` : 'decoder process stopped');
  setText('controlFrequencyCard', formatHz(status.active_control_frequency_hz));
  setText('controlFrequencyDetail', status.config?.source ? `config: ${status.config.source}` : 'configured active control');
  setText('launchStateCard', ready ? 'Ready' : 'Not ready');
  setText('launchStateDetail', marker.path || 'validated marker missing');
  setText('op25UiCard', listener ? `Pi-local ${listener.port}` : 'Not detected');
  setText('op25UiDetail', listener ? listener.piLocalUrl : 'starts with OP25 runtime');
  setText('op25HttpListener', listener ? listener.piLocalUrl : '-');

  if (running) {
    setText('dashboardSummary', `Scanner is running on ${formatHz(status.active_control_frequency_hz)}. ${warnings.length ? warnings[0] : status.last_event || ''}`);
  } else if (ready) {
    setText('dashboardSummary', `Scanner is ready to start. ${status.last_event || ''}`);
  } else {
    setText('dashboardSummary', `Scanner is not launch-ready. ${warnings[0] || status.last_event || 'Check validated OP25 marker.'}`);
  }
  setBadge('dashboardStateBadge', state, running ? 'badge-ok' : (ready ? 'badge-warn' : 'badge-bad'));
}

function renderStatus(status) {
  latestStatus = status;
  const process = status.decoder_process || {};
  const marker = process.validated_marker || {};
  const running = Boolean(process.running);
  const markerReady = markerIsReady(marker);
  const state = status.scanner_state || '-';

  renderDashboard(status);
  renderBrowserAudioState(status.browser_audio);
  setText('scannerState', state);
  setText('decoderEngine', status.decoder_engine || '-');
  setText('configSource', status.config?.source || '-');
  setText('controlFrequency', formatHz(status.active_control_frequency_hz));
  setText('voiceFrequency', formatHz(status.active_voice_frequency_hz));
  setText('activeTgid', status.active_tgid || '-');
  setText('activeTalkgroupLabel', status.active_talkgroup_label || '-');
  setText('p25Phase', status.p25_phase || '-');
  setText('encrypted', formatBool(status.encrypted));
  setText('muted', formatBool(status.muted));
  renderActivitySummary(status.activity_summary || {});
  setText('processState', running ? 'running' : 'stopped');
  setText('decoderPid', process.pid || '-');
  setText('launchReady', markerReady || process.start_enabled ? 'yes' : 'no');
  setText('commandSource', process.command_source || '-');
  setText('validatedMarkerState', markerReady ? 'validated' : (marker.exists ? 'present' : 'missing'));
  setText('validatedMarkerPath', marker.path || '-');
  setText('op25Cwd', process.cwd || marker.cwd || '-');
  setText('op25DeviceArgs', marker.device_args || '-');
  setText('op25TrunkTsv', marker.trunk_tsv || '-');
  setText('validatedCommand', formatList(process.command));
  setText('lastEvent', status.last_event || '-');
  setText('logTail', formatList(status.log_tail));
  setText('lastUpdated', `Last update: ${new Date().toLocaleTimeString()}`);

  setBadge('connectionStatus', 'Connected', 'badge-ok');
  setBadge('stateBadge', state, running ? 'badge-ok' : (status.ok ? 'badge-warn' : 'badge-bad'));
  setBadge('markerBadge', markerReady ? 'Validated' : (marker.exists ? 'Present' : 'Missing'), markerReady ? 'badge-ok' : 'badge-warn');
  setButtonsForState(status);
}

async function refreshStatus() {
  try {
    const status = await fetchJson('/api/status');
    renderStatus(status);
  } catch (error) {
    setBadge('connectionStatus', 'Offline', 'badge-bad');
    setBadge('dashboardStateBadge', 'Offline', 'badge-bad');
    setText('dashboardSummary', `Status error: ${error.message}`);
    setText('lastEvent', `Status error: ${error.message}`);
  }
}

function toMhzLines(values) {
  return (values || []).map((value) => (Number(value) / 1000000).toFixed(6)).join('\n');
}

function parseFrequencyLines(value) {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const cleaned = line.toLowerCase().replace('mhz', '').replace('hz', '').replace(/[, _]/g, '');
      const numeric = Number(cleaned);
      if (!Number.isFinite(numeric) || numeric <= 0) {
        throw new Error(`Invalid frequency: ${line}`);
      }
      return numeric < 10000 ? Math.round(numeric * 1000000) : Math.round(numeric);
    });
}

function parseTalkgroupLines(value) {
  return value
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => {
      const parts = line.split(',');
      const tgid = Number(parts.shift().trim());
      if (!Number.isInteger(tgid) || tgid <= 0) {
        throw new Error(`Invalid TGID: ${line}`);
      }
      const label = parts.join(',').trim() || String(tgid);
      return { tgid, label, enabled: true };
    });
}

function populateForm(config) {
  const system = config?.systems?.[0] || {};
  field('systemName').value = system.name || '';
  field('siteName').value = system.site || '';
  field('controlChannels').value = toMhzLines(system.control_channels_hz);
  field('voiceChannels').value = toMhzLines(system.voice_channels_hz);
  field('talkgroups').value = (system.talkgroups || [])
    .filter((tg) => tg.enabled !== false)
    .map((tg) => `${tg.tgid}, ${tg.label || tg.tgid}`)
    .join('\n');
  field('controlSerial').value = system.receiver_roles?.p25_control?.rtl_serial || '';
  field('voiceSerial').value = system.receiver_roles?.p25_voice?.rtl_serial || '';
  field('gainDb').value = system.receiver_roles?.p25_control?.gain_db ?? 40.2;
  field('ppm').value = system.receiver_roles?.p25_control?.ppm ?? 0;
  field('phaseII').checked = system.decoder?.phase_ii_enabled !== false;
  field('muteEncrypted').checked = system.decoder?.mute_encrypted !== false;
}

function buildConfigFromForm() {
  const gain = field('gainDb').value === '' ? null : Number(field('gainDb').value);
  const ppm = field('ppm').value === '' ? 0 : Number(field('ppm').value);
  const base = currentConfig || { schema_version: 1, systems: [{}] };
  const system = base.systems?.[0] || {};
  return {
    schema_version: Number(base.schema_version || 1),
    systems: [
      {
        ...system,
        name: field('systemName').value.trim() || 'Local P25 System',
        enabled: true,
        mode: 'p25_trunked',
        site: field('siteName').value.trim(),
        control_channels_hz: parseFrequencyLines(field('controlChannels').value),
        voice_channels_hz: parseFrequencyLines(field('voiceChannels').value),
        talkgroups: parseTalkgroupLines(field('talkgroups').value),
        receiver_roles: {
          p25_control: {
            rtl_serial: field('controlSerial').value.trim(),
            gain_db: gain,
            ppm,
          },
          p25_voice: {
            rtl_serial: field('voiceSerial').value.trim(),
            gain_db: gain,
            ppm,
          },
        },
        decoder: {
          ...(system.decoder || {}),
          engine: 'op25',
          phase_ii_enabled: field('phaseII').checked,
          mute_encrypted: field('muteEncrypted').checked,
        },
      },
    ],
  };
}

async function refreshConfig() {
  try {
    const response = await fetchJson('/api/config');
    currentConfig = response.config;
    setText('configPreview', JSON.stringify(response, null, 2));
    if (currentConfig) populateForm(currentConfig);
  } catch (error) {
    setText('configPreview', `Config error: ${error.message}`);
  }
}

async function saveConfig() {
  try {
    const config = buildConfigFromForm();
    const response = await fetchJson('/api/config/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ config }),
    });
    setText('lastEvent', `Saved config: ${response.config_path}`);
    await refreshConfig();
    await refreshStatus();
  } catch (error) {
    setText('lastEvent', `Save error: ${error.message}`);
  }
}

async function initLocalConfig() {
  try {
    const response = await fetchJson('/api/config/init-local', { method: 'POST' });
    setText('lastEvent', `Initialized local config: ${response.config_path}`);
    await refreshConfig();
    await refreshStatus();
  } catch (error) {
    setText('lastEvent', `Init config error: ${error.message}`);
  }
}

async function postScanner(path) {
  try {
    const status = await fetchJson(path, { method: 'POST' });
    renderStatus(status);
  } catch (error) {
    setText('lastEvent', `Control error: ${error.message}`);
  }
  await refreshStatus();
}

document.getElementById('startBtn')?.addEventListener('click', () => postScanner('/api/scanner/start'));
document.getElementById('stopBtn')?.addEventListener('click', () => postScanner('/api/scanner/stop'));
document.getElementById('refreshBtn')?.addEventListener('click', refreshStatus);
document.getElementById('generateConfigBtn')?.addEventListener('click', () => postScanner('/api/decoder/generate-config'));
document.getElementById('loadConfigBtn')?.addEventListener('click', refreshConfig);
document.getElementById('initLocalConfigBtn')?.addEventListener('click', initLocalConfig);
document.getElementById('saveConfigBtn')?.addEventListener('click', saveConfig);
document.getElementById('enableBrowserAudioBtn')?.addEventListener('click', enableBrowserAudio);
document.getElementById('startBrowserAudioBridgeBtn')?.addEventListener('click', startBrowserAudioBridge);
document.getElementById('stopBrowserAudioBridgeBtn')?.addEventListener('click', stopBrowserAudioBridge);
document.getElementById('playBrowserToneBtn')?.addEventListener('click', playBrowserTestTone);

refreshStatus();
refreshConfig();
refreshAudioStatus();
setInterval(refreshStatus, 3000);
setInterval(refreshAudioStatus, 3000);
