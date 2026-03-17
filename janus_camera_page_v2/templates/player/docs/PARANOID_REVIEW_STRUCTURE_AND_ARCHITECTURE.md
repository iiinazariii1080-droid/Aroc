# Параноидальное ревью: структура и чистая архитектура

**Дата:** 9 февраля 2026  
**Область:** `templates/` — структура, границы слоёв, принципы Clean Architecture  
**Тип:** Строгая проверка зависимостей и контрактов

---

## 1. Структура каталогов и загрузка

### 1.1 Дерево слоёв (player/)

```
player/
├── ns.js              # Namespace only (Core, Ports, Adapters, App)
├── config.js          # Config from DOM/URL; runtime uses AP.Core
├── core/              # Domain + pure logic (no I/O, no Ports/Adapters/App)
├── ports/             # Interfaces (JSDoc typedefs only)
├── adapters/          # Implementations of ports + infra
├── app/               # Use cases, coordinators, controller
├── bootstrap.js       # Composition root (wires adapters → controller)
└── docs/
```

**Вердикт:** Структура соответствует разделению Clean Architecture (Entities/Rules → Interfaces → Adapters → Application).

### 1.2 Порядок загрузки скриптов (color_view.html)

Порядок корректен:

1. `ns.js` — создание пространства имён  
2. `config.js` — конфиг (при первом вызове `computeConfig()` уже загружены core-модули)  
3. **Core** (player_state, codes, domain_events, connection_policy, state_machine_canonical, invariants, fail_closed, backoff, recovery_policy)  
4. **Ports** (clock, logger, streaming, video)  
5. **Adapters** (clock, logger, janus_session_manager, dom_ui, janus_streaming, janus_textroom)  
6. **App** (stats, joystick, recovery_map, reconnect_coordinator, timer_coordinator, watchdog, player_controller)  
7. `bootstrap.js` — композиция и запуск  

**Важно:** `state_machine_legacy.js` **не** подключается в браузере — только в `run_core_tests.js` для duality guard. В рантайме используется только StateMachineCanonical (ADR 0001).

**Вердикт:** Зависимости по загрузке не нарушены. Config вызывается из bootstrap после загрузки всех скриптов, поэтому `AP.Core.MAX_RECONNECT_ATTEMPTS` доступен.

---

## 2. Правило зависимостей (Dependency Rule)

### 2.1 Core не зависит от внешних слоёв

Проверка: в `core/` нет обращений к `AP.Ports`, `AP.Adapters`, `AP.App`.

```bash
# Результат: No matches
grep -r "AP\.(Ports|Adapters|App)" player/core/
```

**Вердикт:** Core зависит только от `AP` (namespace) и от других модулей Core. Нарушений нет.

### 2.2 App зависит только от Core и от портов (инъекция)

- `PlayerController` в конструкторе принимает `ui` (VideoPort), `clock` (ClockPort), `logger` (LoggerPort), `streaming` (StreamingPort), плюс `statsService`, `joystickService`.
- Обращения к внешнему миру только через эти зависимости: `this.ui.*`, `this.streaming.*`, `this.clock.*`, `this.log.*`.
- Используются только типы и константы из Core: `PlayerState`, `ConnectionPolicy`, `StateMachineCanonical`, `InvariantGate`, `DomainEventType`, `EventType`, `ActionType`, `RecoveryReason`, `RecoverySeverity`, `PlayerErrorCode`, `PolicyAction`.
- `RecoveryPolicy` живёт в App (`recovery_map.js`), Controller использует `AP.App.RecoveryPolicy.defaultSeverityForReason` — зависимость App → App допустима.

**Вердикт:** Граница App соблюдена: нет прямых обращений к DOM, Janus, `window` (кроме глобального `AP` и присвоения `window.autonomousPlayerController` в bootstrap для отладки).

### 2.3 Adapters не зависят от App

Проверка: в `adapters/` нет обращений к `AP.App` или `window.autonomousPlayerController`.

```bash
# Результат: No matches
grep -r "AP\.App\.|window\.autonomousPlayerController" player/adapters/
```

**Вердикт:** Адаптеры зависят только от Core (типы/константы для логов и состояний) и от инфраструктуры (Janus, DOM). Нарушений нет.

### 2.4 Config и слой Core

`config.js` при вызове `computeConfig()` использует `AP.Core.MAX_RECONNECT_ATTEMPTS`. Вызов идёт из `bootstrap.js` после загрузки всех скриптов, поэтому к моменту вызова Core уже инициализирован. Зависимость Config → Core по факту есть; для строгой чистоты константу по умолчанию (12) можно продублировать в config и не ссылаться на Core — опциональное улучшение, не блокер.

**Вердикт:** Текущее использование допустимо, циклических зависимостей нет.

---

## 3. Порты и контракты

### 3.1 StreamingPort

Описание в `ports/streaming_port.js`: init, ensureReady, listStreams, watch, stop, detach, recreate, getPeerConnection, getInboundStream, setEventSink.  
`JanusStreamingAdapter` помечен `@implements {AP.Ports.StreamingPort}` и реализует все перечисленные методы.

**Вердикт:** Контракт полный, расхождений нет.

### 3.2 VideoPort (исправлено в рамках ревью)

- **Было:** В typedef не было `setStatsText`; контроллер вызывал `this.ui.setStatsText(txt)` из колбэка StatsService.
- **Сделано:** В `ports/video_port.js` в контракт VideoPort добавлено свойство `setStatsText(text: string)` с комментарием о назначении.

