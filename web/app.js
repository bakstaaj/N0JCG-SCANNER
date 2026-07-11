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
  const rawLabel = status?.active_talkgroup_label || status?.last_active_talkgroup_label || fallback?.talkgroup_label || configuredLabel || '';
  const running = Boolean(status?.decoder_process?.running);
  const labelOnly = rawLabel || (tgid ? 'Talkgroup activity' : (running ? 'Scanning for voice activity' : 'Waiting for activity'));
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
  setText('dashboardSummary', running ? (talkgroup.has_talkgroup ? talkgroup.short_label : `Running on ${formatHz(status?.active_control_frequency_hz)}`) : (ready ? 'Ready to start' : 'Not launch-ready'));
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
  return {
    state: String(field('wizardState')?.value || '').trim(),
    county: String(field('wizardCounty')?.value || '').trim(),
    city: String(field('wizardCity')?.value || '').trim(),
    categories: selectedCategories(),
    system_id: selectedSystemId(),
    site_id: selectedSiteId(),
    name: String(field('profileName')?.value || '').trim(),
  };
}

function selectedSystemId() { return numberOrNull(field('rrSystemSelect')?.value); }
function selectedSiteId() { return numberOrNull(field('rrSiteSelect')?.value); }

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
    setText('profileStatusText', safeJson(payload));
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
    setText('profileStatusText', safeJson(payload));
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
    setText('profileStatusText', safeJson(payload));
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
  const params = new URLSearchParams({ system_id: String(systemId), sid: String(systemId) });
  try { return await fetchJson(`/api/radioreference/sites?${params.toString()}`); }
  catch (_first) { return postJson('/api/radioreference/sites', { system_id: systemId, sid: systemId }); }
}

function optionLabel(item, fallback) {
  return item.label || item.name || item.sName || item.site || item.siteName || item.siteDescr || item.description || fallback;
}

async function findRrSystems() {
  const payload = locationPayload();
  setBadge('importStatusBadge', 'Searching', 'warn');
  setText('wizardPreview', 'Searching RadioReference systems...');
  try {
    const result = await callSystemsEndpoint(payload);
    rrSystems = Array.isArray(result.systems) ? result.systems : [];
    const select = field('rrSystemSelect');
    if (select) {
      select.innerHTML = '';
      rrSystems.forEach((system, index) => {
        const id = system.system_id || system.sid || system.id || system.rr_system_id;
        const option = document.createElement('option');
        option.value = String(id || '');
        option.textContent = optionLabel(system, `System ${index + 1}`);
        option.dataset.index = String(index);
        select.append(option);
      });
    }
    setBadge('importStatusBadge', `${rrSystems.length} systems`, rrSystems.length ? 'ok' : 'warn');
    setText('wizardPreview', safeJson(result));
  } catch (error) {
    setBadge('importStatusBadge', 'Search failed', 'bad');
    setText('wizardPreview', `Find systems failed: ${error.message}`);
  }
}

async function loadRrSites() {
  const sid = selectedSystemId();
  if (!sid) { setText('wizardPreview', 'Select an RR system first.'); return; }
  setBadge('importStatusBadge', 'Loading sites', 'warn');
  try {
    const result = await callSitesEndpoint(sid);
    rrSites = Array.isArray(result.sites) ? result.sites : (Array.isArray(result.site_candidates) ? result.site_candidates : []);
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
    setBadge('importStatusBadge', `${rrSites.length} sites`, rrSites.length ? 'ok' : 'warn');
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
  refreshStatus();
  refreshConfig();
  setInterval(refreshStatus, 3000);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}

/* V0_5E_AUTO_START_RTL_POOL_BEGIN */
(function installV05EAutoStartScannerAudio() {
  const MARKER = 'V0_5E_AUTO_START_RTL_POOL';
  window.PI_P25_V05E_MARKER = MARKER;
  window.PI_P25_ALLOWED_RTL_SERIAL_POOL = '0000025X';

  function el(id) { return document.getElementById(id); }
  function setUiText(id, text) {
    const target = el(id);
    if (target) target.textContent = text;
  }
  function audioUrl() { return `http://${window.location.hostname}:8072/audio.wav`; }

  async function jsonFetch(url, options) {
    const response = await fetch(url, { cache: 'no-store', ...(options || {}) });
    const bodyText = await response.text();
    let payload = {};
    try { payload = bodyText ? JSON.parse(bodyText) : {}; }
    catch (error) { payload = { ok: false, error: `Invalid JSON: ${error.message}`, raw: bodyText }; }
    if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
    return payload;
  }

  async function startScannerIfNeeded() {
    const status = await jsonFetch('/api/status');
    const running = Boolean(status?.decoder_process?.running);
    if (running) return status;
    return jsonFetch('/api/scanner/start', { method: 'POST' });
  }

  async function tryStartAudio(reason) {
    const audio = el('browserAudioPlayer');
    if (!audio) return false;
    if (audio.src !== audioUrl()) audio.src = audioUrl();
    try {
      await audio.play();
      setUiText('browserAudioLastEvent', reason || 'Auto-started browser audio');
      return true;
    } catch (error) {
      setUiText('browserAudioLastEvent', 'Scanner auto-started; tap/click once to enable audio');
      window.__p25AutoAudioBlocked = true;
      return false;
    }
  }

// V0.5F removed page-load scanner auto-start call.
    if (window.__p25V05EAutoStartAttempted) return;
    window.__p25V05EAutoStartAttempted = true;
    try {
      setUiText('lastEvent', 'Auto-starting scanner and browser audio...');
      await startScannerIfNeeded();
      await tryStartAudio('Scanner and browser audio auto-started');
      if (typeof window.refreshStatus === 'function') window.refreshStatus();
      setUiText('lastEvent', 'Auto-start requested for scanner and browser audio.');
    } catch (error) {
      setUiText('lastEvent', `Auto-start failed: ${error.message}`);
      setUiText('browserAudioLastEvent', `Auto-start failed: ${error.message}`);
    }
  }

  function retryAudioAfterUserGesture() {
    if (!window.__p25AutoAudioBlocked) return;
    window.__p25AutoAudioBlocked = false;
    tryStartAudio('Browser audio enabled after tap/click');
  }

  function install() {
// V0.5F removed page-load scanner auto-start call.
    document.addEventListener('pointerdown', retryAudioAfterUserGesture, { passive: true });
    document.addEventListener('keydown', retryAudioAfterUserGesture, { passive: true });
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', install, { once: true });
  else install();
})();
/* V0_5E_AUTO_START_RTL_POOL_END */


function p25RemoveDashboardAutostartTuningRemnants() {
  document.querySelectorAll('button').forEach((button) => {
    const txt = (button.textContent || '').trim().toLowerCase();
    if (txt === 'auto calibrate ppm' && !button.closest('#wizardScreen') && !button.closest('#radioSetupScreen')) {
      const card = button.closest('section, article, div');
      if (card) card.remove(); else button.remove();
    }
  });
}
