"""Compatibility module; code mới sử dụng trực tiếp package common."""

from common.network_utils import is_lab_address, validate_lab_address
from common.protocol import (
    ProtocolError,
    create_error,
    create_request,
    create_response,
    decode_json,
    encode_json,
    receive_tcp_message,
    utc_now,
    validate_request,
    validate_response,
)

__all__ = [
    "ProtocolError",
    "create_error",
    "create_request",
    "create_response",
    "decode_json",
    "encode_json",
    "is_lab_address",
    "receive_tcp_message",
    "utc_now",
    "validate_lab_address",
    "validate_request",
    "validate_response",
]
