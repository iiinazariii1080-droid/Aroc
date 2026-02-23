# Инструкция для деплоя на 192.168.1.55 (depth camera Pi)

## Цель

Добавить HTTP-эндпоинт `/depth/color_frame`, который возвращает **реальный RGB-кадр** с цветного сенсора RealSense D435 (а не depth colormap из `rs.colorizer()`).

Текущий эндпоинт `/depth/frame_color_overlay` возвращает **depth colormap** (раскрашенную карту глубины), а НЕ реальные цвета. Нам нужен новый эндпоинт с настоящими RGB-пикселями.

## Архитектура

На Pi (192.168.1.55) работают два сервиса:

1. **realsense_mux** (порт 8000) — Python-скрипт, который:
   - Запускает RealSense pipeline (color + depth)
   - Пишет кадры в FIFO-файлы для Janus WebRTC
   - Поднимает FastAPI на порту 8000 с единственным эндпоинтом `GET /depth?x=&y=`

2. **cam-control** (порт 8900) — FastAPI-приложение (`janus_camera_page/`):
   - Управление камерой, конфигурация, Janus proxy
   - Проксирует `GET /depth` к localhost:8000
   - Реализует `GET /depth/frame` и `GET /depth/frame_color_overlay`

## Что нужно сделать

### Файл 1: `realsense_mux.py`

Нужно найти файл `realsense_mux.py` (скорее всего в `/home/boris/janus_camera_page/` или `/opt/camera/` или аналогичном пути).

#### Изменение 1a: Расширить класс `CameraService`

В классе `CameraService` (сразу после `self._updated_at = 0.0` в `__init__`) добавить хранение цветного кадра:

```python
class CameraService:
    """
    Хранит *уже повернутый* depth-кадр (float32, метры) и даёт глубину по нормализованным координатам [0..1].
    Также хранит последний цветной кадр (RGB24, uint8) для colour-overlay.
    """
    def __init__(self, rotate="none", flip_x=False, flip_y=False):
        self.rotate = rotate
        self.flip_x = flip_x
        self.flip_y = flip_y
        self._lock = threading.Lock()
        self._depth_m = None
        self._shape = None
        self._updated_at = 0.0
        # ---- НОВОЕ: Real RGB colour frame ----
        self._color_rgb: Optional[np.ndarray] = None
        self._color_shape: Optional[tuple] = None
        self._color_updated_at: float = 0.0
```

Добавить три новых метода (после существующего `update_depth_from_z16`):

```python
    def update_color_rgb(self, rgb: np.ndarray):
        """Store the latest colour frame (uint8 HxWx3, already rotated)."""
        with self._lock:
            self._color_rgb = rgb
            self._color_shape = rgb.shape[:2]
            self._color_updated_at = time.time()

    def get_color_frame(self) -> Optional[np.ndarray]:
        """Return the latest colour frame or None."""
        with self._lock:
            return self._color_rgb.copy() if self._color_rgb is not None else None

    def get_color_timestamp(self) -> float:
        with self._lock:
            return self._color_updated_at
```

> **Важно:** `Optional` может быть ещё не импортирован — проверь что `from typing import ..., Optional` есть в файле.

#### Изменение 1b: Сохранять цветной кадр в pipeline loop

В основном цикле `while running:`, найти блок обработки цветного кадра:

```python
# БЫЛО:
            if color_mode:
                cf = frames.get_color_frame()
                if cf:
                    color_count += 1
                    if color_writer:
                        img = np.asanyarray(cf.get_data())
                        img = rotate_img(img, rotate)
                        img = np.ascontiguousarray(img)
                        color_writer.write(img.tobytes())
```

Заменить на (теперь `img` создаётся ВСЕГДА, не только при наличии writer):

```python
# СТАЛО:
            if color_mode:
                cf = frames.get_color_frame()
                if cf:
                    color_count += 1
                    img = np.asanyarray(cf.get_data())
                    img = rotate_img(img, rotate)
                    img = np.ascontiguousarray(img)
                    # Store latest colour frame for HTTP endpoint
                    service.update_color_rgb(img)
                    if color_writer:
                        color_writer.write(img.tobytes())
```

#### Изменение 1c: Добавить эндпоинт `/color_frame` в `make_fastapi`

Внутри функции `make_fastapi(service)`, перед строкой `app.include_router(router)`, добавить:

