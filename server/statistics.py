"""Bộ đếm thread-safe dùng chung cho TCP và UDP server."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

from common.config import ACTIVE_CLIENT_WINDOW_SECONDS


@dataclass(frozen=True)
class StatisticsSnapshot:
    current_clients: int
    current_connections: int
    total_connections: int
    total_requests: int
    requests_per_second: int
    successful_requests: int
    failed_requests: int
    average_response_ms: float
    bytes_received: int
    bytes_sent: int
    uptime_seconds: float


class ServerStatistics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_at = time.monotonic()
        self._current_connections = 0
        self._total_connections = 0
        self._total_requests = 0
        self._successful_requests = 0
        self._failed_requests = 0
        self._total_response_seconds = 0.0
        self._completed_requests = 0
        self._bytes_received = 0
        self._bytes_sent = 0
        self._recent_requests: deque[float] = deque()
        self._active_clients: dict[tuple[str, str, str], float] = {}

    def connection_opened(self) -> None:
        with self._lock:
            self._current_connections += 1
            self._total_connections += 1

    def connection_closed(self) -> None:
        with self._lock:
            self._current_connections = max(0, self._current_connections - 1)

    def request_received(
        self,
        protocol: str,
        client_id: str,
        address: tuple[str, int],
        bytes_received: int,
    ) -> None:
        now = time.monotonic()
        with self._lock:
            self._total_requests += 1
            self._bytes_received += bytes_received
            self._recent_requests.append(now)
            # Port tạm có thể đổi mỗi request, client logic được nhận diện bằng client_id.
            self._active_clients[(protocol, client_id, address[0])] = now
            self._discard_old(now)

    def request_completed(
        self, success: bool, response_seconds: float, bytes_sent: int
    ) -> None:
        with self._lock:
            if success:
                self._successful_requests += 1
            else:
                self._failed_requests += 1
            self._total_response_seconds += response_seconds
            self._completed_requests += 1
            self._bytes_sent += bytes_sent

    def _discard_old(self, now: float) -> None:
        one_second_ago = now - 1.0
        while self._recent_requests and self._recent_requests[0] < one_second_ago:
            self._recent_requests.popleft()
        inactive_before = now - ACTIVE_CLIENT_WINDOW_SECONDS
        inactive = [
            key for key, last_seen in self._active_clients.items() if last_seen < inactive_before
        ]
        for key in inactive:
            del self._active_clients[key]

    def snapshot(self) -> StatisticsSnapshot:
        now = time.monotonic()
        with self._lock:
            self._discard_old(now)
            average_ms = (
                self._total_response_seconds / self._completed_requests * 1_000
                if self._completed_requests
                else 0.0
            )
            return StatisticsSnapshot(
                current_clients=len(self._active_clients),
                current_connections=self._current_connections,
                total_connections=self._total_connections,
                total_requests=self._total_requests,
                requests_per_second=len(self._recent_requests),
                successful_requests=self._successful_requests,
                failed_requests=self._failed_requests,
                average_response_ms=average_ms,
                bytes_received=self._bytes_received,
                bytes_sent=self._bytes_sent,
                uptime_seconds=now - self._started_at,
            )
