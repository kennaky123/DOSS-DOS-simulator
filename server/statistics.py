"""Bộ đếm thread-safe dùng chung cho TCP và UDP server."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

from common.config import ACTIVE_CLIENT_WINDOW_SECONDS, DEFENSE_ALERT_WINDOW_SECONDS


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
    suspicious_requests: int
    blocked_requests: int
    defense_status: str
    last_block_reason: str
    traffic_classification: str = "NORMAL"
    trust_score: int = 100
    defense_enabled: bool = True
    overloaded_requests: int = 0
    active_ips: int = 0


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
        self._active_ips: dict[str, float] = {}
        self._suspicious_requests = 0
        self._blocked_requests = 0
        self._overloaded_requests = 0
        self._defense_enabled = True
        self._last_suspicious_at: float | None = None
        self._last_blocked_at: float | None = None
        self._last_overloaded_at: float | None = None
        self._last_flash_crowd_at: float | None = None
        self._last_block_reason = "-"
        self._last_traffic_pattern = "NORMAL"
        self._last_pattern_at: float | None = None
        self._trust_score = 100

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
            self._active_ips[address[0]] = now
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

    def defense_observation(
        self,
        *,
        suspicious: bool,
        blocked_reason: str | None,
        traffic_pattern: str = "NORMAL",
        trust_score: int = 100,
    ) -> None:
        now = time.monotonic()
        with self._lock:
            self._trust_score = trust_score
            if traffic_pattern != "NORMAL":
                self._last_traffic_pattern = traffic_pattern
                self._last_pattern_at = now
            if traffic_pattern == "FLASH_CROWD" and not blocked_reason:
                self._last_flash_crowd_at = now
            if suspicious:
                self._suspicious_requests += 1
                self._last_suspicious_at = now
            if blocked_reason:
                self._blocked_requests += 1
                self._last_blocked_at = now
                self._last_block_reason = blocked_reason

    def set_defense_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._defense_enabled = bool(enabled)
            if enabled:
                self._last_overloaded_at = None
                if self._last_block_reason == "SERVER_OVERLOADED":
                    self._last_block_reason = "-"
            else:
                self._last_blocked_at = None

    def server_overloaded(self) -> None:
        now = time.monotonic()
        with self._lock:
            self._overloaded_requests += 1
            self._last_overloaded_at = now
            self._last_block_reason = "SERVER_OVERLOADED"

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
        inactive_ips = [
            ip for ip, last_seen in self._active_ips.items() if last_seen < inactive_before
        ]
        for ip in inactive_ips:
            del self._active_ips[ip]

    def snapshot(self) -> StatisticsSnapshot:
        now = time.monotonic()
        with self._lock:
            self._discard_old(now)
            average_ms = (
                self._total_response_seconds / self._completed_requests * 1_000
                if self._completed_requests
                else 0.0
            )
            if not self._defense_enabled:
                if (
                    self._last_overloaded_at is not None
                    and now - self._last_overloaded_at <= DEFENSE_ALERT_WINDOW_SECONDS
                ):
                    defense_status = "OVERLOADED"
                else:
                    defense_status = "DISABLED"
            elif (
                self._last_blocked_at is not None
                and now - self._last_blocked_at <= DEFENSE_ALERT_WINDOW_SECONDS
            ):
                defense_status = "BLOCKING"
            elif (
                self._last_suspicious_at is not None
                and now - self._last_suspicious_at <= DEFENSE_ALERT_WINDOW_SECONDS
            ):
                defense_status = "ELEVATED"
            elif (
                self._last_flash_crowd_at is not None
                and now - self._last_flash_crowd_at <= DEFENSE_ALERT_WINDOW_SECONDS
            ):
                defense_status = "FLASH_CROWD"
            else:
                defense_status = "NORMAL"

            if (
                self._last_pattern_at is not None
                and now - self._last_pattern_at <= DEFENSE_ALERT_WINDOW_SECONDS
            ):
                traffic_classification = self._last_traffic_pattern
                trust_score = self._trust_score
            else:
                traffic_classification = "NORMAL"
                trust_score = 100

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
                suspicious_requests=self._suspicious_requests,
                blocked_requests=self._blocked_requests,
                defense_status=defense_status,
                last_block_reason=self._last_block_reason,
                traffic_classification=traffic_classification,
                trust_score=trust_score,
                defense_enabled=self._defense_enabled,
                overloaded_requests=self._overloaded_requests,
                active_ips=len(self._active_ips),
            )