```python
    @router.get("/color_frame")
    async def get_color_frame(
        format: str = "json",
        service: CameraService = Depends(get_camera_service),
    ):
        """Return latest D435 color frame as base64 RGB24 JSON."""
        import base64 as b64mod
        frame = service.get_color_frame()
        if frame is None:
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=503, content={"detail": "no color frame yet"})
        h, w = frame.shape[:2]
        raw_bytes = frame.tobytes()
        if format == "raw":
            return Response(content=raw_bytes, media_type="application/octet-stream",
                            headers={"X-Width": str(w), "X-Height": str(h), "X-Dtype": "uint8-rgb24"})
        encoded = b64mod.b64encode(raw_bytes).decode("ascii")
        return {"width": w, "height": h, "dtype": "uint8-rgb24",
                "timestamp": service.get_color_timestamp(), "data": encoded}
```

> **Примечание:** нужен импорт `Response` из FastAPI. Проверь что в начале `make_fastapi` или в файле есть:
> `from fastapi.responses import Response` (или он уже доступен через другие импорты).

### Файл 2: `app/routes/system.py` (cam-control сервис)

Нужно найти файл маршрутов (обычно `app/routes/system.py`).

#### Изменение 2a: Добавить proxy-маршрут `/depth/color_frame`

Найти блок `if CAM_TYPE == "depth_camera":` и внутри него, **после** существующего маршрута `get_depth`, добавить:

```python
    color_frame_description = (
        "Returns the latest D435 colour (RGB24) frame from the RealSense color sensor. "
        "Use format=json for base64-encoded JSON payload, or format=raw for raw bytes."
    )

    @router.get(
        f"/api/v1/{CAM_TYPE}/depth/color_frame",
        summary="Get real D435 colour frame (RGB24)",
        description=color_frame_description,
    )
    @router.get(
        "/depth/color_frame",
        summary="Get real D435 colour frame (RGB24)",
        description=color_frame_description,
    )
    async def get_depth_color_frame(format: str = "json"):
        import requests as _req
        try:
            url = f"http://localhost:8000/color_frame?format={format}"
            resp = _req.get(url, timeout=5)
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=resp.text)
            if format == "raw":
                return Response(
                    content=resp.content,
                    media_type="application/octet-stream",
                    headers={
                        "X-Width": resp.headers.get("X-Width", ""),
                        "X-Height": resp.headers.get("X-Height", ""),
                        "X-Dtype": resp.headers.get("X-Dtype", "uint8-rgb24"),
                    },
                )
            return JSONResponse(content=resp.json())
        except requests.RequestException as e:
            raise HTTPException(status_code=502, detail=f"Depth color frame proxy error: {e}")
```

> **Важно:** `JSONResponse` и `Response` должны быть импортированы — они уже есть в файле:
> `from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response`

## Перезапуск

После внесения изменений нужно перезапустить оба сервиса:

```bash
# Перезапуск realsense_mux (порт 8000):
sudo systemctl restart realsense-mux  # или как называется сервис

# Перезапуск cam-control (порт 8900):
sudo systemctl restart cam-control    # или как называется сервис
```

> Имена systemd-сервисов узнать командой:
> ```bash
> systemctl list-units --type=service --state=running | grep -iE 'cam|janus|realsense|mux'
> ```

## Проверка

```bash
# 1. Проверить что realsense_mux отдаёт цветной кадр:
curl -s "http://localhost:8000/color_frame?format=json" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('Keys:', list(d.keys()))
print('Width:', d.get('width'), 'Height:', d.get('height'))
print('Dtype:', d.get('dtype'))
print('Data length:', len(d.get('data', '')))
"

# 2. Проверить что cam-control проксирует:
curl -s "http://localhost:8900/depth/color_frame?format=json" | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('OK! Width:', d.get('width'), 'Height:', d.get('height'), 'Dtype:', d.get('dtype'))
"

# 3. Проверить что это реальные RGB (а не colormap):
curl -s "http://localhost:8900/depth/color_frame?format=json" | python3 -c "
import json, sys, base64
d = json.load(sys.stdin)
raw = base64.b64decode(d['data'])
w, h = d['width'], d['height']
# Sample center pixel
ci = (h//2 * w + w//2) * 3
r, g, b = raw[ci], raw[ci+1], raw[ci+2]
print(f'Center pixel: R={r} G={g} B={b}')
print(f'Size: {w}x{h}, bytes: {len(raw)}, expected: {w*h*3}')
# Values should look natural (not colormap pattern)
print('Looks like real RGB!' if max(r,g,b) > 30 else 'WARNING: very dark, check lighting')
"
```

## Ожидаемый результат

После деплоя фронтенд (кнопка "Shot Color" в 3D-вьюере) будет запрашивать
`/api/v1/depth_camera/depth/color_frame?format=json` через API-гейтвей,
и точки облака будут раскрашены реальными цветами камеры.

Кнопка "Shot" по-прежнему рисует моноцветные точки (голубые) без запроса цвета.
