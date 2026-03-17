"""Tests for app/services/v4l2.py — V4L2 control wrappers."""
from __future__ import annotations

from unittest.mock import patch

from app.services.v4l2 import list_v4l2_modes

V4L2_LIST_FMT_OUTPUT = """\
ioctl: VIDIOC_ENUM_FMT
	Type: Video Capture

	[0]: 'YUYV' (YUYV 4:2:2)
		Size: Discrete 640x480
			Interval: Discrete 0.033s (30.000 fps)
			Interval: Discrete 0.040s (25.000 fps)
		Size: Discrete 1280x720
			Interval: Discrete 0.100s (10.000 fps)
	[1]: 'MJPG' (Motion-JPEG, compressed)
		Size: Discrete 1920x1080
			Interval: Discrete 0.033s (30.000 fps)
"""


class TestListV4l2Modes:
    @patch("app.services.v4l2.run_cmd", return_value=V4L2_LIST_FMT_OUTPUT)
    def test_parses_yuyv_modes(self, mock_run):
        result = list_v4l2_modes("/dev/video0")
        assert result["pixel_format"] == "YUYV"
        modes = result["modes"]
        assert len(modes) >= 2
        # 640x480 at 25+30 fps
        m640 = next(m for m in modes if m["width"] == 640)
        assert 30 in m640["fps"]
        assert 25 in m640["fps"]

    @patch("app.services.v4l2.run_cmd", return_value="")
    def test_empty_output(self, mock_run):
        result = list_v4l2_modes("/dev/video0")
        assert result["modes"] == []
