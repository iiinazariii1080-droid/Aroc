# ── Stage 1: build the v2 viewer ──
FROM node:20-slim AS viewer-build
WORKDIR /viewer
COPY viewer/package.json viewer/package-lock.json ./
RUN npm ci
COPY viewer/ .
RUN npm run build

# ── Stage 2: Python runtime ──
FROM python:3.11-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Копируем файл зависимостей
COPY requirements.txt .

# Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt

# Копируем все файлы проекта
COPY . .

# Copy the built viewer from stage 1
COPY --from=viewer-build /viewer/dist /app/viewer/dist

# Создаем пользователя для безопасности
RUN useradd --create-home --shell /bin/bash app && chown -R app:app /app
USER app

# Открываем порт
EXPOSE 8401

# Команда для запуска приложения
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8401"]
