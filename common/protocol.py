"""Giao thức JSON đơn giản dùng cho cả TCP stream và UDP datagram."""

from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from typing import Any

from common.config import IP_MODES, MAX_MESSAGE_BYTES
from common.network_utils import is_lab_address


class ProtocolError(Exception):
    """Dữ liệu không đúng định dạng giao thức của project."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_request(
    client_id: str,
    path: str = "/",
    *,
    source_ip: str | None = None,
    ip_mode: str = "SINGLE_IP",
) -> dict[str, str]:
    payload: dict[str, str] = {
        "client_id": client_id,
        "type": "REQUEST",
        "path": path,
        "ip_mode": ip_mode,
        "timestamp": utc_now(),
    }
    if source_ip:
        payload["source_ip"] = source_ip
    return payload


SERVICE_CATALOG = (
    {"id": "P01", "name": "Laptop Pro RTX 4070", "price": "32.900.000đ", "stock": 12, "tag": "HOT"},
    {"id": "P02", "name": "Bàn phím Cơ Custom Aluminum", "price": "2.490.000đ", "stock": 38, "tag": "SALE -30%"},
    {"id": "P03", "name": "Tai nghe Không dây ANC Studio", "price": "4.150.000đ", "stock": 19, "tag": "FLASH"},
    {"id": "P04", "name": "Chuột Gaming Siêu nhẹ 4KHz", "price": "1.890.000đ", "stock": 54, "tag": "NEW"},
)


def build_service_payload(path: str, client_id: str) -> dict[str, Any]:
    normalized = path.strip() if isinstance(path, str) and path.strip() else "/"
    if normalized == "/products":
        return {
            "page": "Danh sách Sản phẩm Flash Sale",
            "path": normalized,
            "items": list(SERVICE_CATALOG),
            "message": "Hiển thị 4 sản phẩm đang mở bán trực tuyến.",
        }
    if normalized == "/search":
        return {
            "page": "Kết quả Tìm kiếm",
            "path": normalized,
            "items": list(SERVICE_CATALOG[:2]),
            "message": "Tìm thấy 2 sản phẩm khớp từ khóa 'Gaming Pro'.",
        }
    if normalized == "/detail":
        return {
            "page": "Chi tiết Sản phẩm",
            "path": normalized,
            "items": [SERVICE_CATALOG[0]],
            "message": "Bảo hành chính hãng 24 tháng, giao nhanh 2h.",
        }
    if normalized == "/cart":
        return {
            "page": "Giỏ hàng của bạn",
            "path": normalized,
            "items": [SERVICE_CATALOG[1], SERVICE_CATALOG[3]],
            "total": "4.380.000đ",
            "message": "Giỏ hàng có 2 sản phẩm sẵn sàng thanh toán.",
        }
    if normalized == "/checkout":
        return {
            "page": "Xác nhận Đặt hàng",
            "path": normalized,
            "order_id": f"ORD-{abs(hash((client_id, utc_now()))) % 90000 + 10000}",
            "total": "4.380.000đ",
            "message": "Đặt hàng Flash Sale thành công! Đơn hàng đã được ghi nhận.",
        }
    if normalized == "/login":
        return {
            "page": "Cổng Đăng nhập Thành viên",
            "path": normalized,
            "user": client_id,
            "message": "Xác thực phiên đăng nhập thành công.",
        }
    return {
        "page": "Trang chủ NetShop",
        "path": normalized,
        "items": list(SERVICE_CATALOG[:3]),
        "message": "Hệ thống thương mại điện tử đang hoạt động bình thường.",
    }


def create_response(client_id: str, path: str = "/") -> dict[str, Any]:
    return {
        "client_id": client_id,
        "status": "OK",
        "path": path,
        "service": build_service_payload(path, client_id),
        "server_time": utc_now(),
    }


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
    path = message.get("path", "/")
    if not isinstance(path, str) or not path.strip() or len(path) > 64:
        raise ProtocolError("path không hợp lệ.")
    return client_id


def extract_request_path(message: dict[str, Any]) -> str:
    path = message.get("path", "/")
    if isinstance(path, str) and path.strip() and len(path) <= 64:
        return path.strip()
    return "/"


def extract_source_ip(
    message: dict[str, Any], default_ip: str, protocol: str
) -> tuple[str, str]:
    """Trả về (effective_source_ip, ip_mode). TCP không cho phép UDP_SPOOF do cần 3-way handshake."""
    raw_mode = str(message.get("ip_mode", "SINGLE_IP")).upper()
    ip_mode = raw_mode if raw_mode in IP_MODES else "SINGLE_IP"
    if protocol == "TCP" and ip_mode == "UDP_SPOOF":
        # TCP bắt buộc hoàn tất 3-way handshake nên không thể dùng UDP_SPOOF.
        ip_mode = "MULTI_IP"

    declared_ip = message.get("source_ip")
    if (
        ip_mode in ("MULTI_IP", "UDP_SPOOF")
        and isinstance(declared_ip, str)
        and is_lab_address(declared_ip)
    ):
        return declared_ip.strip(), ip_mode
    return default_ip, ip_mode


def validate_response(message: dict[str, Any], client_id: str) -> bool:
    return message.get("status") == "OK" and message.get("client_id") == client_id

