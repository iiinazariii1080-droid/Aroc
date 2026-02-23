# xArm Service — New Architecture & Refactoring Plan (V2)

Дата: 2026-01-28 (Europe/Berlin)

Этот пакет содержит обновлённый (robotics‑grade) план архитектуры и рефакторинга контейнера xArm Service.
Фокус: **детерминированное управление манипулятором**, безопасность, предсказуемая конкуренция и production‑операционность.

## Содержание
- `ARCHITECTURE_V2.md` — целевая архитектура (слои, компоненты, инварианты, диаграммы)
- `REFACTORING_PLAN_V2.md` — пошаговый план миграции (фазы, критерии приёмки)
- `API_EVOLUTION.md` — стратегия совместимости API (v1 facade + v2 jobs/actions)
- `MIGRATION_CHECKLIST.md` — чек‑лист выполнения работ
- `TEST_PLAN_V2.md` — тест‑план (unit/integration/hardware smoke)
- `SECURITY_SAFETY.md` — safety/ops требования (fault recovery, motion enable, deadman)
- `diagrams/architecture.mmd` — Mermaid диаграмма компонентов
- `diagrams/state_machine.mmd` — Mermaid диаграмма состояний/ready gate

## Ключевое отличие V2
Главный инвариант: **Single‑Writer / RobotActor** — только один исполнитель владеет `XArmAPI` и последовательно применяет команды.
Это устраняет гонки, “две команды одновременно”, и даёт корректную отмену/таймауты.

- `WORKSPACE_SAFETY_ENVELOPE_V3.md` — рабочая зона 40×90×120 cm, reduced mode boundary и проверка «локти внутри зоны»
