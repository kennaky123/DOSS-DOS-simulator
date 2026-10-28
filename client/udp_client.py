"""UDP client thực hiện socket -> sendto -> recvfrom -> close."""

from __future__ import annotations

import socket
import time

from common.config import MAX_MESSAGE_BYTES, SOCKET_TIMEOUT_SECONDS
from common.network_utils import validate_lab_address, validate_port
from common.protocol import (
    ProtocolError,
    create_request,
    decode_json,
    encode_json,
    validate_response,
)


class UdpClient:
    def __init__(self, host: str, port: int) -> None:
        self.host = validate_lab_address(host)
        self.port = validate_port(port)
        self._server_address = (socket.gethostbyname(self.host), self.port)

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
        try:
            request_data = encode_json(
                create_request(
                    client_id,
                    path=path,
                    source_ip=source_ip,
                    ip_mode=ip_mode,
                ),
                tcp_framed=False,
            )
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client_socket:
                # UDP không đảm bảo phản hồi nên timeout là bắt buộc.
                client_socket.settimeout(SOCKET_TIMEOUT_SECONDS)
                if bind_ip:
                    client_socket.bind((bind_ip, 0))
                client_socket.sendto(request_data, self._server_address)
                response_data, response_address = client_socket.recvfrom(MAX_MESSAGE_BYTES)
            response = decode_json(response_data)
            success = (
                response_address == self._server_address
                and validate_response(response, client_id)
            )
        except (OSError, ProtocolError):
            success = False
        return success, time.perf_counter() - started_at
