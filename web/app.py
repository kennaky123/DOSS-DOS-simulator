"""Dependency-free local web UI and JSON API for the network lab."""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import socket
import threading
import time
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from client.simulator import TrafficSimulator
from common import config
from common.network_utils import is_lab_address, validate_lab_address, validate_port
from common.protocol import (
    ProtocolError,
    create_request,
    decode_json,
    encode_json,
    extract_request_path,
    receive_tcp_message,
)
from server.server import TestServer, configure_logging

WEB_ROOT = Path(__file__).resolve().parent
STATIC_ROOT = WEB_ROOT / "static"
MAX_BODY_BYTES = 8_192


class WebController:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._server: TestServer | None = None
        self._defense_enabled: bool = True
        self._simulator: TrafficSimulator | None = None
        self._simulation_thread: threading.Thread | None = None
        self._simulation_config: dict[str, Any] | None = None
        self._simulation_error: str | None = None

    def set_defense(self, payload: dict[str, Any]) -> None:
        enabled = bool(payload.get("enabled", not self._defense_enabled))
        with self._lock:
            self._defense_enabled = enabled
            if self._server:
                self._server.set_defense_enabled(enabled)

    def start_server(self, payload: dict[str, Any]) -> None:
        host = validate_lab_address(str(payload.get("host", config.DEFAULT_HOST)))
        port = validate_port(self._as_int(payload.get("port", config.DEFAULT_PORT), "Port"))
        protocol = str(payload.get("protocol", "BOTH")).upper()
        if protocol not in config.SERVER_PROTOCOLS:
            raise ValueError("Protocol server không hợp lệ.")

        with self._lock:
            if self._server and self._server.running:
                raise ValueError("Server đang chạy. Hãy dừng server trước khi đổi cấu hình.")
            if "defenseEnabled" in payload:
                self._defense_enabled = bool(payload["defenseEnabled"])
            server = TestServer(
                host, port, protocol, defense_enabled=self._defense_enabled
            )
            server.start()
            self._server = server

    def stop_server(self) -> None:
        with self._lock:
            server = self._server
        if server:
            server.shutdown()
            server.join()

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
        limits = config.MODE_LIMITS[mode]
        max_clients = min(config.MAX_CLIENTS, limits["clients"])
        max_rate = min(config.MAX_REQUEST_RATE, limits["rate"])
        if not 1 <= clients <= max_clients:
            raise ValueError(f"Mode {mode} chỉ cho phép 1..{max_clients} clients.")
        if not 0 < rate <= max_rate:
            raise ValueError(f"Mode {mode} chỉ cho phép rate tối đa {max_rate:g}.")
        if not 1 <= duration <= config.MAX_DURATION:
            raise ValueError(f"Duration phải từ 1 đến {config.MAX_DURATION} giây.")

        with self._lock:
            if self._simulation_thread and self._simulation_thread.is_alive():
                raise ValueError("Một simulation khác đang chạy.")
            simulator = TrafficSimulator(
                host, port, protocol, mode, clients, rate, duration, ip_mode=ip_mode
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

    def call_service(self, payload: dict[str, Any]) -> dict[str, Any]:
        path = extract_request_path(payload)
        client_id = str(payload.get("client_id", "web_visitor_01")).strip() or "web_visitor_01"
        with self._lock:
            server = self._server
        host = validate_lab_address(
            str(payload.get("host") or (server.host if server else config.DEFAULT_HOST))
        )
        port = validate_port(
            self._as_int(
                payload.get("port") or (server.port if server else config.DEFAULT_PORT),
                "Port",
            )
        )
        server_proto = server.protocol if server and server.running else "TCP"
        transport = "UDP" if server_proto == "UDP" else "TCP"

        started_at = time.perf_counter()
        try:
            if transport == "TCP":
                frame = encode_json(create_request(client_id, path=path), tcp_framed=True)
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                    sock.settimeout(config.SOCKET_TIMEOUT_SECONDS)
                    sock.connect((host, port))
                    sock.sendall(frame)
                    response, _ = receive_tcp_message(sock)
            else:
                datagram = encode_json(create_request(client_id, path=path), tcp_framed=False)
                target_addr = (socket.gethostbyname(host), port)
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.settimeout(config.SOCKET_TIMEOUT_SECONDS)
                    sock.sendto(datagram, target_addr)
                    raw, _ = sock.recvfrom(config.MAX_MESSAGE_BYTES)
                response = decode_json(raw)
        except (OSError, ProtocolError):
            latency_ms = round((time.perf_counter() - started_at) * 1_000, 2)
            return {
                "ok": False,
                "httpStatus": 503,
                "latencyMs": latency_ms,
                "protocol": transport,
                "target": f"{transport.lower()}://{host}:{port}{path}",
                "response": {"status": "ERROR", "error": "SERVER_UNREACHABLE"},
            }

        latency_ms = round((time.perf_counter() - started_at) * 1_000, 2)
        ok = response.get("status") == "OK"
        err = response.get("error", "")
        http_status = 200 if ok else (503 if err in ("SERVER_OVERLOADED", "SERVER_BUSY") else 429)
        return {
            "ok": ok,
            "httpStatus": http_status,
            "latencyMs": latency_ms,
            "protocol": transport,
            "target": f"{transport.lower()}://{host}:{port}{path}",
            "response": response,
        }

    def status(self) -> dict[str, Any]:
        with self._lock:
            server = self._server
            simulator = self._simulator
            simulation_thread = self._simulation_thread
            simulation_config = self._simulation_config
            simulation_error = self._simulation_error

        server_data: dict[str, Any] = {
            "running": False,
            "host": None,
            "port": None,
            "protocol": None,
            "defenseEnabled": self._defense_enabled,
            "error": None,
            "metrics": None,
        }
        if server:
            server_data.update(
                running=server.running,
                host=server.host,
                port=server.port,
                protocol=server.protocol,
                defenseEnabled=server.traffic_guard.enabled,
                error=server.error_message,
                metrics=asdict(server.statistics.snapshot()),
            )

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
        self.stop_server()

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
            if path == "/api/service/request":
                service_result = self.controller.call_service(payload)
                self._send_json(
                    {"ok": True, "service": service_result, "status": self.controller.status()}
                )
                return
            actions = {
                "/api/server/start": lambda: self.controller.start_server(payload),
                "/api/server/stop": self.controller.stop_server,
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
            "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Network Lab Web Dashboard")
    parser.add_argument("--host", default="127.0.0.1", help="Local/private web bind IP")
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
    controller = WebController()
    handler = type("BoundDashboardHandler", (DashboardHandler,), {"controller": controller})
    web_server = ThreadingHTTPServer((args.host, args.port), handler)
    print(f"Web dashboard: http://{args.host}:{args.port}")
    print("Nhấn Ctrl+C để dừng web, simulation và test server.")
    try:
        web_server.serve_forever()
    except KeyboardInterrupt:
        print("\nĐang shutdown...")
    finally:
        web_server.server_close()
        controller.shutdown()


if __name__ == "__main__":
    main()
