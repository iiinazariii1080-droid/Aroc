# Устранение проблемы: "Unknown service 'mqtt'"

## Проблема

После добавления сервиса "mqtt" в конфигурацию, при отправке команды на топик `aroc/robot/fahrdummy-01/cmd/mqtt` получаем ошибку:

```json
{
  "type": "ack",
  "service": "mqtt",
  "success": false,
  "status_code": 400,
  "body": {"detail": "Unknown service 'mqtt'"},
  "error": {"type": "routing_error", "message": "Unknown service"}
}
```

## Причина

Сервис "mqtt" добавлен в код (`config.py`), но **сервис не был перезапущен** после изменения кода. Конфигурация загружается при старте сервиса, поэтому изменения в коде требуют перезапуска.

## Решение

### 1. Перезапустить Docker контейнер

```bash
# Остановить контейнер
docker compose down

# Запустить заново
docker compose up -d

# Проверить логи
docker compose logs -f
```

### 2. Проверить, что сервис загружен

После перезапуска проверьте логи - должны быть видны загруженные сервисы:

```bash
docker compose logs | grep -i "service\|mqtt"
```

### 3. Проверить конфигурацию через Python

В контейнере можно проверить загруженные сервисы:

```bash
docker exec mqtt_command_service python3 -c "
from config import load_bridge_config
config = load_bridge_config()
print('Services:', list(config.services.keys()))
print('MQTT service:', 'mqtt' in config.services)
if 'mqtt' in config.services:
    print('MQTT URL:', config.services['mqtt'].base_url)
"
```

**Ожидаемый вывод:**
```
Services: ['robot', 'igus', 'xarm', 'symovo', 'mqtt']
MQTT service: True
MQTT URL: http://localhost:7900
```

### 4. Если сервис все еще не найден

Проверьте переменные окружения:

```bash
# Проверить API_PORT
docker exec mqtt_command_service env | grep API_PORT

# Проверить SERVICE_USE_LOCAL
docker exec mqtt_command_service env | grep SERVICE_USE_LOCAL
```

## Альтернативное решение: Переопределение через переменную окружения

Если перезапуск не помог, можно переопределить конфигурацию через переменную окружения:

```bash
# В docker-compose.yml или .env
SERVICE_MAP_JSON='{"mqtt":{"base_url":"http://localhost:7900"}}'
```

Или для локального режима:

```bash
SERVICE_USE_LOCAL=1
```

## Проверка работы

После перезапуска попробуйте отправить команду снова:

```bash
mosquitto_pub -h your-broker-ip -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'password' \
  -t "aroc/robot/fahrdummy-01/cmd/mqtt" \
  -m '{
    "request_id": "test-001",
    "method": "GET",
    "path": "/health"
  }'
```

**Ожидаемый ответ:**
```json
{
  "type": "ack",
  "request_id": "test-001",
  "service": "mqtt",
  "success": true,
  "status_code": 200,
  "body": {
    "status": "healthy",
    ...
  }
}
```

## Примечания

- Изменения в `config.py` требуют **перезапуска сервиса**
- Конфигурация загружается при инициализации `BridgeConfig`
- Сервисы не перезагружаются динамически (только при перезапуске)
- Для динамического изменения сервисов используйте `SERVICE_MAP_JSON`





