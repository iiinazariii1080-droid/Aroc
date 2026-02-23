# Чек-лист ручного тестирования

Этот документ содержит чек-лист для ручного тестирования всех требований архитектуры.

## Подготовка

1. Убедитесь, что сервис запущен:
   ```bash
   python main.py
   ```

2. Убедитесь, что MQTT брокер доступен (можно использовать тестовый брокер)

3. Убедитесь, что API включен:
   ```bash
   export API_ENABLED=true
   ```

## 1. Старт сервиса

### Тест 1.1: Старт с валидным конфигом
- [ ] Сервис стартует без ошибок
- [ ] Подключается к брокеру (проверить логи: "MQTT connected")
- [ ] Публикует статусы (проверить MQTT топики)
- [ ] `/health` показывает `mqtt_broker: "ok"`

**Команды для проверки:**
```bash
# Проверить логи
tail -f logs/mqtt_service.log | grep -i "connected\|error"

# Проверить health
curl http://localhost:7900/health

# Проверить MQTT публикации (если есть mosquitto_sub)
mosquitto_sub -h <broker> -p <port> -t "aroc/robot/+/status/+"
```

## 2. Потеря сети / брокера

### Тест 2.1: Отключение брокера
- [ ] Отключить брокер/закрыть порт
- [ ] Сервис переходит в `mqtt_connected=False` (проверить логи)
- [ ] Появляются 1–2 WARN, затем редкие уведомления (throttling)
- [ ] Восстановить брокер
- [ ] Сервис автоматически переподключается
- [ ] `mqtt_connected=True` (проверить логи)
- [ ] Статусы и телеметрия снова публикуются

**Команды для проверки:**
```bash
# Отключить брокер (зависит от вашей настройки)
sudo systemctl stop mosquitto  # или другой способ

# Проверить логи (должны быть throttled)
tail -f logs/mqtt_service.log | grep -i "disconnect\|reconnect"

# Восстановить брокер
sudo systemctl start mosquitto

# Проверить автоматическое переподключение
tail -f logs/mqtt_service.log | grep -i "connected"
```

## 3. Изменение конфига без рестарта

### Тест 3.1: Изменение host/port брокера
- [ ] Изменить host/port брокера через API:
  ```bash
  curl -X POST http://localhost:7900/api/v1/config/broker \
    -H "Content-Type: application/json" \
    -H "X-API-Key: <your-key>" \
    -d '{"broker": "new.broker.com", "broker_port": 1883}'
  ```
- [ ] Bridge и telemetry автоматически переподключаются к новому брокеру
- [ ] Нет необходимости рестартовать systemd-сервис/контейнер
- [ ] Все топики продолжают работать

### Тест 3.2: Включение/отключение TLS
- [ ] Включить TLS через API:
  ```bash
  curl -X POST http://localhost:7900/api/v1/config/broker/tls/toggle \
    -H "Content-Type: application/json" \
    -H "X-API-Key: <your-key>" \
    -d '{"enable": true, "test_connection": true}'
  ```
- [ ] Bridge и telemetry автоматически переподключаются с TLS
- [ ] Проверить, что порт изменился на 8883

### Тест 3.3: Замена сертификатов
- [ ] Загрузить новые сертификаты через API
- [ ] Bridge и telemetry используют новые сертификаты без рестарта

**Команды для проверки:**
```bash
# Проверить текущий конфиг
curl http://localhost:7900/api/v1/config/broker

# Проверить логи переподключения
tail -f logs/mqtt_service.log | grep -i "config changed\|reconnect"
```

## 4. Неверные креды / TLS-ошибки

### Тест 4.1: Неправильный пароль/логин
- [ ] Ввести неправильный пароль через API:
  ```bash
  curl -X POST http://localhost:7900/api/v1/config/broker \
    -H "Content-Type: application/json" \
    -H "X-API-Key: <your-key>" \
    -d '{"mqtt_password": "wrong_password"}'
  ```
