"""Điều phối logical clients và hiển thị trạng thái simulation."""

from __future__ import annotations

import random
import threading
import time
from collections import deque
from dataclasses import dataclass

from client.tcp_client import TcpClient
from client.udp_client import UdpClient
from common import config
from common.network_utils import validate_lab_address, validate_port

try:
    from rich.console import Console
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
except ImportError:  # Project vẫn chạy được nếu chưa cài Rich.
    Console = Live = Panel = Table = None


@dataclass(frozen=True)
class ClientSnapshot:
    sent: int
    successful: int
    failed: int
    requests_per_second: int
    average_latency_ms: float
    elapsed_seconds: float


class ClientMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_at = time.monotonic()
        self._sent = 0
        self._successful = 0
        self._failed = 0
        self._total_latency_seconds = 0.0
        self._recent_requests: deque[float] = deque()

    def record(self, success: bool, latency_seconds: float) -> None:
        now = time.monotonic()
        with self._lock:
            self._sent += 1
            if success:
                self._successful += 1
            else:
                self._failed += 1
            self._total_latency_seconds += latency_seconds
            self._recent_requests.append(now)
            self._discard_old(now)

    def _discard_old(self, now: float) -> None:
        threshold = now - 1.0
        while self._recent_requests and self._recent_requests[0] < threshold:
            self._recent_requests.popleft()

    def snapshot(self) -> ClientSnapshot:
        now = time.monotonic()
        with self._lock:
            self._discard_old(now)
            average_ms = (
                self._total_latency_seconds / self._sent * 1_000 if self._sent else 0.0
            )
            return ClientSnapshot(
                sent=self._sent,
                successful=self._successful,
                failed=self._failed,
                requests_per_second=len(self._recent_requests),
                average_latency_ms=average_ms,
                elapsed_seconds=now - self._started_at,
            )


class WorkerPacer:
    """Giữ request rate riêng cho từng logical client, hỗ trợ jitter cho người dùng thật."""

    def __init__(self, requests_per_second: float, *, jitter_ratio: float = 0.0) -> None:
        self._interval = 1.0 / requests_per_second
        self._jitter_ratio = max(0.0, min(0.5, jitter_ratio))
        self._next_request = time.monotonic()

    def wait(self, stop_event: threading.Event, deadline: float) -> bool:
        slot = self._next_request
        if slot >= deadline:
            stop_event.wait(max(0.0, deadline - time.monotonic()))
            return False
        if stop_event.wait(max(0.0, slot - time.monotonic())):
            return False
        step = self._interval
        if self._jitter_ratio > 0.0:
            factor = random.uniform(1.0 - self._jitter_ratio, 1.0 + self._jitter_ratio)
            step = self._interval * factor
        self._next_request = max(slot + step, time.monotonic())
        return time.monotonic() < deadline


