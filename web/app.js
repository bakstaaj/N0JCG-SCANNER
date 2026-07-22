'use strict';

// V0.5F_DESKTOP_LAUNCHER_NO_PAGE_AUTOSTART
// Normal browser page loads must not start the scanner automatically.
// The Pi desktop launcher starts the scanner intentionally by calling the backend API,
// then opens this page. Manual browser starts still work from the Start Scanner + Audio button.
window.__P25_REQUIRE_USER_START__ = true;
window.__P25_USER_START_REQUESTED__ = false;
window.__P25_DESKTOP_LAUNCHER_MODE__ = false;
function p25AllowManualStart(event) {
  if (event && event.isTrusted === false) {
    setText('lastEvent', 'Ignored non-user scanner start request.');
    return false;
  }
  window.__P25_USER_START_REQUESTED__ = true;
  return true;
}
(function installP25NoPageAutostartFetchGuard() {
  if (window.__P25_FETCH_GUARD_INSTALLED__) return;
  window.__P25_FETCH_GUARD_INSTALLED__ = true;
  const originalFetch = window.fetch.bind(window);
  window.fetch = function guardedP25Fetch(input, init) {
    const url = typeof input === 'string' ? input : String(input && input.url || '');
    const method = String((init && init.method) || (input && input.method) || 'GET').toUpperCase();
    const scannerStart = method === 'POST' && url.includes('/api/scanner/start');
    if (scannerStart && window.__P25_REQUIRE_USER_START__ && !window.__P25_USER_START_REQUESTED__) {
      const body = JSON.stringify({
        ok: false,
        blocked: true,
        autostart_disabled: true,
        marker: 'V0.5F_DESKTOP_LAUNCHER_NO_PAGE_AUTOSTART',
        error: 'Page-load scanner auto-start is disabled. Use the desktop launcher or the Start Scanner + Audio button.'
      });
      return Promise.resolve(new Response(body, {
        status: 409,
        headers: { 'Content-Type': 'application/json' }
      }));
    }
    return originalFetch(input, init);
  };
})();
// V0.5D_EMERGENCY_UI_RESTORE

let latestStatus = null;
let currentConfig = null;
let rrSystems = [];
let rrSites = [];
let browserAudioLastEvent = 'Ready';
let latestReceiverInventory = null; // PHASE2_MULTI_RECEIVER_INVENTORY_V0_6A
let latestAnalogStatus = null; // PHASE3_ANALOG_WORKER_AUDIO_ARBITER_V0_6B

const CATEGORY_DEFAULTS = [
  'Fire', 'EMS', 'Law Enforcement', 'Public Works', 'Utilities', 'Transportation',
  'Interop', 'Emergency Management', 'Corrections', 'Schools', 'Federal', 'Other',
];

function field(id) { return document.getElementById(id); }
function setText(id, value) { const el = field(id); if (el) el.textContent = value ?? '-'; }
function setBadge(id, text, kind) { const el = field(id); if (!el) return; el.textContent = text; el.className = `pill ${kind || ''}`.trim(); }
function formatHz(value) { return value ? `${(Number(value) / 1000000).toFixed(6)} MHz` : '-'; }
function formatList(values) { return Array.isArray(values) && values.length ? values.join('\n') : '-'; }
function commandText(command) { return Array.isArray(command) ? command.join(' ') : (typeof command === 'string' ? command : ''); }
function audioStreamUrl() { return `http://${window.location.hostname}:8072/audio.wav`; }
function testToneUrl() { return `http://${window.location.hostname}:8072/test-tone.wav`; }
function safeJson(value) { try { return JSON.stringify(value, null, 2); } catch { return String(value); } }
function numberOrNull(value) { const n = Number(value); return Number.isFinite(n) && n > 0 ? n : null; }

async function fetchJson(url, options = {}) {
  const response = await fetch(url, { cache: 'no-store', ...options });
  const text = await response.text();
  let payload = {};
  try { payload = text ? JSON.parse(text) : {}; } catch (error) { payload = { ok: false, error: `Invalid JSON: ${error.message}`, raw: text }; }
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
}

async function postJson(url, payload = {}) {
  return fetchJson(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
}

function openDrawer() { field('drawer')?.classList.add('open'); field('drawerBackdrop')?.classList.add('open'); }
function closeDrawer() { field('drawer')?.classList.remove('open'); field('drawerBackdrop')?.classList.remove('open'); }
function showScreen(id) {
  document.querySelectorAll('.screen').forEach((el) => el.classList.toggle('active', el.id === id));
  document.querySelectorAll('.nav-item').forEach((el) => el.classList.toggle('active', el.dataset.screen === id));
  closeDrawer();
}

function markerIsReady(marker) { return Boolean(marker?.start_ready || (marker?.exists && marker?.validated)); }
function extractOp25HttpListener(status) {
  const process = status?.decoder_process || {};
  const marker = process.validated_marker || {};
  const combined = `${commandText(process.command)} ${safeJson(marker)}`;
  const match = combined.match(/http:(?:\[[^\]]+\]|[^:\s]+):(\d{1,5})/);
  if (!match) return null;
  const port = Number(match[1]);
  return Number.isInteger(port) && port > 0 && port < 65536 ? { port, piLocalUrl: `http://127.0.0.1:${port}/` } : null;
}

