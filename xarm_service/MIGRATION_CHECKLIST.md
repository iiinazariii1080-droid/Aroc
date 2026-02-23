# Migration Checklist — V2

Дата: 2026-01-28

## A. Инварианты (проверить в каждом PR)
- [ ] НЕТ прямых вызовов `XArmAPI` из FastAPI роутеров/operations
- [ ] Все mutating команды идут через `RobotActor`
- [ ] Callbacks регистрируются один раз и освобождаются на shutdown
- [ ] `connect()` не делает auto clean_error/enable_motion
- [ ] Есть readiness gate и единый источник истины `StateStore`

## B. Фаза 0
- [ ] Quick fixes применены
- [ ] Exception model унифицирована
- [ ] Логи не содержат секреты/пароли

## C. Фаза 1–2
- [ ] `/health/live` и `/health/ready` работают
- [ ] Actor loop стартует/останавливается корректно
- [ ] reconnect backoff

## D. Фаза 3–4
- [ ] movement через actor (vertical slice)
- [ ] busy policy реализован и протестирован
- [ ] idempotency по command_id
- [ ] recovery endpoints работают и документированы
- [ ] stop/cancel best‑effort

## E. Тесты
- [ ] unit tests: policies/readiness/idempotency
- [ ] integration tests: actor + mocked SDK
- [ ] hardware smoke: безопасные сценарии

## F. Документация
- [ ] обновлены diagrams + архитектурный документ
- [ ] описаны ограничения и режимы выполнения
