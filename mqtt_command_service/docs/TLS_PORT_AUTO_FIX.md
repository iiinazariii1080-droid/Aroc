# Автоматическое переключение порта при включении TLS

**Дата:** 2025-12-12  
**Проблема:** При включении TLS (`MQTT_USE_TLS=true`) сервис пытался подключиться к порту 1883 (обычный MQTT) вместо 8883 (MQTTS), что приводило к ошибке `ConnectionResetError: [Errno 104] Connection reset by peer`.

## Решение

Добавлена автоматическая корректировка порта в функции `load_bridge_config()`:

- **Если TLS включен (`MQTT_USE_TLS=true`) и порт = 1883** → автоматически переключается на 8883
- **Если TLS выключен (`MQTT_USE_TLS=false`) и порт = 8883** → автоматически переключается на 1883

Изменения сохраняются в базу данных для предотвращения повторения проблемы.

## Код

```python
# Auto-adjust port based on TLS setting
if mqtt_use_tls and broker_port == MQTT_DEFAULT_PORT:
    logger.info(
        "TLS enabled but port is %d (default). Auto-switching to TLS port %d",
        broker_port,
        MQTT_TLS_PORT
    )
    broker_port = MQTT_TLS_PORT
    save_config_variable("MQTT_PORT", str(MQTT_TLS_PORT), updated_by="system", reason="Auto-switched to TLS port")
elif not mqtt_use_tls and broker_port == MQTT_TLS_PORT:
    logger.info(
        "TLS disabled but port is %d (TLS port). Auto-switching to default port %d",
        broker_port,
        MQTT_DEFAULT_PORT
    )
    broker_port = MQTT_DEFAULT_PORT
    save_config_variable("MQTT_PORT", str(MQTT_DEFAULT_PORT), updated_by="system", reason="Auto-switched to default port")
```

## Использование

После обновления кода:

1. Перезапустите сервис:
   ```bash
   docker compose down
   docker compose up -d
   ```

2. При следующем запуске, если обнаружено несоответствие порта и TLS настройки, порт будет автоматически скорректирован и сохранен в БД.

3. Проверьте логи - должно появиться сообщение:
   ```
   TLS enabled but port is 1883 (default). Auto-switching to TLS port 8883
   ```

## Примечания

- Автоматическая корректировка происходит только при загрузке конфигурации (при старте сервиса)
- Если порт явно установлен на нестандартное значение (не 1883 и не 8883), автоматическая корректировка не выполняется
- Изменения сохраняются в базу данных, поэтому проблема не повторится при следующем запуске





