"""Dependency-free local web UI and JSON API for the network lab."""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import socket
import sys
import threading
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from client.simulator import TrafficSimulator
from common import config
from common.network_utils import is_lab_address, validate_lab_address, validate_port

WEB_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = WEB_ROOT.parent
STATIC_ROOT = WEB_ROOT / "static"
MAX_BODY_BYTES = 8_192


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(threadName)s | %(message)s",
        handlers=[
            logging.FileHandler(PROJECT_ROOT / config.LOG_FILE, encoding="utf-8")
        ],
    )


class WebController:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._defense_enabled: bool = True
        self._simulator: TrafficSimulator | None = None
        self._simulation_thread: threading.Thread | None = None
        self._simulation_config: dict[str, Any] | None = None
        self._simulation_error: str | None = None

    def set_defense(self, payload: dict[str, Any]) -> None:
        enabled = bool(payload.get("enabled", not self._defense_enabled))
        with self._lock:
            self._defense_enabled = enabled
            # Đồng bộ trạng thái phòng thủ sang Web Bán Hàng (Port 5000)
            try:
                import sys
                local_shop = Path(__file__).resolve().parent.parent / "real_ecommerce_shop"
                shop_dir = str(local_shop if local_shop.exists() else Path(__file__).resolve().parents[2] / "real_ecommerce_shop")
                if shop_dir not in sys.path:
                    sys.path.insert(0, shop_dir)
                import shop_server
                with shop_server.state_lock:
                    shop_server.defense_enabled = enabled
                    if enabled:
                        shop_server._last_cpu_time = shop_server._worker_cpu_seconds_total
                        shop_server._last_wall_time = shop_server.time.monotonic()
                        shop_server._measured_cpu_pct = 0.5
                    shop_server.login_failure_tracker.clear()
                    shop_server.search_query_cache.clear()
            except Exception:
                pass

    def start_simulation(self, payload: dict[str, Any]) -> None:
        host = validate_lab_address(str(payload.get("host", config.DEFAULT_HOST)))
        port = validate_port(self._as_int(payload.get("port", config.DEFAULT_PORT), "Port"))
        protocol = str(payload.get("protocol", config.DEFAULT_PROTOCOL)).upper()
        mode = str(payload.get("mode", "NORMAL")).upper()
        raw_ip_mode = payload.get("ip_mode")
        ip_mode = str(raw_ip_mode).upper() if raw_ip_mode else None
        if protocol not in config.SUPPORTED_PROTOCOLS:
            raise ValueError("Protocol mô phỏng không hợp lệ.")
        if mode not in config.SIMULATION_MODES:
            raise ValueError("Chế độ mô phỏng không hợp lệ.")
        if ip_mode is not None and ip_mode not in config.IP_MODES:
            raise ValueError("Chế độ IP nguồn không hợp lệ.")

        defaults = config.MODE_DEFAULTS[mode]
        clients = self._as_int(payload.get("clients", defaults["clients"]), "Clients")
        rate = self._as_float(payload.get("rate", defaults["rate"]), "Rate")
        duration = self._as_int(payload.get("duration", defaults["duration"]), "Duration")
        slow_header_interval = self._as_float(
            payload.get("slow_header_interval", 5), "Slow-header interval"
        )
        search_keyword = str(payload.get("search_keyword", "laptop")).strip()[:32] or "laptop"
        login_username_prefix = str(payload.get("login_username_prefix", "lab_user")).strip()[:24] or "lab_user"
        limits = config.MODE_LIMITS[mode]
        max_clients = (
            config.MAX_SLOW_HEADER_CLIENTS
            if protocol == "SLOW_HEADERS"
            else config.MAX_HTTP_LAB_CLIENTS
            if protocol in ("HTTP_GET_SEARCH", "HTTP_POST_LOGIN")
            else min(config.MAX_CLIENTS, limits["clients"])
        )
        max_rate = (
            config.MAX_HTTP_LAB_RATE
            if protocol in ("HTTP_GET_SEARCH", "HTTP_POST_LOGIN")
            else min(config.MAX_REQUEST_RATE, limits["rate"])
        )
        if not 1 <= clients <= max_clients:
            raise ValueError(f"Mode {mode} chỉ cho phép 1..{max_clients} clients.")
        if not 0 < rate <= max_rate:
            raise ValueError(f"Mode {mode} chỉ cho phép rate tối đa {max_rate:g}.")
        if not 1 <= duration <= config.MAX_DURATION:
            raise ValueError(f"Duration phải từ 1 đến {config.MAX_DURATION} giây.")
        if protocol == "SLOW_HEADERS":
            if host not in ("localhost", "127.0.0.1") and not host.startswith("127."):
                raise ValueError("SLOW_HEADERS chỉ được phép chạy trên loopback.")
            if clients > config.MAX_SLOW_HEADER_CLIENTS:
                raise ValueError(
                    f"SLOW_HEADERS chỉ cho phép tối đa {config.MAX_SLOW_HEADER_CLIENTS} kết nối."
                )
            if duration > config.MAX_SLOW_HEADER_DURATION:
                raise ValueError(
                    f"SLOW_HEADERS chỉ cho phép tối đa {config.MAX_SLOW_HEADER_DURATION} giây."
                )
            if not config.MIN_SLOW_HEADER_INTERVAL <= slow_header_interval <= config.MAX_SLOW_HEADER_INTERVAL:
                raise ValueError(
                    f"Khoảng cách header phải từ {config.MIN_SLOW_HEADER_INTERVAL} đến {config.MAX_SLOW_HEADER_INTERVAL} giây."
                )
            ip_mode = "SINGLE_IP"
        if protocol in ("HTTP_GET_SEARCH", "HTTP_POST_LOGIN"):
            if host not in ("localhost", "127.0.0.1") and not host.startswith("127."):
                raise ValueError("HTTP lab scenarios chỉ được phép chạy trên loopback.")
            if duration > config.MAX_HTTP_LAB_DURATION:
                raise ValueError(f"HTTP lab chỉ cho phép tối đa {config.MAX_HTTP_LAB_DURATION} giây.")
            ip_mode = "SINGLE_IP"

        with self._lock:
            if self._simulation_thread and self._simulation_thread.is_alive():
                raise ValueError("Một simulation khác đang chạy.")
            simulator = TrafficSimulator(
                host,
                port,
                protocol,
                mode,
                clients,
                rate,
                duration,
                ip_mode=ip_mode,
                slow_header_interval_seconds=slow_header_interval,
                search_keyword=search_keyword,
                login_username_prefix=login_username_prefix,
            )
            self._simulator = simulator
            self._simulation_config = {
                "host": host,
                "port": port,
                "protocol": protocol,
                "mode": mode,
                "ip_mode": simulator.ip_mode,
                "clients": clients,
                "rate": rate,
                "duration": duration,
                "slow_header_interval": slow_header_interval,
                "search_keyword": search_keyword,
                "login_username_prefix": login_username_prefix,
            }
            self._simulation_error = None
            self._simulation_thread = threading.Thread(
                target=self._run_simulation,
                args=(simulator,),
                name="web-simulation",
                daemon=True,
            )
            self._simulation_thread.start()

    def _run_simulation(self, simulator: TrafficSimulator) -> None:
        try:
            simulator.run(show_dashboard=False)
        except Exception as exc:  # Keep API alive and surface worker failures in the UI.
            logging.exception("Web simulation failed")
            with self._lock:
                self._simulation_error = str(exc)

    def stop_simulation(self) -> None:
        with self._lock:
            simulator = self._simulator
            thread = self._simulation_thread
        if simulator:
            simulator.stop_event.set()
        if thread:
            thread.join(config.SOCKET_TIMEOUT_SECONDS + 1.0)

    def status(self) -> dict[str, Any]:
        with self._lock:
            simulator = self._simulator
            simulation_thread = self._simulation_thread
            simulation_config = self._simulation_config
            simulation_error = self._simulation_error

        defense_enabled = self._defense_enabled
        shop_metrics: dict[str, Any] | None = None
        shop_mod = sys.modules.get("shop_server")
        shop_running = bool(shop_mod is not None and getattr(shop_mod, "server_running", False))
        if shop_mod is not None:
            with shop_mod.state_lock:
                defense_enabled = bool(shop_mod.defense_enabled)
                total_req = int(shop_mod.total_requests)
                blocked_req = int(shop_mod.blocked_requests)
                active_wk = int(shop_mod.active_workers)
                last_reason = str(getattr(shop_mod, "last_block_reason", "-"))
                shop_metrics = {
                    "total_requests": total_req,
                    "blocked_requests": blocked_req,
                    "successful_requests": max(0, total_req - blocked_req),
                    "failed_requests": blocked_req,
                    "last_block_reason": last_reason,
                    "defense_status": (
                        "OVERLOADED"
                        if (not defense_enabled and active_wk >= shop_mod.WORKER_THREADS)
                        else ("NORMAL" if defense_enabled else "DISABLED")
                    ),
                }

        server_data: dict[str, Any] = {
            "running": shop_running,
            "host": "127.0.0.1" if shop_running else None,
            "port": shop_mod.PORT if shop_running else None,
            "protocol": "HTTP" if shop_running else None,
            "defenseEnabled": defense_enabled,
            "error": None,
            "metrics": shop_metrics if shop_running else None,
        }

        simulation_data: dict[str, Any] = {
            "running": bool(simulation_thread and simulation_thread.is_alive()),
            "config": simulation_config,
            "error": simulation_error,
            "metrics": asdict(simulator.metrics.snapshot()) if simulator else None,
        }
        return {
            "server": server_data,
            "simulation": simulation_data,
            "limits": {
                "maxDuration": config.MAX_DURATION,
                "modes": config.MODE_LIMITS,
                "sourceRps": config.MAX_SOURCE_REQUESTS_PER_SECOND,
                "clientRps": config.MAX_LOGICAL_CLIENT_REQUESTS_PER_SECOND,
                "serverRps": config.MAX_SERVER_REQUESTS_PER_SECOND,
            },
        }

    def shutdown(self) -> None:
        self.stop_simulation()

    @staticmethod
    def _as_int(value: Any, label: str) -> int:
        if isinstance(value, bool):
            raise ValueError(f"{label} phải là số nguyên.")
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} phải là số nguyên.") from exc

    @staticmethod
    def _as_float(value: Any, label: str) -> float:
        if isinstance(value, bool):
            raise ValueError(f"{label} phải là một số.")
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label} phải là một số.") from exc


