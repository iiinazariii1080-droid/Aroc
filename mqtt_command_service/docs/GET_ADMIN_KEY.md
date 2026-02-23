# Как получить админ ключ в Docker контейнере

## Способ 1: Создать первый админ ключ (если ключей еще нет)

Если это первый запуск и админ ключей еще нет, выполните скрипт внутри контейнера:

```bash
# Войти в контейнер
docker exec -it mqtt_command_service bash

# Выполнить скрипт создания первого админ ключа
python3 scripts/create_first_api_key.py
```

**Вывод будет примерно таким:**
```
API Key created!
Key ID: abc123xyz
API Key: sk_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

!!! SAVE THIS KEY NOW! It will not be shown again.
```

**⚠️ ВАЖНО:** Сохраните ключ сразу! Он больше не будет показан.

---

## Способ 2: Создать админ ключ одной командой (без входа в контейнер)

```bash
docker exec mqtt_command_service python3 scripts/create_first_api_key.py
```

---

## Способ 3: Если админ ключ уже существует (но вы его потеряли)

Если админ ключ уже был создан, но вы его потеряли, есть два варианта:

### Вариант 3.1: Проверить существующие ключи (требует админ ключ)

Если у вас есть другой админ ключ:

```bash
curl -X GET "http://localhost:7900/api/v1/auth/api-keys" \
  -H "X-API-Key: your-existing-admin-key"
```

**Примечание:** Этот endpoint показывает только метаданные (key_id, role, description), но **не показывает сам ключ** (по соображениям безопасности).

### Вариант 3.2: Создать новый админ ключ через API (требует админ ключ)

Если у вас есть другой админ ключ:

```bash
curl -X POST "http://localhost:7900/api/v1/auth/api-keys" \
  -H "X-API-Key: your-existing-admin-key" \
  -F "role=admin" \
  -F "description=New admin key"
```

**Ответ:**
```json
{
  "key_id": "abc123xyz",
  "api_key": "sk_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
  "role": "admin",
  "description": "New admin key",
  "warning": "Save this API key now! It will not be shown again."
}
```

### Вариант 3.3: Если нет ни одного админ ключа

Если вы потеряли все админ ключи, нужно:

1. **Остановить контейнер:**
   ```bash
   docker compose down
   ```

2. **Удалить базу данных с ключами** (если она хранится в `./data/config.db`):
   ```bash
   # ВНИМАНИЕ: Это удалит ВСЮ конфигурацию, включая MQTT настройки!
   rm ./data/config.db
   ```

3. **Запустить контейнер заново:**
   ```bash
   docker compose up -d
   ```

4. **Создать новый админ ключ:**
   ```bash
   docker exec mqtt_command_service python3 scripts/create_first_api_key.py
   ```

---

## Использование админ ключа

После получения админ ключа, используйте его в заголовке `X-API-Key`:

```bash
# Пример: Получить конфигурацию брокера
curl -X GET "http://localhost:7900/api/v1/config/broker" \
  -H "X-API-Key: sk_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"

# Пример: Создать новый ключ с ролью write
curl -X POST "http://localhost:7900/api/v1/auth/api-keys" \
  -H "X-API-Key: sk_live_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx" \
  -F "role=write" \
  -F "description=Monitoring key"
```

---

## Роли API ключей

- **`read`** - Только чтение конфигурации
- **`write`** - Чтение и изменение конфигурации
- **`admin`** - Полный доступ, включая управление API ключами

---

## Безопасность

- ⚠️ **Админ ключи имеют полный доступ** - храните их в безопасном месте
- ⚠️ **Ключи показываются только один раз** при создании - сохраняйте их сразу
- ⚠️ **Не коммитьте ключи в Git** - используйте переменные окружения или секреты
- ✅ **Можно отозвать ключи** через API endpoint `/api/v1/auth/api-keys/{key_id}`

---

## Проверка работы ключа

```bash
# Проверить, что ключ работает (должен вернуть список ключей)
curl -X GET "http://localhost:7900/api/v1/auth/api-keys" \
  -H "X-API-Key: your-admin-key"
```

Если ключ валидный, вы получите список всех API ключей. Если нет - ошибку 401 или 403.





