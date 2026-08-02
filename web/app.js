'use strict';

// V0.5F_DESKTOP_LAUNCHER_NO_PAGE_AUTOSTART
// Normal browser page loads must not start the scanner automatically.
// Opening the dashboard never starts a receiver. Only a trusted press of the
// Start Scanning + Audio button may request coordinated scanner startup.
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
        error: 'Page-load scanner auto-start is disabled. Use the Start Scanning + Audio button.'
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
let latestProfilesPayload = null;
let browserAudioLastEvent = 'Ready';

function field(id) { return document.getElementById(id); }
function setText(id, value) { const el = field(id); if (el) el.textContent = value ?? '-'; }
function setBadge(id, text, kind) { const el = field(id); if (!el) return; el.textContent = text; el.className = `pill ${kind || ''}`.trim(); }
function formatHz(value) { return value ? `${(Number(value) / 1000000).toFixed(6)} MHz` : '-'; }
function formatList(values) { return Array.isArray(values) && values.length ? values.join('\n') : '-'; }
function commandText(command) { return Array.isArray(command) ? command.join(' ') : (typeof command === 'string' ? command : ''); }
function audioStreamUrl() { return `http://${window.location.hostname}:8072/audio.wav`; }
function testToneUrl() { return `http://${window.location.hostname}:8072/test-tone.wav`; }
function safeJson(value) { try { return JSON.stringify(value, null, 2); } catch { return String(value); } }

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
  setText(
    'activityClearEvents',
    activity?.distinct_voice_calls ?? activity?.voice_call_events ?? 0,
  );
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
    setText('lastEvent', 'Page-load scanner auto-start is disabled. Use Start Scanning + Audio.');
    return;
  }

  const startBtn = field('startBtn');
  const stopBtn = field('stopBtn');
  if (startBtn) startBtn.disabled = true;
  if (stopBtn) stopBtn.disabled = true;
  setText('lastEvent', 'Starting P25, VHF, and UHF scanners...');

  const audio = field('browserAudioPlayer');
  if (audio) audio.src = audioStreamUrl();
  const playPromise = audio ? audio.play().catch((error) => { updateAudioPanel(`Press audio play if blocked: ${error.message}`); return false; }) : Promise.resolve(false);
  try {
    const status = await postJson('/api/scanner/start');
    renderDashboard(status);
    await playPromise;
    updateAudioPanel('P25, VHF, and UHF started; browser audio attached');
  } catch (error) {
    setText('lastEvent', `Start error: ${error.message}`);
    updateAudioPanel(`Start/audio error: ${error.message}`);
  }
  await refreshStatus();
}

async function stopScanner() {
  const startBtn = field('startBtn');
  const stopBtn = field('stopBtn');
  if (startBtn) startBtn.disabled = true;
  if (stopBtn) stopBtn.disabled = true;
  setText('lastEvent', 'Stopping P25, VHF, and UHF scanners...');

  const audio = field('browserAudioPlayer');
  if (audio) { audio.pause(); audio.src = audioStreamUrl(); }
  updateAudioPanel('Stopping P25, VHF, UHF, and browser audio');
  try {
    const status = await postJson('/api/scanner/stop');
    renderDashboard(status);
  } catch (error) { setText('lastEvent', `Stop error: ${error.message}`); }
  await refreshStatus();
}

function compactProfileSummary(payload, message = '') {
  const configs = Array.isArray(payload?.configs) ? payload.configs : [];
  const lines = [];
  if (message) lines.push(message);
  if (configs.length) {
    lines.push(`${configs.length} saved profile${configs.length === 1 ? '' : 's'}. Choose one above to load or export.`);
    const selectedId = field('profileSelect')?.value || '';
    const selected = configs.find((item) => (item.id || item.name) === selectedId) || configs[0];
    const system = selected?.validation?.first_enabled_system || {};
    const talkgroupCount = Array.isArray(system.talkgroups) ? system.talkgroups.length : 0;
    const analogCounts = selected?.analog_channel_counts;
    const analogSummary = analogCounts && Object.keys(analogCounts).length
      ? `${Number(analogCounts.analog_2m || 0)} VHF / ${Number(analogCounts.analog_70cm || 0)} UHF`
      : 'analog unchanged when loaded';
    lines.push(`Selected: ${selected?.name || selected?.id} · ${system.name || 'Unknown system'} · ${talkgroupCount} talkgroups · ${analogSummary}`);
  } else if (!message) {
    lines.push('No saved profiles found.');
  }
  return lines.join('\n');
}

