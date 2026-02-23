"""File utilities."""
import time
from pathlib import Path

from constants import DEFAULT_CERT_STORAGE_DIR

TEMP_FILE_MAX_AGE = 3600  # 1 hour in seconds


def cleanup_old_temp_files(
    directory: Path | None = None,
    max_age: int = TEMP_FILE_MAX_AGE
) -> int:
    """
    Clean up old temporary files.

    Args:
        directory: Directory to clean (default: CERT_STORAGE_DIR)
        max_age: Maximum age of temp files in seconds

    Returns:
        Number of files deleted
    """
    if directory is None:
        directory = DEFAULT_CERT_STORAGE_DIR

    if not directory.exists():
        return 0

    deleted_count = 0
    current_time = time.time()

    for file_path in directory.glob("*.tmp"):
        try:
            file_age = current_time - file_path.stat().st_mtime
            if file_age > max_age:
                file_path.unlink()
                deleted_count += 1
        except (OSError, FileNotFoundError):
            # File was already deleted or doesn't exist
            pass

    return deleted_count

