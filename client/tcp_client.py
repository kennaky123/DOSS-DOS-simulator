"""TCP client thực hiện socket -> connect -> send -> recv -> close."""

from __future__ import annotations

import socket
import time

from common.config import SOCKET_TIMEOUT_SECONDS
from common.network_utils import validate_lab_address, validate_port
from common.protocol import (
    ProtocolError,
    create_request,
    encode_json,
    receive_tcp_message,
    validate_response,
)


class TcpClient:
    def __init__(self, host: str, port: int) -> None:
        self.host = validate_lab_address(host)
        self.port = validate_port(port)

    def send_request(
        self,
        client_id: str,
        path: str = "/",
        *,
        source_ip: str | None = None,
        bind_ip: str | None = None,
        ip_mode: str = "SINGLE_IP",
    ) -> tuple[bool, float]:
        started_at = time.perf_counter()
        # TCP bắt buộc bắt tay 3 bước nên không dùng UDP_SPOOF, chỉ hỗ trợ SINGLE_IP hoặc MULTI_IP.
        effective_mode = "MULTI_IP" if ip_mode == "UDP_SPOOF" else ip_mode
        try:
            request_data = encode_json(
                create_request(
                    client_id,
                    path=path,
                    source_ip=source_ip,
                    ip_mode=effective_mode,
                ),
                tcp_framed=True,
            )
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as client_socket:
                client_socket.settimeout(SOCKET_TIMEOUT_SECONDS)
                if bind_ip:
                    client_socket.bind((bind_ip, 0))
                client_socket.connect((self.host, self.port))
                client_socket.sendall(request_data)
                response, _ = receive_tcp_message(client_socket)
            success = validate_response(response, client_id)
        except (OSError, ProtocolError):
            success = False
        return success, time.perf_counter() - started_at

