"""Xử lý một TCP client và xử lý nội dung một UDP datagram."""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass

from common.config import SOCKET_TIMEOUT_SECONDS
from common.protocol import (
    ProtocolError,
    create_error,
    create_response,
    decode_json,
    encode_json,
    receive_tcp_message,
    validate_request,
)
from server.statistics import ServerStatistics
from server.traffic_guard import TrafficGuard


def log_network_event(
    protocol: str,
    client_id: str,
    address: tuple[str, int],
    event: str,
    latency_ms: float,
    status: str,
) -> None:
    logging.info(
        "protocol=%s client_id=%s client=%s:%d event=%s latency_ms=%.2f status=%s",
        protocol,
        client_id,
        address[0],
        address[1],
        event,
        latency_ms,
        status,
    )


def handle_tcp_client(
    connection: socket.socket,
    address: tuple[str, int],
    statistics: ServerStatistics,
    traffic_guard: TrafficGuard,
) -> None:
    """Một handler thread thực hiện recv -> process -> send -> close."""
    started_at = time.perf_counter()
    client_id = "unknown"
    request_counted = False
    success = False
    bytes_sent = 0
    log_network_event("TCP", client_id, address, "CONNECT", 0.0, "OPEN")

    try:
        with connection:
            connection.settimeout(SOCKET_TIMEOUT_SECONDS)
            message, bytes_received = receive_tcp_message(connection)
            client_id = str(message.get("client_id", "unknown"))
            statistics.request_received("TCP", client_id, address, bytes_received)
            request_counted = True
            client_id = validate_request(message)

            decision = traffic_guard.check("TCP", client_id, address[0])
            statistics.defense_observation(
                suspicious=decision.suspicious, blocked_reason=decision.reason
            )
            if not decision.allowed:
                logging.warning(
                    "TCP defense blocked client=%s source=%s reason=%s",
                    client_id,
                    address[0],
                    decision.reason,
                )
                response_data = encode_json(
                    create_error(decision.reason or "RATE_LIMITED"), tcp_framed=True
                )
                response_ok = False
            else:
                response_data = encode_json(create_response(client_id), tcp_framed=True)
                response_ok = True
            connection.sendall(response_data)
            bytes_sent = len(response_data)
            success = response_ok
    except (ProtocolError, socket.timeout, OSError) as exc:
        logging.warning("TCP client %s:%d failed: %s", address[0], address[1], exc)
        if not request_counted:
            statistics.request_received("TCP", client_id, address, 0)
            request_counted = True
        if isinstance(exc, ProtocolError):
            decision = traffic_guard.check("TCP", client_id, address[0])
            statistics.defense_observation(
                suspicious=decision.suspicious, blocked_reason=decision.reason
            )
    finally:
        elapsed = time.perf_counter() - started_at
        if request_counted:
            statistics.request_completed(success, elapsed, bytes_sent)
        statistics.connection_closed()
        log_network_event(
            "TCP",
            client_id,
            address,
            "REQUEST",
            elapsed * 1_000,
            "SUCCESS" if success else "FAILED",
        )


@dataclass(frozen=True)
class UdpProcessResult:
    response_data: bytes
    client_id: str
    success: bool
    response_seconds: float


def process_udp_datagram(
    data: bytes,
    address: tuple[str, int],
    statistics: ServerStatistics,
    traffic_guard: TrafficGuard,
) -> UdpProcessResult:
    """Xử lý một datagram; UDP không có accept hay client socket riêng."""
    started_at = time.perf_counter()
    client_id = "unknown"
    request_counted = False
    success = False
    try:
        message = decode_json(data)
        client_id = str(message.get("client_id", "unknown"))
        statistics.request_received("UDP", client_id, address, len(data))
        request_counted = True
        client_id = validate_request(message)
        decision = traffic_guard.check("UDP", client_id, address[0])
        statistics.defense_observation(
            suspicious=decision.suspicious, blocked_reason=decision.reason
        )
        if not decision.allowed:
            logging.warning(
                "UDP defense blocked client=%s source=%s reason=%s",
                client_id,
                address[0],
                decision.reason,
            )
            response = create_error(decision.reason or "RATE_LIMITED")
        else:
            response = create_response(client_id)
            success = True
    except ProtocolError as exc:
        logging.warning("UDP client %s:%d failed: %s", address[0], address[1], exc)
        if not request_counted:
            statistics.request_received("UDP", client_id, address, len(data))
        decision = traffic_guard.check("UDP", client_id, address[0])
        statistics.defense_observation(
            suspicious=decision.suspicious, blocked_reason=decision.reason
        )
        response = create_error(decision.reason or "INVALID_REQUEST")

    response_data = encode_json(response, tcp_framed=False)
    elapsed = time.perf_counter() - started_at
    return UdpProcessResult(
        response_data=response_data,
        client_id=client_id,
        success=success,
        response_seconds=elapsed,
    )
