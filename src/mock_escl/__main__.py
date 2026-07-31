"""Command-line entry point for the mock eSCL scanner server."""

from __future__ import annotations

import argparse
import logging
import signal
import sys
from pathlib import Path

import uvicorn

from .config import ScannerConfig
from .discovery import MdnsAdvertiser
from .server import create_app

LOGGER = logging.getLogger("mock_escl")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mock-escl",
        description=(
            "Run a local mock eSCL/AirScan scanner server. "
            "The server announces itself via mDNS so real client apps "
            "on the LAN can discover and use it."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/scanner.json"),
        help="Path to the scanner configuration JSON (default: %(default)s).",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="Override the bind host from the config file.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Override the bind port from the config file.",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="Log level for uvicorn and the application.",
    )
    parser.add_argument(
        "--no-mdns",
        action="store_true",
        help="Disable mDNS advertisement (server is still reachable via HTTP).",
    )
    return parser.parse_args()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    args = parse_args()

    if not args.config.exists():
        LOGGER.error("Configuration file not found: %s", args.config)
        return 1

    config = ScannerConfig.load(args.config)

    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port

    app = create_app(config)
    advertiser = MdnsAdvertiser(config) if not args.no_mdns else None

    server = uvicorn.Server(
        uvicorn.Config(
            app=app,
            host=config.host,
            port=config.port,
            log_level=args.log_level,
            access_log=True,
        )
    )

    def shutdown(_signum, _frame) -> None:
        LOGGER.info("Shutdown signal received")
        server.should_exit = True

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    if advertiser is not None:
        advertiser.start()

    LOGGER.info(
        "Capabilities: http://%s:%d/eSCL/ScannerCapabilities",
        config.host,
        config.port,
    )

    try:
        server.run()
    finally:
        if advertiser is not None:
            advertiser.stop()

    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main", "parse_args"]