function setButtonsForState(status) {
  const process = status?.decoder_process || {};
  const marker = process.validated_marker || {};
  const running = Boolean(process.running);
  const canStart = markerIsReady(marker) || Boolean(process.start_enabled);
  const startBtn = field('startBtn');
  const stopBtn = field('stopBtn');
  if (startBtn) startBtn.disabled = running || !canStart;
  if (stopBtn) stopBtn.disabled = !running;
}

function bestTalkgroup(status) {
  const recent = Array.isArray(status?.activity_summary?.recent_events) ? status.activity_summary.recent_events : [];
  const fallback = [...recent].reverse().find((event) => event && (event.tgid || event.talkgroup_label));
  const tgid = status?.active_tgid || status?.last_active_tgid || fallback?.tgid || null;
  const configuredLabel = tgid ? status?.talkgroup_catalog?.labels?.[String(tgid)] : '';
  const activeLabel = status?.active_tgid === tgid ? status?.active_talkgroup_label : '';
  const lastLabel = status?.last_active_tgid === tgid ? status?.last_active_talkgroup_label : '';
  const fallbackLabel = fallback?.tgid === tgid || !fallback?.tgid ? fallback?.talkgroup_label : '';
  const rawLabel = configuredLabel || activeLabel || lastLabel || fallbackLabel || '';
  const running = Boolean(status?.decoder_process?.running);
  const labelOnly = rawLabel || (tgid ? 'Unmapped talkgroup' : (running ? 'Scanning for voice activity' : 'Waiting for activity'));
  const activeNow = Boolean(status?.active_tgid || status?.active_talkgroup_label);
  const prefix = activeNow ? 'Active' : (tgid ? 'Last heard' : '');
  return {
    has_talkgroup: Boolean(tgid || rawLabel),
    tgid,
    tgid_text: tgid ? `TGID ${tgid}` : '-',
    label: prefix ? `${prefix}: ${labelOnly}` : labelOnly,
    short_label: tgid ? `${labelOnly} · TGID ${tgid}` : labelOnly,
    voice_frequency_hz: status?.active_voice_frequency_hz || status?.last_active_voice_frequency_hz || fallback?.voice_frequency_hz || null,
  };
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
  setText('activityUniqueTgids', activity?.unique_tgid_count ?? 0);
  setText('activityClearEvents', activity?.clear_voice_events ?? 0);
  setText('activityEncryptedEvents', activity?.encrypted_events ?? 0);
  setText('activityMutedEvents', activity?.muted_events ?? 0);
  const recent = Array.isArray(activity?.recent_events) ? activity.recent_events : [];
  setText('activityRecentEvents', recent.length ? recent.slice(-10).map(formatActivityEvent).join('\n') : 'No parsed activity yet.');
}

function updateAudioPanel(message) {
  const audio = field('browserAudioPlayer');
  if (audio && !audio.src) audio.src = audioStreamUrl();
  if (message) browserAudioLastEvent = message;
  setText('browserAudioLastEvent', browserAudioLastEvent);
}

function renderDashboard(status) {
  const process = status?.decoder_process || {};
  const marker = process.validated_marker || {};
  const running = Boolean(process.running);
  const ready = markerIsReady(marker) || Boolean(process.start_enabled);
  const talkgroup = bestTalkgroup(status || {});
  const listener = extractOp25HttpListener(status || {});
  const controlState = status?.control_channel_state || (running ? 'searching' : 'idle');
  const controlSummary = controlState === 'locked'
    ? `Locked: ${formatHz(status?.active_control_frequency_hz)}`
    : `Searching: ${formatHz(status?.active_control_frequency_hz)}`;
  setText('dashboardSummary', running ? (talkgroup.has_talkgroup ? talkgroup.short_label : controlSummary) : (ready ? 'Ready to start' : 'Not launch-ready'));
  setText('scannerState', status?.scanner_state || '-');
  setText('decoderPid', process.pid || '-');
  setText('controlFrequency', formatHz(status?.active_control_frequency_hz));
  setText('voiceFrequency', formatHz(talkgroup.voice_frequency_hz));
  setText('activeTgid', talkgroup.tgid_text);
  setText('activeTalkgroupLabel', talkgroup.label);
  setText('p25Phase', status?.p25_phase || '-');
  setText('op25HttpListener', listener ? listener.piLocalUrl : '-');
  setText('launchReady', ready ? 'yes' : 'no');
  setText('commandSource', process.command_source || '-');
  setText('validatedMarkerState', markerIsReady(marker) ? 'validated' : (marker.exists ? 'present' : 'missing'));
  setText('validatedCommand', formatList(process.command));
  setText('lastEvent', status?.last_event || '-');
  setText('logTail', formatList(status?.log_tail));
  setText('lastUpdated', `Last update: ${new Date().toLocaleTimeString()}`);
  setBadge('stateBadge', running ? 'ON AIR' : (status?.scanner_state || '-'), running ? 'ok' : (status?.ok ? 'warn' : 'bad'));
  setBadge('connectionStatus', 'Connected', 'ok');
  renderActivitySummary(status?.activity_summary || {});
  updateAudioPanel();
  setButtonsForState(status || {});
}

async function refreshStatus() {
  try {
    latestStatus = await fetchJson('/api/status');
    renderDashboard(latestStatus);
  } catch (error) {
    setBadge('connectionStatus', 'Offline', 'bad');
    setText('dashboardSummary', `Status error: ${error.message}`);
    setText('lastEvent', `Status error: ${error.message}`);
  }
}

