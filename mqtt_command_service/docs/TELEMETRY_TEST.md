# Тест подключения к MQTT брокеру и получения телеметрии

## Описание

Тест `tests/test_telemetry_subscription.py` проверяет:
- ✅ Подключение к MQTT брокеру с TLS
- ✅ Подписку на топик телеметрии
- ✅ Получение сообщений телеметрии
- ✅ Валидацию формата данных

## Использование

### Базовое использование (с конфигурацией из БД/ENV)

```bash
# В контейнере
docker exec mqtt_command_service python3 tests/test_telemetry_subscription.py

# Или локально (если установлены зависимости)
python tests/test_telemetry_subscription.py
```

### С параметрами командной строки

```bash
python tests/test_telemetry_subscription.py \
  --broker your-broker-ip \
  --port 8883 \
  --username bridge_user \
  --password your_password \
  --robot-id fahrdummy-01 \
  --use-tls \
  --ca-certs /app/certs/ca.crt \
  --tls-insecure \
  --message-count 5 \
  --wait-time 120
```

### Параметры

| Параметр | Описание | По умолчанию |
|----------|----------|--------------|
| `--broker` | Адрес MQTT брокера | Из конфигурации |
| `--port` | Порт MQTT брокера | Из конфигурации (8883 для TLS) |
| `--username` | Имя пользователя MQTT | Из конфигурации |
| `--password` | Пароль MQTT | Из конфигурации |
| `--robot-id` | ID робота | Из конфигурации |
| `--use-tls` | Включить TLS | Из конфигурации |
| `--no-tls` | Отключить TLS | - |
| `--ca-certs` | Путь к CA сертификату | Из конфигурации |
| `--tls-insecure` | Отключить проверку сертификата | Из конфигурации |
| `--timeout` | Таймаут подключения (сек) | 30.0 |
| `--wait-time` | Время ожидания сообщений (сек) | 60.0 |
| `--message-count` | Количество сообщений для ожидания | 3 |
| `--subscribe-status` | Также подписаться на status топики | - |

## Примеры использования

### 1. Быстрая проверка подключения

```bash
# Подождать 3 сообщения телеметрии (по умолчанию)
python tests/test_telemetry_subscription.py
```

### 2. Проверка с подпиской на все топики

```bash
# Подписаться также на status/system, status/connection, status/navigation
python tests/test_telemetry_subscription.py --subscribe-status
```

### 3. Долгая проверка (для отладки)

```bash
# Ждать 10 сообщений в течение 5 минут
python tests/test_telemetry_subscription.py \
  --message-count 10 \
  --wait-time 300
```

### 4. Тест с явными параметрами

```bash
python tests/test_telemetry_subscription.py \
  --broker your-broker-ip \
  --port 8883 \
  --username bridge_user \
  --password your_password \
  --robot-id fahrdummy-01 \
  --use-tls \
  --tls-insecure
```

## Вывод теста

Тест выводит:

1. **Конфигурацию подключения** (без пароля)
2. **Статус подключения** (✅ или ❌)
3. **Статус подписки** (✅ или ❌)
4. **Полученные сообщения** с деталями
5. **Результаты валидации** формата данных
6. **Сводку** всех полученных сообщений

### Пример успешного вывода:

