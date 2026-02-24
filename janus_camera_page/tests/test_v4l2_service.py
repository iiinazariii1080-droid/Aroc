"""Tests for app/services/v4l2.py — V4L2 control wrappers."""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.v4l2 import is_supported, list_v4l2_ctrls, list_v4l2_modes, v4l2_current


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

V4L2_GET_FMT_OUTPUT = """\
Format Video Capture:
	Width/Height      : 640/480
	Pixel Format      : 'YUYV'
	Field             : None
"""

V4L2_GET_PRM_OUTPUT = """\
Streaming Parameters Video Capture:
	Capabilities     : timeperframe
	Frames per second: 30.000
"""

V4L2_LIST_CTRLS_OUTPUT = """\
                     brightness (int)    : min=0 max=255 step=1 default=128 value=128
                     contrast (int)    : min=0 max=255 step=1 default=128 value=140
                     exposure_auto (menu)   : min=0 max=3 step=1 default=3 value=3
"""


class TestListV4l2Modes:
    @patch("app.services.v4l2.run", return_value=V4L2_LIST_FMT_OUTPUT)
    def test_parses_yuyv_modes(self, mock_run):
        result = list_v4l2_modes("/dev/video0")
        assert result["pixel_format"] == "YUYV"
        modes = result["modes"]
        assert len(modes) >= 2
        # 640x480 at 25+30 fps
        m640 = next(m for m in modes if m["width"] == 640)
        assert 30 in m640["fps"]
        assert 25 in m640["fps"]

    @patch("app.services.v4l2.run", return_value="")
    def test_empty_output(self, mock_run):
        result = list_v4l2_modes("/dev/video0")
        assert result["modes"] == []


class TestIsSupported:
    def test_match(self):
        modes = {"modes": [{"width": 640, "height": 480, "fps": [30, 25]}]}
        assert is_supported(modes, 640, 480, 30) is True

    def test_no_match(self):
        modes = {"modes": [{"width": 640, "height": 480, "fps": [30]}]}
        assert is_supported(modes, 1920, 1080, 60) is False


class TestV4l2Current:
    @patch("app.services.v4l2.run")
    def test_parses_fmt_and_parm(self, mock_run):
        mock_run.side_effect = [V4L2_GET_FMT_OUTPUT, V4L2_GET_PRM_OUTPUT]
        result = v4l2_current("/dev/video0")
        assert result["width"] == 640
        assert result["height"] == 480
        assert result["fps"] == 30
        assert result["pixfmt"] == "YUYV"


class TestListV4l2Ctrls:
    @patch("app.services.v4l2.run", return_value=V4L2_LIST_CTRLS_OUTPUT)
    def test_parses_controls(self, mock_run):
        result = list_v4l2_ctrls("/dev/video0")
        controls = result["controls"]
        assert "brightness" in controls
        assert controls["brightness"]["default"] == 128
