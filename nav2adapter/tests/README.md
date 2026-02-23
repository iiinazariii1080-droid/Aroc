# Тесты

Структура тестов для Symovo Service.

## Структура

```
tests/
├── conftest.py              # Общие фикстуры и конфигурация pytest
├── unit/                    # Юнит тесты
│   ├── test_state_store.py
│   ├── test_event_bus.py
│   ├── test_persistence_store.py
│   └── test_base_http_client.py
└── integration/            # Интеграционные тесты
    └── test_api_endpoints.py
```

## Запуск тестов

### Установка зависимостей
```bash
pip install -r requirements.txt
```

**Примечание:** Если установка `pydantic-core` не удается (требует Rust), попробуйте:
```bash
pip install pydantic --no-build-isolation
```

### Все тесты
```bash
pytest
# или
python -m pytest
```

**Результат:** 31 тест (22 юнит + 9 интеграционных)

### Только юнит тесты
```bash
pytest tests/unit/
# или
python -m pytest tests/unit/
```

**Результат:** 22 теста

### Только интеграционные тесты
```bash
pytest tests/integration/
# или
python -m pytest tests/integration/
```

**Результат:** 9 тестов

### С покрытием кода
```bash
pytest --cov=. --cov-report=html
# Отчет будет в htmlcov/index.html
```

### Конкретный тест
```bash
pytest tests/unit/test_state_store.py::test_register_command
```

### С маркерами
```bash
pytest -m unit          # Только юнит тесты
pytest -m integration   # Только интеграционные тесты
pytest -m "not slow"    # Исключить медленные тесты
```

## Статус тестов

✅ **Все тесты проходят:** 31/31 passed

### Юнит тесты (22)
- ✅ `test_state_store.py` - 9 тестов
- ✅ `test_event_bus.py` - 5 тестов
- ✅ `test_persistence_store.py` - 4 теста
- ✅ `test_base_http_client.py` - 4 теста

### Интеграционные тесты (9)
- ✅ `test_api_endpoints.py` - 6 тестов
- ✅ `test_status_publisher.py` - 3 теста

## Фикстуры

Основные фикстуры в `conftest.py`:
- `state_store` - экземпляр StateStore для тестирования
- `event_bus_instance` - экземпляр EventBus для тестирования
- `persistence_store` - экземпляр JsonPersistenceStore для тестирования
- `mock_symovo_client` - мок SymovoAgvClient
- `mock_mqtt_adapter` - мок MqttAdapter
- `test_client` - FastAPI TestClient
- `sample_*` - тестовые данные

## Добавление новых тестов

1. **Юнит тесты** - для изолированных компонентов (services, domain)
   - Размещайте в `tests/unit/`
   - Используйте моки для внешних зависимостей
   - Тестируйте логику без реальных HTTP/MQTT соединений

2. **Интеграционные тесты** - для API endpoints и взаимодействия компонентов
   - Размещайте в `tests/integration/`
   - Используйте TestClient для FastAPI endpoints
   - Мокайте внешние сервисы (Symovo API, MQTT)

## Маркеры

- `@pytest.mark.unit` - юнит тесты
- `@pytest.mark.integration` - интеграционные тесты
- `@pytest.mark.slow` - медленные тесты

Запуск по маркерам:
```bash
pytest -m unit
pytest -m integration
pytest -m "not slow"
```
