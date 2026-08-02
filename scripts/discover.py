#!/usr/bin/env python3
"""Browse the LAN for eSCL services and print a human-readable summary.

Used by ``make discover``. Works on Linux, macOS, and Windows because it
talks to mDNS via zeroconf instead of relying on avahi-utils or dns-sd.
"""

import time

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf


class _L(ServiceListener):
    def __init__(self) -> None:
        self.found: list[tuple[str, str, int, dict]] = []

    def update_service(self, *args, **kwargs) -> None:
        pass

    def remove_service(self, *args, **kwargs) -> None:
        pass

    def add_service(self, zc: Zeroconf, stype: str, name: str) -> None:
        info = zc.get_service_info(stype, name)
        if info is None:
            return
        addr = ".".join(str(b) for b in info.addresses[0])
        txt = {
            k.decode(): v.decode() if isinstance(v, bytes) else v
            for k, v in (info.properties or {}).items()
        }
        self.found.append((name, addr, info.port, txt))


def main() -> None:
    zc = Zeroconf()
    listener = _L()
    ServiceBrowser(zc, "_uscan._tcp.local.", listener)
    time.sleep(2.0)
    zc.close()
    print(f"Found {len(listener.found)} eSCL service(s):")
    for name, addr, port, txt in listener.found:
        print(f"  {name}")
        print(f"    address: {addr}:{port}")
        for key in ("ty", "mfg", "mdl", "pdl", "cs", "is", "duplex",
                    "priority", "uuid"):
            if key in txt:
                print(f"    {key}: {txt[key]}")


if __name__ == "__main__":
    main()
