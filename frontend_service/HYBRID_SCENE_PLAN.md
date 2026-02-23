# Hybrid 3D Scene — Technical Plan

> **Date:** 2026-02-19  
> **Status:** IMPLEMENTING — Этап 1 в работе  
> **Scope:** `frontend_service/static/js/arm3d-runtime.js` + new `scene-config.js`, `coord-utils.js`, `scene-helpers.js`

---

## 1. Цель

Перевести 3D-визуализацию из «демо-режима» (произвольные единицы, Y-up, магические числа) в **точную гибридную среду** с привязкой к реальным координатам. Это основа для:

- Отображения данных лидара Symovo на карте
- Fusion depth-данных D435 с виртуальной сценой
- Точного позиционирования TCP/gripper в мировых координатах
- Будущей AR/digital-twin интеграции

---

## 2. Принятые решения

| Параметр | Значение | Обоснование |
|----------|----------|-------------|
| Координатная конвенция | **Z-up (ROS-style)** | Совместимость с лидаром, навигацией, стандарт робототехники |
| Единицы | **метры** | Единый масштаб для всех данных |
| Depth camera | **RealSense D435** (eye-in-hand) | Подтверждено пользователем |
| Лидар | **Symovo AGV built-in** (через AGV API) | Подтверждено пользователем |
| Монтаж arm→AGV | **Есть чертёж/измерения** | Пользователь предоставит данные |
| Обратная совместимость | **Сохранить** текущий рендер | Новый конфиг подключается параллельно |

---

## 3. Текущее состояние сцены

### 3.1 Координатная система

- **Three.js native**: Y-up
- **Единицы**: 1 world unit = 50mm (scale factor ×20 от pre-scale, где 1 pre-scale = 1 метр)
- **Нет явной привязки** к реальному миру

### 3.2 Камера сцены

```
Type:     PerspectiveCamera
FOV:      45°
Near:     0.1
Far:      500
Position: (20, 15, 20) world units = (1.0, 0.75, 1.0) m
Target:   (0, 5, 0)   world units = (0, 0.25, 0) m
```

**Файл:** `arm3d-runtime.js` L487–494

### 3.3 Освещение

| # | Тип | Цвет | Интенсивность | Позиция (world units) | Позиция (метры) | Тени |
|---|-----|------|---------------|-----------------------|-----------------|------|
| 1 | AmbientLight | `#ffffff` | 0.5 | — | — | Нет |
| 2 | DirectionalLight (key) | `#ffffff` | 0.9 | (20, 40, 30) | (1.0, 2.0, 1.5) | PCFSoftShadowMap |
| 3 | DirectionalLight (fill) | `#8090ff` | 0.3 | (-30, 20, -30) | (-1.5, 1.0, -1.5) | Нет |

**Файл:** `arm3d-runtime.js` L497–503

### 3.4 Сетка (Grid)

```
Type:       GridHelper(80, 24)
Position:   Y = -5 world units = -0.25 m
Plane:      XZ (горизонтальная в Y-up)
Colors:     0x303846 / 0x232a36
```

**Файл:** `arm3d-runtime.js` L505–506

### 3.5 Кинематическая цепь xArm 6-6

**Scale:** `SCALE = [20, 20, 20]` (main.b58ea3f.js L256)  
**Base rotation:** `ROTATION = [-90, 0, -90]` deg (main.b58ea3f.js L295)

Иерархия: `rootGroup → groups[0] → groups[1] → ... → groups[6] → toolGroup`

| Group | Pre-scale (m) | World units (×20) | Joint | Реальный offset (mm) |
|-------|---------------|-------------------|-------|---------------------|
| 0 (base) | [0, -0.25, 0] | (0, -5, 0) | фиксированный | — |
| 1 | [0, 0.267, 0] | (0, 5.34, 0) | J1 (Y rot) | 267 mm по Y |
| 2 | [0, 0, 0] | (0, 0, 0) | J2 (X rot) | 0 (совмещённый) |
| 3 | [0, 0.285, -0.0535] | (0, 5.7, -1.07) | J3 (X rot) | 290 mm |
| 4 | [0, -0.3425, -0.0775] | (0, -6.85, -1.55) | J4 (Y rot) | 351 mm |
| 5 | [0, 0, 0] | (0, 0, 0) | J5 (X rot) | 0 (совмещённый) |
| 6 | [0, -0.097, -0.076] | (0, -1.94, -1.52) | J6 (Y rot) | 123 mm |