// PHASE2_MULTI_RECEIVER_INVENTORY_V0_6A
function receiverStateKind(state) {
  if (state === 'active' || state === 'ready') return 'ok';
  if (state === 'missing') return 'bad';
  return 'warn';
}

function renderReceiverInventory(payload) {
  const grid = field('receiverInventoryGrid');
  const roles = Array.isArray(payload?.roles) ? payload.roles : [];
  const expected = Number(payload?.expected_rtl_count || 0);
  const present = Number(payload?.device_count || 0);
  setBadge('receiverInventoryBadge', `${present}/${expected || '?'} RTL`, payload?.ok ? 'ok' : 'bad');

  if (grid) {
    grid.innerHTML = '';
    roles.forEach((role) => {
      const card = document.createElement('article');
      card.className = `receiver-card ${role.state || 'unknown'}`;

      const top = document.createElement('div');
      top.className = 'receiver-card-top';

      const title = document.createElement('strong');
      title.textContent = role.label || role.role || 'Receiver';

      const badge = document.createElement('span');
      badge.className = `pill ${receiverStateKind(role.state)}`;
      badge.textContent = String(role.state || 'unknown').toUpperCase();

      top.append(title, badge);

      const serial = document.createElement('div');
      serial.className = 'receiver-serial';
      serial.textContent = role.rtl_serial || '-';

      const detail = document.createElement('div');
      detail.className = 'receiver-detail';
      const device = role.device || {};
      const pieces = [
        role.role,
        device.product || role.service,
        device.usb_path ? `USB ${device.usb_path}` : '',
        role.active && Array.isArray(role.processes) && role.processes.length
          ? `PID ${role.processes[0].pid}`
          : '',
      ].filter(Boolean);
      detail.textContent = pieces.join(' · ') || '-';

      const note = document.createElement('div');
      note.className = 'receiver-note';
      note.textContent = role.notes || (role.enabled ? 'Enabled' : 'Reserved');

      card.append(top, serial, detail, note);
      grid.append(card);
    });
  }

  const warnings = Array.isArray(payload?.warnings) ? payload.warnings : [];
  const summary = [
    `Role registry: ${payload?.role_config_path || '-'}`,
    `Configured roles: ${payload?.configured_role_count ?? 0}`,
    `Present devices: ${present} / ${expected || '-'}`,
    `Missing configured serials: ${(payload?.missing_configured_serials || []).join(', ') || 'none'}`,
    `Unassigned serials: ${(payload?.unassigned_serials || []).join(', ') || 'none'}`,
    `Warnings: ${warnings.join(' | ') || 'none'}`,
  ];
  setText('receiverInventoryStatus', summary.join('\n'));
}

async function refreshReceiverInventory() {
  try {
    latestReceiverInventory = await fetchJson('/api/receivers/inventory');
    renderReceiverInventory(latestReceiverInventory);
  } catch (error) {
    setBadge('receiverInventoryBadge', 'Inventory error', 'bad');
    setText('receiverInventoryStatus', `Receiver inventory failed: ${error.message}`);
  }
}

// PHASE3_ANALOG_WORKER_AUDIO_ARBITER_V0_6B
function analogWorkerRecord(payload, role) {
  return (Array.isArray(payload?.workers) ? payload.workers : [])
    .find((item) => item?.role === role) || null;
}

function renderAnalogStatus(payload) {
  const record = analogWorkerRecord(payload, 'analog_2m');
  const service = record?.service || {};
  const runtime = record?.runtime || {};
  const config = record?.config || {};
  const active = Boolean(service.active);
  setBadge('analog2mBadge', active ? 'Running' : 'Stopped', active ? 'ok' : 'warn');
  setText('analog2mSerial', config.rtl_serial || '00000440');
  const channel = Array.isArray(config.channels) ? config.channels[0] : null;
  setText('analog2mChannel', channel?.frequency_hz ? formatHz(channel.frequency_hz) : '-');
  setText('analog2mRms', runtime.last_rms ?? '-');
  setText('audioArbiterSource', payload?.audio_arbiter?.active_source || 'none');
  const summary = {
    service,
    runtime,
    audio_arbiter: payload?.audio_arbiter || {},
    config_path: payload?.config_path || '',
  };
  setText('analog2mStatusText', safeJson(summary));
  const startButton = field('startAnalog2mBtn');
  const stopButton = field('stopAnalog2mBtn');
  if (startButton) startButton.disabled = active || !record?.controllable;
  if (stopButton) stopButton.disabled = !active || !record?.controllable;
}

async function refreshAnalogStatus() {
  try {
    latestAnalogStatus = await fetchJson('/api/analog/status');
    renderAnalogStatus(latestAnalogStatus);
  } catch (error) {
    setBadge('analog2mBadge', 'Analog error', 'bad');
    setText('analog2mStatusText', `Analog status failed: ${error.message}`);
  }
}

async function analog2mAction(action) {
  try {
    latestAnalogStatus = await postJson(`/api/analog/2m/${action}`);
    renderAnalogStatus(latestAnalogStatus);
  } catch (error) {
    setBadge('analog2mBadge', 'Action failed', 'bad');
    setText('analog2mStatusText', `Analog ${action} failed: ${error.message}`);
  }
}

