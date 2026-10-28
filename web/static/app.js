const $ = (id) => document.getElementById(id);
const serverForm = $('serverForm');
const simulationForm = $('simulationForm');
let toastTimer;
let autoBrowseTimer = null;
let autoBrowseStep = 0;
const SERVICE_PATHS = ['/home', '/products', '/search', '/cart', '/checkout', '/login'];

function formData(form) {
  const data = Object.fromEntries(new FormData(form));
  for (const key of ['port', 'clients', 'rate', 'duration']) {
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

function setText(id, value) { $(id).textContent = value; }
function bytes(value = 0) {
  if (value < 1024) return `${value} B`;
  if (value < 1048576) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / 1048576).toFixed(1)} MB`;
}

let currentDefenseEnabled = true;

function render(data) {
  const server = data.server;
  const metrics = server.metrics || {};
  const simulation = data.simulation;
  const sim = simulation.metrics || {};
  const online = server.running;
  currentDefenseEnabled = server.defenseEnabled ?? true;
  const defense = metrics.defense_status || (currentDefenseEnabled ? 'NORMAL' : 'DISABLED');
  const overloaded = defense === 'OVERLOADED';

  $('globalPulse').classList.toggle('online', online || simulation.running);
  setText('globalStatus', overloaded ? 'SERVER OVERLOADED' : online ? 'SERVER ONLINE' : simulation.running ? 'SIMULATION ACTIVE' : 'SYSTEM IDLE');
  $('serverState').className = `state-pill${overloaded ? ' overloaded' : online ? ' online' : ''}`;
  setText('serverState', overloaded ? 'OVERLOADED (SẬP)' : online ? 'ONLINE' : server.error ? 'ERROR' : 'OFFLINE');
  $('simulationState').className = `state-pill${simulation.running ? ' running' : ''}`;
  setText('simulationState', simulation.running ? 'RUNNING' : 'READY');

  setText('rps', metrics.requests_per_second || 0);
  setText('activeClients', metrics.current_clients || 0);
  setText('blocked', metrics.blocked_requests || 0);
  setText('blockReason', metrics.last_block_reason && metrics.last_block_reason !== '-' ? metrics.last_block_reason : 'no active rule');
  setText('latency', Number(metrics.average_response_ms || 0).toFixed(2));
  setText('connections', metrics.current_connections || 0);
  setText('activeIps', metrics.active_ips || 0);
  setText('totalRequests', metrics.total_requests || 0);
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

  $('startServer').disabled = online;
  $('stopServer').disabled = !online;
  $('startSimulation').disabled = simulation.running;
  $('stopSimulation').disabled = !simulation.running;
  if (server.error) toast(server.error, true);
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

serverForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  try { await request('/api/server/start', formData(serverForm)); toast('Test server đã khởi động.'); await refresh(); }
  catch (error) { toast(error.message, true); }
});
$('stopServer').addEventListener('click', async () => {
  try { await request('/api/server/stop'); toast('Test server đã dừng.'); await refresh(); }
  catch (error) { toast(error.message, true); }
});
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
    toast(nextState ? 'Đã BẬT TrafficGuard bảo vệ server.' : 'Đã TẮT TrafficGuard (chế độ dễ bị DoS làm quá tải).');
    await refresh();
  } catch (error) { toast(error.message, true); }
});

function renderServiceView(service) {
  const viewport = $('serviceViewport');
  const badge = $('serviceBadge');
  const itemsNode = $('serviceItems');
  viewport.classList.remove('loading');

  $('serviceUrl').textContent = service.target || 'tcp://127.0.0.1:9000/';
  $('serviceLatency').textContent = `${Number(service.latencyMs || 0).toFixed(2)} ms`;

  if (!service.ok) {
    viewport.classList.add('crashed');
    const errCode = service.response?.error || 'SERVICE_UNAVAILABLE';
    badge.className = 'state-pill overloaded';
    badge.textContent = `${service.httpStatus || 503} ${errCode}`;
    $('servicePageTitle').textContent = '503 Service Unavailable — Dịch vụ Web bị tê liệt!';
    $('servicePageMessage').textContent = ` Phản hồi thất bại sau ${Number(service.latencyMs || 0).toFixed(1)} ms (${errCode}).`;
    itemsNode.innerHTML = '';
    const crashBox = document.createElement('div');
    crashBox.className = 'crash-box';
    crashBox.style.gridColumn = '1 / -1';
    const title = document.createElement('strong');
    title.textContent = `⚠ KHÔNG THỂ TẢI TRANG DỊCH VỤ (${errCode})`;
    const desc = document.createElement('p');
    desc.textContent = errCode === 'SERVER_UNREACHABLE'
      ? 'Test Server chưa khởi động hoặc không thể kết nối. Hãy bấm "Khởi động server" ở mục 01.'
      : 'Toàn bộ khe xử lý (Worker Slots) của Server đang bị DoS/DDoS chiếm dụng khiến khách hàng thật không thể truy cập! Hãy BẬT PHÒNG THỦ ở mục 03 để khôi phục dịch vụ ngay lập tức.';
    crashBox.append(title, desc);
    itemsNode.appendChild(crashBox);
    return;
  }

  viewport.classList.remove('crashed');
  const slow = Number(service.latencyMs || 0) > 80;
  badge.className = `state-pill ${slow ? 'running' : 'online'}`;
  badge.textContent = `${service.httpStatus} OK · ${Number(service.latencyMs || 0).toFixed(1)} ms`;

  const payload = service.response?.service || {};
  $('servicePageTitle').textContent = payload.page || 'NetShop Portal';
  $('servicePageMessage').textContent = payload.message || 'Phản hồi thành công từ Test Server.';
  if (payload.order_id) {
    $('serviceOrderBadge').textContent = `MÃ ĐƠN: ${payload.order_id} (${payload.total})`;
  } else if (payload.total) {
    $('serviceOrderBadge').textContent = `TỔNG GIỎ HÀNG: ${payload.total}`;
  } else {
    $('serviceOrderBadge').textContent = 'CLIENT: web_visitor_01';
  }

  const items = payload.items || [];
  itemsNode.innerHTML = '';
  if (!items.length) {
    const card = document.createElement('div');
    card.className = 'product-card';
    card.style.gridColumn = '1 / -1';
    const h4 = document.createElement('h4');
    h4.textContent = payload.order_id ? `Đơn hàng ${payload.order_id} đã xác nhận` : 'Trạng thái phiên làm việc';
    const p = document.createElement('p');
    p.className = 'product-price';
    p.textContent = payload.total || '200 OK — Xử lý tức thời';
    const small = document.createElement('small');
    small.textContent = `Thời gian phản hồi socket: ${Number(service.latencyMs || 0).toFixed(2)} ms`;
    card.append(h4, p, small);
    itemsNode.appendChild(card);
    return;
  }

  for (const item of items) {
    const card = document.createElement('div');
    card.className = 'product-card';
    const tag = document.createElement('span');
    tag.className = 'product-tag';
    tag.textContent = item.tag || 'ITEM';
    const h4 = document.createElement('h4');
    h4.textContent = item.name;
    const price = document.createElement('p');
    price.className = 'product-price';
    price.textContent = item.price;
    const stock = document.createElement('small');
    stock.textContent = `Kho: ${item.stock} sản phẩm`;
    card.append(tag, h4, price, stock);
    itemsNode.appendChild(card);
  }
}

async function browseService(path) {
  for (const btn of document.querySelectorAll('.service-tab')) {
    btn.classList.toggle('active', btn.dataset.path === path);
  }
  $('serviceViewport').classList.add('loading');
  try {
    const result = await request('/api/service/request', {
      path,
      host: serverForm.elements.host.value,
      port: Number(serverForm.elements.port.value)
    });
    renderServiceView(result.service);
    render(result.status);
  } catch (error) {
    $('serviceViewport').classList.remove('loading');
    toast(error.message, true);
  }
}

for (const btn of document.querySelectorAll('.service-tab')) {
  btn.addEventListener('click', () => browseService(btn.dataset.path));
}

$('toggleAutoBrowse').addEventListener('click', () => {
  if (autoBrowseTimer) {
    clearInterval(autoBrowseTimer);
    autoBrowseTimer = null;
    $('toggleAutoBrowse').textContent = 'Tự động duyệt: TẮT';
    $('toggleAutoBrowse').classList.remove('enable-defense');
  } else {
    $('toggleAutoBrowse').textContent = 'Tự động duyệt: BẬT (1.2s)';
    $('toggleAutoBrowse').classList.add('enable-defense');
    browseService(SERVICE_PATHS[autoBrowseStep % SERVICE_PATHS.length]);
    autoBrowseTimer = setInterval(() => {
      autoBrowseStep += 1;
      browseService(SERVICE_PATHS[autoBrowseStep % SERVICE_PATHS.length]);
    }, 1200);
  }
});

function syncIpModeWithProtocol(protocol, notify = false) {
  const ipModeSelect = $('ipMode');
  const spoofOption = $('udpSpoofOption');
  const currentScenario = $('mode').value;
  if (protocol === 'UDP') {
    spoofOption.disabled = false;
    if (currentScenario === 'DDOS_SIMULATION' || currentScenario === 'DOS_SIMULATION') {
      ipModeSelect.value = 'UDP_SPOOF';
      if (notify) toast('Đã chuyển sang UDP: Tự động bật chế độ UDP IP Spoofing!');
    }
  } else {
    spoofOption.disabled = true;
    if (ipModeSelect.value === 'UDP_SPOOF') {
      ipModeSelect.value = currentScenario === 'DOS_SIMULATION' ? 'SINGLE_IP' : 'MULTI_IP';
      if (notify) toast('TCP cần bắt tay 3 bước: Đã chuyển về chế độ Đa IP nguồn (MULTI_IP).');
    }
  }
}

$('simProtocol').addEventListener('change', (event) => {
  syncIpModeWithProtocol(event.target.value, true);
});

serverForm.elements.protocol.addEventListener('change', (event) => {
  const srvProto = event.target.value;
  if (srvProto === 'UDP') {
    $('simProtocol').value = 'UDP';
    syncIpModeWithProtocol('UDP', true);
  } else if (srvProto === 'TCP') {
    $('simProtocol').value = 'TCP';
    syncIpModeWithProtocol('TCP', true);
  }
});

$('mode').addEventListener('change', (event) => {
  const presets = {
    NORMAL: [2, 1, 10],
    FLASH_CROWD: [35, 4.5, 15],
    DOS_SIMULATION: [20, 5, 15],
    DDOS_SIMULATION: [30, 6, 15]
  };
  const modeVal = event.target.value;
  const [clients, rate, duration] = presets[modeVal] || presets.NORMAL;
  simulationForm.elements.clients.value = clients;
  simulationForm.elements.rate.value = rate;
  simulationForm.elements.duration.value = duration;
  if (modeVal === 'DOS_SIMULATION' && $('simProtocol').value === 'TCP') {
    $('ipMode').value = 'SINGLE_IP';
  } else if ($('simProtocol').value === 'UDP' && (modeVal === 'DDOS_SIMULATION' || modeVal === 'DOS_SIMULATION')) {
    $('ipMode').value = 'UDP_SPOOF';
  } else {
    $('ipMode').value = 'MULTI_IP';
  }
});

setInterval(() => setText('clock', new Date().toLocaleTimeString('vi-VN', {hour12: false})), 1000);
setInterval(refresh, 1000);
refresh();