**Вердикт:** Контракт приведён в соответствие с использованием.

### 3.3 ClockPort (уточнено в рамках ревью)

- Контроллер вызывает `this.clock.debugSnapshot()` только при наличии метода: `(typeof this.clock.debugSnapshot === 'function') ? this.clock.debugSnapshot() : null`.
- В контракт ClockPort добавлено опциональное свойство `[debugSnapshot]` с пояснением, что используется для отладочной панели.

**Вердикт:** Опциональное расширение порта задокументировано, нарушений нет.

### 3.4 LoggerPort

Описание: debug, info, warn, error. Адаптер logger их реализует. Контроллер и сервисы используют только эти методы.

**Вердикт:** Контракт соблюдён.

---

## 4. Взаимодействие компонентов

### 4.1 Единая точка входа для событий

- Все внешние воздействия (UI, потоковые события, таймеры, watchdog, reconnect) в итоге приводят к `controller.handleEvent(...)`.
- Переход состояния и список действий определяются только в `StateMachineCanonical.transition(event, snapshot)`.
- Применение снапшота — только в `_applySnapshot(next)`.
- Выполнение действий — только в `_executeActions(actions)`.

**Вердикт:** Соответствует SAFETY_LAWS L1–L3, L8, L16. Один путь диспетчеризации, побочные эффекты только через действия.

### 4.2 Поток данных по слоям

- **Адаптеры** генерируют доменные события (StreamEvent с type: 'ICE_STATE', 'WEBRTC_STATE', …) и передают их в контроллер через setEventSink.
- **Контроллер** маппит StreamEvent в события state machine (EventType) и передаёт snapshot в ConnectionPolicy.decide (DomainEventType + snapshot → PolicyAction).
- **Core** не знает про адаптеры: ConnectionPolicy, StateMachineCanonical, InvariantGate — чистые функции от (event, snapshot).

**Вердикт:** Направление зависимостей и поток данных соответствуют чистой архитектуре.

### 4.3 ReconnectCoordinator и RecoveryPolicy

- ReconnectCoordinator (App) владеет таймерами переподключения и счётчиком попыток; вызывается из контроллера по действию START_RECONNECT_TIMER.
- Severity по умолчанию задаётся через App.RecoveryPolicy.defaultSeverityForReason; решение о действии восстановления (SOFT_RESTART / REATTACH / RECREATE) принимает Core (decideRecoveryAction в recovery_policy.js).

**Вердикт:** Разделение ответственности соблюдено: политика в Core, оркестрация и дефолты в App.

---

## 5. Риски и мелкие замечания

### 5.1 Глобальный хендл контроллера

`bootstrap.js` присваивает `window.autonomousPlayerController = controller`. Это отладочный выход во внешний мир; для продакшена можно обернуть в `if (cfg.debug)` или убрать, если не нужно.

**Рекомендация:** Не блокер; при желании ограничить только режимом debug.

### 5.2 Константа MAX_RECONNECT_ATTEMPTS

Сейчас она определена в Core (codes.js), а config использует её при вычислении конфига. Дублирование значения по умолчанию (12) в config.js позволило бы полностью убрать зависимость config от Core; тесты и код уже опираются на Core. Оставить как есть — приемлемо.

**Рекомендация:** Опционально вынести дефолт в одно место (например, только в config) и в Core импортировать/не задавать дефолт — на усмотрение команды.

### 5.3 Именование: recovery_policy (Core) vs RecoveryPolicy (App)

- `core/recovery_policy.js` экспортирует `AP.Core.decideRecoveryAction` (чистая функция).
- `app/recovery_map.js` экспортирует `AP.App.RecoveryPolicy` (defaultSeverityForReason, DefaultSeverityByReason).

Разные имена и слои уменьшают путаницу; можно в документации явно указать: Core — «что делать» (action), App — «с какой severity по умолчанию».

**Рекомендация:** Одна строка в docs или в JSDoc.

---

## 6. Чеклист параноидального ревью

| Проверка | Статус |
|----------|--------|
| Core не ссылается на Ports/Adapters/App | ✅ |
| App использует только Core + инжектированные порты | ✅ |
| Adapters не ссылаются на App | ✅ |
| Порты описаны только интерфейсами (typedef), без реализации | ✅ |
| Контракт VideoPort включает все вызовы контроллера (в т.ч. setStatsText) | ✅ (исправлено) |
| Опциональный метод ClockPort (debugSnapshot) задокументирован | ✅ (добавлено) |
| Один источник истины для переходов (StateMachineCanonical) | ✅ |
| События обрабатываются только через handleEvent | ✅ |
| Побочные эффекты только в _executeActions | ✅ |
| Legacy state machine не загружается в браузере | ✅ |
| Порядок загрузки скриптов корректен | ✅ |
| Config вызывается после инициализации Core | ✅ |

---

## 7. Итог

- **Структура:** Строгая, слои разделены, порядок загрузки корректен.  
- **Clean Architecture:** Правило зависимостей соблюдено (Core → Ports ← Adapters, App использует Core и порты).  
- **Взаимодействие:** Единая точка входа для событий, один state machine, побочные эффекты только через действия.  
- **Контракты портов:** Приведены в соответствие с использованием (VideoPort.setStatsText, ClockPort.debugSnapshot опционально).

Рекомендации выше — мелкие улучшения и документация; критических нарушений структуры и принципов чистой архитектуры не выявлено.
