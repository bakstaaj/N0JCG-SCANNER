'use strict';

let currentConfig = null;
let latestStatus = null;
let catalog = null;
let matchedSystems = [];
let browserAudioLastEvent = 'Ready';

const CATEGORY_DEFAULTS = [
  'Fire', 'EMS', 'Law Enforcement', 'Public Works', 'Utilities', 'Transportation',
  'Interop', 'Emergency Management', 'Corrections', 'Schools', 'Federal', 'Other',
];

function field(id) { return document.getElementById(id); }
function setText(id, value) { const el = field(id); if (el) el.textContent = value ?? '-'; }
function setBadge(id, text, kind) { const el = field(id); if (!el) return; el.textContent = text; el.className = `pill ${kind || ''}`.trim(); }
function formatHz(value) { if (!value) return '-'; return `${(Number(value) / 1000000).toFixed(6)} MHz`; }
function formatBool(value) { return value ? 'yes' : 'no'; }
function formatList(values) { return Array.isArray(values) && values.length ? values.join('\n') : '-'; }
function commandText(command) { if (Array.isArray(command)) return command.join(' '); return typeof command === 'string' ? command : ''; }
function normalizeText(value) {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
}

const STATE_ALIASES = {
  al: ['al', 'alabama'], ak: ['ak', 'alaska'], az: ['az', 'arizona'], ar: ['ar', 'arkansas'],
  ca: ['ca', 'california'], co: ['co', 'colorado'], ct: ['ct', 'connecticut'], de: ['de', 'delaware'],
  fl: ['fl', 'florida'], ga: ['ga', 'georgia'], hi: ['hi', 'hawaii'], id: ['id', 'idaho'],
  il: ['il', 'illinois'], in: ['in', 'indiana'], ia: ['ia', 'iowa'], ks: ['ks', 'kansas'],
  ky: ['ky', 'kentucky'], la: ['la', 'louisiana'], me: ['me', 'maine'], md: ['md', 'maryland'],
  ma: ['ma', 'massachusetts'], mi: ['mi', 'michigan'], mn: ['mn', 'minnesota'], ms: ['ms', 'mississippi'],
  mo: ['mo', 'missouri'], mt: ['mt', 'montana'], ne: ['ne', 'nebraska'], nv: ['nv', 'nevada'],
  nh: ['nh', 'new hampshire'], nj: ['nj', 'new jersey'], nm: ['nm', 'new mexico'], ny: ['ny', 'new york'],
  nc: ['nc', 'north carolina'], nd: ['nd', 'north dakota'], oh: ['oh', 'ohio'], ok: ['ok', 'oklahoma'],
  or: ['or', 'oregon'], pa: ['pa', 'pennsylvania'], ri: ['ri', 'rhode island'], sc: ['sc', 'south carolina'],
  sd: ['sd', 'south dakota'], tn: ['tn', 'tennessee'], tx: ['tx', 'texas'], ut: ['ut', 'utah'],
  vt: ['vt', 'vermont'], va: ['va', 'virginia'], wa: ['wa', 'washington'], wv: ['wv', 'west virginia'],
  wi: ['wi', 'wisconsin'], wy: ['wy', 'wyoming'], dc: ['dc', 'district of columbia']
};

function stateAliasSet(value) {
  const text = normalizeText(value);
  for (const aliases of Object.values(STATE_ALIASES)) {
    if (aliases.includes(text)) return aliases;
  }
  return text ? [text] : [];
}

function catalogSearchValues(system) {
  const values = [system.state, system.county, system.city, system.site, system.name, system.notes];
  ['aliases', 'coverage', 'counties', 'cities', 'sites'].forEach((key) => {
    if (Array.isArray(system[key])) values.push(...system[key]);
  });
  return values.map(normalizeText).filter(Boolean);
}

function containsQuery(values, query) {
  const normalized = normalizeText(query);
  if (!normalized) return true;
  return values.some((value) => value === normalized || value.includes(normalized) || normalized.includes(value));
}

function audioStreamUrl() { return `http://${window.location.hostname}:8072/audio.wav`; }
function testToneUrl() { return `http://${window.location.hostname}:8072/test-tone.wav`; }

