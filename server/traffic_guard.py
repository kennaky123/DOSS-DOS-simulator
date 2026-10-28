"""Multi-layer traffic defense with Flash Crowd vs DoS/DDoS behavioral classification."""

from __future__ import annotations

import threading
import time
from collections import Counter, deque
from dataclasses import dataclass

from common import config


@dataclass(frozen=True)
class DefenseDecision:
    allowed: bool
    reason: str | None = None
    suspicious: bool = False
    traffic_pattern: str = "NORMAL"
    trust_score: int = 100
    overload_delay_seconds: float = 0.0


class TrafficGuard:
    """Apply sliding-window limits and behavioral analysis to distinguish Flash Crowd from DoS/DDoS."""

    def __init__(self, *, enabled: bool = True) -> None:
        self._lock = threading.Lock()
        self._enabled = enabled
        self._global: deque[float] = deque()
        self._global_ips: dict[str, float] = {}
        self._global_clients: dict[str, float] = {}
        self._global_paths: deque[tuple[float, str]] = deque()
        self._sources: dict[str, deque[float]] = {}
        self._clients: dict[tuple[str, str, str], deque[float]] = {}
        self._source_clients: dict[str, dict[str, float]] = {}
        self._source_paths: dict[str, deque[tuple[float, str]]] = {}
        self._client_paths: dict[tuple[str, str, str], deque[tuple[float, str]]] = {}
        self._unprotected_slots = threading.BoundedSemaphore(
            config.UNPROTECTED_WORKER_SLOTS
        )
        self._active_unprotected_workers = 0

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)

    def simulate_unprotected_work(self, delay_seconds: float) -> bool:
        """Khi tắt TrafficGuard, các request flood chiếm giữ worker slot và gây nghẽn toàn server."""
        if delay_seconds <= 0.0:
            return True
        if not self._unprotected_slots.acquire(blocking=False):
            # Hàng đợi bị đầy: client phải chờ xử lý trong vô vọng rồi bị từ chối dịch vụ (503)
            time.sleep(min(delay_seconds, 0.25))
            return False
        with self._lock:
            self._active_unprotected_workers += 1
            saturated = (
                self._active_unprotected_workers >= config.UNPROTECTED_WORKER_SLOTS
                or len(self._global) > config.DDOS_BOT_SOURCE_RPS_THRESHOLD
            )
        try:
            time.sleep(delay_seconds)
            return not saturated
        finally:
            with self._lock:
                self._active_unprotected_workers = max(
                    0, self._active_unprotected_workers - 1
                )
            self._unprotected_slots.release()

    def check(
        self,
        protocol: str,
        client_id: str,
        source_ip: str,
        path: str = "/",
        *,
        ip_mode: str = "SINGLE_IP",
    ) -> DefenseDecision:
        now = time.monotonic()
        client_key = (protocol, source_ip, client_id)
        normalized_path = path.strip() if isinstance(path, str) and path.strip() else "/"

        with self._lock:
            self._discard(self._global, now)
            self._discard_source_clients(self._global_ips, now)
            self._discard_source_clients(self._global_clients, now)
            self._discard_path_entries(self._global_paths, now, window_seconds=1.0)
            source = self._sources.setdefault(source_ip, deque())
            client = self._clients.setdefault(client_key, deque())
            source_clients = self._source_clients.setdefault(source_ip, {})
            source_paths = self._source_paths.setdefault(source_ip, deque())
            client_paths = self._client_paths.setdefault(client_key, deque())

            self._discard(source, now)
            self._discard(client, now)
            self._discard_path_entries(source_paths, now, window_seconds=1.0)
            self._discard_path_entries(client_paths, now, window_seconds=2.0)
            self._discard_source_clients(source_clients, now)

            self._global.append(now)
            self._global_ips[source_ip] = now
            self._global_clients[client_id] = now
            self._global_paths.append((now, normalized_path))
            source.append(now)
            client.append(now)
            source_clients[client_id] = now
            source_paths.append((now, normalized_path))
            client_paths.append((now, normalized_path))

            global_rps = len(self._global)
            source_rps = len(source)
            client_rps = len(client)
            active_source_clients = max(len(source_clients), len(self._global_clients))
            active_unique_ips = len(self._global_ips)
            effective_paths = (
                self._global_paths if active_unique_ips > 1 else source_paths
            )
            unique_source_paths = len({p for _, p in effective_paths})
            unique_client_paths = len({p for _, p in client_paths})
            dominant_path, dominant_path_ratio = self._dominant_path_info(effective_paths)

            is_human_client = (
                ip_mode != "UDP_SPOOF"
                and client_rps <= config.FLASH_CROWD_MAX_CLIENT_RPS
                and (
                    unique_client_paths >= 2
                    or (
                        client_rps <= 4
                        and len(effective_paths) >= config.DDOS_BOT_MIN_CLIENTS
                        and normalized_path != dominant_path
                    )
                )
            )
            is_flash_crowd = (
                ip_mode != "UDP_SPOOF"
                and active_source_clients >= config.FLASH_CROWD_MIN_CLIENTS
                and unique_source_paths >= config.FLASH_CROWD_MIN_PATHS
                and dominant_path_ratio < 0.75
                and client_rps <= config.FLASH_CROWD_MAX_CLIENT_RPS
            )
            is_udp_spoof_flood = (
                protocol == "UDP"
                and not is_flash_crowd
                and not is_human_client
                and (
                    (
                        ip_mode == "UDP_SPOOF"
                        and global_rps > config.ALERT_LOGICAL_CLIENT_REQUESTS_PER_SECOND
                    )
                    or (
                        active_unique_ips >= config.UDP_SPOOF_MIN_UNIQUE_IPS
                        and global_rps > config.DDOS_BOT_SOURCE_RPS_THRESHOLD
                        and dominant_path_ratio >= 0.75
                    )
                )
            )
            is_ddos_bot_flood = (
                max(source_rps, global_rps) > config.DDOS_BOT_SOURCE_RPS_THRESHOLD
                and active_source_clients >= config.DDOS_BOT_MIN_CLIENTS
                and not is_flash_crowd
                and not is_human_client
            )

            effective_source_limit = (
                config.FLASH_CROWD_SOURCE_REQUESTS_PER_SECOND
                if (is_flash_crowd or is_human_client)
                else config.MAX_SOURCE_REQUESTS_PER_SECOND
            )

            reason = None
            if global_rps > config.MAX_SERVER_REQUESTS_PER_SECOND:
                reason = "GLOBAL_RATE_LIMIT"
            elif client_rps > config.MAX_LOGICAL_CLIENT_REQUESTS_PER_SECOND:
                reason = "CLIENT_RATE_LIMIT"
            elif is_udp_spoof_flood:
                reason = "UDP_SPOOF_DETECTED"
            elif is_ddos_bot_flood:
                reason = "DDOS_BOT_DETECTED"
            elif source_rps > effective_source_limit:
                reason = "SOURCE_RATE_LIMIT"

            if is_flash_crowd and reason is None:
                suspicious = False
                traffic_pattern = "FLASH_CROWD"
            else:
                suspicious = (
                    reason is not None
                    or ip_mode == "UDP_SPOOF"
                    or client_rps > config.ALERT_LOGICAL_CLIENT_REQUESTS_PER_SECOND
                    or (
                        source_rps > config.ALERT_SOURCE_REQUESTS_PER_SECOND
                        and not is_human_client
                    )
                )
                if reason == "UDP_SPOOF_DETECTED" or ip_mode == "UDP_SPOOF":
                    traffic_pattern = "UDP_SPOOF_ATTACK"
                elif reason == "CLIENT_RATE_LIMIT" or (
                    client_rps > config.ALERT_LOGICAL_CLIENT_REQUESTS_PER_SECOND
                    and active_source_clients < config.DDOS_BOT_MIN_CLIENTS
                ):
                    traffic_pattern = "DOS_ATTACK"
                elif reason in (
                    "DDOS_BOT_DETECTED",
                    "SOURCE_RATE_LIMIT",
                    "GLOBAL_RATE_LIMIT",
                ) or (suspicious and active_source_clients >= config.DDOS_BOT_MIN_CLIENTS):
                    traffic_pattern = "DDOS_ATTACK"
                elif suspicious:
                    traffic_pattern = "DOS_ATTACK"
                elif is_flash_crowd:
                    traffic_pattern = "FLASH_CROWD"
                else:
                    traffic_pattern = "NORMAL"

            trust_score = self._compute_trust_score(
                client_rps=client_rps,
                source_rps=source_rps,
                is_flash_crowd=is_flash_crowd,
                is_human_client=is_human_client,
                is_ddos_bot_flood=is_ddos_bot_flood,
                reason=reason,
            )
            self._prune_identity_maps(now)

            if not self._enabled:
                # Khi tắt phòng thủ: không chặn ở biên, nhưng nếu có tải cao/flood từ bất kỳ IP nào thì toàn server bị nghẽn.
                is_heavy_load = (
                    reason is not None
                    or suspicious
                    or global_rps > config.MAX_LOGICAL_CLIENT_REQUESTS_PER_SECOND
                    or source_rps > config.MAX_LOGICAL_CLIENT_REQUESTS_PER_SECOND
                )
                overload_delay = (
                    config.UNPROTECTED_OVERLOAD_DELAY_SECONDS if is_heavy_load else 0.0
                )
                return DefenseDecision(
                    allowed=True,
                    reason=None,
                    suspicious=suspicious,
                    traffic_pattern=traffic_pattern,
                    trust_score=trust_score,
                    overload_delay_seconds=overload_delay,
                )

            return DefenseDecision(
                allowed=reason is None,
                reason=reason,
                suspicious=suspicious,
                traffic_pattern=traffic_pattern,
                trust_score=trust_score,
                overload_delay_seconds=0.0,
            )

    @staticmethod
    def _dominant_path_info(entries: deque[tuple[float, str]]) -> tuple[str, float]:
        if not entries:
            return "/", 1.0
        counts = Counter(path for _, path in entries)
        top_path, top_count = counts.most_common(1)[0]
        return top_path, top_count / len(entries)

    @staticmethod
    def _compute_trust_score(
        *,
        client_rps: int,
        source_rps: int,
        is_flash_crowd: bool,
        is_human_client: bool,
        is_ddos_bot_flood: bool,
        reason: str | None,
    ) -> int:
        if is_flash_crowd or (is_human_client and reason is None):
            return 95
        score = 100
        if client_rps > config.ALERT_LOGICAL_CLIENT_REQUESTS_PER_SECOND:
            score -= min(
                60, (client_rps - config.ALERT_LOGICAL_CLIENT_REQUESTS_PER_SECOND) * 2
            )
        if source_rps > config.ALERT_SOURCE_REQUESTS_PER_SECOND:
            score -= min(
                45, (source_rps - config.ALERT_SOURCE_REQUESTS_PER_SECOND) // 2
            )
        if is_ddos_bot_flood:
            score -= 45
        if reason is not None:
            score = min(score, 15)
        return max(5, min(100, score))

    @staticmethod
    def _discard(timestamps: deque[float], now: float) -> None:
        threshold = now - 1.0
        while timestamps and timestamps[0] <= threshold:
            timestamps.popleft()

    @staticmethod
    def _discard_path_entries(
        entries: deque[tuple[float, str]], now: float, *, window_seconds: float
    ) -> None:
        threshold = now - window_seconds
        while entries and entries[0][0] <= threshold:
            entries.popleft()

    @staticmethod
    def _discard_source_clients(clients: dict[str, float], now: float) -> None:
        threshold = now - 1.0
        stale = [cid for cid, seen_at in clients.items() if seen_at <= threshold]
        for cid in stale:
            del clients[cid]

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
        for path_map, window in ((self._source_paths, 1.0), (self._client_paths, 2.0)):
            stale_paths = []
            for key, entries in path_map.items():
                self._discard_path_entries(entries, now, window_seconds=window)
                if not entries:
                    stale_paths.append(key)
            for key in stale_paths:
                del path_map[key]
        stale_sc = []
        for ip, sc_map in self._source_clients.items():
            self._discard_source_clients(sc_map, now)
            if not sc_map:
                stale_sc.append(ip)
        for ip in stale_sc:
            del self._source_clients[ip]
