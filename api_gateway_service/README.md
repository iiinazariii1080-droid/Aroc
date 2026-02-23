## API Gateway Service

Resilient FastAPI/Gunicorn gateway that proxies HTTP traffic to internal services and aggregates their OpenAPI specs.

### Environment variables
- Runtime mode: `APP_ENV` (`dev`/`prod`), `STRICT_RUNTIME` (defaults to enabled in `prod`)
- `LOCAL_IP` (default `192.168.1.10`)
- `DEPTH_CAMERA_IP` (default `192.168.1.55`)
- Flexible service config: `SERVICE_<KEY>_URL`, `SERVICE_<KEY>_PREFIX`, `SERVICE_MAP_JSON`
- Client security: `VERIFY_TLS`, `ALLOW_INSECURE_TLS`
- Secrets: `ROBOT_API_KEY` (optional env override), `ROBOT_API_KEY_FILE` (file-based source, required in strict runtime)
- HTTP client tuning: `HTTP_CONNECT_TIMEOUT`, `HTTP_READ_TIMEOUT`, `HTTP_WRITE_TIMEOUT`, `HTTP_POOL_TIMEOUT`, `HTTP_MAX_CONNECTIONS`, `HTTP_MAX_KEEPALIVE_CONNECTIONS`, `HTTP_KEEPALIVE_EXPIRY`, `HTTP_RETRY_ATTEMPTS`, `HTTP_RETRY_BACKOFF`
- Server knobs: `PORT` (default `8201`), `WEB_CONCURRENCY`, `UVICORN_WORKERS`
- Readiness probes: `READINESS_CHECK_SERVICES`, `READINESS_CHECK_AUTH`, `READINESS_CHECK_TIMEOUT`

### Strict runtime behavior
- In strict runtime (`APP_ENV=prod` or `STRICT_RUNTIME=1`) the service validates startup config and fails fast when:
  - insecure TLS toggles are enabled,
  - readiness dependency checks are disabled,
  - demo defaults are configured,
  - `ROBOT_API_KEY_FILE` is missing or points to a non-existing file.

### Containerization
**Build the image**
```bash
docker build -t api_gateway_service:latest .
```

**Run a single container**
```bash
docker run -d --name api_gateway_service \
  --restart unless-stopped \
  -p 8201:8201 \
  -e PORT=8201 \
  api_gateway_service:latest
```

**Docker Compose**
```bash
docker compose up -d
```
The compose file (`docker-compose.yml`) ships with a healthcheck and `restart: unless-stopped`, so Docker restarts the service automatically whenever `/readyz` fails. The base image exposes the same healthcheck for standalone runs.

### Probes and monitoring
- `GET /livez` — lightweight liveness check
- `GET /readyz` — validates HTTP client, upstream dependencies, and auth readiness
- `GET /healthz` — aggregated status with the number of configured services

### Proxy behavior
Any path `/api/v1/{service}/{path}` is proxied to `{SERVICE_MAP[service].url}{prefix}/{path}` while forwarding headers.

### Local run (without Docker)
```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8201
```