class DashboardHandler(BaseHTTPRequestHandler):
    controller: WebController

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        if not self._client_allowed():
            self._send_json({"error": "Chỉ cho phép truy cập từ private LAN."}, HTTPStatus.FORBIDDEN)
        elif path == "/api/status":
            self._send_json(self.controller.status())
        elif path == "/":
            self._send_file("index.html")
        elif path in ("/assets/app.css", "/assets/app.js"):
            self._send_file(path.removeprefix("/assets/"))
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if not self._client_allowed():
            self._send_json({"error": "Chỉ cho phép truy cập từ private LAN."}, HTTPStatus.FORBIDDEN)
            return
        path = urlsplit(self.path).path
        try:
            payload = self._read_json()
            actions = {
                "/api/defense/toggle": lambda: self.controller.set_defense(payload),
                "/api/simulation/start": lambda: self.controller.start_simulation(payload),
                "/api/simulation/stop": self.controller.stop_simulation,
            }
            action = actions.get(path)
            if action is None:
                self._send_json({"error": "Endpoint không tồn tại."}, HTTPStatus.NOT_FOUND)
                return
            action()
            self._send_json({"ok": True, "status": self.controller.status()})
        except (ValueError, json.JSONDecodeError) as exc:
            self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def _read_json(self) -> dict[str, Any]:
        content_type = self.headers.get_content_type()
        if content_type != "application/json":
            raise ValueError("Request phải dùng application/json.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Content-Length không hợp lệ.") from exc
        if not 0 <= length <= MAX_BODY_BYTES:
            raise ValueError("Request body quá lớn.")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("JSON body phải là object.")
        return data

    def _send_file(self, filename: str) -> None:
        path = STATIC_ROOT / filename
        if not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = path.read_bytes()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; "
            "frame-src 'self' http://127.0.0.1:5000; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        )
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def _client_allowed(self) -> bool:
        return is_lab_address(self.client_address[0])

    def log_message(self, message: str, *args: object) -> None:
        logging.info("web client=%s " + message, self.client_address[0], *args)