async function refreshConfig() {
  try {
    const response = await fetchJson('/api/config');
    currentConfig = response.config;
    setText('configPreview', safeJson(response));
  } catch (error) {
    setText('configPreview', `Config error: ${error.message}`);
  }
}

async function startScannerAndAudio() {
  if (window.__P25_REQUIRE_USER_START__ && !window.__P25_USER_START_REQUESTED__) {
    setText('lastEvent', 'Page-load scanner auto-start is disabled. Use the desktop launcher or Start Scanner + Audio.');
    return;
  }

  const audio = field('browserAudioPlayer');
  if (audio) audio.src = audioStreamUrl();
  const playPromise = audio ? audio.play().catch((error) => { updateAudioPanel(`Press audio play if blocked: ${error.message}`); return false; }) : Promise.resolve(false);
  try {
    const status = await postJson('/api/scanner/start');
    renderDashboard(status);
    await playPromise;
    updateAudioPanel('Scanner started; browser audio attached');
  } catch (error) {
    setText('lastEvent', `Start error: ${error.message}`);
    updateAudioPanel(`Start/audio error: ${error.message}`);
  }
  await refreshStatus();
}

async function stopScanner() {
  const audio = field('browserAudioPlayer');
  if (audio) { audio.pause(); audio.src = audioStreamUrl(); }
  updateAudioPanel('Browser audio stopped');
  try {
    const status = await postJson('/api/scanner/stop');
    renderDashboard(status);
  } catch (error) { setText('lastEvent', `Stop error: ${error.message}`); }
  await refreshStatus();
}

function renderCategoryChoices(categories = CATEGORY_DEFAULTS) {
  const target = field('categoryChoices');
  if (!target) return;
  target.innerHTML = '';
  categories.forEach((category) => {
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.value = category;
    input.checked = ['Fire', 'EMS', 'Law Enforcement', 'Interop'].includes(category);
    label.append(input, document.createTextNode(category));
    target.append(label);
  });
}

function selectedCategories() {
  return Array.from(document.querySelectorAll('#categoryChoices input[type="checkbox"]:checked')).map((el) => el.value);
}

function locationPayload() {
  const selectedSite = selectedSiteRecord();
  return {
    state: String(field('wizardState')?.value || '').trim(),
    county: String(field('wizardCounty')?.value || '').trim(),
    city: String(field('wizardCity')?.value || '').trim(),
    categories: selectedCategories(),
    system_id: selectedSystemId(),
    site_id: selectedSiteId(),
    site: selectedSite?.name || selectedSite?.site_description || '',
    site_label: selectedSite?.label || '',
    name: String(field('profileName')?.value || '').trim(),
  };
}

function selectedSystemId() { return numberOrNull(field('rrSystemSelect')?.value); }
function selectedSiteId() { return numberOrNull(field('rrSiteSelect')?.value); }
function selectedSiteRecord() {
  const option = field('rrSiteSelect')?.selectedOptions?.[0];
  const index = Number.parseInt(String(option?.dataset?.index ?? ''), 10);
  return Number.isInteger(index) && index >= 0 ? rrSites[index] : null;
}

function compactProfileSummary(payload, message = '') {
  const configs = Array.isArray(payload?.configs) ? payload.configs : [];
  const lines = [];
  if (message) lines.push(message);
  if (configs.length) {
    lines.push(`${configs.length} saved profile${configs.length === 1 ? '' : 's'}:`);
    configs.forEach((item) => {
      const system = item?.validation?.first_enabled_system || {};
      const talkgroupCount = Array.isArray(system.talkgroups) ? system.talkgroups.length : 0;
      const profileName = item?.name || item?.id || 'Unnamed profile';
      const systemName = system.name || 'Unknown system';
      const siteName = system.site || 'Unknown site';
      lines.push(`- ${profileName}: ${systemName} / ${siteName} / ${talkgroupCount} talkgroups`);
    });
  } else if (!message) {
    lines.push('No saved profiles found.');
  }
  return lines.join('\n');
}

async function refreshProfiles() {
  try {
    const payload = await fetchJson('/api/config/named');
    const select = field('profileSelect');
    if (select) {
      select.innerHTML = '';
      (payload.configs || []).forEach((item) => {
        const option = document.createElement('option');
        option.value = item.id || item.name;
        option.textContent = `${item.name || item.id}${item.active ? ' (active)' : ''}`;
        select.append(option);
      });
    }
    setBadge('profileStatusBadge', `${payload.count || 0} profiles`, 'ok');
    setText('profileStatusText', compactProfileSummary(payload));
  } catch (error) {
    setBadge('profileStatusBadge', 'Profile error', 'bad');
    setText('profileStatusText', `Profile load failed: ${error.message}`);
  }
}

async function loadSelectedProfile() {
  const id = field('profileSelect')?.value || '';
  if (!id) { setText('profileStatusText', 'Select a profile first.'); return; }
  try {
    const payload = await postJson('/api/config/named/load', { id, apply: true });
    setBadge('profileStatusBadge', 'Loaded', 'ok');
    setText('profileStatusText', `Loaded profile: ${id}`);
    await refreshConfig();
    await refreshStatus();
  } catch (error) {
    setBadge('profileStatusBadge', 'Load failed', 'bad');
    setText('profileStatusText', `Load failed: ${error.message}`);
  }
}

