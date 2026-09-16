"""Giao thức JSON đơn giản dùng cho cả TCP stream và UDP datagram."""

from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from typing import Any

from common.config import MAX_MESSAGE_BYTES


class ProtocolError(Exception):
    """Dữ liệu không đúng định dạng giao thức của project."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_request(client_id: str) -> dict[str, str]:
    return {"client_id": client_id, "type": "REQUEST", "timestamp": utc_now()}


def create_response(client_id: str) -> dict[str, str]:
    return {"client_id": client_id, "status": "OK", "server_time": utc_now()}


def create_error(error: str) -> dict[str, str]:
    return {"status": "ERROR", "error": error, "server_time": utc_now()}


def encode_json(message: dict[str, Any], *, tcp_framed: bool) -> bytes:
    payload = json.dumps(message, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_MESSAGE_BYTES:
        raise ProtocolError("Thông điệp vượt quá kích thước cho phép.")
    return payload + (b"\n" if tcp_framed else b"")


def decode_json(payload: bytes) -> dict[str, Any]:
    if not payload or len(payload) > MAX_MESSAGE_BYTES:
        raise ProtocolError("Thông điệp rỗng hoặc quá dài.")
    try:
        message = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProtocolError("Thông điệp không phải JSON hợp lệ.") from exc
    if not isinstance(message, dict):
        raise ProtocolError("Thông điệp JSON phải là một object.")
    return message


def receive_tcp_message(connection: socket.socket) -> tuple[dict[str, Any], int]:
    """Đọc một JSON frame kết thúc bằng newline từ TCP stream."""
    buffer = bytearray()
    while len(buffer) <= MAX_MESSAGE_BYTES:
        chunk = connection.recv(min(1_024, MAX_MESSAGE_BYTES + 1 - len(buffer)))
        if not chunk:
            break
        buffer.extend(chunk)
        if b"\n" in chunk:
            break

    if not buffer:
        raise ProtocolError("Kết nối đóng trước khi gửi dữ liệu.")
    if len(buffer) > MAX_MESSAGE_BYTES or b"\n" not in buffer:
        raise ProtocolError("TCP frame quá dài hoặc thiếu newline.")
    payload = bytes(buffer).split(b"\n", 1)[0]
    return decode_json(payload), len(buffer)


def validate_request(message: dict[str, Any]) -> str:
    if message.get("type") != "REQUEST":
        raise ProtocolError("Trường type phải là REQUEST.")
    client_id = message.get("client_id")
    if not isinstance(client_id, str) or not client_id.strip() or len(client_id) > 64:
        raise ProtocolError("client_id không hợp lệ.")
    if not isinstance(message.get("timestamp"), str):
        raise ProtocolError("timestamp không hợp lệ.")
    return client_id


def validate_response(message: dict[str, Any], client_id: str) -> bool:
    return message.get("status") == "OK" and message.get("client_id") == client_id

