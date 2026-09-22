"""Multi-layer traffic defense for the private lab server."""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

from common import config


@dataclass(frozen=True)
class DefenseDecision:
    allowed: bool
    reason: str | None = None
    suspicious: bool = False


class TrafficGuard:
    """Apply sliding-window limits globally, per source IP, and per client."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._global: deque[float] = deque()
        self._sources: dict[str, deque[float]] = {}
        self._clients: dict[tuple[str, str, str], deque[float]] = {}

    def check(self, protocol: str, client_id: str, source_ip: str) -> DefenseDecision:
        now = time.monotonic()
        client_key = (protocol, source_ip, client_id)
        with self._lock:
            self._discard(self._global, now)
            source = self._sources.setdefault(source_ip, deque())
            client = self._clients.setdefault(client_key, deque())
            self._discard(source, now)
            self._discard(client, now)

            self._global.append(now)
            source.append(now)
            client.append(now)

            reason = None
            if len(self._global) > config.MAX_SERVER_REQUESTS_PER_SECOND:
                reason = "GLOBAL_RATE_LIMIT"
            elif len(source) > config.MAX_SOURCE_REQUESTS_PER_SECOND:
                reason = "SOURCE_RATE_LIMIT"
            elif len(client) > config.MAX_LOGICAL_CLIENT_REQUESTS_PER_SECOND:
                reason = "CLIENT_RATE_LIMIT"

            suspicious = (
                len(source) > config.ALERT_SOURCE_REQUESTS_PER_SECOND
                or len(client) > config.ALERT_LOGICAL_CLIENT_REQUESTS_PER_SECOND
            )
            self._prune_identity_maps(now)
            return DefenseDecision(reason is None, reason, suspicious)

    @staticmethod
    def _discard(timestamps: deque[float], now: float) -> None:
        threshold = now - 1.0
        while timestamps and timestamps[0] <= threshold:
            timestamps.popleft()

    def _prune_identity_maps(self, now: float) -> None:
        if (
            len(self._sources) <= config.MAX_TRACKED_TRAFFIC_IDENTITIES
            and len(self._clients) <= config.MAX_TRACKED_TRAFFIC_IDENTITIES
        ):
            return
        for mapping in (self._sources, self._clients):
            stale = []
            for key, timestamps in mapping.items():
                self._discard(timestamps, now)
                if not timestamps:
                    stale.append(key)
            for key in stale:
                del mapping[key]
