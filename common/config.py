"""Cấu hình chung và các hard limit an toàn của project."""

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9000
DEFAULT_PROTOCOL = "TCP"

SUPPORTED_PROTOCOLS = ("TCP", "UDP")
SERVER_PROTOCOLS = ("TCP", "UDP", "BOTH")
SIMULATION_MODES = ("NORMAL", "DOS_SIMULATION", "DDOS_SIMULATION")

# Hard limit không thể tăng bằng tham số dòng lệnh.
MAX_CLIENTS = 50
MAX_REQUEST_RATE = 20.0  # request / client / giây
MAX_DURATION = 60
MAX_SERVER_REQUESTS_PER_SECOND = 1_000
MAX_SOURCE_REQUESTS_PER_SECOND = 250
MAX_LOGICAL_CLIENT_REQUESTS_PER_SECOND = 40
ALERT_SOURCE_REQUESTS_PER_SECOND = 100
ALERT_LOGICAL_CLIENT_REQUESTS_PER_SECOND = 15
DEFENSE_ALERT_WINDOW_SECONDS = 5.0
MAX_TRACKED_TRAFFIC_IDENTITIES = 1_024
MAX_CONCURRENT_CONNECTIONS = 100
MAX_MESSAGE_BYTES = 4_096

SOCKET_TIMEOUT_SECONDS = 3.0
LISTEN_BACKLOG = 128
DASHBOARD_INTERVAL_SECONDS = 1.0
ACTIVE_CLIENT_WINDOW_SECONDS = 2.0
LOG_FILE = "server.log"

MODE_DEFAULTS = {
    "NORMAL": {"clients": 2, "rate": 1.0, "duration": 10},
    "DOS_SIMULATION": {"clients": 20, "rate": 5.0, "duration": 15},
    "DDOS_SIMULATION": {"clients": 30, "rate": 5.0, "duration": 15},
}

# Giới hạn riêng giúp ba profile có mức tải khác nhau và vẫn an toàn.
MODE_LIMITS = {
    "NORMAL": {"clients": 5, "rate": 2.0},
    "DOS_SIMULATION": {"clients": 30, "rate": 10.0},
    "DDOS_SIMULATION": {"clients": 50, "rate": 10.0},
}
