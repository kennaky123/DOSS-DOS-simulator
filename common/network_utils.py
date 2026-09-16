"""Validation địa chỉ lab và rate limiter dùng chung."""

from __future__ import annotations

import ipaddress
import threading
import time
from collections import deque

LAB_NETWORKS = tuple(
    ipaddress.ip_network(network)
    for network in ("127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
)
PUBLIC_IP_ERROR = "Project chỉ cho phép localhost hoặc private LAN IP."


def is_lab_address(host: str) -> bool:
    """Chỉ nhận localhost, IPv4 loopback hoặc đúng ba dải RFC1918."""
    value = host.strip().lower()
    if value == "localhost":
        return True
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return isinstance(address, ipaddress.IPv4Address) and any(
        address in network for network in LAB_NETWORKS
    )


def validate_lab_address(host: str) -> str:
    value = host.strip()
    if not is_lab_address(value):
        raise ValueError(PUBLIC_IP_ERROR)
    return value


def validate_port(port: int) -> int:
    if not 1 <= port <= 65_535:
        raise ValueError("Port phải nằm trong khoảng 1..65535.")
    return port


class SlidingWindowRateLimiter:
    """Giới hạn số thao tác trong cửa sổ trượt một giây."""

    def __init__(self, max_per_second: int) -> None:
        self.max_per_second = max_per_second
        self._timestamps: deque[float] = deque()
        self._lock = threading.Lock()

    def allow(self) -> bool:
        now = time.monotonic()
        with self._lock:
            threshold = now - 1.0
            while self._timestamps and self._timestamps[0] < threshold:
                self._timestamps.popleft()
            if len(self._timestamps) >= self.max_per_second:
                return False
            self._timestamps.append(now)
            return True

