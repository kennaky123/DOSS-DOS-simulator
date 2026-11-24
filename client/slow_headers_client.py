"""Bounded slow-header client for the local HTTP resilience lab."""

from __future__ import annotations

import random
import socket
import time

from common.network_utils import validate_port


class SlowHeadersClient:
    """Open one incomplete HTTP request, then finish it after a short hold."""

    MAX_HOLD_SECONDS = 30
    HEADER_INTERVAL_SECONDS = 5

    def __init__(self, host: str, port: int) -> None:
        if host not in ("localhost", "127.0.0.1") and not host.startswith("127."):
            raise ValueError("SLOW_HEADERS chỉ được phép chạy trên loopback.")
        self.host = host
        self.port = validate_port(port)

    def run_once(
        self,
        client_id: str,
        *,
        hold_seconds: int,
        header_interval_seconds: float = HEADER_INTERVAL_SECONDS,
        stop_event,
    ) -> tuple[bool, float]:
        hold_seconds = max(1, min(int(hold_seconds), self.MAX_HOLD_SECONDS))
        header_interval_seconds = max(1.0, min(float(header_interval_seconds), 10.0))
        started_at = time.perf_counter()
        sock: socket.socket | None = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(1.0)
            sock.connect((self.host, self.port))
            initial = (
                f"GET / HTTP/1.1\r\n"
                f"Host: {self.host}:{self.port}\r\n"
                f"User-Agent: SlowHeaderLab/{client_id}\r\n"
            )
            sock.sendall(initial.encode("ascii", errors="ignore"))

            deadline = time.monotonic() + hold_seconds
            while time.monotonic() < deadline:
                wait_for = min(header_interval_seconds, deadline - time.monotonic())
                if stop_event.wait(max(0.0, wait_for)):
                    return False, time.perf_counter() - started_at
                header = (
                    f"X-Lab-Header-{random.randint(1, 9999)}: "
                    f"{random.randint(1, 9999)}\r\n"
                )
                sock.sendall(header.encode("ascii"))

            sock.sendall(b"\r\n")
            try:
                response = sock.recv(1024)
            except socket.timeout:
                response = b""
            return response.startswith(b"HTTP/"), time.perf_counter() - started_at
        except (OSError, ValueError):
            return False, time.perf_counter() - started_at
        finally:
            if sock is not None:
                sock.close()