async function saveCurrentProfile() {
  const name = String(field('profileName')?.value || '').trim();
  if (!name) { setText('profileStatusText', 'Enter a profile name first.'); return; }
  try {
    const payload = await postJson('/api/config/named/save', { name, apply: false });
    setBadge('profileStatusBadge', 'Saved', 'ok');
    setText('profileStatusText', `Saved profile: ${name}`);
    await refreshProfiles();
  } catch (error) {
    setBadge('profileStatusBadge', 'Save failed', 'bad');
    setText('profileStatusText', `Save failed: ${error.message}`);
  }
}

function renderRadioReferenceStatus(payload) {
  const configured = Boolean(payload?.configured);
  const zeepOk = Boolean(payload?.zeep?.available);
  setBadge('rrStatusBadge', configured ? (zeepOk ? 'RR Ready' : 'Need zeep') : 'Not configured', configured && zeepOk ? 'ok' : 'warn');
  const safe = { ...(payload || {}) };
  if (safe.password) safe.password = '<hidden>';
  setText('rrStatusText', safeJson(safe));
  if (payload?.username && field('rrUsername') && !field('rrUsername').value) field('rrUsername').value = payload.username;
}

async function refreshRadioReferenceStatus() {
  try { renderRadioReferenceStatus(await fetchJson('/api/radioreference/status')); }
  catch (error) { setBadge('rrStatusBadge', 'RR Offline', 'bad'); setText('rrStatusText', `RadioReference status error: ${error.message}`); }
}

async function saveRadioReferenceCredentials() {
  const payload = {
    app_key: String(field('rrAppKey')?.value || '').trim(),
    username: String(field('rrUsername')?.value || '').trim(),
    password: String(field('rrPassword')?.value || ''),
  };
  try {
    const status = await postJson('/api/radioreference/save-credentials', payload);
    if (field('rrPassword')) field('rrPassword').value = '';
    if (field('rrAppKey')) field('rrAppKey').value = '';
    renderRadioReferenceStatus(status);
    setText('lastEvent', 'Saved RadioReference credentials locally on the Pi.');
  } catch (error) { setBadge('rrStatusBadge', 'Save failed', 'bad'); setText('rrStatusText', `Save RadioReference login failed: ${error.message}`); }
}

async function testRadioReferenceLogin() {
  try {
    const result = await postJson('/api/radioreference/test-login');
    setBadge('rrStatusBadge', 'Login OK', 'ok');
    setText('rrStatusText', safeJson(result));
  } catch (error) { setBadge('rrStatusBadge', 'Login failed', 'bad'); setText('rrStatusText', `RadioReference login failed: ${error.message}`); }
}

async function callSystemsEndpoint(payload) {
  const params = new URLSearchParams();
  ['state', 'county', 'city'].forEach((key) => { if (payload[key]) params.set(key, payload[key]); });
  try { return await fetchJson(`/api/radioreference/systems?${params.toString()}`); }
  catch (_first) { return postJson('/api/radioreference/systems', payload); }
}

async function callSitesEndpoint(systemId) {
  const state = String(field('wizardState')?.value || '').trim();
  const county = String(field('wizardCounty')?.value || '').trim();
  const city = String(field('wizardCity')?.value || '').trim();
  return postJson('/api/radioreference/sites', {
    system_id: systemId,
    sid: systemId,
    state,
    county,
    city
  });
}

function optionLabel(item, fallback) {
  return item.label || item.name || item.sName || item.site || item.siteName || item.siteDescr || item.description || fallback;
}

async function findRrSystems() {
  // V0.5S: discovery must not reuse a stale selected system/site ID.
  const payload = locationPayload();
  delete payload.system_id;
  delete payload.site_id;

  rrSystems = [];
  rrSites = [];

  const systemSelect = field('rrSystemSelect');
  const siteSelect = field('rrSiteSelect');
  if (systemSelect) systemSelect.innerHTML = '';
  if (siteSelect) siteSelect.innerHTML = '';

  setBadge('importStatusBadge', 'Searching', 'warn');
  setText('wizardPreview', 'Searching RadioReference systems...');

  try {
    // The import discovery path contains the corrected SOAP traversal.
    // HTTP 202 is a successful "selection required" response and fetchJson accepts it.
    const result = await postJson('/api/radioreference/import', payload);
    rrSystems = Array.isArray(result.matches)
      ? result.matches
      : (Array.isArray(result.systems) ? result.systems : []);

    if (systemSelect) {
      rrSystems.forEach((system, index) => {
        const id = system.system_id || system.sid || system.rr_system_id;
        if (!id) return;
        const option = document.createElement('option');
        option.value = String(id);
        option.textContent = optionLabel(system, `RR System ${id}`);
        option.dataset.index = String(index);
        systemSelect.append(option);
      });
      if (rrSystems.length) systemSelect.selectedIndex = 0;
    }

    const ids = rrSystems
      .map((system) => system.system_id || system.sid || system.rr_system_id)
      .filter(Boolean);

    setBadge(
      'importStatusBadge',
      `${rrSystems.length} systems`,
      rrSystems.length ? 'ok' : 'warn'
    );
    setText(
      'wizardPreview',
      `${result.message || 'RadioReference search complete.'}

` +
      `System IDs: ${ids.join(', ') || 'none'}

` +
      safeJson(result)
    );
  } catch (error) {
    setBadge('importStatusBadge', 'Search failed', 'bad');
    setText('wizardPreview', `Find systems failed: ${error.message}`);
  }
}

