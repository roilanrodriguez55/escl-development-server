"""Shared pytest fixtures for the mock eSCL test suite.

We use FastAPI's ``TestClient`` (built on httpx + ASGI) for synchronous tests.
The app is built once per test module and seeded so the rendered bytes are
byte-stable — that way snapshot-style assertions on sizes and magic bytes are
deterministic.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Iterator

import pytest
from fastapi.testclient import TestClient

from mock_escl.config import ScannerConfig
from mock_escl.server import create_app

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = REPO_ROOT / "config" / "scanner.json"


@pytest.fixture
def config() -> ScannerConfig:
    """Load the bundled ``config/scanner.json`` (A4 platen, RGB24+Gray)."""
    return ScannerConfig.load(DEFAULT_CONFIG_PATH)


@pytest.fixture
def adf_config(config: ScannerConfig) -> ScannerConfig:
    """A copy of the default config with ADF + duplex enabled."""
    return config.model_copy(
        update={"adf_enabled": True, "duplex_supported": True}
    )


@pytest.fixture
def color_config(config: ScannerConfig) -> ScannerConfig:
    """A copy of the default config with all 3 color modes enabled."""
    return config.model_copy(
        update={"color_modes": ["RGB24", "Grayscale8", "BlackAndWhite1"]}
    )


@pytest.fixture
def client(config: ScannerConfig) -> Iterator[TestClient]:
    """A TestClient backed by an app with seed=42 (deterministic scans)."""
    app = create_app(config, seed=42)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def adf_client(adf_config: ScannerConfig) -> Iterator[TestClient]:
    """A TestClient with ADF + duplex enabled."""
    app = create_app(adf_config, seed=42)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def color_client(color_config: ScannerConfig) -> Iterator[TestClient]:
    """A TestClient with all 3 color modes enabled."""
    app = create_app(color_config, seed=42)
    with TestClient(app) as c:
        yield c


def run_async(coro):
    """Drive an awaitable to completion from synchronous test code.

    TestClient itself is sync, but the JobManager internals schedule work
    via ``asyncio.create_task``; we don't need to await it manually because
    the TestClient runs the ASGI loop synchronously between requests.
    Kept as a helper in case a test needs to call a coroutine directly.
    """
    return asyncio.get_event_loop().run_until_complete(coro)
