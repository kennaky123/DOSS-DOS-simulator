"""Bounded HTTP workload client for the local e-commerce lab."""

from __future__ import annotations

import json
import socket
import time

from common.network_utils import validate_lab_address, validate_port


class HttpLabClient:
    """Send one finite HTTP request to a loopback lab target."""

    def __init__(self, host: str, port: int) -> None:
        self.host = validate_lab_address(host)
        if self.host != "localhost" and not self.host.startswith("127."):
            raise ValueError("HTTP lab scenarios chỉ được phép chạy trên loopback.")
        self.port = validate_port(port)

    def send(self, scenario: str, client_id: str) -> tuple[bool, float]:
        started_at = time.perf_counter()
        if scenario == "HTTP_GET_SEARCH":
            request = (
                "GET /api/search?q=laptop HTTP/1.1\r\n"
                f"Host: {self.host}:{self.port}\r\n"
                f"User-Agent: NetGuard-Lab/{client_id}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii")
        elif scenario == "HTTP_POST_LOGIN":
            body = json.dumps(
                {"username": f"lab_user_{client_id}", "password": "invalid-lab-password"}
            ).encode("utf-8")
            request = (
                "POST /api/login HTTP/1.1\r\n"
                f"Host: {self.host}:{self.port}\r\n"
                f"User-Agent: NetGuard-Lab/{client_id}\r\n"
                "Content-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n"
                "Connection: close\r\n\r\n"
            ).encode("ascii") + body
        else:
            raise ValueError(f"HTTP lab scenario không hợp lệ: {scenario}")

        try:
            with socket.create_connection((self.host, self.port), timeout=3.0) as sock:
                sock.sendall(request)
                response = sock.recv(1024)
            status_line = response.split(b"\r\n", 1)[0]
            success = status_line.startswith(b"HTTP/") and (b" 200 " in status_line or b" 201 " in status_line)
        except OSError:
            success = False
        return success, time.perf_counter() - started_at
