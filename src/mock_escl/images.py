"""Standalone helper for writing a static "scanned" image to disk.

This module is not used by the running server. It exists so a developer
can quickly produce a sample PNG/JPEG artifact outside the HTTP path:

    python -m mock_escl.images --output sample.png
"""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .config import ScannerConfig
from .jobs import JobManager


def render_simple_test_image(
    config: ScannerConfig,
    output_path: Path,
    resolution: int = 300,
) -> Path:
    """Write a static synthetic scan to ``output_path`` and return it."""
    mm_per_inch = 25.4
    width_px = int((config.max_width_mm / mm_per_inch) * resolution)
    height_px = int((config.max_height_mm / mm_per_inch) * resolution)

    image = Image.new("RGB", (width_px, height_px), "white")
    draw = ImageDraw.Draw(image)
    font = JobManager._load_font(int(resolution / 4))

    draw.text((50, 50), f"Mock eSCL — {config.name}", fill="black", font=font)
    draw.text(
        (50, 110),
        datetime.now().isoformat(timespec="seconds"),
        fill="black",
        font=font,
    )

    image.save(output_path, format="PNG")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render a sample synthetic scan to disk."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config/scanner.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("sample-scan.png"),
    )
    parser.add_argument(
        "--resolution",
        type=int,
        default=150,
    )
    args = parser.parse_args()

    config = ScannerConfig.load(args.config)
    path = render_simple_test_image(config, args.output, args.resolution)
    print(f"Wrote sample scan to {path}")


if __name__ == "__main__":
    main()


__all__ = ["render_simple_test_image"]