def get_local_lan_ip() -> str:
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Network Lab Web Dashboard")
    parser.add_argument("--host", default="0.0.0.0", help="Local/private web bind IP")
    parser.add_argument("--port", type=int, default=8080, help="Web dashboard port")
    args = parser.parse_args()
    try:
        args.host = validate_lab_address(args.host)
        args.port = validate_port(args.port)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main() -> None:
    args = parse_args()
    configure_logging()
    lan_ip = get_local_lan_ip()

    # Tự động khởi chạy Web Bán Hàng E-Commerce trên Port 5000 để làm mục tiêu DoS/DDoS
    try:
        import sys
        local_shop = Path(__file__).resolve().parent.parent / "real_ecommerce_shop"
        shop_dir = str(local_shop if local_shop.exists() else Path(__file__).resolve().parents[2] / "real_ecommerce_shop")
        if shop_dir not in sys.path:
            sys.path.insert(0, shop_dir)
        import shop_server
        shop_thread = threading.Thread(target=shop_server.run_web_server, name="TechStoreShop", daemon=True)
        shop_thread.start()
        print("=================================================================")
        print(f"🛒 WEB BÁN HÀNG (Mở trên máy bạn):        http://127.0.0.1:5000")
        print(f"🌐 LINK GỬI CHO MÁY BẠN BÈ (Cùng Wi-Fi):  http://{lan_ip}:5000")
    except Exception as exc:
        logging.warning("Không thể khởi động Web Bán Hàng: %s", exc)

    controller = WebController()
    handler = type("BoundDashboardHandler", (DashboardHandler,), {"controller": controller})
    web_server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"🎛️ WEB ĐIỀU KHIỂN DOS/DDOS (Máy của bạn): http://127.0.0.1:{args.port}")
    print("=================================================================")
    print("Nhấn Ctrl+C để dừng web và simulation.")
    try:
        web_server.serve_forever()
    except KeyboardInterrupt:
        print("\nĐang shutdown...")
    finally:
        web_server.server_close()
        controller.shutdown()


if __name__ == "__main__":
    main()
