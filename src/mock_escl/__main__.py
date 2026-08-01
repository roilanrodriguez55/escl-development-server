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
from .jobs import JobManager
from .server import create_app

LOGGER = logging.getLogger("mock_escl")

# A single log format used everywhere so timestamps, levels, and logger
# names are aligned for grep'ing.
LOG_FORMAT = "%(asctime)s.%(msecs)03d [%(levelname)-7s] %(name)s: %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Loggers we always set to the user-requested level, regardless of what
# uvicorn configures for its own loggers.
APP_LOGGERS: tuple[str, ...] = (
    "mock_escl",
    "mock_escl.server",
    "mock_escl.jobs",
    "mock_escl.discovery",
    "mock_escl.config",
)


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
        help="Log level for the application and uvicorn.",
    )
    parser.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help=(
            "Optional path to a log file. The file always captures everything "
            "(DEBUG and up); stdout respects --log-level."
        ),
    )
    parser.add_argument(
        "--no-mdns",
        action="store_true",
        help="Disable mDNS advertisement (server is still reachable via HTTP).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help=(
            "Seed for deterministic scans. When set, the synthetic text "
            "content and timestamps become byte-stable for a given job_id."
        ),
    )
    return parser.parse_args()


def configure_logging(log_level: str, log_file: Path | None) -> None:
    """Wire up stdout + optional file logging, with our preferred format.

    Without this, ``--log-level debug`` only affects Uvicorn's own loggers —
    our ``mock_escl.*`` loggers stay at the ``basicConfig`` default of WARNING
    and the diagnostic output never appears.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)
    # Wipe handlers uvicorn may have pre-installed.
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT)

    stdout = logging.StreamHandler(sys.stdout)
    stdout.setLevel(level)
    stdout.setFormatter(formatter)
    root.addHandler(stdout)

    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = logging.FileHandler(log_file, encoding="utf-8")
        except OSError as exc:
            sys.stderr.write(f"WARNING: cannot open log file {log_file}: {exc}\n")
        else:
            file_handler.setLevel(logging.DEBUG)
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)

    # Force every app logger to honor the requested level — without this
    # Uvicorn's startup hook can leave them at WARNING.
    for name in APP_LOGGERS:
        logging.getLogger(name).setLevel(level)

    # Quiet down a couple of noisy third-party loggers at non-debug levels.
    if level > logging.DEBUG:
        logging.getLogger("zeroconf").setLevel(logging.WARNING)


def main() -> int:
    args = parse_args()
    configure_logging(args.log_level, args.log_file)

    if not args.config.exists():
        LOGGER.error("Configuration file not found: %s", args.config)
        return 1

    config = ScannerConfig.load(args.config)

    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port

    LOGGER.info(
        "Starting mock eSCL scanner: name=%s manufacturer=%s model=%s uuid=%s",
        config.name,
        config.manufacturer,
        config.model,
        config.uuid,
    )
    LOGGER.debug(
        "Loaded config: host=%s port=%d delay=%.1fs adf=%s duplex=%s "
        "color_modes=%s resolutions=%s max_size=%dx%dmm",
        config.host,
        config.port,
        config.delay_seconds,
        config.adf_enabled,
        config.duplex_supported,
        config.color_modes,
        config.resolutions,
        config.max_width_mm,
        config.max_height_mm,
    )

    app = create_app(config, seed=args.seed)
    advertiser = MdnsAdvertiser(config) if not args.no_mdns else None

    server = uvicorn.Server(
        uvicorn.Config(
            app=app,
            host=config.host,
            port=config.port,
            log_level=args.log_level,
            access_log=True,
            server_header=False,
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
    if args.seed is not None:
        LOGGER.info("Deterministic scans enabled (seed=%d)", args.seed)

    try:
        server.run()
    finally:
        if advertiser is not None:
            advertiser.stop()
        LOGGER.info("Server stopped")

    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["main", "parse_args"]