async function fetchJson(url, options = {}) {
  const response = await fetch(url, { cache: 'no-store', ...options });
  const text = await response.text();
  let payload = {};
  try { payload = text ? JSON.parse(text) : {}; } catch (error) { payload = { ok: false, error: `Invalid JSON: ${error.message}`, raw: text }; }
  if (!response.ok) throw new Error(payload.error || `HTTP ${response.status}`);
  return payload;
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
  const combined = `${commandText(process.command)} ${JSON.stringify(marker)}`;
  const match = combined.match(/http:(?:\[[^\]]+\]|[^:\s]+):(\d{1,5})/);
  if (!match) return null;
  const port = Number(match[1]);
  if (!Number.isInteger(port) || port <= 0 || port > 65535) return null;
  return { port, piLocalUrl: `http://127.0.0.1:${port}/` };
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

function updateAudioPanel(message) {
  const audio = field('browserAudioPlayer');
  if (audio && !audio.src) audio.src = audioStreamUrl();
  if (message) browserAudioLastEvent = message;
  setText('browserAudioLastEvent', browserAudioLastEvent);
}

async function playBrowserAudio() {
  const audio = field('browserAudioPlayer');
  if (!audio) return false;
  if (audio.src !== audioStreamUrl()) audio.src = audioStreamUrl();
  try {
    await audio.play();
    updateAudioPanel('Browser audio playing');
    return true;
  } catch (error) {
    updateAudioPanel(`Press audio play if blocked: ${error.message}`);
    return false;
  }
}

async function playBrowserTestTone() {
  const audio = field('browserAudioPlayer');
  if (!audio) return;
  audio.src = testToneUrl();
  try {
    await audio.play();
    updateAudioPanel('Playing bridge test tone');
  } catch (error) {
    updateAudioPanel(`Test tone failed: ${error.message}`);
  }
}

function stopBrowserAudio() {
  const audio = field('browserAudioPlayer');
  if (!audio) return;
  audio.pause();
  audio.src = audioStreamUrl();
  updateAudioPanel('Browser audio stopped');
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

function bestTalkgroup(status) {
  const recent = Array.isArray(status?.activity_summary?.recent_events) ? status.activity_summary.recent_events : [];
  const fallback = [...recent].reverse().find((event) => event && (event.tgid || event.talkgroup_label));
  const tgid = status?.active_tgid || status?.last_active_tgid || fallback?.tgid || null;
  const configuredLabel = tgid ? status?.talkgroup_catalog?.labels?.[String(tgid)] : '';
  const rawLabel = status?.active_talkgroup_label || status?.last_active_talkgroup_label || fallback?.talkgroup_label || configuredLabel || '';
  const process = status?.decoder_process || {};
  const running = Boolean(process.running);
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

function renderActivitySummary(activity) {
  setText('activityUniqueTgids', activity?.unique_tgid_count ?? 0);  setText('activityClearEvents', activity?.clear_voice_events ?? 0);
  setText('activityEncryptedEvents', activity?.encrypted_events ?? 0);
  setText('activityMutedEvents', activity?.muted_events ?? 0);
  const recent = Array.isArray(activity?.recent_events) ? activity.recent_events : [];
  setText('activityRecentEvents', recent.length ? recent.slice(-10).map(formatActivityEvent).join('\n') : 'No parsed activity yet.');
}

function renderDashboard(status) {
  const process = status.decoder_process || {};
  const marker = process.validated_marker || {};
  const running = Boolean(process.running);
  const ready = markerIsReady(marker) || Boolean(process.start_enabled);
  const state = status.scanner_state || '-';
  const listener = extractOp25HttpListener(status);
  const talkgroup = bestTalkgroup(status);
  setText('dashboardSummary', running ? (talkgroup.has_talkgroup ? talkgroup.short_label : `Running on ${formatHz(status.active_control_frequency_hz)}`) : (ready ? 'Ready to start' : 'Not launch-ready'));
  setText('scannerState', state);
  setText('decoderPid', process.pid || '-');
  setText('controlFrequency', formatHz(status.active_control_frequency_hz));
  setText('voiceFrequency', formatHz(talkgroup.voice_frequency_hz));
  setText('activeTgid', talkgroup.tgid_text);
  setText('activeTalkgroupLabel', talkgroup.label);
  setText('p25Phase', status.p25_phase || '-');
  setText('op25HttpListener', listener ? listener.piLocalUrl : '-');
  setText('launchReady', ready ? 'yes' : 'no');
  setText('commandSource', process.command_source || '-');
  setText('validatedMarkerState', markerIsReady(marker) ? 'validated' : (marker.exists ? 'present' : 'missing'));
  setText('validatedCommand', formatList(process.command));
  setText('lastEvent', status.last_event || '-');
  setText('logTail', formatList(status.log_tail));
  setText('lastUpdated', `Last update: ${new Date().toLocaleTimeString()}`);
  setBadge('stateBadge', running ? 'ON AIR' : state, running ? 'ok' : (status.ok ? 'warn' : 'bad'));
  setBadge('connectionStatus', 'Connected', 'ok');
  renderActivitySummary(status.activity_summary || {});
  updateAudioPanel();
  setButtonsForState(status);
}

async function refreshStatus() {
  try {
    const status = await fetchJson('/api/status');
    latestStatus = status;
    renderDashboard(status);
  } catch (error) {
    setBadge('connectionStatus', 'Offline', 'bad');
    setText('dashboardSummary', `Status error: ${error.message}`);
    setText('lastEvent', `Status error: ${error.message}`);
  }
}

function toMhzLines(values) { return (values || []).map((value) => (Number(value) / 1000000).toFixed(6)).join('\n'); }
function parseFrequencyLines(value) {
  return value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
    const cleaned = line.toLowerCase().replace('mhz', '').replace('hz', '').replace(/[, _]/g, '');
    const numeric = Number(cleaned);
    if (!Number.isFinite(numeric) || numeric <= 0) throw new Error(`Invalid frequency: ${line}`);
    return numeric < 10000 ? Math.round(numeric * 1000000) : Math.round(numeric);
  });
}
function parseTalkgroupLines(value) {
  return value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line) => {
    const parts = line.split(',');
    const tgid = Number(parts.shift().trim());
    if (!Number.isInteger(tgid) || tgid <= 0) throw new Error(`Invalid TGID: ${line}`);
    const label = parts.join(',').trim() || String(tgid);
    return { tgid, label, enabled: true };
  });
}

