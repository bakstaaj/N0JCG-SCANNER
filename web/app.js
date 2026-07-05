'use strict';

function formatHz(value) {
  if (!value) return '-';
  return `${(Number(value) / 1000000).toFixed(6)} MHz`;
}

function setText(id, value) {
  const el = document.getElementById(id);
  if (el) el.textContent = value;
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

async function refreshStatus() {
  try {
    const status = await fetchJson('/api/status');
    setText('scannerState', status.scanner_state || '-');
    setText('decoderEngine', status.decoder_engine || '-');
    setText('controlFrequency', formatHz(status.active_control_frequency_hz));
    setText('voiceFrequency', formatHz(status.active_voice_frequency_hz));
    setText('activeTgid', status.active_tgid || '-');
    setText('p25Phase', status.p25_phase || '-');
    setText('encrypted', status.encrypted ? 'yes' : 'no');
    setText('muted', status.muted ? 'yes' : 'no');
    setText('lastEvent', status.last_event || '-');
  } catch (error) {
    setText('lastEvent', `Status error: ${error.message}`);
  }
}

async function refreshConfig() {
  try {
    const config = await fetchJson('/api/config');
    setText('configPreview', JSON.stringify(config, null, 2));
  } catch (error) {
    setText('configPreview', `Config error: ${error.message}`);
  }
}

async function postScanner(path) {
  try {
    await fetchJson(path, { method: 'POST' });
  } catch (error) {
    setText('lastEvent', `Control error: ${error.message}`);
  }
  await refreshStatus();
}

document.getElementById('startBtn')?.addEventListener('click', () => postScanner('/api/scanner/start'));
document.getElementById('stopBtn')?.addEventListener('click', () => postScanner('/api/scanner/stop'));
document.getElementById('refreshBtn')?.addEventListener('click', refreshStatus);

refreshStatus();
refreshConfig();
setInterval(refreshStatus, 5000);
