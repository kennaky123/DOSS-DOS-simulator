const $ = (id) => document.getElementById(id);
const serverForm = $('serverForm');
const simulationForm = $('simulationForm');
let toastTimer;

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

function render(data) {
  const server = data.server;
  const metrics = server.metrics || {};
  const simulation = data.simulation;
  const sim = simulation.metrics || {};
  const online = server.running;

  $('globalPulse').classList.toggle('online', online || simulation.running);
  setText('globalStatus', online ? 'SERVER ONLINE' : simulation.running ? 'SIMULATION ACTIVE' : 'SYSTEM IDLE');
  $('serverState').className = `state-pill${online ? ' online' : ''}`;
  setText('serverState', online ? 'ONLINE' : server.error ? 'ERROR' : 'OFFLINE');
  $('simulationState').className = `state-pill${simulation.running ? ' running' : ''}`;
  setText('simulationState', simulation.running ? 'RUNNING' : 'READY');

  setText('rps', metrics.requests_per_second || 0);
  setText('activeClients', metrics.current_clients || 0);
  setText('blocked', metrics.blocked_requests || 0);
  setText('blockReason', metrics.last_block_reason && metrics.last_block_reason !== '-' ? metrics.last_block_reason : 'no active rule');
  setText('latency', Number(metrics.average_response_ms || 0).toFixed(2));
  setText('connections', metrics.current_connections || 0);
  setText('totalRequests', metrics.total_requests || 0);
  setText('successful', metrics.successful_requests || 0);
  setText('failed', metrics.failed_requests || 0);
  setText('suspicious', metrics.suspicious_requests || 0);
  setText('bytesReceived', bytes(metrics.bytes_received || 0));

  const total = Number(metrics.total_requests || 0);
  const successRate = total ? Math.round((Number(metrics.successful_requests || 0) / total) * 100) : 100;
  setText('successRate', successRate);
  $('gauge').style.setProperty('--value', successRate);
  const defense = metrics.defense_status || 'NORMAL';
  setText('defenseState', defense);
  $('defenseState').className = `shield-state ${defense.toLowerCase()}`;

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

$('mode').addEventListener('change', (event) => {
  const presets = {
    NORMAL: [2, 1, 10],
    DOS_SIMULATION: [20, 5, 15],
    DDOS_SIMULATION: [30, 5, 15]
  };
  const [clients, rate, duration] = presets[event.target.value];
  simulationForm.elements.clients.value = clients;
  simulationForm.elements.rate.value = rate;
  simulationForm.elements.duration.value = duration;
});

setInterval(() => setText('clock', new Date().toLocaleTimeString('vi-VN', {hour12: false})), 1000);
setInterval(refresh, 1000);
refresh();
