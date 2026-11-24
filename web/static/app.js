const $ = (id) => document.getElementById(id);
const simulationForm = $('simulationForm');
let toastTimer;

function formData(form) {
  const data = Object.fromEntries(new FormData(form));
  for (const key of ['port', 'clients', 'rate', 'duration', 'slow_header_interval']) {
    if (key in data) data[key] = Number(data[key]);
  }
  return data;
}

async function request(path, data = {}) {
  const response = await fetch(path, {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(data)
  });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || 'Yêu cầu thất bại');
  return body;
}

function toast(message, error = false) {
  const node = $('toast');
  node.textContent = message;
  node.className = `toast show${error ? ' error' : ''}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => node.className = 'toast', 3200);
}

function setText(id, value) {
  const el = $(id);
  if (el) el.textContent = value;
}

function bytes(value = 0) {
  if (value < 1024) return `${value} B`;
  if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1048576).toFixed(1)} MB`;
}

let currentDefenseEnabled = true;

function render(data) {
  const server = data.server || {};
  const metrics = server.metrics || {};
  const simulation = data.simulation || {};
  const sim = simulation.metrics || {};
  currentDefenseEnabled = server.defenseEnabled ?? true;
  const defense = metrics.defense_status || (currentDefenseEnabled ? 'NORMAL' : 'DISABLED');
  const overloaded = defense === 'OVERLOADED';

  $('globalPulse').classList.toggle('online', Boolean(simulation.running));
  setText('globalStatus', overloaded ? 'SERVER OVERLOADED' : simulation.running ? 'SIMULATION ACTIVE' : 'SYSTEM READY');
  $('simulationState').className = `state-pill${simulation.running ? ' running' : ''}`;
  setText('simulationState', simulation.running ? 'RUNNING' : 'READY');

  setText('rps', metrics.requests_per_second || 0);
  setText('activeClients', metrics.current_clients || 0);
  setText('blocked', metrics.blocked_requests || 0);
  setText('blockReason', metrics.last_block_reason && metrics.last_block_reason !== '-' ? metrics.last_block_reason : 'no active rule');
  setText('latency', Number(metrics.average_response_ms || 0).toFixed(2));
  setText('trafficPattern', metrics.traffic_classification || 'NORMAL');
  setText('trustScore', `${metrics.trust_score ?? 100} / 100`);
  setText('successful', metrics.successful_requests || 0);
  setText('failed', metrics.failed_requests || 0);
  setText('suspicious', metrics.suspicious_requests || 0);
  setText('bytesReceived', bytes(metrics.bytes_received || 0));

  const total = Number(metrics.total_requests || 0);
  const successRate = total ? Math.round((Number(metrics.successful_requests || 0) / total) * 100) : 100;
  setText('successRate', successRate);
  $('gauge').style.setProperty('--value', successRate);
  setText('defenseState', defense === 'OVERLOADED' ? 'OVERLOADED (SẬP)' : defense);
  $('defenseState').className = `shield-state ${defense.toLowerCase()}`;
  const toggleBtn = $('toggleDefense');
  toggleBtn.textContent = currentDefenseEnabled ? 'Tắt phòng thủ (Demo sập)' : 'Bật phòng thủ (Bảo vệ)';
  toggleBtn.className = `ghost toggle-defense ${currentDefenseEnabled ? 'danger' : 'enable-defense'}`;

  setText('sent', sim.sent || 0);
  setText('simSuccessful', sim.successful || 0);
  setText('simFailed', sim.failed || 0);
  setText('simLatency', `${Number(sim.average_latency_ms || 0).toFixed(2)} ms`);
  setText('elapsed', `${Number(sim.elapsed_seconds || 0).toFixed(1).padStart(4, '0')}s`);
  const duration = Number(simulation.config?.duration || 0);
  const progress = duration ? Math.min(100, Number(sim.elapsed_seconds || 0) / duration * 100) : 0;
  $('progressBar').style.width = `${progress}%`;

  $('startSimulation').disabled = Boolean(simulation.running);
  $('stopSimulation').disabled = !simulation.running;
  if (simulation.error) toast(simulation.error, true);
}

async function refresh() {
  try {
    const response = await fetch('/api/status', {cache: 'no-store'});
    render(await response.json());
  } catch (error) {
    toast(`Mất kết nối dashboard: ${error.message}`, true);
  }
}

simulationForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  try { await request('/api/simulation/start', formData(simulationForm)); toast('Simulation đang chạy.'); await refresh(); }
  catch (error) { toast(error.message, true); }
});
$('stopSimulation').addEventListener('click', async () => {
  try { await request('/api/simulation/stop'); toast('Simulation đã dừng.'); await refresh(); }
  catch (error) { toast(error.message, true); }
});
$('toggleDefense').addEventListener('click', async () => {
  try {
    const nextState = !currentDefenseEnabled;
    await request('/api/defense/toggle', {enabled: nextState});
    toast(nextState ? 'Đã BẬT phòng thủ bảo vệ server.' : 'Đã TẮT phòng thủ (chế độ dễ bị DoS làm quá tải).');
    await refresh();
  } catch (error) { toast(error.message, true); }
});

function syncIpMode(notify = false) {
  const protocol = $('simProtocol').value;
  const scenario = $('mode').value;
  const ipModeSelect = $('ipMode');
  const multiOption = $('multiIpOption');
  const spoofOption = $('udpSpoofOption');
  const singleOption = $('singleIpOption');

  if (protocol === 'SLOW_HEADERS') {
    $('httpLabOptions').hidden = true;
    $('slowHeadersOptions').hidden = false;
    ipModeSelect.value = 'SINGLE_IP';
    ipModeSelect.disabled = true;
    multiOption.disabled = true;
    spoofOption.disabled = true;
    singleOption.disabled = false;
    simulationForm.elements.clients.max = 20;
    $('slowHeadersOptions').hidden = false;
    if (notify) toast('SLOW_HEADERS chỉ chạy loopback, tối đa 20 kết nối và 30 giây.');
    return;
  }

  const isHttpLab = protocol === 'HTTP_GET_SEARCH' || protocol === 'HTTP_POST_LOGIN';
  $('httpLabOptions').hidden = !isHttpLab;
  $('getSearchOptions').hidden = protocol !== 'HTTP_GET_SEARCH';
  $('postLoginOptions').hidden = protocol !== 'HTTP_POST_LOGIN';
  if (isHttpLab) {
    $('slowHeadersOptions').hidden = true;
    ipModeSelect.value = 'SINGLE_IP';
    ipModeSelect.disabled = true;
    multiOption.disabled = true;
    spoofOption.disabled = true;
    singleOption.disabled = false;
    simulationForm.elements.clients.max = 20;
    simulationForm.elements.rate.max = 10;
    simulationForm.elements.duration.max = 30;
    if (Number(simulationForm.elements.clients.value) > 20) simulationForm.elements.clients.value = 20;
    if (Number(simulationForm.elements.rate.value) > 10) simulationForm.elements.rate.value = 10;
    if (Number(simulationForm.elements.duration.value) > 30) simulationForm.elements.duration.value = 30;
    return;
  }

  ipModeSelect.disabled = false;
  $('slowHeadersOptions').hidden = true;
  $('httpLabOptions').hidden = true;
  $('getSearchOptions').hidden = true;
  $('postLoginOptions').hidden = true;
  simulationForm.elements.rate.max = 50;
  simulationForm.elements.duration.max = 300;
  simulationForm.elements.clients.max = 100;

  if (scenario === 'DOS_SIMULATION') {
    multiOption.disabled = true;
    multiOption.textContent = 'MULTI_IP — Đa IP nguồn (Khóa ở chế độ DoS)';
    spoofOption.disabled = true;
    spoofOption.textContent = 'UDP_SPOOF — UDP IP Spoofing (Khóa ở chế độ DoS)';
    singleOption.disabled = false;
    singleOption.textContent = 'SINGLE_IP — 1 IP nguồn duy nhất (DoS)';
    ipModeSelect.value = 'SINGLE_IP';
    if (notify) toast('Chế độ DoS: Khóa MULTI_IP và UDP_SPOOF, chỉ dùng 1 IP nguồn duy nhất (SINGLE_IP).');
    return;
  }

  multiOption.disabled = false;
  multiOption.textContent = 'MULTI_IP — Đa IP nguồn (Dùng chung TCP & UDP)';

  if (scenario === 'DDOS_SIMULATION') {
    singleOption.disabled = true;
    singleOption.textContent = 'SINGLE_IP — 1 IP nguồn duy nhất (Khóa ở chế độ DDoS)';
  } else {
    singleOption.disabled = false;
    singleOption.textContent = 'SINGLE_IP — 1 IP nguồn duy nhất (DoS)';
  }

  if (protocol === 'UDP') {
    spoofOption.disabled = false;
    spoofOption.textContent = 'UDP_SPOOF — UDP IP Spoofing (Đang mở)';
    if (scenario === 'DDOS_SIMULATION' || ipModeSelect.value === 'SINGLE_IP') {
      ipModeSelect.value = 'UDP_SPOOF';
    }
    if (notify) toast('Đã chuyển sang UDP: Đã mở chế độ UDP IP Spoofing!');
  } else {
    spoofOption.disabled = true;
    spoofOption.textContent = 'UDP_SPOOF — UDP IP Spoofing (Chỉ mở khi dùng UDP)';
    if (ipModeSelect.value === 'UDP_SPOOF' || (scenario === 'DDOS_SIMULATION' && ipModeSelect.value === 'SINGLE_IP')) {
      ipModeSelect.value = 'MULTI_IP';
      if (notify) toast('TCP cần bắt tay 3 bước: Đã chuyển về chế độ Đa IP nguồn (MULTI_IP).');
    }
  }
}