async function refreshProfiles() {
  try {
    const payload = await fetchJson('/api/config/named');
    latestProfilesPayload = payload;
    const select = field('profileSelect');
    if (select) {
      const selected = select.value;
      select.innerHTML = '';
      (payload.configs || []).forEach((item) => {
        const option = document.createElement('option');
        option.value = item.id || item.name;
        option.textContent = `${item.name || item.id}${item.active ? ' (active)' : ''}`;
        select.append(option);
      });
      if (selected && Array.from(select.options).some((option) => option.value === selected)) {
        select.value = selected;
      }
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
    if (field('profileName')) field('profileName').value = payload.name || id;
    await refreshConfig();
    await refreshAnalogChannels();
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

async function deleteSelectedProfile() {
  const id = field('profileSelect')?.value || '';
  if (!id) { setText('profileStatusText', 'Select a profile first.'); return; }
  try {
    await postJson('/api/config/named/delete', { id });
    setBadge('profileStatusBadge', 'Deleted', 'warn');
    await refreshProfiles();
    setText('profileStatusText', `Deleted profile: ${id}`);
  } catch (error) {
    setBadge('profileStatusBadge', 'Delete failed', 'bad');
    setText('profileStatusText', `Delete failed: ${error.message}`);
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

function profileNameForFile(file) {
  const entered = String(field('profileName')?.value || '').trim();
  const fallback = String(file?.name || 'Imported Profile')
    .replace(/\.csv$/i, '')
    .replace(/[_-]+/g, ' ')
    .trim();
  const name = entered || fallback || 'Imported Profile';
  if (field('profileName')) field('profileName').value = name;
  return name;
}

async function saveImportedProfile(name) {
  const result = await postJson('/api/config/named/save', { name, apply: false });
  await refreshProfiles();
  if (field('profileSelect')) field('profileSelect').value = result.id || result.slug || name;
  return result;
}

async function refreshAnalogChannels() {
  try {
    const result = await fetchJson('/api/analog/channels');
    const counts = result?.channel_counts || {};
    const vhf = Number(counts.analog_2m || 0);
    const uhf = Number(counts.analog_70cm || 0);
    setBadge('analogCsvStatusBadge', `${vhf} VHF / ${uhf} UHF`, (vhf + uhf) ? 'ok' : 'warn');
    setText('analogCsvStatusText', `Current radio: ${vhf} VHF channels and ${uhf} UHF channels.`);
  } catch (error) {
    setBadge('analogCsvStatusBadge', 'Load failed', 'bad');
    setText('analogCsvStatusText', `Analog channel status failed: ${error.message}`);
  }
}

async function importAnalogCsv() {
  const file = field('analogCsvFile')?.files?.[0];
  if (!file) {
    setBadge('analogCsvStatusBadge', 'Choose file', 'warn');
    setText('analogCsvStatusText', 'Choose a CHIRP CSV file first.');
    return;
  }
  const button = field('importAnalogCsvBtn');
  if (button) button.disabled = true;
  setBadge('analogCsvStatusBadge', 'Uploading', 'warn');
  try {
    const name = profileNameForFile(file);
    const result = await postJson('/api/analog/channels/import', {
      filename: file.name,
      csv_text: await file.text(),
      replace_mode: 'roles_in_file',
    });
    await saveImportedProfile(name);
    const counts = result.channel_counts || {};
    setBadge('analogCsvStatusBadge', 'Saved', 'ok');
    setText('analogCsvStatusText', `Saved profile “${name}” with ${Number(counts.analog_2m || 0)} VHF and ${Number(counts.analog_70cm || 0)} UHF channels.`);
  } catch (error) {
    setBadge('analogCsvStatusBadge', 'Upload failed', 'bad');
    setText('analogCsvStatusText', `Analog CSV upload failed: ${error.message}`);
  } finally {
    if (button) button.disabled = false;
  }
}

async function importP25CsvFile() {
  const file = field('p25CsvFile')?.files?.[0];
  if (!file) {
    setBadge('p25CsvStatusBadge', 'Choose file', 'warn');
    setText('p25CsvStatusText', 'Choose a P25 CSV file first.');
    return;
  }
  const button = field('importP25CsvBtn');
  if (button) button.disabled = true;
  setBadge('p25CsvStatusBadge', 'Uploading', 'warn');
  try {
    const name = profileNameForFile(file);
    const result = await postJson('/api/p25/csv/import', {
      filename: file.name,
      csv_text: await file.text(),
      replace_mode: 'systems_in_file',
    });
    await refreshConfig();
    await saveImportedProfile(name);
    const systems = Array.isArray(result.systems) ? result.systems.join(', ') : 'P25 system';
    setBadge('p25CsvStatusBadge', 'Saved', 'ok');
    setText('p25CsvStatusText', `Saved profile “${name}” from ${result.imported_rows || 0} rows: ${systems}.`);
  } catch (error) {
    setBadge('p25CsvStatusBadge', 'Upload failed', 'bad');
    setText('p25CsvStatusText', `P25 CSV upload failed: ${error.message}`);
  } finally {
    if (button) button.disabled = false;
  }
}

function downloadCsv(filename, csvText) {
  const blob = new Blob([csvText], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

async function exportSelectedProfile(kind) {
  const id = field('profileSelect')?.value || '';
  const statusPrefix = kind === 'analog' ? 'analogCsv' : 'p25Csv';
  if (!id) {
    setBadge(`${statusPrefix}StatusBadge`, 'Select profile', 'warn');
    setText(`${statusPrefix}StatusText`, 'Select a saved profile first.');
    return;
  }
  try {
    const result = await postJson('/api/config/named/export', { id, kind });
    downloadCsv(result.filename, result.csv_text);
    setBadge(`${statusPrefix}StatusBadge`, 'Exported', 'ok');
    setText(`${statusPrefix}StatusText`, `Downloaded ${result.filename}.`);
  } catch (error) {
    setBadge(`${statusPrefix}StatusBadge`, 'Export failed', 'bad');
    setText(`${statusPrefix}StatusText`, `CSV export failed: ${error.message}`);
  }
}

function suggestProfileName(event) {
  if (String(field('profileName')?.value || '').trim()) return;
  const file = event.currentTarget?.files?.[0];
  if (file) profileNameForFile(file);
}

function attachEventHandlers() {
  field('menuBtn')?.addEventListener('click', openDrawer);
  field('closeDrawerBtn')?.addEventListener('click', closeDrawer);
  field('drawerBackdrop')?.addEventListener('click', closeDrawer);
  document.querySelectorAll('.nav-item').forEach((button) => button.addEventListener('click', () => showScreen(button.dataset.screen)));
  field('startBtn')?.addEventListener('click', (event) => { if (p25AllowManualStart(event)) startScannerAndAudio(); });
  field('stopBtn')?.addEventListener('click', stopScanner);
  field('refreshProfilesBtn')?.addEventListener('click', refreshProfiles);
  field('profileSelect')?.addEventListener('change', () => {
    if (latestProfilesPayload) setText('profileStatusText', compactProfileSummary(latestProfilesPayload));
  });
  field('loadProfileBtn')?.addEventListener('click', loadSelectedProfile);
  field('saveProfileBtn')?.addEventListener('click', saveCurrentProfile);
  field('deleteProfileBtn')?.addEventListener('click', deleteSelectedProfile);
  field('importAnalogCsvBtn')?.addEventListener('click', importAnalogCsv);
  field('importP25CsvBtn')?.addEventListener('click', importP25CsvFile);
  field('exportAnalogCsvBtn')?.addEventListener('click', () => exportSelectedProfile('analog'));
  field('exportP25CsvBtn')?.addEventListener('click', () => exportSelectedProfile('p25'));
  field('analogCsvFile')?.addEventListener('change', suggestProfileName);
  field('p25CsvFile')?.addEventListener('change', suggestProfileName);
  field('browserAudioPlayer')?.addEventListener('play', () => updateAudioPanel('Browser audio playing'));
  field('browserAudioPlayer')?.addEventListener('pause', () => updateAudioPanel('Browser audio paused'));
}

function boot() {
  attachEventHandlers();
p25RemoveDashboardAutostartTuningRemnants();
  updateAudioPanel();
  refreshProfiles();
  refreshAnalogChannels();
  refreshStatus();
  refreshConfig();
  setInterval(refreshStatus, 3000);
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', boot);
} else {
  boot();
}

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

/* PI Scanner analog lock counter patch */
(function installAnalogLockCounters() {
  if (window.__analogLockCountersInstalled) return;
  window.__analogLockCountersInstalled = true;

  function setText(id, value) {
    const node = document.getElementById(id);
    if (node) node.textContent = String(value ?? 0);
  }

  async function refreshAnalogLockCounters() {
    try {
      const response = await fetch("/api/analog/status", {
        cache: "no-store",
        credentials: "same-origin",
      });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);

      const data = await response.json();
      const roles = data && data.roles ? data.roles : {};
      const vhf = roles.analog_2m || {};
      const uhf = roles.analog_70cm || {};

      setText("analogVhfLocks", Number(vhf.lock_count || 0));
      setText("analogUhfLocks", Number(uhf.lock_count || 0));
    } catch (error) {
      console.warn("Analog lock counter refresh failed:", error);
    }
  }

  refreshAnalogLockCounters();
  window.setInterval(refreshAnalogLockCounters, 2000);
})();

/* PI Scanner accurate system and activity status v1.0.3 */
(function installAccurateScannerStatus() {
  if (window.__accurateScannerStatusInstalled) return;
  window.__accurateScannerStatusInstalled = true;

  const originalSetBadge = setBadge;
  let backendReachable = false;
  let decoderRunning = false;
  let activeSource = null;
  let audioReachable = false;
  let audioStatus = null;

  setBadge = function accurateStatusBadgeGuard(id, text, kind) {
    if (id === 'connectionStatus' || id === 'stateBadge') return;
    originalSetBadge(id, text, kind);
  };

  function renderAudioArbitratorStatus() {
    const muteButton = field('arbitratorMuteBtn');
    const muted = muteButton?.getAttribute('aria-pressed') === 'true';
    let text;

    if (!audioReachable) {
      text = 'Offline';
    } else if (muted) {
      text = `Muted · ${activeSource || 'Idle'}`;
    } else if (activeSource) {
      text = audioStatus?.playback_started
        ? `${activeSource} Playing`
        : `${activeSource} Buffering`;
    } else if (Number(audioStatus?.clients || 0) > 0) {
      text = 'Idle · Connected';
    } else {
      text = 'Idle · No Listener';
    }

    browserAudioLastEvent = text;
    setText('browserAudioLastEvent', text);
  }

  function renderAccurateStatus() {
    renderAudioArbitratorStatus();
    if (!backendReachable) {
      originalSetBadge('connectionStatus', 'Offline', 'bad');
      originalSetBadge('stateBadge', 'Unavailable', 'bad');
      setText('activeSourceStat', '-');
      return;
    }
    originalSetBadge('connectionStatus', 'Online', 'ok');
    if (!audioReachable) {
      originalSetBadge('stateBadge', 'Audio Error', 'bad');
      setText('activeSourceStat', 'Unavailable');
      return;
    }
    if (activeSource) {
      originalSetBadge('stateBadge', `${activeSource} ON AIR`, 'ok');
      setText('activeSourceStat', activeSource);
      return;
    }
    if (decoderRunning) {
      originalSetBadge('stateBadge', 'Scanning', 'warn');
      setText('activeSourceStat', 'None');
      return;
    }
    originalSetBadge('stateBadge', 'Stopped', 'warn');
    setText('activeSourceStat', 'None');
  }

  async function refreshAccurateStatus() {
    try {
      const backendResponse = await fetch('/api/status', {cache:'no-store', credentials:'same-origin'});
      if (!backendResponse.ok) throw new Error(`backend HTTP ${backendResponse.status}`);
      const backend = await backendResponse.json();
      backendReachable = true;
      decoderRunning = Boolean(backend?.decoder_process?.running);
    } catch (_error) {
      backendReachable = false;
      decoderRunning = false;
      activeSource = null;
      audioReachable = false;
      audioStatus = null;
      renderAccurateStatus();
      return;
    }

    try {
      const audioResponse = await fetch(`http://${window.location.hostname}:8072/api/audio/status`, {cache:'no-store', mode:'cors'});
      if (!audioResponse.ok) throw new Error(`audio HTTP ${audioResponse.status}`);
      const audio = await audioResponse.json();
      audioReachable = Boolean(audio?.ok);
      audioStatus = audioReachable ? audio : null;
      activeSource = audioReachable && audio.active_source ? String(audio.active_source).toUpperCase() : null;
    } catch (_error) {
      audioReachable = false;
      audioStatus = null;
      activeSource = null;
    }
    renderAccurateStatus();
  }

  refreshAccurateStatus();
  window.setInterval(refreshAccurateStatus, 500);
})();


/* ANALOG_CHANNEL_CENTER_DISPLAY_V109 */
(function installAnalogChannelCenterDisplay() {
  if (window.__analogChannelCenterDisplayInstalled) return;
  window.__analogChannelCenterDisplayInstalled = true;

  let refreshInFlight = false;

  function analogRoleForSource(source) {
    const value = String(source || '').trim().toLowerCase();
    if (value === 'vhf' || value === 'analog_2m') return 'analog_2m';
    if (value === 'uhf' || value === 'analog_70cm') return 'analog_70cm';
    return null;
  }

  function analogChannel(status) {
    const current = status?.current_channel || {};
    const last = status?.last_lock || {};

    if (status?.state === 'locked') {
      return {
        name: current.name || last.name || status.label || 'Analog channel',
        frequency_hz: current.frequency_hz || last.frequency_hz || null,
      };
    }

    return {
      name: last.name || current.name || status?.label || 'Analog channel',
      frequency_hz: last.frequency_hz || current.frequency_hz || null,
    };
  }

  function displayAnalogChannel(role, status) {
    if (!role || !status) return;

    const channel = analogChannel(status);
    const label = field('activeTalkgroupLabel');
    const identifier = field('activeTgid');

    if (label) {
      const band = role === 'analog_2m' ? 'VHF' : 'UHF';
      label.textContent = `${band}: ${channel.name}`;
    }

    if (identifier) {
      identifier.textContent = channel.frequency_hz
        ? formatHz(channel.frequency_hz)
        : 'Frequency unavailable';
    }
  }

  async function refreshAnalogCenterDisplay() {
    if (refreshInFlight) return;
    refreshInFlight = true;

    try {
      const audioStatus = await fetchJson(
        `http://${window.location.hostname}:8072/api/audio/status`
      );
      const role = analogRoleForSource(audioStatus?.active_source);

      if (!role) return;

      const analogStatus = await fetchJson('/api/analog/status');
      const roleStatus = analogStatus?.roles?.[role];
      if (!roleStatus) return;

      displayAnalogChannel(role, roleStatus);
    } catch (_error) {
      // Main dashboard status handling remains authoritative on failures.
    } finally {
      refreshInFlight = false;
    }
  }

  refreshAnalogCenterDisplay();
  window.setInterval(refreshAnalogCenterDisplay, 500);
})();

/* ANALOG_SQUELCH_VALUE_LAYOUT_V114 */
(function installAnalogSquelchValueLayoutV114() {
  if (window.__analogSquelchValueLayoutV114Installed) return;
  window.__analogSquelchValueLayoutV114Installed = true;

  let activeAnalogRole = null;
  let actionInFlight = false;

  const squelchIds = [
    'analogSquelchDownBtn',
    'analogSquelchUpBtn',
  ];

  const channelActionIds = [
    'analogSkipBtn',
    'analogBlockBtn',
    'analogClearLockBtn',
  ];

  function setButtonsDisabled(ids, disabled) {
    ids.forEach((id) => {
      const button = field(id);
      if (button) button.disabled = disabled;
    });
  }

  function hasAnyBlocksOrSkips(controlsPayload) {
    const roles = controlsPayload?.roles || {};

    return Object.values(roles).some((item) => {
      const blocked = Array.isArray(
        item?.blocked_frequencies_hz
      )
        ? item.blocked_frequencies_hz
        : [];

      const skips = item?.skip_until_epoch || {};
      const activeSkips = Object.values(skips).some(
        (value) => Number(value) > Date.now() / 1000
      );

      return blocked.length > 0 || activeSkips;
    });
  }

  function analogRoleFromSource(source) {
    const value = String(source || '').toLowerCase();

    if (value === 'vhf' || value === 'analog_2m') {
      return 'analog_2m';
    }

    if (value === 'uhf' || value === 'analog_70cm') {
      return 'analog_70cm';
    }

    return null;
  }

  function p25IsActive(source) {
    return String(source || '').toLowerCase() === 'p25';
  }

  function roleIsAvailable(status) {
    const state = String(status?.state || '').toLowerCase();

    return Boolean(status?.ok) && ![
      'offline',
      'error',
      'stopped',
    ].includes(state);
  }

  function analogScanningAvailable(analogPayload) {
    const roles = analogPayload?.roles || {};
    return Object.values(roles).some(roleIsAvailable);
  }

  function absoluteSquelchForRole(status) {
    const threshold = Number(status?.threshold_rms);
    if (Number.isFinite(threshold) && threshold >= 0) {
      return Math.round(threshold);
    }

    const baseline = Number(status?.baseline_rms);
    if (Number.isFinite(baseline) && baseline >= 0) {
      return Math.round(baseline);
    }

    return null;
  }

  function renderAbsoluteSquelch(analogPayload) {
    const roles = analogPayload?.roles || {};
    const vhf = absoluteSquelchForRole(roles.analog_2m);
    const uhf = absoluteSquelchForRole(roles.analog_70cm);

    let text = '—';

    if (activeAnalogRole === 'analog_2m' && vhf !== null) {
      text = String(vhf);
    } else if (
      activeAnalogRole === 'analog_70cm'
      && uhf !== null
    ) {
      text = String(uhf);
    } else if (vhf !== null && uhf !== null) {
      text = vhf === uhf
        ? String(vhf)
        : `VHF ${vhf} · UHF ${uhf}`;
    } else if (vhf !== null) {
      text = `VHF ${vhf}`;
    } else if (uhf !== null) {
      text = `UHF ${uhf}`;
    }

    setText('analogSquelchValue', text);
  }

  function updateControlState({
    audioSource,
    analogPayload,
    controlsPayload,
  }) {
    const p25Active = p25IsActive(audioSource);
    activeAnalogRole = analogRoleFromSource(audioSource);

    const analogAvailable = analogScanningAvailable(
      analogPayload
    );

    const squelchEnabled = !p25Active && analogAvailable;
    setButtonsDisabled(squelchIds, !squelchEnabled);

    const channelActionsEnabled = Boolean(activeAnalogRole);
    setButtonsDisabled(
      channelActionIds,
      !channelActionsEnabled
    );

    const clearButton = field('analogClearBlockBtn');
    if (clearButton) {
      clearButton.disabled = !hasAnyBlocksOrSkips(
        controlsPayload
      );
    }

    renderAbsoluteSquelch(analogPayload);

    const panel = field('analogLiveControls');
    if (panel) {
      panel.classList.toggle(
        'disabled',
        !squelchEnabled && !channelActionsEnabled
      );
      panel.setAttribute(
        'aria-disabled',
        squelchEnabled || channelActionsEnabled
          ? 'false'
          : 'true'
      );
    }
  }

  async function postAnalogAction(role, action) {
    return postJson('/api/analog/control', {
      role,
      action,
    });
  }

  async function clearAllAnalogBlocks() {
    const results = await Promise.all([
      postAnalogAction('analog_2m', 'clear_blocks'),
      postAnalogAction('analog_70cm', 'clear_blocks'),
    ]);

    return {
      message: 'Cleared all VHF and UHF skips and blocks',
      results,
    };
  }

  async function analogControlAction(action) {
    if (actionInFlight) return;

    if (
      ['skip', 'block'].includes(action)
      && !activeAnalogRole
    ) {
      return;
    }

    actionInFlight = true;

    try {
      let result;

      if (action === 'clear_blocks') {
        result = await clearAllAnalogBlocks();
      } else if (
        action === 'squelch_up'
        || action === 'squelch_down'
      ) {
        if (activeAnalogRole) {
          result = await postAnalogAction(
            activeAnalogRole,
            action
          );
        } else {
          const results = await Promise.all([
            postAnalogAction('analog_2m', action),
            postAnalogAction('analog_70cm', action),
          ]);

          result = {
            message: (
              action === 'squelch_up'
                ? 'Raised VHF and UHF squelch'
                : 'Lowered VHF and UHF squelch'
            ),
            results,
          };
        }
      } else {
        result = await postAnalogAction(
          activeAnalogRole,
          action
        );
      }

      setText(
        'lastEvent',
        result.message || `Analog action: ${action}`
      );

      await refreshStatus();
      await refreshAnalogControlState();
    } catch (error) {
      setText(
        'lastEvent',
        `Analog control error: ${error.message}`
      );
    } finally {
      actionInFlight = false;
    }
  }

  field('analogSquelchDownBtn')?.addEventListener(
    'click',
    () => analogControlAction('squelch_down')
  );
  field('analogSquelchUpBtn')?.addEventListener(
    'click',
    () => analogControlAction('squelch_up')
  );
  field('analogSkipBtn')?.addEventListener(
    'click',
    () => analogControlAction('skip')
  );
  field('analogBlockBtn')?.addEventListener(
    'click',
    () => analogControlAction('block')
  );
  field('analogClearLockBtn')?.addEventListener(
    'click',
    () => analogControlAction('clear_lock')
  );
  field('analogClearBlockBtn')?.addEventListener(
    'click',
    () => analogControlAction('clear_blocks')
  );

  async function refreshAnalogControlState() {
    try {
      const [
        audioStatus,
        analogPayload,
        controlsPayload,
      ] = await Promise.all([
        fetchJson(
          `http://${window.location.hostname}:8072/api/audio/status`
        ),
        fetchJson('/api/analog/status'),
        fetchJson('/api/analog/controls'),
      ]);

      updateControlState({
        audioSource: audioStatus?.active_source,
        analogPayload,
        controlsPayload,
      });
    } catch (_error) {
      setButtonsDisabled(squelchIds, true);
      setButtonsDisabled(channelActionIds, true);

      const clearButton = field('analogClearBlockBtn');
      if (clearButton) clearButton.disabled = true;

      setText('analogSquelchValue', '—');
    }
  }

  refreshAnalogControlState();
  window.setInterval(refreshAnalogControlState, 500);
})();


/* SAME_ORIGIN_ANALOG_CONTROLS_V117 */
(function installSameOriginAnalogControlsV117() {
  if (window.__sameOriginAnalogControlsV117Installed) return;
  window.__sameOriginAnalogControlsV117Installed = true;

  const roles = ['analog_2m', 'analog_70cm'];
  let activeRole = null;
  let controlsPayload = null;
  let busy = false;

  function button(id) {
    return document.getElementById(id);
  }

  function roleState(payload, role) {
    return payload?.roles?.[role] || {};
  }

  function roleAvailable(payload, role) {
    const item = roleState(payload, role);
    const state = String(item.state || '').toLowerCase();
    return Boolean(item.ok) && ![
      'offline', 'error', 'stopped', 'retrying'
    ].includes(state);
  }

  function chooseActiveRole(payload) {
    const locked = roles.filter((role) => {
      const state = String(
        roleState(payload, role).state || ''
      ).toLowerCase();
      return state === 'locked';
    });

    if (locked.length === 1) return locked[0];

    if (locked.length > 1) {
      return locked.sort((a, b) => {
        const aa = Number(
          roleState(payload, a).status_age_seconds ?? 999
        );
        const bb = Number(
          roleState(payload, b).status_age_seconds ?? 999
        );
        return aa - bb;
      })[0];
    }

    return null;
  }

  function thresholdValue(payload, role) {
    const value = Number(
      roleState(payload, role).threshold_rms
    );
    return Number.isFinite(value) ? Math.round(value) : null;
  }

  function renderThreshold(payload) {
    const vhf = thresholdValue(payload, 'analog_2m');
    const uhf = thresholdValue(payload, 'analog_70cm');
    const output = button('analogSquelchValue');
    if (!output) return;

    if (activeRole === 'analog_2m' && vhf !== null) {
      output.textContent = String(vhf);
    } else if (
      activeRole === 'analog_70cm' && uhf !== null
    ) {
      output.textContent = String(uhf);
    } else if (vhf !== null && uhf !== null) {
      output.textContent = (
        vhf === uhf
          ? String(vhf)
          : `VHF ${vhf} · UHF ${uhf}`
      );
    } else {
      output.textContent = '—';
    }
  }

  function hasSuppressions(payload) {
    return roles.some((role) => {
      const item = payload?.roles?.[role] || {};
      const blocked = item.blocked_frequencies_hz || [];
      const skips = item.skip_until_epoch || {};
      return blocked.length > 0 || Object.values(skips).some(
        (until) => Number(until) > Date.now() / 1000
      );
    });
  }

  function setState(statusPayload, newControlsPayload) {
    controlsPayload = newControlsPayload;
    activeRole = chooseActiveRole(statusPayload);

    const anyAvailable = roles.some(
      (role) => roleAvailable(statusPayload, role)
    );

    const down = button('analogSquelchDownBtn');
    const up = button('analogSquelchUpBtn');
    const skip = button('analogSkipBtn');
    const block = button('analogBlockBtn');
    const clearLock = button('analogClearLockBtn');
    const clear = button('analogClearBlockBtn');

    if (down) down.disabled = !anyAvailable || busy;
    if (up) up.disabled = !anyAvailable || busy;
    if (skip) skip.disabled = !activeRole || busy;
    if (block) block.disabled = !activeRole || busy;
    if (clearLock) clearLock.disabled = !activeRole || busy;
    if (clear) {
      clear.disabled = !hasSuppressions(
        newControlsPayload
      ) || busy;
    }

    const panel = button('analogLiveControls');
    if (panel) {
      panel.classList.toggle('disabled', !anyAvailable);
      panel.setAttribute(
        'aria-disabled',
        anyAvailable ? 'false' : 'true'
      );
    }

    renderThreshold(statusPayload);
  }

  async function refresh() {
    try {
      const [statusPayload, newControlsPayload] =
        await Promise.all([
          fetchJson('/api/analog/status'),
          fetchJson('/api/analog/controls'),
        ]);

      setState(statusPayload, newControlsPayload);
    } catch (_error) {
      for (const id of [
        'analogSquelchDownBtn',
        'analogSquelchUpBtn',
        'analogSkipBtn',
        'analogBlockBtn',
        'analogClearLockBtn',
        'analogClearBlockBtn',
      ]) {
        const item = button(id);
        if (item) item.disabled = true;
      }
    }
  }

  async function post(role, action) {
    return postJson('/api/analog/control', {
      role,
      action,
    });
  }

  async function act(action) {
    if (busy) return;
    busy = true;
    await refresh();

    try {
      let result = null;

      if (
        action === 'skip'
        || action === 'block'
        || action === 'clear_lock'
      ) {
        if (!activeRole) return;
        result = await post(activeRole, action);
      } else if (action === 'clear_blocks') {
        const results = await Promise.all(
          roles.map((role) => post(role, action))
        );
        result = {
          message: 'Cleared all VHF and UHF skips and blocks',
          results,
        };
      } else if (
        action === 'squelch_up'
        || action === 'squelch_down'
      ) {
        const targetRoles = activeRole
          ? [activeRole]
          : roles;

        const results = await Promise.all(
          targetRoles.map((role) => post(role, action))
        );
        result = { results };
      }

      setText(
        'lastEvent',
        result?.message || `Analog action: ${action}`
      );
      await refresh();
    } finally {
      busy = false;
      await refresh();
    }
  }

  const bindings = {
    analogSquelchDownBtn: 'squelch_down',
    analogSquelchUpBtn: 'squelch_up',
    analogSkipBtn: 'skip',
    analogBlockBtn: 'block',
    analogClearLockBtn: 'clear_lock',
    analogClearBlockBtn: 'clear_blocks',
  };

  Object.entries(bindings).forEach(([id, action]) => {
    const item = button(id);
    if (!item) return;

    item.addEventListener(
      'click',
      (event) => {
        event.preventDefault();
        event.stopImmediatePropagation();
        act(action);
      },
      true
    );
  });

  refresh();
  window.setInterval(refresh, 500);
})();
