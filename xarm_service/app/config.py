import os
from typing import List, Tuple

XARM_IP = os.getenv("XARM_IP", "192.168.1.220")
WS_XARM_BACKEND_URL = os.getenv("WS_XARM_BACKEND_URL", f"ws://{XARM_IP}:18333/ws")
WS_CHECK_DISABLED: bool = os.getenv("WS_CHECK_DISABLED", "0").strip().lower() in ("1", "true", "yes", "on")

# Workspace (WS) — 40×90×120 cm, all values in mm
WS_SIZE_MM: Tuple[float, float, float] = (400.0, 900.0, 1200.0)  # X, Y, Z
BASE_IN_WS_MM: Tuple[float, float, float] = (150.0, 450.0, 0.0)  # base position in WS frame
WS_MARGIN_MM: float = float(os.getenv("WS_MARGIN_MM", "40"))
# T_ws_base: identity rotation (axes aligned); translation = base_in_ws
WS_ROTATION_RPY_DEG: Tuple[float, float, float] = (0.0, 0.0, 0.0)

# Motion limits (from WORKSPACE_SAFETY_ENVELOPE_V3)
TCP_SPEED_MM_S: float = float(os.getenv("TCP_SPEED_MM_S", "150"))
TCP_ACC_MM_S2: float = float(os.getenv("TCP_ACC_MM_S2", "800"))
TCP_JERK_MM_S3: float = float(os.getenv("TCP_JERK_MM_S3", "4000"))
JOINT_SPEED_DEG_S: float = float(os.getenv("JOINT_SPEED_DEG_S", "40"))
JOINT_ACC_DEG_S2: float = float(os.getenv("JOINT_ACC_DEG_S2", "150"))
# Safe high pose (joints deg) — inside WS; use for pick/place template
SAFE_HIGH_POSE_JOINTS: Tuple[float, ...] = (0.0, 0.0, 0.0, 90.0, 0.0, 0.0)

# ── Smart Grasp pipeline ───────────────────────────────────────────────────
DEPTH_BASE_URL: str = os.getenv("DEPTH_BASE_URL", "http://192.168.1.55:8000")
DEPTH_FRAME_WIDTH: int = 640
DEPTH_FRAME_HEIGHT: int = 480
DEPTH_FRAME_BYTES: int = DEPTH_FRAME_WIDTH * DEPTH_FRAME_HEIGHT * 2  # uint16
DEPTH_HTTP_TIMEOUT_S: float = float(os.getenv("DEPTH_HTTP_TIMEOUT_S", "10"))

# Camera intrinsics (RealSense depth)
CAMERA_FX: float = float(os.getenv("CAMERA_FX", "380.4253845214844"))
CAMERA_FY: float = float(os.getenv("CAMERA_FY", "380.4253845214844"))
CAMERA_CX: float = float(os.getenv("CAMERA_CX", "320.0"))
CAMERA_CY: float = float(os.getenv("CAMERA_CY", "240.0"))
DEPTH_SCALE: float = float(os.getenv("DEPTH_SCALE", "0.9"))

# Grasp behaviour
GRASP_MAX_RETRIES: int = int(os.getenv("GRASP_MAX_RETRIES", "3"))
GRASP_APPROACH_SPEED_PCT: float = float(os.getenv("GRASP_APPROACH_SPEED_PCT", "15"))
GRASP_MOVE_SPEED_PCT: float = float(os.getenv("GRASP_MOVE_SPEED_PCT", "30"))
GRASP_LIFT_HEIGHT_MM: float = float(os.getenv("GRASP_LIFT_HEIGHT_MM", "50"))
GRASP_RETRACT_MM: float = float(os.getenv("GRASP_RETRACT_MM", "10"))
VACUUM_SETTLE_TIME_S: float = float(os.getenv("VACUUM_SETTLE_TIME_S", "0.5"))
VACUUM_POST_LIFT_VERIFY_S: float = float(os.getenv("VACUUM_POST_LIFT_VERIFY_S", "1.0"))

# Torque‑based contact detection thresholds (Nm)
TORQUE_CONTACT_THRESHOLD: float = float(os.getenv("TORQUE_CONTACT_THRESHOLD", "1.5"))
TORQUE_GRIP_THRESHOLD: float = float(os.getenv("TORQUE_GRIP_THRESHOLD", "0.3"))

# Depth detection parameters
DEPTH_MIN_RANGE_MM: int = int(os.getenv("DEPTH_MIN_RANGE_MM", "100"))  # camera blind zone
DEPTH_SEGMENT_THRESHOLD_MM: int = int(os.getenv("DEPTH_SEGMENT_THRESHOLD_MM", "10"))
DEPTH_MIN_CONTOUR_AREA_PX: int = int(os.getenv("DEPTH_MIN_CONTOUR_AREA_PX", "200"))
DEPTH_AVG_FRAMES: int = int(os.getenv("DEPTH_AVG_FRAMES", "5"))

# Calibration: true_distance ≈ a * measured + b  (mm)
DEPTH_CALIB_A: float = float(os.getenv("DEPTH_CALIB_A", "0.957"))
DEPTH_CALIB_B: float = float(os.getenv("DEPTH_CALIB_B", "45.2"))

# Smart grasp global timeout
SMART_GRASP_TIMEOUT_S: float = float(os.getenv("SMART_GRASP_TIMEOUT_S", "90"))

# ── Gripper idle-vacuum watchdog ───────────────────────────────────────────
# Auto-release vacuum after this many seconds if no part is detected.
# Only fires when vacuum is ON but sensor reads VACUUM_NO_PART / OFF / ERROR.
# Set to 0 to disable.  Override with env GRIPPER_IDLE_TIMEOUT_S.
GRIPPER_IDLE_TIMEOUT_S: float = float(os.getenv("GRIPPER_IDLE_TIMEOUT_S", "120"))
GRIPPER_WATCHDOG_ENABLED: bool = os.getenv(
    "GRIPPER_WATCHDOG_ENABLED", "1"
).strip().lower() in ("1", "true", "yes", "on")