**Файл:** `arm3d-runtime.js` L54–67

### 3.6 Lift (Igus)

**ВАЖНО:** Все присвоения — абсолютные (`=`), НЕ инкрементные (`+=`/`-=`).

```
Формула:    L = (lift / 10000) * 1.55        // world units (1.55 = wu per 10k mU)

groups[1].position.y = L + 5.0               // абсолютное присвоение, +5.0 hardcoded base
groups[1].position.x = -(L / 3.5)            // абсолютное присвоение
groups[1].position.z = -(L / 2)              // абсолютное присвоение

liftVisualNode.position.y = L                // без +5.0 offset
liftVisualNode.position.x = -(L / 3.5)
liftVisualNode.position.z = -(L / 2)
```

**Дискрепанция base offset:**
- `groupsPosition[1] = [0, 0.267, 0]` → × 20 = **5.34** wu (геометрическое значение)
- `updateFn` использует **5.0** (hardcoded из production bundle)
- Разница: 0.34 wu = **17 mm** в реальном масштабе
- `applyJointState()` сбрасывает на 5.34, но `updateFn` немедленно перезаписывает на 5.0

**Наклонный вектор лифта:**
Лифт движется НЕ вертикально. Вектор смещения на единицу L:
```
d = (+1, -1/3.5, -1/2) × L
|d| = √(1 + 1/12.25 + 1/4) = √1.332 ≈ 1.154
```
При max lift (120k mU): L=18.6 wu, реальное перемещение = 21.5 wu = **1.074 m**.

### 3.7 Вакуумный захват (Gripper)

```
STL:        xarm_vacuum_gripper.2f2a316.stl
Scale:      (0.02, 0.02, 0.02)  — т.е. STL в 0.001 × SCALE(20)
Rotation:   (90°, 0°, -90°)     — т.е. [180,0,0] + [-90,0,-90]
Привязка:   child of groups[N-1] через toolGroup
```

**Файл:** `arm3d-runtime.js` L614–625

### 3.8 База (Base plate)

```
STL:        base.stl
Position:   [-0.01, 0.05, -0.02] × 20 = (-0.2, 1.0, -0.4)
Scale:      (20, 20, 20)
Rotation:   (-90°, 0°, -90°)
Привязка:   child of groups[0] (статична, не двигается с лифтом)
```

**Файл:** `arm3d-runtime.js` L592–612

---

## 4. Depth Camera D435

### 4.1 Intrinsics (из конфигурации)

| Параметр | Значение | Источник |
|----------|----------|----------|
| fx | 380.4253845214844 | `xarm_service/app/config.py` L31 |
| fy | 380.4253845214844 | `xarm_service/app/config.py` L32 |
| cx | 320.0 | `xarm_service/app/config.py` L33 |
| cy | 240.0 | `xarm_service/app/config.py` L34 |
| Width | 640 px | `xarm_service/app/config.py` L35 |
| Height | 480 px | `xarm_service/app/config.py` L36 |
| Depth scale | 0.9 (raw→mm) | `xarm_service/app/config.py` L37 |
| Depth calibration | `true = 0.957 × measured + 45.2` (mm) | `xarm_service/app/config.py` L57–58 |

### 4.2 Рассчитанные FOV

Используется каноническая формула пинхол-камеры:
```
HFOV = 2 × atan(width / (2 × fx)) = 2 × atan(640 / (2 × 380.4)) ≈ 80.2°
VFOV = 2 × atan(height / (2 × fy)) = 2 × atan(480 / (2 × 380.4)) ≈ 64.5°
```

**Примечание:** Формула `2 × atan(cx/fx)` даёт тот же результат только когда principal point в центре
(`cx = width/2`). Каноническая формула корректна при любом cx/cy.

### 4.3 Монтаж (eye-in-hand)

