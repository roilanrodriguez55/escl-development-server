"""mDNS/DNS-SD advertisement of the mock scanner."""

from __future__ import annotations

import logging
import socket

from zeroconf import ServiceInfo, Zeroconf

from .config import ScannerConfig

LOGGER = logging.getLogger("mock_escl.discovery")


def get_local_ip() -> str:
    """Return the IP address of the outbound network interface.

    Uses the standard "connect a UDP socket to a public address" trick.
    No traffic actually leaves the host.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    finally:
        sock.close()


def build_txt_record(config: ScannerConfig, address: str) -> dict[bytes, bytes]:
    """Construct the DNS-SD TXT record for the mock scanner.

    Keys mirror what real eSCL/AirScan devices advertise so that client
    applications (`sane-airscan`, macOS Image Capture, iOS Notes, Windows
    Scanner Service, etc.) recognize the device as a generic scanner.
    """
    admin_uri = f"http://{address}:{config.port}".encode()

    color_support = ",".join(
        "color" if m == "RGB24" else "grayscale"
        for m in config.color_modes
    )

    return {
        b"txtvers": b"1",
        b"adminurl": admin_uri,
        b"representation": b"image/jpeg,image/png,image/tiff,application/pdf",
        b"protocol": b"uscan",
        b"rs": b"eSCL",
        b"cs": color_support.encode(),
        b"pdl": b"application/pdf,image/jpeg,image/png,image/tiff",
        b"is": b"platen" + (b",adf" if config.adf_enabled else b""),
        b"uuid": config.uuid.encode(),
        b"ty": config.model.encode(),
        b"note": config.name.encode(),
        b"mfg": config.manufacturer.encode(),
        b"mdl": config.model.encode(),
        b"duplex": b"F",
        b"Scan": b"1",
    }


def _server_name(config: ScannerConfig) -> str:
    """Return a stable, dot-separated mDNS server hostname."""
    base = config.name.replace(" ", "-")
    return f"{base}.local."


def build_service_info(config: ScannerConfig, address: str) -> list[ServiceInfo]:
    """Build the list of ServiceInfo objects to advertise.

    Always advertises the base service (e.g. ``_uscan._tcp.local.``).
    When the base service is the canonical eSCL/AirScan type, also
    advertises a ``_universal`` subtype so that broad-casting clients
    (notably macOS/iOS) can pick the device up.
    """
    txt = build_txt_record(config, address)
    port = config.port
    addresses = [socket.inet_aton(address)]
    server = _server_name(config)

    services: list[ServiceInfo] = [
        ServiceInfo(
            type_=config.service_type,
            name=f"{config.name}.{config.service_type}",
            addresses=addresses,
            port=port,
            properties=txt,
            server=server,
        )
    ]

    if config.service_type == "_uscan._tcp.local.":
        universal_info = ServiceInfo(
            type_="_universal._sub._uscan._tcp.local.",
            name=f"{config.name}._universal._sub._uscan._tcp.local.",
            addresses=addresses,
            port=port,
            properties=txt,
            server=server,
        )
        services.append(universal_info)

    return services


class MdnsAdvertiser:
    """Lifecycles a Zeroconf instance and registers the eSCL service(s)."""

    def __init__(self, config: ScannerConfig) -> None:
        self.config = config
        self._zeroconf = Zeroconf()
        self._services: list[ServiceInfo] = []

    def start(self) -> None:
        address = get_local_ip()
        LOGGER.info("Advertising on mDNS at %s:%s", address, self.config.port)

        for service in build_service_info(self.config, address):
            self._zeroconf.register_service(service)
            self._services.append(service)
            LOGGER.info("Registered: %s", service.name)

    def stop(self) -> None:
        for service in self._services:
            try:
                self._zeroconf.unregister_service(service)
            except Exception as exc:  # noqa: BLE001 - shutdown best-effort
                LOGGER.warning("Failed to unregister %s: %s", service.name, exc)
        try:
            self._zeroconf.close()
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning("Failed to close Zeroconf: %s", exc)
        self._services = []


__all__ = [
    "get_local_ip",
    "build_txt_record",
    "build_service_info",
    "MdnsAdvertiser",
]