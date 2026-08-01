"""Configuration loader for the mock eSCL scanner."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class ScannerConfig(BaseModel):
    """Runtime configuration for the mock scanner.

    All fields can be overridden from the JSON file at `config/scanner.json`
    or via CLI flags (`--host`, `--port`).
    """

    host: str = "0.0.0.0"
    port: int = 8080

    name: str = "Python Mock eSCL Scanner"
    manufacturer: str = "Mock Inc."
    model: str = "ESCL-2000"
    serial: str = "MOCK-001"
    uuid: str = "00000000-0000-0000-0000-000000000001"

    color_modes: list[str] = Field(
        default_factory=lambda: ["RGB24", "Grayscale8"]
    )
    resolutions: list[int] = Field(
        default_factory=lambda: [75, 150, 200, 300, 600]
    )

    max_width_mm: int = 210
    max_height_mm: int = 297

    default_format: str = "image/png"
    delay_seconds: float = 0.0

    pages_total: int = 1
    """Number of pages per ADF job. 1 = single-page platen scan.
    Set higher to simulate a feeder delivering multiple pages."""

    service_type: str = "_uscan._tcp.local."

    adf_enabled: bool = False
    duplex_supported: bool = False

    @classmethod
    def load(cls, path: Path) -> "ScannerConfig":
        """Load configuration from a JSON file."""
        return cls.model_validate_json(path.read_text(encoding="utf-8"))