- Камера установлена на tool frame (гриппер)
- Axis mapping (legacy): `tool_x = -cam_dy`, `tool_y = cam_dx` (`depth_service.py` L273–276)
- Offset: через `prefix.posX/posY/posZ` в trajectory config (runtime-конфигурируемый)
- **TODO:** получить точный `T_flange_camera` (4×4) из чертежа пользователя

### 4.4 Data pipeline

```
D435 → pyrealsense2 (realsense_mux.py) 
     → camera service (192.168.1.55:8000)
     → GET /depth/frame (614,400 bytes, uint16)
     → GET /depth?x=&y= (single pixel)
     → depth_service.py → object detection (OpenCV contours)
```

---

## 5. Лидар Symovo AGV

### 5.1 Текущий статус

- Лидар встроен в AGV Symovo
- Используется для навигации и safety
- В коде workspace: `laser_timeout` flag в `readiness_checker.py` — подтверждает наличие
- **Нет прямого API для получения scan data** в текущем коде

### 5.2 Навигационный фрейм

```
Frame ID:     "map"
Координаты:   (x, y, theta) — метры, радианы
Pixel→World:  worldX = pixelX × resolution + offsetX
              worldY = pixelY × resolution + offsetY
```

**Файл:** `nav2adapter/domain/models.py` L39–43, `nav2adapter/static/map_viewer.html` L737–750

### 5.3 TODO

- Уточнить API Symovo для получения raw scan (формат, endpoint, частота)
- Определить высоту монтажа лидара над полом

---

## 6. Трансформ-цепочка (Transform Chain)

### 6.1 Полная цепочка

```
T_world          (map frame, Z-up, метры)
  └─ T_agv       (AGV base → map) — динамический, из навигации
      └─ T_arm   (arm base → AGV) — статический, из чертежа монтажа
          └─ T_lift  (lift offset → arm base) — динамический, из igus position
              └─ FK(J1..J6)  (joint chain) — динамический, из xArm joints
                  └─ T_flange (tool flange)
                      ├─ T_gripper  (gripper → flange) — статический
                      └─ T_camera   (D435 → flange) — статический, из чертежа
```

### 6.2 Трансформы: источники данных

| Трансформ | Тип | Источник | Частота | Единицы |
|-----------|-----|----------|---------|---------|
| T_agv | динамический | Symovo API → nav2adapter | ~10 Hz | метры, рад |
| T_arm | **статический** | чертёж монтажа | — | метры |
| T_lift | динамический | igus_service `/position` | 200ms | motor units → метры |
| FK(joints) | динамический | xArm joints (WebSocket/REST) | ~100ms | радианы |
| T_gripper | **статический** | известен (vacuum gripper spec) | — | метры |
| T_camera | **статический** | чертёж/измерения | — | метры |

### 6.3 Конвертация координат

**ROS (Z-up) ↔ Three.js (Y-up):**

```
Three.x =  ROS.x
Three.y =  ROS.z
Three.z = -ROS.y

ROS.x =  Three.x
ROS.y = -Three.z
ROS.z =  Three.y
```

**Масштаб (legacy):**

```
Three world unit = 0.05 m = 50 mm
worldUnits = meters × 20
meters = worldUnits / 20
```

---

## 7. Workspace робота

**Из конфигурации `xarm_service/app/config.py`:**

```
Workspace:      400 × 900 × 1200 mm (X × Y × Z)
Base position:  (150.0, 450.0, 0.0) mm в WS frame
WS rotation:    identity (оси совпадают)
TCP speed:      150 mm/s max
TCP accel:      800 mm/s² max
xArm IP:        192.168.1.220
```

---

## 8. Архитектура решения

### 8.1 Новый файл: `scene-config.js`

Декларативное описание всех сущностей сцены. Экспортирует:

