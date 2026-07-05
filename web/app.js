'use strict';

let currentConfig = null;
let latestStatus = null;

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

function setButtonsForState(status) {
  const startBtn = field('startBtn');
  const stopBtn = field('stopBtn');
  const running = Boolean(status?.decoder_process?.running);
  const startEnabled = Boolean(status?.decoder_process?.start_enabled);
  if (startBtn) startBtn.disabled = running || !startEnabled;
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

function renderStatus(status) {
  latestStatus = status;
  const process = status.decoder_process || {};
  const marker = process.validated_marker || {};
  const running = Boolean(process.running);
  const markerReady = Boolean(marker.exists && marker.validated);
  const state = status.scanner_state || '-';

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
  setText('processState', running ? 'running' : 'stopped');
  setText('decoderPid', process.pid || '-');
  setText('launchReady', process.start_enabled ? 'yes' : 'no');
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

refreshStatus();
refreshConfig();
setInterval(refreshStatus, 3000);
