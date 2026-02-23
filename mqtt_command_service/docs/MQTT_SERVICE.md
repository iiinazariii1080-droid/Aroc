# Сервис MQTT (API Proxy)

## Описание

Добавлен новый сервис `mqtt` для проксирования HTTP запросов к API сервису через MQTT.

**Топик:** `aroc/robot/{ROBOT_ID}/cmd/mqtt`

**Назначение:** Позволяет вызывать API endpoints (порт 7900) через MQTT команды.

## Конфигурация

Сервис автоматически добавляется в конфигурацию при загрузке:

- **Base URL:** `http://localhost:7900` (порт берется из `API_PORT`, по умолчанию 7900)
- **Доступен в:** Локальном и удаленном режимах

## Использование

### Пример 1: Получить конфигурацию брокера

```bash
mosquitto_pub -h your-broker-ip -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'password' \
  -t "aroc/robot/fahrdummy-01/cmd/mqtt" \
  -m '{
    "request_id": "get-broker-config-001",
    "method": "GET",
    "path": "/api/v1/config/broker",
    "headers": {
      "X-API-Key": "your-api-key"
    }
  }'
```

**Ответ будет опубликован в топик:** `aroc/robot/fahrdummy-01/resp/mqtt`

### Пример 2: Обновить конфигурацию брокера

```bash
mosquitto_pub -h your-broker-ip -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'password' \
  -t "aroc/robot/fahrdummy-01/cmd/mqtt" \
  -m '{
    "request_id": "update-broker-001",
    "method": "POST",
    "path": "/api/v1/config/broker",
    "headers": {
      "X-API-Key": "your-api-key",
      "Content-Type": "application/json"
    },
    "body": {
      "broker": "your-broker-ip",
      "broker_port": 8883,
      "mqtt_use_tls": true
    }
  }'
```

### Пример 3: Получить список API ключей

```bash
mosquitto_pub -h your-broker-ip -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'password' \
  -t "aroc/robot/fahrdummy-01/cmd/mqtt" \
  -m '{
    "request_id": "list-keys-001",
    "method": "GET",
    "path": "/api/v1/auth/api-keys",
    "headers": {
      "X-API-Key": "your-admin-api-key"
    }
  }'
```

### Пример 4: Health check

```bash
mosquitto_pub -h your-broker-ip -p 8883 \
  --cafile /path/to/ca.crt \
  -u bridge_user -P 'password' \
  -t "aroc/robot/fahrdummy-01/cmd/mqtt" \
  -m '{
    "request_id": "health-check-001",
    "method": "GET",
    "path": "/health"
  }'
```

## Формат запроса

Стандартный формат HTTP Proxy команды:

```json
{
  "request_id": "unique-request-id",
  "method": "GET|POST|PUT|DELETE",
  "path": "/api/v1/...",
  "headers": {
    "X-API-Key": "your-api-key",
    "Content-Type": "application/json"
  },
  "body": {
    // JSON body для POST/PUT запросов
  },
  "timeout": 5.0
}
```

## Формат ответа

Ответ публикуется в топик: `aroc/robot/{ROBOT_ID}/resp/mqtt`

**Успешный ответ:**
```json
{
  "type": "ack",
  "request_id": "unique-request-id",
  "service": "mqtt",
  "success": true,
  "status_code": 200,
  "headers": {
    "Content-Type": "application/json"
  },
  "body": {
    // Ответ от API
  },
  "error": null
}
```

**Ошибка:**
```json
{
  "type": "ack",
  "request_id": "unique-request-id",
  "service": "mqtt",
  "success": false,
  "status_code": 401,
  "headers": {},
  "body": null,
  "error": {
    "type": "http_error",
    "message": "Unauthorized"
  }
}
```

## Особенности

1. **Аутентификация:** Большинство endpoints требуют API ключ в заголовке `X-API-Key`
2. **Локальный доступ:** Сервис использует `localhost:7900`, так как API работает в том же контейнере
3. **Порт:** Порт берется из переменной окружения `API_PORT` (по умолчанию 7900)
4. **Таймаут:** По умолчанию используется `HTTP_TIMEOUT` (5 секунд), можно переопределить в запросе

## Переопределение конфигурации

Можно переопределить URL сервиса через переменную окружения:

```bash
SERVICE_MAP_JSON='{"mqtt":{"base_url":"http://192.168.1.10:7900"}}'
```

Или для локального режима:

```bash
SERVICE_USE_LOCAL=1
```

## Примеры использования в коде

### Python

```python
import paho.mqtt.client as mqtt
import json

client = mqtt.Client()
client.username_pw_set("bridge_user", "password")
client.tls_set(ca_certs="/path/to/ca.crt")
client.connect("your-broker-ip", 8883)

# Отправить запрос
request = {
    "request_id": "test-001",
    "method": "GET",
    "path": "/api/v1/config/broker",
    "headers": {
        "X-API-Key": "your-api-key"
    }
}

client.publish(
    "aroc/robot/fahrdummy-01/cmd/mqtt",
    json.dumps(request),
    qos=1
)

# Подписаться на ответ
def on_message(client, userdata, msg):
    response = json.loads(msg.payload)
    print(f"Response: {response}")

client.subscribe("aroc/robot/fahrdummy-01/resp/mqtt", qos=1)
client.on_message = on_message
client.loop_forever()
```

## Примечания

- Сервис работает только если API включен (`API_ENABLED=true`)
- Все запросы идут на `localhost:7900` внутри контейнера
- Для внешнего доступа используйте прямой HTTP запрос к API
- MQTT proxy полезен для удаленного управления через MQTT брокер





