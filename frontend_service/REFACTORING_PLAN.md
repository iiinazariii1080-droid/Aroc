# Arm3D Viewer — Аудит математики и план рефакторинга

> **Дата:** 2026-02-23
> **Автор:** Copilot (аудит по запросу)
> **Статус:** PLAN

---

## 1. Обнаруженные математические баги и конфликты

### 1.1 ❌ Lift offset discrepancy (17 mm ошибка позиционирования)

**Файл:** [arm3d-runtime.js](static/js/arm3d-runtime.js#L68)  
**Проблема:** В `ARM_SPECS["6-6"].updateFn` group[1] позиция при lift:

```javascript
g[1].position.y = newPos + 5;      // hardcoded +5.0
```

Но геометрическое значение из `groupsPosition[1] = [0, 0.267, 0]`:

```
0.267 × 20 (SCALE_FACTOR) = 5.34 wu
```

**Ошибка:** 5.34 − 5.0 = **0.34 wu = 17 mm** смещение по Y при любом значении лифта.

Это задокументировано в `scene-config.js` L201-203 как "carried from production bundle", но **не исправлено**. Все вычисления FK, позиция камеры D435, world-frame cloud — содержат эту ошибку.

**Исправление:**
```javascript
g[1].position.y = newPos + spec.groupsPosition[1][1] * 20;  // 5.34, не 5.0
```

---

### 1.2 ❌ applyMountDegrees() — ad-hoc визуальная интерполяция вместо кинематики

**Файл:** [arm3d-runtime.js](static/js/arm3d-runtime.js#L400-L433)  
**Проблема:** Позиция arm base вычисляется как:

```javascript
const posX = (tilt <= 90) ? (5/90)*tilt : (5/90)*(180-tilt);
const posY = (6/180)*tilt - 5;
```

Это **кусочно-линейная аппроксимация**, а не реальный кинематический расчёт mount-transform. Коэффициенты `5/90` и `6/180` — магические числа без физического обоснования.

**Следствие:** При ненулевом tilt (`mountDegrees[0] ≠ 0`), позиция робота в сцене приблизительна. Все child-объекты (links, tool, camera mount) наследуют эту ошибку.

**Исправление:** Использовать правильный rigid-body transform:
```javascript
// Правильно: T = translation_from_mount × Rz(rotate) × Rx(tilt)
const mountHeight = TRANSFORMS.agvToArmBase.tz;  // реальная высота монтажа
```

---

### 1.3 ⚠️ Depth cloud pixel shift при изменении камеры сцены

**Файл:** [scene-helpers.js](static/js/scene-helpers.js#L780-L900)  
**Основная причина «смещения пикселей»:**

1. **`sizeAttenuation: true`** на обоих PointsMaterial (live + map). При изменении расстояния сцена-камеры, размер точек в пикселях **меняется пропорционально** — создаёт визуальное "плывущее" облако.

2. **pointSize_wu = 0.5** (25mm в реальном мире) — это крупные точки. При орбите камеры, из-за атенюации, точки визуально сдвигаются.

3. **Depth frame sampling stride = 4** — огрубляет cloud, усиливая визуальный артефакт.

**Исправление (быстрое):**
```javascript
// scene-helpers.js — ensureLiveDepthCloud / ensureMapDepthCloud
mat.sizeAttenuation = false;  // фиксированный screen-space размер
mat.size = 2.0;               // пиксели, не world units
```

Или (лучше): использовать custom shader с контролируемым blend size.

---

### 1.4 ⚠️ pixelRotationDeg → intrinsicUV mapping не согласован с depth индексацией

**Файл:** [scene-helpers.js](static/js/scene-helpers.js#L810-L830)  
**Проблема:** `pixelRotationDeg = 90` трансформирует UV для intrinsics:

```javascript
if (normDeg === 90) {
  intrU = srcV;
  intrV = (logicalWidth - 1 - srcU);
}
```

Но `rawIndex` для depth данных использует **оригинальную** сетку:
```javascript
const rawIndex = swapPayloadWH
  ? (srcV * height + srcU)
  : (srcV * width + srcU);
```

Если камера физически повёрнута на 90°, то и depth, и intrinsics должны быть трансформированы **согласованно**. Текущий код применяет ротацию только к UV-маппингу intrinsics, но depth-значение берётся из неповёрнутого индекса. Это может быть намеренным (если endpoint уже выпрямляет depth), но создаёт хрупкую связь.

---

### 1.5 ⚠️ FOV двойное определение

**Файл:** [scene-config.js](static/js/scene-config.js#L290-L300), [coord-utils.js](static/js/coord-utils.js#L220-L230)

FOV определён дважды:
- `DEPTH_CAMERA.fov.h/v` — pre-computed в config
- `CoordUtils.getCameraFOV()` — вычисляется runtime из intrinsics

Frustum geometry использует `getCameraFOV()`, а config-FOV нигде не используется runtime. Дублирование → риск рассогласования.

---

### 1.6 ⚠️ Calibration offset сдвигает все depth точки на 45.2mm

**Файл:** [coord-utils.js](static/js/coord-utils.js#L210)

```javascript
const z_mm = cal.k * depth_raw_mm + cal.b;  // k=0.957, b=45.2
```

Постоянное смещение **+45.2mm** ко всем глубинам. Если калибрация изменится, это нужно синхронизировать в одном месте (сейчас только в config — хорошо).

---

### 1.7 ⚠️ Frozen config мутируется через UI sliders

**Файл:** [arm3d-runtime.js](static/js/arm3d-runtime.js#L560-L580)

```javascript
const cfg = SC.AGV_VISUALIZATION.mapLayer; // SC = Object.freeze(...)
cfg.planePosXM = Number(el.value) || 0;    // ← мутация frozen object!
```

`Object.freeze()` на SCENE_CONFIG — **shallow**. Вложенные объекты (mapLayer, pose, etc.) не заморожены и мутируются runtime. Это design flaw: config перестаёт быть source of truth.

---

## 2. Архитектурные проблемы

### 2.1 God-файлы

| Файл | Строки | Ответственности |
|------|--------|----------------|
| arm3d-runtime.js | 1326 | Scene init, Arm FK, Lift, AGV pose, Map layer, postMessage API, polling, UI controls, render loop, resize |
| scene-helpers.js | 1477 | Axes, Camera mount, Frustum, Depth cloud fetch/decode/build/accumulate, Voxel map, Shot/Record, Persistence, HUD, UI panels |

Оба файла — **>1000 строк** со смешанными ответственностями.

### 2.2 Глобальные переменные через window

```javascript
window.SCENE_CONFIG  // config
window.CoordUtils    // math library
window.SceneHelpers  // UI + depth cloud manager
```

Нет dependency injection, нет testability, порядок загрузки — implicit через HTML `<script>` порядок.

### 2.3 Hardcoded magic numbers выжили рефакторинг

Несмотря на подробный `scene-config.js`, в runtime всё ещё:
- `* 20` вместо `* CFG.WORLD.SCALE_FACTOR` (arm3d-runtime L1024, L1060, etc.)
- `+ 5` вместо вычисления из groupsPosition (arm3d-runtime L68)
- `degToRad(-90)` вместо использования meshRotationBase из config

### 2.4 Нет отделения модели от визуализации

FK computation, arm state management и Three.js rendering смешаны в одном IIFE. Невозможно:
- Протестировать FK без Three.js
- Подключить другой renderer
- Запустить headless для validation

### 2.5 Depth cloud — mixed concerns

`scene-helpers.js` совмещает:
- Network I/O (fetch depth/color frames)
- Data decoding (base64 → binary)
- 3D geometry construction
- Voxel map management (Map accumulation, ring buffer)
- Binary persistence (encode/decode DMP1 format)
- UI (record/shot/save/load panels)

---

## 3. План рефакторинга

### Phase 1: Критические баги (1-2 дня)

| # | Задача | Файл | Приоритет |
|---|--------|------|-----------|
| 1.1 | Исправить lift offset 5.0 → 5.34 | arm3d-runtime.js | HIGH |
| 1.2 | sizeAttenuation=false для depth cloud | scene-helpers.js | HIGH |
| 1.3 | Убрать оставшиеся `* 20` → `* S` | arm3d-runtime.js | MEDIUM |
| 1.4 | Deep-freeze config или использовать runtime-state | scene-config.js / runtime | MEDIUM |

### Phase 2: Модульная структура (3-5 дней)

Предлагаемая файловая структура:

```
static/js/
  core/
    scene-config.js          ← readonly config (deep-frozen)
    coord-utils.js           ← pure math functions (без зависимости от THREE)
    arm-kinematics.js        ← NEW: FK model, joint specs, lift math (pure JS)
    
  rendering/
    arm3d-renderer.js        ← NEW: Three.js scene setup, materials, render loop
    arm-visual.js            ← NEW: STL loading, mesh management, visual updates
    grid-floor.js            ← NEW: grid, floor, lighting setup
    
  features/
    depth-cloud/
      depth-decoder.js       ← NEW: base64→binary, payload parsing
      depth-geometry.js      ← NEW: point cloud geometry builder
      depth-voxel-map.js     ← NEW: voxel accumulation, ring buffer, persistence
      depth-cloud-manager.js ← NEW: orchestrator (fetch → decode → build → accumulate)
      depth-cloud-ui.js      ← NEW: control panel, buttons, status
    agv/
      agv-map-layer.js       ← NEW: map texture loading, transform, sliders
      agv-pose.js            ← NEW: robot pose polling, coordinate transform
    debug/
      debug-helpers.js       ← axes, frustum, mount marker
      debug-transform.js     ← rotation/position sliders, localStorage
      coords-hud.js          ← TCP/Robot coordinate overlay
      
  api/
    api-connector.js         ← (existing) HTTP helpers
    
  arm3d-app.js               ← NEW: entry point, DI container, lifecycle
```

### Phase 3: Event-driven архитектура (2-3 дня)

```javascript
// Вместо прямых вызовов — EventBus
class SceneEventBus {
  // Typed events:
  //   joints:updated    → {joints: number[], lift: number}
  //   pose:updated      → {x, y, theta}
  //   depth:frame       → {positions, colors, count}
  //   camera:moved      → {position, target}
  //   config:changed    → {key, value}
}
```

Это позволит:
- Depth cloud обновляться только при изменении arm pose (не каждый frame)
- HUD обновляться по событию, а не в render loop
- Тестировать каждый модуль изолированно

### Phase 4: Proper Transform Chain (2-3 дня)

Заменить ad-hoc `applyMountDegrees()` на правильную цепочку:

```
T_world → T_agv(x,y,θ) → T_mount(measured) → T_lift(motorUnits) → FK(J1..J6) → T_flange → T_camera
```

Каждый transform — `THREE.Matrix4`, составленный из config:
```javascript
class TransformChain {
  constructor(config) {
    this.agvToWorld = new THREE.Matrix4();
    this.mountToAgv = this.fromConfig(config.TRANSFORMS.agvToArmBase);
    this.flangeToCamera = this.fromConfig(config.TRANSFORMS.flangeToCamera);
  }
  
  update(agvPose, liftMU, joints) {
    // Compose: world ← agv ← mount ← lift ← FK ← flange
    // Each matrix is clean, testable, inspectable
  }
}
```

### Phase 5: ES Modules + Build tool (опционально, 2-3 дня)

Перейти с `<script>` + IIFE на proper ES modules:
```javascript
// arm3d-app.js
import { SceneConfig } from './core/scene-config.js';
import { CoordUtils } from './core/coord-utils.js';
import { ArmKinematics } from './core/arm-kinematics.js';
import { Renderer } from './rendering/arm3d-renderer.js';
```

Опционально добавить Vite/esbuild для:
- Tree shaking
- Hot reload
- TypeScript (постепенная миграция)
- Source maps

---

## 4. Рекомендуемый порядок действий

```
Week 1:  Phase 1 (баг-фиксы) + начало Phase 2 (extract arm-kinematics.js)
Week 2:  Phase 2 (split scene-helpers.js на depth-cloud/* + debug/*)
Week 3:  Phase 3 (EventBus) + Phase 4 (TransformChain)
Week 4:  Phase 5 (ES Modules) + тесты + документация
```

---

## 5. Быстрые исправления (можно применить сейчас)

### Fix 1: Pixel shift — отключить sizeAttenuation

В `scene-helpers.js`, функции `ensureLiveDepthCloud()` и `ensureMapDepthCloud()`:

```javascript
// BEFORE:
sizeAttenuation: true,

// AFTER:
sizeAttenuation: false,
```

И изменить `size` на screen-space пиксели (например, `2.0` вместо `0.5`).

### Fix 2: Lift offset

В `arm3d-runtime.js`, `ARM_SPECS["6-6"].updateFn`:

```javascript
// BEFORE:
g[1].position.y = newPos + 5;

// AFTER:
g[1].position.y = newPos + 0.267 * 20;  // = 5.34 (geometric exact)
```

### Fix 3: Использовать SCALE_FACTOR constant

Заменить все `* 20` на:
```javascript
const S = SC?.WORLD?.SCALE_FACTOR || 20;
```