/* V0_5S_RR_UI_DISCOVERY_ENDPOINT_FIX */


// BEGIN V0.5AJ DIRECT COUNTY SITE FILTER
function splitLocationValues(value) {
  const seen = new Set();
  return String(value || '').split(',').map((item) => item.trim()).filter((item) => {
    const normalized = item.toLowerCase();
    if (!normalized || seen.has(normalized)) return false;
    seen.add(normalized);
    return true;
  });
}

function normalizeCountyName(value) {
  return String(value || '').trim().replace(/\s+county$/i, '').toLowerCase();
}

function selectedWizardCounties() {
  return splitLocationValues(field('wizardCounty')?.value).map(normalizeCountyName);
}

function selectedWizardCities() {
  return splitLocationValues(field('wizardCity')?.value).map((value) => value.toLowerCase());
}

function rrSiteCountyId(site) {
  const values = [
    site?.county_id,
    site?.countyId,
    site?.ctid,
    site?.siteCtid,
    site?.site_ctid
  ];
  for (const value of values) {
    const parsed = Number.parseInt(String(value ?? ''), 10);
    if (Number.isFinite(parsed) && parsed > 0) return parsed;
  }
  return null;
}

function filterRrSitesForSelectedCounty(sites, countyIdMap = {}) {
  const countyNames = selectedWizardCounties();
  const cityNames = selectedWizardCities();
  if (!countyNames.length && !cityNames.length) return Array.isArray(sites) ? sites : [];

  const normalizedCountyIdMap = Object.fromEntries(
    Object.entries(countyIdMap || {}).map(([name, value]) => [normalizeCountyName(name), Number(value)])
  );
  const expectedCountyIds = countyNames
    .map((name) => normalizedCountyIdMap[name])
    .filter((value) => Number.isFinite(value) && value > 0);
  return (Array.isArray(sites) ? sites : []).filter((site) => {
    const siteCountyId = rrSiteCountyId(site);
    if (countyNames.length && siteCountyId !== null && expectedCountyIds.length) {
      return expectedCountyIds.includes(siteCountyId);
    }

    const countyValues = [
      site?.location,
      site?.county,
      site?.county_name,
      site?.siteLocation
    ].filter(Boolean).flatMap((value) => String(value).split(/[;/]/)).map((value) => {
      const firstPart = value.split(',')[0];
      return normalizeCountyName(firstPart);
    }).filter(Boolean);

    if (countyNames.length) {
      return countyNames.some((countyName) => countyValues.includes(countyName));
    }
    const citySearchable = [
      site?.location,
      site?.siteLocation,
      site?.label,
      site?.name,
      site?.site_description
    ].filter(Boolean).join(' ').toLowerCase();
    return cityNames.some((cityName) => citySearchable.includes(cityName));
  });
}
// END V0.5AJ DIRECT COUNTY SITE FILTER

async function loadRrSites() {
  const sid = selectedSystemId();
  if (!sid) { setText('wizardPreview', 'Select an RR system first.'); return; }
  setBadge('importStatusBadge', 'Loading sites', 'warn');
  try {
    const result = await callSitesEndpoint(sid);
    const allSites = Array.isArray(result.sites)
      ? result.sites
      : (Array.isArray(result.site_candidates) ? result.site_candidates : []);
    rrSites = filterRrSitesForSelectedCounty(allSites, result.county_id_map || {});
    result.ui_county_filter = {
      county: String(field('wizardCounty')?.value || '').trim(),
      county_id_map: result.county_id_map || {},
      unmatched_counties: result.unmatched_counties || [],
      unfiltered_site_count: allSites.length,
      filtered_site_count: rrSites.length
    };
    const select = field('rrSiteSelect');
    if (select) {
      select.innerHTML = '';
      rrSites.forEach((site, index) => {
        const id = site.site_id || site.siteId || site.siteNumber || site.sid || site.id || site.rfss_site_id;
        const option = document.createElement('option');
        option.value = String(id || '');
        option.textContent = optionLabel(site, `Site ${index + 1}`);
        option.dataset.index = String(index);
        select.append(option);
      });
    }
    const countyLabel = splitLocationValues(field('wizardCounty')?.value).join(', ');
    setBadge(
      'importStatusBadge',
      `${rrSites.length} ${countyLabel ? `${countyLabel} ` : ''}sites`,
      rrSites.length ? 'ok' : 'warn'
    );
    setText('wizardPreview', safeJson(result));
  } catch (error) {
    setBadge('importStatusBadge', 'Sites failed', 'bad');
    setText('wizardPreview', `Load sites failed: ${error.message}`);
  }
}

async function importAndSaveRr() {
  const payload = locationPayload();
  if (!payload.system_id) { setText('wizardPreview', 'Select an RR system first.'); return; }
  if (!payload.name) payload.name = field('rrSystemSelect')?.selectedOptions?.[0]?.textContent || `RR System ${payload.system_id}`;
  setBadge('importStatusBadge', 'Importing', 'warn');
  try {
    const result = await postJson('/api/radioreference/import', payload);
    if (result.ok && result.config) {
      try {
        const saved = await postJson('/api/config/named/save', { name: payload.name, config: result.config, apply: true });
        result.named_config = saved;
      } catch (saveError) {
        result.named_config_error = saveError.message;
      }
      await refreshProfiles();
      await refreshConfig();
      await refreshStatus();
    }
    setBadge('importStatusBadge', result.ok ? 'Imported' : 'Import issue', result.ok ? 'ok' : 'warn');
    setText('wizardPreview', safeJson(result));
  } catch (error) {
    setBadge('importStatusBadge', 'Import failed', 'bad');
    setText('wizardPreview', `Import and save failed: ${error.message}`);
  }
}

