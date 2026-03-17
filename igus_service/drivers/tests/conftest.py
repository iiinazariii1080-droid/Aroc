import os
import sys
from pathlib import Path

def _add_repo_paths() -> None:
    # Make imports work in both layouts:
    # 1) src-layout: repo_root/src/dryve_d1
    # 2) flat-layout: repo_root/dryve_d1
    here = Path(__file__).resolve()
    tests_dir = here.parent          # drivers/tests/
    repo_root = here.parents[1]      # drivers/
    src = repo_root / "src"
    if src.exists() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    # Allow ``from test_utils.xxx`` imports in integration/property tests.
    if str(tests_dir) not in sys.path:
        sys.path.insert(0, str(tests_dir))

_add_repo_paths()

import asyncio
import logging
import time

import pytest
import pytest_asyncio
from drivers.dryve_d1.api.drive import DryveD1, DryveD1Config
from drivers.dryve_d1.config.models import DriveConfig, ConnectionConfig
from test_utils.config import TestConfig, get_test_config

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session")
def test_config() -> TestConfig:
    """Test configuration with timeouts, tolerances, and polling intervals."""
    return get_test_config()


@pytest.fixture(scope="session")
def drive_config() -> DriveConfig:
    """Fixture providing drive configuration from environment variables."""
    host = os.getenv("DRYVE_HOST", "127.0.0.1")
    unit_id = int(os.getenv("DRYVE_UNIT_ID", "0"))
    port = int(os.getenv("DRYVE_PORT", "502"))
    connection = ConnectionConfig(host=host, port=port, unit_id=unit_id)
    return DriveConfig(connection=connection)


@pytest_asyncio.fixture(scope="session")
async def drive(drive_config: DriveConfig) -> DryveD1:
    """Fixture providing a connected DryveD1 instance.

    Session-scoped: All tests share the same connection.
    Skips test if device is unavailable.
    """
    cfg = DryveD1Config(drive=drive_config)
    drive_instance = DryveD1(config=cfg)

    try:
        await drive_instance.connect()

        # Verify connection with retry
        for attempt in range(3):
            try:
                if attempt > 0:
                    await asyncio.sleep(0.1)
                await drive_instance.read_u16(0x6041, 0)
                break
            except Exception as e:
                if attempt == 2:
                    await drive_instance.close()
                    pytest.skip(f"Device connection verification failed: {e}")

        yield drive_instance

    except (ConnectionRefusedError, TimeoutError, OSError) as e:
        try:
            await drive_instance.close()
        except Exception:
            pass
        pytest.skip(f"Device unavailable: {e}")
    finally:
        try:
            await drive_instance.close()
        except Exception:
            pass
