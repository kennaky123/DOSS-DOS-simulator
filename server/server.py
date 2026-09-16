"""CLI điều phối TCP Server, UDP Server và dashboard dùng chung."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
project_root_text = str(PROJECT_ROOT)
if project_root_text in sys.path:
    sys.path.remove(project_root_text)
sys.path.insert(0, project_root_text)

from common import config
from common.network_utils import (
    SlidingWindowRateLimiter,
    validate_lab_address,
    validate_port,
)
from server.monitor import Dashboard
from server.statistics import ServerStatistics
from server.tcp_server import TcpServer
from server.udp_server import UdpServer


class TestServer:
    def __init__(self, host: str, port: int, protocol: str) -> None:
        self.host = validate_lab_address(host)
        self.port = validate_port(port)
        self.protocol = protocol
        self.statistics = ServerStatistics()
        self.stop_event = threading.Event()
        self._services: list[TcpServer | UdpServer] = []
        self._threads: list[threading.Thread] = []
        self._service_errors: list[OSError] = []
        self._errors_lock = threading.Lock()

        # TCP và UDP dùng chung rate limit để tổng tải server luôn có trần.
        limiter = SlidingWindowRateLimiter(config.MAX_SERVER_REQUESTS_PER_SECOND)
        if protocol in ("TCP", "BOTH"):
            self._services.append(
                TcpServer(host, port, self.statistics, limiter, self.stop_event)
            )
        if protocol in ("UDP", "BOTH"):
            self._services.append(
                UdpServer(host, port, self.statistics, limiter, self.stop_event)
            )

    def serve_forever(self) -> None:
        dashboard = Dashboard(
            self.statistics,
            self.host,
            self.port,
            self.protocol,
            config.DASHBOARD_INTERVAL_SECONDS,
        )
        self._threads = [
            threading.Thread(
                target=self._run_service,
                args=(service,),
                name=type(service).__name__,
                daemon=True,
            )
            for service in self._services
        ]
        for thread in self._threads:
            thread.start()

        try:
            dashboard.run(self.stop_event)
        finally:
            self.shutdown()
            for thread in self._threads:
                thread.join(config.SOCKET_TIMEOUT_SECONDS + 2.0)
        if self._service_errors:
            raise OSError(f"Không thể khởi động server: {self._service_errors[0]}")

    def _run_service(self, service: TcpServer | UdpServer) -> None:
        try:
            service.serve_forever()
        except OSError as exc:
            logging.error("%s failed: %s", type(service).__name__, exc)
            with self._errors_lock:
                self._service_errors.append(exc)
            self.stop_event.set()

    def shutdown(self) -> None:
        self.stop_event.set()
        for service in self._services:
            service.shutdown()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TCP/UDP Test Server cho private lab")
    parser.add_argument("--host", default=config.DEFAULT_HOST, help="IPv4 bind private")
    parser.add_argument("--port", type=int, default=config.DEFAULT_PORT)
    parser.add_argument(
        "--protocol",
        choices=config.SERVER_PROTOCOLS,
        default="BOTH",
        help="Mặc định chạy đồng thời TCP và UDP trên cùng số port",
    )
    args = parser.parse_args()
    try:
        args.host = validate_lab_address(args.host)
        args.port = validate_port(args.port)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(threadName)s | %(message)s",
        handlers=[
            logging.FileHandler(PROJECT_ROOT / config.LOG_FILE, encoding="utf-8")
        ],
    )


def configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    configure_console_encoding()
    args = parse_args()
    configure_logging()
    server = TestServer(args.host, args.port, args.protocol)
    print(f"Starting server on {args.host}:{args.port} ({args.protocol})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nĐang shutdown server...")
        server.shutdown()
    except OSError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