class TrafficSimulator:
    def __init__(
        self,
        host: str,
        port: int,
        protocol: str,
        mode: str,
        logical_clients: int,
        request_rate: float,
        duration_seconds: int,
        *,
        ip_mode: str | None = None,
    ) -> None:
        self.host = validate_lab_address(host)
        self.port = validate_port(port)
        self.protocol = protocol
        self.mode = mode
        self.logical_clients = logical_clients
        self.request_rate = request_rate
        self.duration_seconds = duration_seconds
        default_ip_mode = "SINGLE_IP" if mode == "DOS_SIMULATION" else "MULTI_IP"
        chosen_ip_mode = (ip_mode or default_ip_mode).upper()
        if chosen_ip_mode not in config.IP_MODES:
            chosen_ip_mode = default_ip_mode
        if self.protocol == "TCP" and chosen_ip_mode == "UDP_SPOOF":
            # TCP cần bắt tay 3 bước nên tự động chuyển về MULTI_IP.
            chosen_ip_mode = "MULTI_IP"
        self.ip_mode = chosen_ip_mode
        self.metrics = ClientMetrics()
        self.stop_event = threading.Event()

    def run(self, *, show_dashboard: bool = True) -> ClientSnapshot:
        deadline = time.monotonic() + self.duration_seconds
        threads = [
            threading.Thread(
                target=self._worker,
                args=(number, deadline),
                name=f"logical-client-{number:02d}",
                daemon=True,
            )
            for number in range(1, self.logical_clients + 1)
        ]
        for thread in threads:
            thread.start()

        try:
            if show_dashboard:
                self._monitor(threads)
            else:
                for thread in threads:
                    thread.join()
        except KeyboardInterrupt:
            print("\nĐang dừng simulation...")
        finally:
            self.stop_event.set()
            for thread in threads:
                thread.join(4.0)

        snapshot = self.metrics.snapshot()
        if show_dashboard:
            self._print_plain(snapshot, final=True)
        return snapshot

    def _worker(self, number: int, deadline: float) -> None:
        transport = TcpClient(self.host, self.port) if self.protocol == "TCP" else UdpClient(
            self.host, self.port
        )
        base_client_id = self._client_id(number)
        is_human_mode = self.mode in ("NORMAL", "FLASH_CROWD")
        pacer = WorkerPacer(self.request_rate, jitter_ratio=0.35 if is_human_mode else 0.0)
        step = 0
        while pacer.wait(self.stop_event, deadline):
            path = self._request_path(number, step, is_human_mode)
            source_ip, bind_ip = self._resolve_worker_ip(number)
            if self.ip_mode == "UDP_SPOOF" and self.protocol == "UDP":
                client_id = f"spoof_udp_{random.randint(1000, 9999)}"
            else:
                client_id = base_client_id
            step += 1
            success, latency = transport.send_request(
                client_id,
                path=path,
                source_ip=source_ip,
                bind_ip=bind_ip,
                ip_mode=self.ip_mode,
            )
            self.metrics.record(success, latency)

    def _resolve_worker_ip(self, number: int) -> tuple[str | None, str | None]:
        is_loopback = self.host == "localhost" or self.host.startswith("127.")
        if self.ip_mode == "SINGLE_IP":
            return None, None
        if self.ip_mode == "UDP_SPOOF" and self.protocol == "UDP":
            octet_3 = random.randint(2, 250)
            octet_4 = random.randint(1, 254)
            if is_loopback:
                ip = f"127.0.{octet_3}.{octet_4}"
                return ip, ip
            return f"10.{random.randint(1, 250)}.{octet_3}.{octet_4}", None
        # MULTI_IP dùng cho cả TCP và UDP: mỗi logical client giữ 1 IP riêng.
        worker_octet = max(2, min(250, number + 1))
        if is_loopback:
            ip = f"127.0.1.{worker_octet}"
            return ip, ip
        return f"192.168.100.{worker_octet}", None

    def _client_id(self, number: int) -> str:
        if self.mode == "DOS_SIMULATION":
            # Nhiều worker của một simulator dùng chung một nguồn logic.
            return "dos_source_01"
        if self.mode == "FLASH_CROWD":
            return f"crowd_user_{number:02d}"
        prefix = "normal_client" if self.mode == "NORMAL" else "ddos_client"
        return f"{prefix}_{number:02d}"

    @staticmethod
    def _request_path(number: int, step: int, is_human_mode: bool) -> str:
        if is_human_mode:
            paths = config.HUMAN_PATHS
            return paths[(number + step) % len(paths)]
        return config.ATTACK_TARGET_PATH

    def _monitor(self, threads: list[threading.Thread]) -> None:
        if Live is None:
            while any(thread.is_alive() for thread in threads):
                self._print_plain(self.metrics.snapshot(), final=False)
                if self.stop_event.wait(1.0):
                    break
            return

        with Live(
            self._render_rich(),
            console=Console(),
            refresh_per_second=4,
        ) as live:
            while any(thread.is_alive() for thread in threads):
                if self.stop_event.wait(1.0):
                    break
                live.update(self._render_rich())

    def _render_rich(self):
        item = self.metrics.snapshot()
        table = Table(show_header=False, expand=True)
        table.add_column("Metric")
        table.add_column("Value", justify="right")
        table.add_row("Mode", self.mode)
        table.add_row("Protocol", self.protocol)
        table.add_row("IP source mode", self.ip_mode)
        table.add_row("Server", f"{self.host}:{self.port}")
        table.add_row("Logical clients", str(self.logical_clients))
        table.add_row("Duration", f"{self.duration_seconds} s")
        table.add_row("Rate / client", f"{self.request_rate:g} req/s")
        table.add_row("Sent requests", str(item.sent))
        table.add_row("Successful", str(item.successful))
        table.add_row("Failed", str(item.failed))
        table.add_row("Requests / second", str(item.requests_per_second))
        table.add_row("Average latency", f"{item.average_latency_ms:.2f} ms")
        table.add_row("Elapsed time", f"{item.elapsed_seconds:.1f} s")
        return Panel(table, title="Simulation Status")

    def _print_plain(self, item: ClientSnapshot, final: bool) -> None:
        label = "FINAL" if final else "SIMULATION"
        print(
            f"[{label}] mode={self.mode} protocol={self.protocol} "
            f"server={self.host}:{self.port} clients={self.logical_clients} "
            f"sent={item.sent} ok={item.successful} failed={item.failed} "
            f"rps={item.requests_per_second} latency={item.average_latency_ms:.2f}ms "
            f"elapsed={item.elapsed_seconds:.1f}s"
        )