async function autoCalibratePpm() {
  setBadge('importStatusBadge', 'Calibrating', 'warn');
  setText('wizardPreview', 'Running PPM calibration. Scanner should be stopped. This can take a little while.');
  try {
    const result = await postJson('/api/calibration/ppm/run', { span_ppm: 3, step_ppm: 1, dwell_seconds: 8, apply_voice: false });
    setBadge('importStatusBadge', result.ok ? 'PPM updated' : 'PPM issue', result.ok ? 'ok' : 'warn');
    setText('wizardPreview', safeJson(result));
    await refreshConfig();
    await refreshStatus();
  } catch (error) {
    setBadge('importStatusBadge', 'PPM failed', 'bad');
    setText('wizardPreview', `Auto Calibrate PPM failed: ${error.message}`);
  }
}

function attachEventHandlers() {
  field('menuBtn')?.addEventListener('click', openDrawer);
  field('closeDrawerBtn')?.addEventListener('click', closeDrawer);
  field('drawerBackdrop')?.addEventListener('click', closeDrawer);
  document.querySelectorAll('.nav-item').forEach((button) => button.addEventListener('click', () => showScreen(button.dataset.screen)));
  field('startBtn')?.addEventListener('click', (event) => { if (p25AllowManualStart(event)) startScannerAndAudio(); });
  field('stopBtn')?.addEventListener('click', stopScanner);
  field('refreshReceiverInventoryBtn')?.addEventListener('click', refreshReceiverInventory);
  field('startAnalog2mBtn')?.addEventListener('click', () => analog2mAction('start'));
  field('stopAnalog2mBtn')?.addEventListener('click', () => analog2mAction('stop'));
  field('refreshAnalogStatusBtn')?.addEventListener('click', refreshAnalogStatus);
  field('refreshProfilesBtn')?.addEventListener('click', refreshProfiles);
  field('loadProfileBtn')?.addEventListener('click', loadSelectedProfile);
  field('saveProfileBtn')?.addEventListener('click', saveCurrentProfile);
  field('saveRrCredentialsBtn')?.addEventListener('click', saveRadioReferenceCredentials);
  field('testRrLoginBtn')?.addEventListener('click', testRadioReferenceLogin);
  field('findRrSystemsBtn')?.addEventListener('click', findRrSystems);
  field('loadRrSitesBtn')?.addEventListener('click', loadRrSites);
  field('importAndSaveRrBtn')?.addEventListener('click', importAndSaveRr);
  field('autoCalibratePpmBtn')?.addEventListener('click', autoCalibratePpm);
  field('browserAudioPlayer')?.addEventListener('play', () => updateAudioPanel('Browser audio playing'));
  field('browserAudioPlayer')?.addEventListener('pause', () => updateAudioPanel('Browser audio paused'));
}

function boot() {
  attachEventHandlers();
p25RemoveDashboardAutostartTuningRemnants();
  renderCategoryChoices();
  updateAudioPanel();
  refreshProfiles();
  refreshRadioReferenceStatus();
  refreshReceiverInventory();
  refreshAnalogStatus();
  refreshStatus();
  refreshConfig();
  setInterval(refreshStatus, 3000);
  setInterval(refreshReceiverInventory, 15000);
  setInterval(refreshAnalogStatus, 3000);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}

/* V0_5K_AUTO_START_RTL_POOL_BEGIN */
(function installV05KAutoStartScannerAudio() {
  'use strict';

  window.PI_P25_V05K_MARKER = 'V0_5K_AUTO_START_RTL_POOL';
  window.PI_P25_ALLOWED_RTL_SERIAL_POOL = '0000025X';

  function byId(id) {
    return document.getElementById(id);
  }

  function setUiText(id, text) {
    const target = byId(id);
    if (target) target.textContent = text;
  }

  function audioUrl() {
    return `http://${window.location.hostname}:8072/audio.wav`;
  }

  async function jsonFetch(url, options = {}) {
    const response = await fetch(url, { cache: 'no-store', ...options });
    const bodyText = await response.text();
    let payload = {};
    try {
      payload = bodyText ? JSON.parse(bodyText) : {};
    } catch (error) {
      throw new Error(`Invalid JSON from ${url}: ${error.message}`);
    }
    if (!response.ok) {
      throw new Error(payload.error || `HTTP ${response.status}`);
    }
    return payload;
  }

  async function startScannerIfNeeded() {
    const status = await jsonFetch('/api/status');
    if (status?.decoder_process?.running) return status;
    return jsonFetch('/api/scanner/start', { method: 'POST' });
  }

  async function tryStartAudio(reason) {
    const audio = byId('browserAudioPlayer');
    if (!audio) return false;

    if (audio.src !== audioUrl()) audio.src = audioUrl();

    try {
      await audio.play();
      setUiText('browserAudioLastEvent', reason || 'Browser audio started');
      window.__p25AutoAudioBlocked = false;
      return true;
    } catch (error) {
      setUiText('browserAudioLastEvent', 'Scanner started; tap/click once to enable audio');
      window.__p25AutoAudioBlocked = true;
      return false;
    }
  }

  async function autoStart() {
    if (window.__p25V05KAutoStartAttempted) return;
    window.__p25V05KAutoStartAttempted = true;

    try {
      setUiText('lastEvent', 'Auto-starting scanner and browser audio...');
      await startScannerIfNeeded();
      await tryStartAudio('Scanner and browser audio auto-started');
      setUiText('lastEvent', 'Scanner auto-start requested.');
    } catch (error) {
      setUiText('lastEvent', `Auto-start failed: ${error.message}`);
      setUiText('browserAudioLastEvent', `Auto-start failed: ${error.message}`);
    }
  }

  function retryAudioAfterUserGesture() {
    if (!window.__p25AutoAudioBlocked) return;
    tryStartAudio('Browser audio enabled after tap/click');
  }

  function install() {
    window.setTimeout(autoStart, 400);
    document.addEventListener('pointerdown', retryAudioAfterUserGesture, { passive: true });
    document.addEventListener('keydown', retryAudioAfterUserGesture);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', install, { once: true });
  } else {
    install();
  }
})();
/* V0_5K_AUTO_START_RTL_POOL_END */