```javascript
export const WORLD = {
  convention: "Z-up",
  units: "meters",
  coordinateSystem: "ROS",
  SCALE_FACTOR: 20,  // legacy: 1m → 20 world units
};

export const TRANSFORMS = {
  // T_agv_armbase — static, from mounting measurements
  agvToArmBase: { tx: 0, ty: 0, tz: 0, rx: 0, ry: 0, rz: 0 },  // PLACEHOLDER
  
  // T_flange_camera — static, from measurements
  flangeToCamera: { tx: 0, ty: 0, tz: 0, rx: 0, ry: 0, rz: 0 }, // PLACEHOLDER
  
  // T_flange_gripper — static, known from gripper spec
  flangeToGripper: { tx: 0, ty: 0, tz: 0, rx: 0, ry: 0, rz: 0 },
};

export const ROBOT_ARM = {
  model: "xArm6",
  links: [267, 0, 290, 351, 0, 123],  // mm, from groupsPosition
  jointAxes: ["Y", "X", "X", "Y", "X", "Y"],
  mountDegrees: [0, 0],  // tilt, rotation
  basePositionInWS: [150, 450, 0],  // mm
};

export const DEPTH_CAMERA = {
  model: "RealSense D435",
  intrinsics: { fx: 380.425, fy: 380.425, cx: 320, cy: 240, w: 640, h: 480 },
  depthScale: 0.9,
  depthCalibration: { k: 0.957, b: 45.2 },  // true_mm = k × raw_mm + b
  fov: { h: 80.2, v: 64.8 },  // degrees, calculated
  frustum: { near: 0.1, far: 1.0 },  // meters, for visualization
  mounting: "eye-in-hand",
  endpoint: "http://192.168.1.55:8000",
};

export const SCENE_CAMERA = {
  fov: 45,
  near: 0.1,
  far: 500,
  initialPosition: [20, 15, 20],     // world units (Three.js Y-up)
  initialTarget: [0, 5, 0],
  minDistance: 5,
  maxDistance: 200,
};

export const LIGHTS = [
  { type: "ambient", color: 0xffffff, intensity: 0.5 },
  { type: "directional", color: 0xffffff, intensity: 0.9,
    position: [20, 40, 30], shadow: true },
  { type: "directional", color: 0x8090ff, intensity: 0.3,
    position: [-30, 20, -30], shadow: false },
];

export const FLOOR = {
  gridSize: 80,
  gridDivisions: 24,
  positionY: -5,   // world units (Three.js)
  realHeight: 0,   // meters (Z in ROS = floor level)
};

export const LIFT = {
  formula: "newPos = (motorUnits / 10000) * 1.55",
  maxMotorUnits: 120000,
  positionLimits: { min: 0, max: 120000 },  // motor units
  toMeters: (motorUnits) => (motorUnits / 10000) * 1.55 / 20,
};

export const AGV = {
  model: "Symovo",
  ip: "192.168.1.100",          // verified from nav2adapter/app/config.py
  protocol: "https",
  port: 443,                     // AGV uses HTTPS (port 443)
  basePath: "/v0",
  robotNumber: 15,
  lidar: { type: "built-in", dataSource: "AGV API" },
  adapterService: { port: 7905, host: "localhost" },  // nav2adapter, NOT the AGV
};
```

### 8.2 Утилиты конвертации координат

```javascript
// ROS Z-up → Three.js Y-up
export function rosToThree(x, y, z) {
  return [x, z, -y];
}

// Three.js Y-up → ROS Z-up
export function threeToRos(x, y, z) {
  return [x, -z, y];
}

// Метры → world units
export function metersToWorld(m) {
  return m * WORLD.SCALE_FACTOR;
}

// World units → метры
export function worldToMeters(wu) {
  return wu / WORLD.SCALE_FACTOR;
}

// ROS метры → Three.js world units (полная конвертация)
export function rosMetersToThreeWorld(x, y, z) {
  const [tx, ty, tz] = rosToThree(x, y, z);
  return [metersToWorld(tx), metersToWorld(ty), metersToWorld(tz)];
}

// Three.js world units → ROS метры (полная обратная)
export function threeWorldToRosMeters(x, y, z) {
  const [mx, my, mz] = [worldToMeters(x), worldToMeters(y), worldToMeters(z)];
  return threeToRos(mx, my, mz);
}
```

### 8.3 Debug визуализации (query params)