function populateForm(config) {
  const system = config?.systems?.[0] || {};
  field('systemName').value = system.name || '';
  field('siteName').value = system.site || '';
  field('controlChannels').value = toMhzLines(system.control_channels_hz);
  field('talkgroups').value = (system.talkgroups || []).filter((tg) => tg.enabled !== false).map((tg) => `${tg.tgid}, ${tg.label || tg.tgid}`).join('\n');
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
    systems: [{
      ...system,
      name: field('systemName').value.trim() || 'Local P25 System',
      enabled: true,
      mode: 'p25_trunked',
      site: field('siteName').value.trim(),
      control_channels_hz: parseFrequencyLines(field('controlChannels').value),
      voice_channels_hz: system.voice_channels_hz || [],
      talkgroups: parseTalkgroupLines(field('talkgroups').value),
      receiver_roles: {
        p25_control: { rtl_serial: field('controlSerial').value.trim(), gain_db: gain, ppm },
        p25_voice: { rtl_serial: field('voiceSerial').value.trim(), gain_db: gain, ppm },
      },
      decoder: { ...(system.decoder || {}), engine: 'op25', phase_ii_enabled: field('phaseII').checked, mute_encrypted: field('muteEncrypted').checked },
    }],
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

async function saveConfig(config = null) {
  try {
    const payload = config || buildConfigFromForm();
    const response = await fetchJson('/api/config/save', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ config: payload }) });
    currentConfig = payload;
    setText('lastEvent', `Saved config: ${response.config_path}`);
    await refreshConfig();
    await refreshStatus();
    return response;
  } catch (error) {
    setText('lastEvent', `Save error: ${error.message}`);
    throw error;
  }
}

