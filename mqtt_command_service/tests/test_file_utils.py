"""Tests for file_utils (app/utils/file_utils.py)."""
import time

import pytest

from app.utils.file_utils import cleanup_old_temp_files


@pytest.fixture
def temp_dir(tmp_path):
    return tmp_path


class TestCleanupOldTempFiles:
    def test_deletes_old_tmp_files(self, temp_dir):
        old_file = temp_dir / "old.tmp"
        old_file.write_text("data")
        # Backdate mtime by 2 hours
        old_mtime = time.time() - 7200
        import os
        os.utime(old_file, (old_mtime, old_mtime))
        deleted = cleanup_old_temp_files(directory=temp_dir, max_age=3600)
        assert deleted == 1
        assert not old_file.exists()

    def test_keeps_recent_tmp_files(self, temp_dir):
        recent = temp_dir / "recent.tmp"
        recent.write_text("data")
        deleted = cleanup_old_temp_files(directory=temp_dir, max_age=3600)
        assert deleted == 0
        assert recent.exists()

    def test_ignores_non_tmp_files(self, temp_dir):
        non_tmp = temp_dir / "data.txt"
        non_tmp.write_text("keep me")
        old_mtime = time.time() - 7200
        import os
        os.utime(non_tmp, (old_mtime, old_mtime))
        deleted = cleanup_old_temp_files(directory=temp_dir, max_age=3600)
        assert deleted == 0
        assert non_tmp.exists()

    def test_nonexistent_directory_returns_zero(self, tmp_path):
        missing = tmp_path / "nonexistent"
        deleted = cleanup_old_temp_files(directory=missing, max_age=3600)
        assert deleted == 0

    def test_empty_directory_returns_zero(self, temp_dir):
        deleted = cleanup_old_temp_files(directory=temp_dir, max_age=3600)
        assert deleted == 0

    def test_multiple_old_files(self, temp_dir):
        import os
        old_mtime = time.time() - 7200
        for i in range(5):
            f = temp_dir / f"file{i}.tmp"
            f.write_text(f"data{i}")
            os.utime(f, (old_mtime, old_mtime))
        deleted = cleanup_old_temp_files(directory=temp_dir, max_age=3600)
        assert deleted == 5
