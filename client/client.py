"""CLI nhập cấu hình và khởi chạy TCP/UDP Client Simulator."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
project_root_text = str(PROJECT_ROOT)
if project_root_text in sys.path:
    sys.path.remove(project_root_text)
sys.path.insert(0, project_root_text)

from client.simulator import TrafficSimulator
from common import config
from common.network_utils import validate_lab_address, validate_port


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TCP/UDP Client Simulator cho private lab")
    parser.add_argument("--host", help="IP Test Server")
    parser.add_argument("--port", type=int, help="Port Test Server")
    parser.add_argument("--protocol", choices=config.SUPPORTED_PROTOCOLS)
    parser.add_argument("--mode", choices=config.SIMULATION_MODES)
    parser.add_argument("--ip-mode", choices=config.IP_MODES, help="Chế độ IP nguồn (SINGLE_IP, MULTI_IP, UDP_SPOOF)")
    parser.add_argument("--clients", type=int, help="Số logical client/worker")
    parser.add_argument("--rate", type=float, help="Request/giây cho mỗi client")
    parser.add_argument("--duration", type=int, help="Thời gian test (giây)")
    parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Dùng mặc định cho giá trị còn thiếu, không hiện prompt",
    )
    return complete_and_validate(parser.parse_args(), parser)


def complete_and_validate(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> argparse.Namespace:
    if args.non_interactive:
        args.host = config.DEFAULT_HOST if args.host is None else args.host
        args.port = config.DEFAULT_PORT if args.port is None else args.port
        args.protocol = config.DEFAULT_PROTOCOL if args.protocol is None else args.protocol
        args.mode = "NORMAL" if args.mode is None else args.mode
    else:
        args.host = args.host or prompt("Server IP", config.DEFAULT_HOST)
        args.port = args.port if args.port is not None else prompt_int(
            "Server port", config.DEFAULT_PORT
        )
        args.protocol = args.protocol or prompt_choice(
            "Protocol", config.SUPPORTED_PROTOCOLS, config.DEFAULT_PROTOCOL
        )
        args.mode = args.mode or prompt_choice(
            "Mode", config.SIMULATION_MODES, "NORMAL"
        )

    defaults = config.MODE_DEFAULTS[args.mode]
    if args.non_interactive:
        args.clients = defaults["clients"] if args.clients is None else args.clients
        args.rate = defaults["rate"] if args.rate is None else args.rate
        args.duration = defaults["duration"] if args.duration is None else args.duration
    else:
        args.clients = args.clients if args.clients is not None else prompt_int(
            "Number of logical clients", defaults["clients"]
        )
        args.duration = args.duration if args.duration is not None else prompt_int(
            "Duration (seconds)", defaults["duration"]
        )
        args.rate = args.rate if args.rate is not None else prompt_float(
            "Request rate (requests/client/second)", defaults["rate"]
        )

    try:
        args.host = validate_lab_address(args.host)
        args.port = validate_port(args.port)
    except ValueError as exc:
        parser.error(str(exc))

    mode_limits = config.MODE_LIMITS[args.mode]
    max_clients = min(config.MAX_CLIENTS, mode_limits["clients"])
    max_rate = min(config.MAX_REQUEST_RATE, mode_limits["rate"])
    if not 1 <= args.clients <= max_clients:
        parser.error(f"Mode {args.mode} chỉ cho phép 1..{max_clients} logical clients.")
    if not 0 < args.rate <= max_rate:
        parser.error(f"Mode {args.mode} chỉ cho phép rate tối đa {max_rate:g}/client/s.")
    if not 1 <= args.duration <= config.MAX_DURATION:
        parser.error(f"Thời gian test phải từ 1 đến {config.MAX_DURATION} giây.")
    return args


def prompt(label: str, default: str) -> str:
    value = input(f"{label} [{default}]: ").strip()
    return value or default


def prompt_int(label: str, default: int) -> int:
    try:
        return int(prompt(label, str(default)))
    except ValueError as exc:
        raise SystemExit(f"{label} phải là số nguyên.") from exc


def prompt_float(label: str, default: float) -> float:
    try:
        return float(prompt(label, str(default)))
    except ValueError as exc:
        raise SystemExit(f"{label} phải là một số.") from exc


def prompt_choice(label: str, choices: tuple[str, ...], default: str) -> str:
    value = prompt(f"{label} ({'/'.join(choices)})", default).upper()
    if value not in choices:
        raise SystemExit(f"{label} không hợp lệ.")
    return value


def configure_console_encoding() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def main() -> None:
    configure_console_encoding()
    args = parse_args()
    print(
        f"Bắt đầu {args.mode}: protocol={args.protocol}, "
        f"target={args.host}:{args.port}, clients={args.clients}, "
        f"rate={args.rate:g}/client/s, duration={args.duration}s"
    )
    simulator = TrafficSimulator(
        args.host,
        args.port,
        args.protocol,
        args.mode,
        args.clients,
        args.rate,
        args.duration,
        ip_mode=getattr(args, "ip_mode", None),
    )
    simulator.run()


if __name__ == "__main__":
    main()
