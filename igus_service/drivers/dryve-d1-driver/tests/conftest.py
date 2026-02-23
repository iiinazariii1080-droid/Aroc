import os
import sys
from pathlib import Path

def _add_repo_paths() -> None:
    # Make imports work in both layouts:
    # 1) src-layout: repo_root/src/dryve_d1
    # 2) flat-layout: repo_root/dryve_d1
    here = Path(__file__).resolve()
    repo_root = here.parents[1]  # tests/ -> repo root
    src = repo_root / "src"
    if src.exists() and str(src) not in sys.path:
        sys.path.insert(0, str(src))
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

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


@pytest.fixture
def test_config() -> TestConfig:
    """Fixture providing test configuration."""
    return get_test_config()


@pytest.fixture(scope="session")
def drive_config() -> DriveConfig:
    """Fixture providing drive configuration from environment variables.
    
    Session-scoped to ensure all tests use the same configuration.
    """
    host = os.getenv("DRYVE_HOST", "127.0.0.1")
    unit_id = int(os.getenv("DRYVE_UNIT_ID", "0"))
    port = int(os.getenv("DRYVE_PORT", "501"))
    
    logger.info(f"Creating drive config: host={host}, port={port}, unit_id={unit_id}")
    
    connection = ConnectionConfig(host=host, port=port, unit_id=unit_id)
    return DriveConfig(connection=connection)


@pytest_asyncio.fixture(scope="session")
async def drive(drive_config: DriveConfig) -> DryveD1:
    """Fixture providing a connected DryveD1 instance.
    
    Session-scoped: All tests share the same connection to avoid conflicts
    with Modbus devices that only accept one client at a time.
    
    Note: This fixture requires a real device connection.
    Set DRYVE_HOST environment variable to the device IP address.
    Skips test if device is unavailable.
    """
    logger.info(
        f"Creating drive instance: host={drive_config.connection.host}, "
        f"port={drive_config.connection.port}"
    )
    
    cfg = DryveD1Config(drive=drive_config)
    drive_instance = DryveD1(config=cfg)
    
    try:
        logger.info(f"Attempting connection to {drive_config.connection.host}")
        connect_start = time.time()
        
        # Check if port might be busy by attempting connection with short timeout first
        try:
            await drive_instance.connect()
        except (ConnectionRefusedError, OSError) as e:
            error_msg = str(e).lower()
            if "busy" in error_msg or "10048" in error_msg or "address already in use" in error_msg:
                logger.warning(
                    f"Port appears to be busy. This may indicate another process is using "
                    f"the Modbus connection. Error: {e}"
                )
            raise
        
        connect_duration = time.time() - connect_start
        logger.info(f"Connection successful in {connect_duration * 1000:.1f}ms")
        
        # Verify connection by attempting a test read with retry
        # This is more reliable than checking is_connected property
        # Some devices may need a moment after connect before accepting requests
        max_retries = 3
        retry_delay = 0.1
        
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    logger.info(f"Connection verification retry {attempt + 1}/{max_retries}")
                    await asyncio.sleep(retry_delay)
                
                test_read_start = time.time()
                await drive_instance.read_u16(0x6041, 0)  # Read statusword
                test_read_duration = time.time() - test_read_start
                
                logger.info(
                    f"Connection verified (attempt {attempt + 1}, "
                    f"read took {test_read_duration * 1000:.1f}ms)"
                )
                break  # Success, exit retry loop
            except (ConnectionAbortedError, ConnectionError, OSError) as e:
                if attempt == max_retries - 1:
                    logger.error(
                        f"Connection verification failed after {max_retries} attempts: {e}"
                    )
                    await drive_instance.close()
                    pytest.skip(f"Device connection verification failed after {max_retries} attempts: {e}")
                logger.warning(f"Connection verification retry {attempt + 1}/{max_retries}: {e}")
            except Exception as e:
                logger.error(f"Connection verification failed (non-retryable): {e}")
                await drive_instance.close()
                pytest.skip(f"Device connection verification failed: {e}")
        
        logger.info("Drive fixture ready for tests")
        yield drive_instance
        
    except (ConnectionRefusedError, TimeoutError, OSError) as e:
        error_msg = str(e).lower()
        if "busy" in error_msg or "10048" in error_msg or "address already in use" in error_msg:
            logger.error(
                f"Connection refused - port may be busy. Ensure no other process is using "
                f"the Modbus connection. Error: {e}"
            )
        else:
            logger.error(f"Connection failed: {e}")
        
        # Close connection before skipping
        try:
            await drive_instance.close()
        except Exception:
            pass
        
        pytest.skip(f"Device unavailable: {e}")
    except Exception as e:
        logger.error(f"Unexpected connection error: {e}", exc_info=True)
        
        # Close connection before raising
        try:
            await drive_instance.close()
        except Exception:
            pass
        
        raise
    finally:
        logger.info("Closing drive fixture (session cleanup)")
        try:
            await drive_instance.close()
        except Exception as e:
            logger.warning(f"Error during drive fixture cleanup: {e}")
