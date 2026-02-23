# FastAPI Application Structure

Этот проект следует best practices FastAPI для организации кода.

## Структура папок

```
app/
├── __init__.py
├── main.py                 # Точка входа FastAPI приложения
├── core/                   # Основные компоненты приложения
│   ├── __init__.py
│   ├── config.py          # Настройки приложения
│   ├── security.py        # Аутентификация и авторизация
│   └── dependencies.py    # FastAPI зависимости
├── api/                    # API роутеры
│   ├── __init__.py
│   └── v1/                # API версия 1
│       ├── __init__.py
│       ├── api.py         # Агрегация всех роутеров
│       └── endpoints/      # Эндпоинты
│           ├── __init__.py
│           ├── auth.py    # Аутентификация
│           ├── broker.py  # Конфигурация брокера
│           ├── certificates.py  # Управление сертификатами
│           └── health.py  # Health check
├── models/                 # Pydantic схемы
│   ├── __init__.py
│   └── schemas.py         # Модели запросов/ответов
├── services/               # Бизнес-логика
│   ├── __init__.py
│   └── certificate_service.py  # Сервис управления сертификатами
├── middleware/             # Middleware
│   ├── __init__.py
│   ├── logging.py         # Логирование запросов
│   └── rate_limit.py      # Rate limiting
└── utils/                  # Утилиты
    ├── __init__.py
    └── certificate.py     # Утилиты для работы с сертификатами
```

## Основные компоненты

### Core
- **config.py**: Настройки приложения (API title, version, paths, etc.)
- **security.py**: Аутентификация через API ключи, роли (READ, WRITE, ADMIN)
- **dependencies.py**: FastAPI зависимости для инъекции

### API Endpoints
- **auth.py**: Создание API ключей
- **broker.py**: Управление конфигурацией MQTT брокера
- **certificates.py**: Загрузка и управление TLS сертификатами
- **health.py**: Health check эндпоинт

### Services
- **certificate_service.py**: Бизнес-логика для работы с сертификатами (валидация, сохранение)

### Middleware
- **logging.py**: Логирование всех запросов с информацией об аутентификации
- **rate_limit.py**: Настройка rate limiting через slowapi

### Models
- **schemas.py**: Pydantic модели для валидации запросов и формирования ответов

## Использование

### Запуск приложения

```python
from app.main import app

# Или через uvicorn
uvicorn app.main:app --host 0.0.0.0 --port 7900
```

## Преимущества структуры

1. **Разделение ответственности**: Каждый модуль отвечает за свою область
2. **Масштабируемость**: Легко добавлять новые эндпоинты и версии API
3. **Тестируемость**: Каждый компонент можно тестировать отдельно
4. **Читаемость**: Понятная структура папок
5. **Переиспользование**: Сервисы и утилиты можно использовать в разных местах

