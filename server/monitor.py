"""Dashboard terminal cập nhật realtime nhưng không spam console."""

from __future__ import annotations

import threading

from server.statistics import ServerStatistics

try:
    from rich.console import Console, Group
    from rich.live import Live
    from rich.panel import Panel
    from rich.table import Table
except ImportError:  # Rich la tuy chon; van co dashboard text don gian.
    Console = Group = Live = Panel = Table = None


class Dashboard:
    def __init__(
        self,
        statistics: ServerStatistics,
        host: str,
        port: int,
        protocol: str,
        interval: float,
    ) -> None:
        self.statistics = statistics
        self.host = host
        self.port = port
        self.protocol = protocol
        self.interval = interval

    def run(self, stop_event: threading.Event) -> None:
        if Live is None:
            self._run_plain(stop_event)
            return
        console = Console()
        with Live(self._render_rich(), console=console, refresh_per_second=4) as live:
            while not stop_event.wait(self.interval):
                live.update(self._render_rich())

    def _render_rich(self):
        snapshot = self.statistics.snapshot()
        table = Table(show_header=True, header_style="bold cyan", expand=True)
        table.add_column("Metric")
        table.add_column("Value", justify="right")
        table.add_row("Current clients", str(snapshot.current_clients))
        table.add_row("Current TCP connections", str(snapshot.current_connections))
        table.add_row("Total TCP connections", str(snapshot.total_connections))
        table.add_row("Total requests", str(snapshot.total_requests))
        table.add_row("Requests / second", str(snapshot.requests_per_second))
        table.add_row("Successful", str(snapshot.successful_requests))
        table.add_row("Failed", str(snapshot.failed_requests))
        table.add_row("Defense status", snapshot.defense_status)
        table.add_row("Suspicious requests", str(snapshot.suspicious_requests))
        table.add_row("Blocked requests", str(snapshot.blocked_requests))
        table.add_row("Last block reason", snapshot.last_block_reason)
        table.add_row("Average server response", f"{snapshot.average_response_ms:.2f} ms")
        table.add_row("Bytes received", str(snapshot.bytes_received))
        table.add_row("Bytes sent", str(snapshot.bytes_sent))
        table.add_row("Uptime", f"{snapshot.uptime_seconds:.1f} s")
        title = f"Test Server - {self.host}:{self.port} - {self.protocol}"
        return Group(Panel(table, title=title), "Nhấn Ctrl+C để shutdown sạch.")

    def _run_plain(self, stop_event: threading.Event) -> None:
        while not stop_event.wait(self.interval):
            item = self.statistics.snapshot()
            print(
                f"[SERVER {self.protocol} {self.host}:{self.port}] "
                f"clients={item.current_clients} conn={item.current_connections} "
                f"connections={item.total_connections} total={item.total_requests} "
                f"rps={item.requests_per_second} ok={item.successful_requests} "
                f"failed={item.failed_requests} avg={item.average_response_ms:.2f}ms "
                f"defense={item.defense_status} suspicious={item.suspicious_requests} "
                f"blocked={item.blocked_requests} reason={item.last_block_reason} "
                f"rx={item.bytes_received}B tx={item.bytes_sent}B "
                f"uptime={item.uptime_seconds:.1f}s"
            )