```
================================================================================
🔧 TEST CONFIGURATION
================================================================================
Broker: your-broker-ip:8883
TLS: True
CA Certs: /app/certs/ca.crt
TLS Insecure: True
Username: bridge_user
Password: ***
Robot ID: fahrdummy-01
================================================================================

[2025-12-12 15:00:00] [INFO] Connecting to mqtts://your-broker-ip:8883...
[2025-12-12 15:00:01] [INFO] ✅ Connected to MQTT broker mqtts://your-broker-ip:8883
[2025-12-12 15:00:01] [INFO] Subscribing to topic: aroc/robot/fahrdummy-01/telemetry
[2025-12-12 15:00:01] [INFO] ✅ Subscribed to topic (mid=1, qos=1)
[2025-12-12 15:00:01] [INFO] Waiting for 3 message(s) (timeout: 60.0 seconds)...
[2025-12-12 15:00:05] [INFO] 📨 Received message on topic: aroc/robot/fahrdummy-01/telemetry
[2025-12-12 15:00:20] [INFO] 📨 Received message on topic: aroc/robot/fahrdummy-01/telemetry
[2025-12-12 15:00:35] [INFO] 📨 Received message on topic: aroc/robot/fahrdummy-01/telemetry
[2025-12-12 15:00:35] [INFO] ✅ Received 3 message(s)
[2025-12-12 15:00:35] [INFO] Validating telemetry messages...
[2025-12-12 15:00:35] [INFO] ✅ Telemetry message format is valid
[2025-12-12 15:00:35] [INFO] ✅ All telemetry messages are valid

================================================================================
📊 TEST SUMMARY
================================================================================
Broker: your-broker-ip:8883 (TLS)
Robot ID: fahrdummy-01
Messages received: 3

📨 Received Messages:

  Message 1:
    Topic: aroc/robot/fahrdummy-01/telemetry
    QoS: 1
    Timestamp: 2025-12-12T15:00:05.123456+00:00
    Robot ID: fahrdummy-01
    Payload timestamp: 2025-12-12T15:00:05.000000Z
    Pose: x=2.5, y=3.8, theta=0.75
    Battery: 85.5%
    State: idle

  Message 2:
    ...

================================================================================
```

## Коды возврата

- `0` - Успех (получено хотя бы одно сообщение)
- `1` - Ошибка (не удалось подключиться или не получено ни одного сообщения)
- `130` - Прервано пользователем (Ctrl+C)

## Валидация данных

Тест проверяет:

### Обязательные поля:
- ✅ `robot_id` - должен совпадать с указанным
- ✅ `timestamp` - должен быть в формате ISO-8601
- ✅ `data` - должен быть объектом

### Структура `data`:
- ✅ `pose` - позиция робота (x, y, theta, map_id)
- ✅ `velocity` - скорость (vx, vy, omega)
- ✅ `battery_percent` - уровень батареи
- ✅ `state` - состояние робота

### Опциональные поля:
- ⚠️ `components` - статусы компонентов (igus, xarm)

## Устранение проблем

### Ошибка подключения

```
❌ Failed to connect: Connection refused - bad username or password
```

**Решение:** Проверьте правильность username и password в конфигурации.

### Таймаут подключения

```
Connection timeout after 30.0 seconds
```

**Решение:** 
- Проверьте доступность брокера
- Проверьте настройки firewall
- Увеличьте таймаут: `--timeout 60`

### Не получены сообщения

```
⚠️  No messages received
```

**Решение:**
- Убедитесь, что сервис телеметрии запущен и публикует данные
- Проверьте правильность `robot_id`
- Увеличьте время ожидания: `--wait-time 120`
- Уменьшите количество ожидаемых сообщений: `--message-count 1`

### Ошибка TLS

```
Failed to configure TLS: [Errno 2] No such file or directory: '/app/certs/ca.crt'
```

**Решение:**
- Проверьте наличие CA сертификата
- Укажите правильный путь: `--ca-certs /path/to/ca.crt`
- Или используйте `--tls-insecure` для тестирования (не рекомендуется для production)

## Интеграция в CI/CD

Пример использования в CI:

```yaml
# .github/workflows/test.yml
- name: Test MQTT Telemetry
  run: |
    docker exec mqtt_command_service python3 tests/test_telemetry_subscription.py \
      --message-count 1 \
      --wait-time 30
```

## Примечания

- Тест использует реальное подключение к брокеру (интеграционный тест)
- Для unit-тестов используйте `tests/test_mqtt_client_service.py`
- Тест автоматически загружает конфигурацию из БД или переменных окружения
- Пароль не выводится в логах для безопасности