async function initLocalConfig() {
  try {
    const response = await fetchJson('/api/config/init-local', { method: 'POST' });
    setText('lastEvent', `Initialized local config: ${response.config_path}`);
    await refreshConfig();
    await refreshStatus();
  } catch (error) { setText('lastEvent', `Init config error: ${error.message}`); }
}

async function generateOp25Config() {
  try {
    const status = await fetchJson('/api/decoder/generate-config', { method: 'POST' });
    setText('lastEvent', 'Generated OP25 runtime config.');
    if (status.status) renderDashboard(status.status);
    await refreshStatus();
  } catch (error) { setText('lastEvent', `Generate config error: ${error.message}`); }
}

async function startScannerAndAudio() {
  const startBtn = field('startBtn');
  if (startBtn) startBtn.disabled = true;
  const audio = field('browserAudioPlayer');
  if (audio) audio.src = audioStreamUrl();
  const playPromise = audio ? audio.play().catch((error) => { updateAudioPanel(`Press audio play if blocked: ${error.message}`); return false; }) : Promise.resolve(false);
  try {
    const status = await fetchJson('/api/scanner/start', { method: 'POST' });
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
  stopBrowserAudio();
  try {
    const status = await fetchJson('/api/scanner/stop', { method: 'POST' });
    renderDashboard(status);
  } catch (error) { setText('lastEvent', `Stop error: ${error.message}`); }
  await refreshStatus();
}

async function loadCatalog() {
  if (catalog) return catalog;
  const response = await fetch('/system_catalog.example.json', { cache: 'no-store' });
  catalog = await response.json();
  renderCategoryChoices(catalog.categories || CATEGORY_DEFAULTS);
  return catalog;
}

function selectedCategories() {
  return Array.from(document.querySelectorAll('#categoryChoices input[type="checkbox"]:checked')).map((el) => el.value);
}

function renderCategoryChoices(categories) {
  const target = field('categoryChoices');
  if (!target) return;
  target.innerHTML = '';
  categories.forEach((category) => {
    const label = document.createElement('label');
    const input = document.createElement('input');
    input.type = 'checkbox';
    input.value = category;
    input.checked = ['Fire', 'EMS', 'Law Enforcement', 'Public Works', 'Interop'].includes(category);
    label.append(input, document.createTextNode(category));
    target.append(label);
  });
}

function systemMatches(system, state, county, city) {
  const values = catalogSearchValues(system);
  const stateQueryAliases = stateAliasSet(state);
  const systemStateAliases = stateAliasSet(system.state);
  const stateMatch = !stateQueryAliases.length || stateQueryAliases.some((query) => systemStateAliases.includes(query));
  const countyMatch = containsQuery(values, county);
  const cityMatch = containsQuery(values, city);
  return stateMatch && countyMatch && cityMatch;
}

function renderWizardMatches() {
  const select = field('wizardSystemSelect');
  if (!select) return;
  select.innerHTML = '';
  matchedSystems.forEach((system, index) => {
    const option = document.createElement('option');
    option.value = String(index);
    option.textContent = `${system.name} — ${system.city || system.site || ''}`;
    select.append(option);
  });
  if (matchedSystems.length) select.value = '0';
  renderWizardPreview();
}

async function findSystems() {
  const data = await loadCatalog();
  const state = normalizeText(field('wizardState').value);
  const county = normalizeText(field('wizardCounty').value);
  const city = normalizeText(field('wizardCity').value);
  matchedSystems = (data.systems || []).filter((system) => systemMatches(system, state, county, city));
  renderWizardMatches();
  if (!matchedSystems.length) {
    setText('wizardPreview', `No local catalog match for State=${field('wizardState').value || '-'}, County=${field('wizardCounty').value || '-'}, City=${field('wizardCity').value || '-'}. Try leaving County or City blank, or add aliases/coverage to web/system_catalog.example.json.`);
  }
}

function selectedWizardSystem() {
  const select = field('wizardSystemSelect');
  const index = Number(select?.value || 0);
  return matchedSystems[index] || null;
}

function filteredTalkgroups(system) {
  const categories = selectedCategories();
  const talkgroups = Array.isArray(system?.talkgroups) ? system.talkgroups : [];
  if (!categories.length) return talkgroups;
  return talkgroups.filter((tg) => categories.includes(tg.category || 'Other'));
}

function renderWizardPreview() {
  const system = selectedWizardSystem();
  if (!system) { setText('wizardPreview', 'No system selected.'); return; }
  const talkgroups = filteredTalkgroups(system);
  const preview = {
    name: system.name,
    site: system.site,
    control_channels_mhz: (system.control_channels_hz || []).map((hz) => (hz / 1000000).toFixed(6)),
    selected_talkgroups: talkgroups.map((tg) => ({ tgid: tg.tgid, label: tg.label, category: tg.category })),
    notes: system.notes || '',
  };
  setText('wizardPreview', JSON.stringify(preview, null, 2));
}

function buildConfigFromWizard(system) {
  const baseSystem = currentConfig?.systems?.[0] || {};
  const controlRole = baseSystem.receiver_roles?.p25_control || {};
  const voiceRole = baseSystem.receiver_roles?.p25_voice || controlRole;
  const talkgroups = filteredTalkgroups(system).map((tg) => ({ tgid: Number(tg.tgid), label: tg.label || String(tg.tgid), category: tg.category || 'Other', enabled: tg.enabled !== false }));
  return {
    schema_version: Number(currentConfig?.schema_version || 1),
    systems: [{
      ...baseSystem,
      name: system.name || 'Local P25 System',
      enabled: true,
      mode: 'p25_trunked',
      site: system.site || system.city || '',
      control_channels_hz: system.control_channels_hz || [],
      voice_channels_hz: system.voice_channels_hz || [],
      talkgroups,
      receiver_roles: {
        p25_control: { rtl_serial: controlRole.rtl_serial || '', gain_db: controlRole.gain_db ?? 40.2, ppm: controlRole.ppm ?? 0 },
        p25_voice: { rtl_serial: voiceRole.rtl_serial || '', gain_db: voiceRole.gain_db ?? controlRole.gain_db ?? 40.2, ppm: voiceRole.ppm ?? controlRole.ppm ?? 0 },
      },
      decoder: { ...(baseSystem.decoder || {}), engine: 'op25', phase_ii_enabled: true, mute_encrypted: true },
    }],
  };
}

async function applyWizardConfig() {
  const system = selectedWizardSystem();
  if (!system) { setText('wizardPreview', 'Select a matched system first.'); return; }
  try {
    const config = buildConfigFromWizard(system);
    await saveConfig(config);
    await generateOp25Config();
    showScreen('dashboardScreen');
    setText('lastEvent', `Applied wizard config: ${system.name}`);
  } catch (error) { setText('wizardPreview', `Apply failed: ${error.message}`); }
}

function attachEventHandlers() {
  field('menuBtn')?.addEventListener('click', openDrawer);
  field('closeDrawerBtn')?.addEventListener('click', closeDrawer);
  field('drawerBackdrop')?.addEventListener('click', closeDrawer);
  document.querySelectorAll('.nav-item').forEach((button) => button.addEventListener('click', () => showScreen(button.dataset.screen)));
  field('startBtn')?.addEventListener('click', startScannerAndAudio);
  field('stopBtn')?.addEventListener('click', stopScanner);
  field('loadConfigBtn')?.addEventListener('click', refreshConfig);
  field('initLocalConfigBtn')?.addEventListener('click', initLocalConfig);
  field('saveConfigBtn')?.addEventListener('click', () => saveConfig());
  field('generateConfigBtn')?.addEventListener('click', generateOp25Config);
  field('findSystemsBtn')?.addEventListener('click', findSystems);
  field('applyWizardBtn')?.addEventListener('click', applyWizardConfig);
  field('wizardSystemSelect')?.addEventListener('change', renderWizardPreview);
  field('categoryChoices')?.addEventListener('change', renderWizardPreview);
  field('browserAudioPlayer')?.addEventListener('play', () => updateAudioPanel('Browser audio playing'));
  field('browserAudioPlayer')?.addEventListener('pause', () => updateAudioPanel('Browser audio paused'));
}

attachEventHandlers();
updateAudioPanel();
loadCatalog().catch((error) => setText('wizardPreview', `Catalog load failed: ${error.message}`));
refreshStatus();
refreshConfig();
setInterval(refreshStatus, 3000);
