"""TCP server: socket -> bind -> listen -> accept -> handler thread."""

from __future__ import annotations

import logging
import socket
import threading

from common.config import (
    LISTEN_BACKLOG,
    MAX_CONCURRENT_CONNECTIONS,
    SOCKET_TIMEOUT_SECONDS,
)
from common.network_utils import SlidingWindowRateLimiter, is_lab_address
from common.protocol import create_error, encode_json
from server.client_handler import handle_tcp_client
from server.statistics import ServerStatistics


class TcpServer:
    def __init__(
        self,
        host: str,
        port: int,
        statistics: ServerStatistics,
        rate_limiter: SlidingWindowRateLimiter,
        stop_event: threading.Event,
    ) -> None:
        self.host = host
        self.port = port
        self.statistics = statistics
        self.rate_limiter = rate_limiter
        self.stop_event = stop_event
        self._server_socket: socket.socket | None = None
        self._slots = threading.BoundedSemaphore(MAX_CONCURRENT_CONNECTIONS)
        self._handlers: set[threading.Thread] = set()
        self._handlers_lock = threading.Lock()

    def serve_forever(self) -> None:
        try:
            # Quy trình TCP server được giữ tường minh để phục vụ trình bày môn học.
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server_socket:
                self._server_socket = server_socket
                if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                    # Windows cần exclusive bind để process khác không chiếm cùng port.
                    server_socket.setsockopt(
                        socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1
                    )
                else:
                    server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                server_socket.bind((self.host, self.port))
                server_socket.listen(LISTEN_BACKLOG)
                server_socket.settimeout(1.0)
                logging.info("TCP server listening on %s:%d", self.host, self.port)

                while not self.stop_event.is_set():
                    try:
                        client_socket, address = server_socket.accept()
                    except socket.timeout:
                        continue
                    except OSError:
                        if self.stop_event.is_set():
                            break
                        raise
                    self._dispatch(client_socket, address)
        finally:
            self._server_socket = None
            self._join_handlers()
            logging.info("TCP server stopped")

    def _dispatch(self, client_socket: socket.socket, address: tuple[str, int]) -> None:
        if not is_lab_address(address[0]):
            logging.warning("TCP rejected non-private client %s:%d", *address)
            client_socket.close()
            return
        self.statistics.connection_opened()
        if not self._slots.acquire(blocking=False):
            self._send_busy(client_socket)
            client_socket.close()
            self.statistics.connection_closed()
            return

        thread = threading.Thread(
            target=self._run_handler,
            args=(client_socket, address),
            name=f"tcp-{address[0]}:{address[1]}",
            daemon=True,
        )
        with self._handlers_lock:
            self._handlers.add(thread)
        thread.start()

    def _run_handler(self, client_socket: socket.socket, address: tuple[str, int]) -> None:
        try:
            handle_tcp_client(
                client_socket,
                address,
                self.statistics,
                self.rate_limiter,
            )
        finally:
            self._slots.release()
            with self._handlers_lock:
                self._handlers.discard(threading.current_thread())

    @staticmethod
    def _send_busy(client_socket: socket.socket) -> None:
        try:
            client_socket.settimeout(SOCKET_TIMEOUT_SECONDS)
            client_socket.sendall(
                encode_json(create_error("SERVER_BUSY"), tcp_framed=True)
            )
        except OSError:
            pass

    def shutdown(self) -> None:
        if self._server_socket is not None:
            try:
                self._server_socket.close()
            except OSError:
                pass

    def _join_handlers(self) -> None:
        with self._handlers_lock:
            handlers = list(self._handlers)
        for thread in handlers:
            thread.join(SOCKET_TIMEOUT_SECONDS + 1.0)
