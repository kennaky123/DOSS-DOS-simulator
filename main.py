"""Entry point tiện lợi; vẫn có thể chạy trực tiếp server.py/client.py."""

from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="Network DoS/DDoS Lab Simulator")
    parser.add_argument("component", choices=("server", "client", "web"))
    args, remaining = parser.parse_known_args()
    sys.argv = [sys.argv[0], *remaining]

    if args.component == "server":
        from server.server import main as run_server

        run_server()
    elif args.component == "client":
        from client.client import main as run_client

        run_client()
    else:
        from web.app import main as run_web

        run_web()


if __name__ == "__main__":
    main()
