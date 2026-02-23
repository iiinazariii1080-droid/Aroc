"""
Конфигурация для FastAPI сервера
"""
import os

# Настройки сервера
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", 8401))

# Настройки CORS
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
_HOST = os.getenv("HOST", "192.168.1.10")
# Настройки API Gateway
API_GATEWAY_URL = os.getenv("API_GATEWAY_URL", f"http://{_HOST}:8201/api")
WS_XARM_BACKEND_URL = os.getenv("WS_XARM_BACKEND_URL", f"ws://192.168.1.220:18333/ws")
COLOR_CAMERA_URL = os.getenv("COLOR_CAMERA_URL", f"http://{_HOST}:7000")
DEPTH_CAMERA_URL = os.getenv("DEPTH_CAMERA_URL", f"http://{_HOST}:8106/depth")

# Базовый URL для страниц просмотра камер (удалённый хост с view.html)
CAMERA_VIEW_BASE_URL = os.getenv("CAMERA_VIEW_BASE_URL", "http://192.168.1.55:8000")


# Local development URLs (commented out)
# API_GATEWAY_URL = os.getenv("API_GATEWAY_URL", "http://127.0.0.1:8201/api")
# WS_XARM_BACKEND_URL = os.getenv("WS_XARM_BACKEND_URL", "ws://127.0.0.1:18333/ws")
# COLOR_CAMERA_WS_URL = os.getenv("COLOR_CAMERA_WS_URL", "ws://127.0.0.1:8104/color")
# DEPTH_CAMERA_WS_URL = os.getenv("DEPTH_CAMERA_WS_URL", "ws://127.0.0.1:8104/depth")