$('simProtocol').addEventListener('change', () => {
  syncIpMode(true);
});

window.applyAttackPreset = function(type) {
  if (type === 'DOS') {
    $('mode').value = 'DOS_SIMULATION';
    simulationForm.elements.clients.value = 25;
    simulationForm.elements.rate.value = 8;
    simulationForm.elements.duration.value = 20;
    simulationForm.elements.port.value = 5000;
    syncIpMode(false);
    toast('Đã chọn cấu hình DoS (1 IP · 25 Luồng · 8 r/s)! Bấm "Chạy mô phỏng" để xem web lag.');
  } else if (type === 'DDOS') {
    $('mode').value = 'DDOS_SIMULATION';
    simulationForm.elements.clients.value = 35;
    simulationForm.elements.rate.value = 10;
    simulationForm.elements.duration.value = 20;
    simulationForm.elements.port.value = 5000;
    syncIpMode(false);
    if ($('simProtocol').value === 'TCP') $('ipMode').value = 'MULTI_IP';
    toast('Đã chọn cấu hình DDoS cực đại (35 Bots · 10 r/s)! Bấm "Chạy mô phỏng" để xem web lag.');
  }
};

window.runSlowHeadersEcommerce = function() {
  $('simProtocol').value = 'SLOW_HEADERS';
  $('mode').value = 'NORMAL';
  simulationForm.elements.host.value = '127.0.0.1';
  simulationForm.elements.port.value = 5000;
  simulationForm.elements.clients.value = 2;
  simulationForm.elements.rate.value = 1;
  simulationForm.elements.duration.value = 20;
  simulationForm.elements.search_keyword.value = 'laptop';
  simulationForm.elements.login_username_prefix.value = 'lab_user';
  simulationForm.elements.slow_header_interval.value = 5;
  syncIpMode(false);
  toast('Đã chọn SLOW_HEADERS cho Web E-commerce. Kiểm tra tham số rồi nhấn “Chạy mô phỏng”.');
};

window.selectHttpLabScenario = function(protocol) {
  $('simProtocol').value = protocol;
  $('mode').value = 'NORMAL';
  simulationForm.elements.host.value = '127.0.0.1';
  simulationForm.elements.port.value = 5000;
  simulationForm.elements.clients.value = 12;
  simulationForm.elements.rate.value = 6;
  simulationForm.elements.duration.value = 20;
  syncIpMode(false);
  toast(`${protocol === 'HTTP_GET_SEARCH' ? 'Database Search GET' : 'HTTP POST Login'} đã sẵn sàng. Nhấn “Chạy mô phỏng” để bắt đầu.`);
};

$('mode').addEventListener('change', (event) => {
  const presets = {
    NORMAL: [2, 1, 10],
    FLASH_CROWD: [35, 4.5, 15],
    DOS_SIMULATION: [25, 8, 20],
    DDOS_SIMULATION: [35, 10, 20]
  };
  const modeVal = event.target.value;
  const [clients, rate, duration] = presets[modeVal] || presets.NORMAL;
  simulationForm.elements.clients.value = clients;
  simulationForm.elements.rate.value = rate;
  simulationForm.elements.duration.value = duration;
  syncIpMode(modeVal === 'DOS_SIMULATION');
  if (modeVal !== 'DOS_SIMULATION' && $('simProtocol').value === 'TCP') {
    $('ipMode').value = 'MULTI_IP';
  }
});

setInterval(() => setText('clock', new Date().toLocaleTimeString('vi-VN', {hour12: false})), 1000);
setInterval(refresh, 1000);
syncIpMode(false);
refresh();