| Параметр | Что включает |
|----------|-------------|
| `?axes` | World origin axes (RGB=XYZ, 1m стрелки с метками) |
| `?axes=all` | + arm base axes + TCP axes + camera frame axes |
| `?camera_frustum` | D435 frustum wireframe на gripper |
| `?agv` | AGV body outline + origin axes |
| `?lidar_ring` | Placeholder кольцо на высоте лидара |
| `?coords` | HUD с текущими координатами TCP в метрах (ROS frame) |
| `?transform` | Существующая панель отладки вращения/позиции |

---

## 9. Данные для заполнения (от пользователя)

### 9.1 КРИТИЧЕСКИЕ (блокируют точность)

| # | Что нужно | Формат | Пример |
|---|-----------|--------|--------|
| 1 | **T_agv_armbase** — положение основания arm относительно центра AGV | `tx, ty, tz` (мм) + `rx, ry, rz` (°) | `tx=200, ty=0, tz=550, rx=0, ry=0, rz=0` |
| 2 | **T_flange_camera** — положение D435 относительно фланца arm | `tx, ty, tz` (мм) + `rx, ry, rz` (°) | `tx=50, ty=0, tz=-30, rx=0, ry=0, rz=180` |
| 3 | **Высота лидара** над полом | мм | `250 мм` |

### 9.2 ЖЕЛАТЕЛЬНЫЕ (улучшают точность)

| # | Что нужно | Формат |
|---|-----------|--------|
| 4 | DH параметры xArm (из `get_dh_params()`) | 28 float'ов |
| 5 | Точные размеры платформы Symovo | длина × ширина × высота мм |
| 6 | Формат scan data лидара Symovo | endpoint, json schema, частота |

---

## 10. Этапы реализации

### Этап 1: Инфраструктура (текущий) ✦
1. Создать `scene-config.js` со всеми константами и плейсхолдерами
2. Функции конвертации координат с unit-тестами
3. Интегрировать конфиг в `arm3d-runtime.js` (замена магических чисел)
4. World axes helper (`?axes`)

### Этап 2: Камера и координатная верификация
5. D435 frustum визуализация
6. TCP координаты HUD (`?coords`)
7. Сверка TCP position с xArm API данными
8. Заполнить `T_flange_camera` из чертежа

### Этап 3: AGV и монтаж
9. AGV body outline
10. Заполнить `T_agv_armbase` из чертежа
11. Arm base axes на AGV
12. Координатная цепочка `map → agv → arm`

### Этап 4: Лидар (future)
13. Получить API лидара Symovo
14. Парсинг scan data → pointcloud
15. Рендеринг scan на карте сцены
16. Синхронизация с AGV позицией

### Этап 5: Depth fusion (future)
17. Рендеринг depth pointcloud в 3D сцене
18. Проекция через T_flange_camera
19. Совмещение depth и виртуальной геометрии
20. Realtime overlay

---

## 11. Риски

| Риск | Вероятность | Митигация |
|------|-------------|-----------|
| Неточные T_flange_camera → ошибка fusion | Высокая | Сначала визуальный frustum, потом точная калибровка |
| Symovo API не отдаёт raw scan | Средняя | Fallback: использовать occupancy grid из map_viewer |
| Latency depth stream > 100ms | Средняя | Async pipeline, previous-frame rendering |
| Scale drift при длинных цепочках | Низкая | Все значения в метрах, конвертация только на рендере |
| Y-up/Z-up путаница в коде | Высокая | Строгие naming conventions: `_ros` / `_three` суффиксы |

---

## 12. Файловая структура (после реализации)

```
frontend_service/static/js/
├── scene-config.js          ← NEW: декларативный конфиг сцены (window.SCENE_CONFIG)
├── coord-utils.js           ← NEW: функции конвертации координат (window.CoordUtils)
├── scene-helpers.js         ← NEW: axes, frustum, HUD (window.SceneHelpers)
├── arm3d-runtime.js         ← MODIFIED: импорт конфига, убраны хардкоды
├── arm3d-iframe-host.js     ← без изменений
└── api/
    └── igus-api.js          ← без изменений
```

**Формат модулей:** plain `<script>` (не ES modules), экспорт через `window.*` глобалы.
Порядок загрузки: `scene-config.js` → `coord-utils.js` → `scene-helpers.js` → `arm3d-runtime.js` (type=module).