- [ ] Тест подключения в UI/API показывает ошибку
- [ ] Сервис не переходит в бесконечный reconnect без понятного лога

### Тест 4.2: Неправильный CA/client-cert
- [ ] Подсунуть неправильный CA/client-cert
- [ ] Тест подключения падает с понятным сообщением
- [ ] В логах видно, что именно TLS-ошибка

**Команды для проверки:**
```bash
# Тест подключения
curl -X POST http://localhost:7900/api/v1/config/broker/test-connection \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <your-key>"

# Проверить логи ошибок
tail -f logs/mqtt_service.log | grep -i "tls\|certificate\|auth"
```

## 5. Поведение телеметрии при отключённом MQTT

### Тест 5.1: Отключение брокера
- [ ] Отключить брокер
- [ ] Телеметрия не публикуется (проверить MQTT топики)
- [ ] Сервис не падает
- [ ] WARN не спамятся каждую секунду (throttling)

**Команды для проверки:**
```bash
# Отключить брокер
sudo systemctl stop mosquitto

# Проверить логи (не должно быть спама)
tail -f logs/telemetry.log | grep -i "warn\|error"

# Проверить, что телеметрия не публикуется
mosquitto_sub -h <broker> -p <port> -t "telemetry" -v
# (не должно быть сообщений)
```

## 6. Regression-тест

### Тест 6.1: Множественные изменения конфига
- [ ] Сменить конфиг несколько раз подряд:
  1. Порт 1883 → 8883 (TLS)
  2. Другой host
  3. Обратно к исходному
- [ ] Сервис остаётся живым
- [ ] Нигде не требуется ручной перезапуск
- [ ] Не остаётся "висящих" старых соединений

**Команды для проверки:**
```bash
# Проверить процессы (не должно быть зомби)
ps aux | grep python | grep -v grep

# Проверить сетевые соединения
netstat -an | grep <broker_port>

# Проверить логи на ошибки
tail -f logs/mqtt_service.log | grep -i "error\|exception"
```

## 7. Health Endpoint

### Тест 7.1: Проверка health endpoint
- [ ] `GET /health` возвращает корректный JSON
- [ ] Поля `mqtt_bridge` и `mqtt_telemetry` присутствуют (даже если "unknown")
- [ ] `last_publish_time` присутствует (может быть null)

**Команда:**
```bash
curl http://localhost:7900/health | jq
```

## 8. Логирование

### Тест 8.1: Throttling логирования
- [ ] Отключить брокер
- [ ] Проверить, что повторяющиеся сообщения не спамятся
- [ ] Должно быть сообщение "Previous message repeated N times"

### Тест 8.2: Контекст в логах
- [ ] Проверить, что в логах есть:
  - Broker URI (mqtt:// или mqtts://)
  - User
  - Компонент (bridge/telemetry/api)

**Команды:**
```bash
# Проверить логи
tail -f logs/mqtt_service.log | grep -E "\[bridge\]|\[telemetry\]|mqtt://|mqtts://"
```

## 9. Graceful Shutdown

### Тест 9.1: SIGTERM/SIGINT
- [ ] Отправить SIGTERM процессу:
  ```bash
  kill -TERM <pid>
  ```
- [ ] Все компоненты корректно завершаются
- [ ] MQTT-подключения закрываются
- [ ] Нет зомби-потоков

**Команды:**
```bash
# Найти PID
ps aux | grep "python.*main.py"

# Отправить SIGTERM
kill -TERM <pid>

# Проверить, что процесс завершился
ps aux | grep <pid>

# Проверить логи завершения
tail -f logs/mqtt_service.log | grep -i "shutdown\|stopped"
```

## Результаты тестирования

Заполните после выполнения тестов:

- [ ] Все тесты пройдены успешно
- [ ] Обнаружены проблемы (описать ниже)
- [ ] Требуется доработка (описать ниже)

### Обнаруженные проблемы:
```
(Опишите найденные проблемы)
```

### Требуется доработка:
```
(Опишите, что нужно доработать)
```

