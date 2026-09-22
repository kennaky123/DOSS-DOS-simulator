"""UDP server: socket -> bind -> recvfrom -> sendto -> close."""

from __future__ import annotations

import logging
import socket
import threading

from common.config import MAX_MESSAGE_BYTES
from common.network_utils import is_lab_address
from server.client_handler import log_network_event, process_udp_datagram
from server.statistics import ServerStatistics
from server.traffic_guard import TrafficGuard


class UdpServer:
    def __init__(
        self,
        host: str,
        port: int,
        statistics: ServerStatistics,
        traffic_guard: TrafficGuard,
        stop_event: threading.Event,
    ) -> None:
        self.host = host
        self.port = port
        self.statistics = statistics
        self.traffic_guard = traffic_guard
        self.stop_event = stop_event
        self._server_socket: socket.socket | None = None

    def serve_forever(self) -> None:
        try:
            # UDP không listen/accept và không tạo client socket riêng.
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server_socket:
                self._server_socket = server_socket
                server_socket.bind((self.host, self.port))
                server_socket.settimeout(1.0)
                logging.info("UDP server listening on %s:%d", self.host, self.port)

                while not self.stop_event.is_set():
                    try:
                        data, address = server_socket.recvfrom(MAX_MESSAGE_BYTES + 1)
                    except socket.timeout:
                        continue
                    except OSError:
                        if self.stop_event.is_set():
                            break
                        raise
                    if not is_lab_address(address[0]):
                        logging.warning("UDP rejected non-private client %s:%d", *address)
                        continue
                    result = process_udp_datagram(
                        data,
                        address,
                        self.statistics,
                        self.traffic_guard,
                    )
                    sent_bytes = 0
                    send_ok = False
                    try:
                        sent_bytes = server_socket.sendto(result.response_data, address)
                        send_ok = (
                            result.success and sent_bytes == len(result.response_data)
                        )
                    except OSError as exc:
                        logging.warning("UDP sendto %s:%d failed: %s", *address, exc)
                    self.statistics.request_completed(
                        send_ok,
                        result.response_seconds,
                        sent_bytes,
                    )
                    log_network_event(
                        "UDP",
                        result.client_id,
                        address,
                        "DATAGRAM",
                        result.response_seconds * 1_000,
                        "SUCCESS" if send_ok else "FAILED",
                    )
        finally:
            self._server_socket = None
            logging.info("UDP server stopped")

    def shutdown(self) -> None:
        if self._server_socket is not None:
            try:
                self._server_socket.close()
            except OSError:
                pass