function p25RemoveDashboardAutostartTuningRemnants() {
  document.querySelectorAll('button').forEach((button) => {
    const txt = (button.textContent || '').trim().toLowerCase();
    if (txt === 'auto calibrate ppm' && !button.closest('#wizardScreen') && !button.closest('#radioSetupScreen')) {
      const card = button.closest('section, article, div');
      if (card) card.remove(); else button.remove();
    }
  });
}

// BEGIN V0.5AH RR SITE COUNTY FILTER
(() => {
  "use strict";

  const originalFetch = window.fetch.bind(window);

  function countyControl() {
    const selectors = [
      "#rrCounty",
      "#rrCountySelect",
      "#radioreferenceCounty",
      "#radioreferenceCountySelect",
      "#county",
      "#countySelect",
      "select[name='county_id']",
      "select[name='county']",
      "select[id*='county' i]",
      "select[name*='county' i]"
    ];

    for (const selector of selectors) {
      const element = document.querySelector(selector);
      if (element) return element;
    }
    return null;
  }

  function selectedCounty() {
    const control = countyControl();
    if (!control) return { id: null, name: "" };

    const option =
      control.tagName === "SELECT" && control.selectedIndex >= 0
        ? control.options[control.selectedIndex]
        : null;

    const rawId =
      control.value ||
      option?.dataset?.countyId ||
      option?.dataset?.id ||
      "";

    const parsedId = Number.parseInt(String(rawId), 10);
    const name = String(
      option?.dataset?.countyName ||
      option?.textContent ||
      control.dataset?.countyName ||
      ""
    )
      .replace(/\s+county\b/i, "")
      .trim();

    return {
      id: Number.isFinite(parsedId) && parsedId > 0 ? parsedId : null,
      name
    };
  }

  function siteCountyId(site) {
    const candidates = [
      site?.county_id,
      site?.countyId,
      site?.ctid,
      site?.siteCtid,
      site?.site_ctid
    ];

    for (const candidate of candidates) {
      const value = Number.parseInt(String(candidate ?? ""), 10);
      if (Number.isFinite(value) && value > 0) return value;
    }
    return null;
  }

  function siteMatchesCounty(site, county) {
    if (!site || !county) return false;

    const id = siteCountyId(site);
    if (county.id !== null && id !== null) {
      return id === county.id;
    }

    if (!county.name) return county.id === null;

    const countyName = county.name.toLowerCase();
    const locationText = [
      site.location,
      site.county,
      site.county_name,
      site.siteLocation,
      site.label,
      site.name
    ]
      .filter(Boolean)
      .join(" ")
      .toLowerCase();

    return locationText.includes(countyName);
  }

  function filterSitePayload(payload) {
    if (!payload || !Array.isArray(payload.sites)) return payload;

    const county = selectedCounty();
    if (county.id === null && !county.name) return payload;

    const filtered = payload.sites.filter((site) =>
      siteMatchesCounty(site, county)
    );

    return {
      ...payload,
      sites: filtered,
      site_count: filtered.length,
      returned_site_count: filtered.length,
      unfiltered_site_count: payload.sites.length,
      ui_county_filter: {
        county_id: county.id,
        county_name: county.name,
        matched_sites: filtered.length
      }
    };
  }

  window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    const requestUrl = String(
      args[0] instanceof Request ? args[0].url : args[0] || ""
    );

    if (!requestUrl.includes("/api/radioreference/sites")) {
      return response;
    }

    const contentType = response.headers.get("content-type") || "";
    if (!contentType.includes("application/json")) {
      return response;
    }

    const payload = await response.clone().json();
    const filteredPayload = filterSitePayload(payload);

    return new Response(JSON.stringify(filteredPayload), {
      status: response.status,
      statusText: response.statusText,
      headers: response.headers
    });
  };

  window.piP25FilterRadioReferenceSitesByCounty = filterSitePayload;
  window.piP25SelectedRadioReferenceCounty = selectedCounty;
})();
// END V0.5AH RR SITE COUNTY FILTER
