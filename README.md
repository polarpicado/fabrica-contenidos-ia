# 🚀 Fábrica Contenidos IA

Generador de piezas visuales para marketing veterinario con flujo automatizado.

## 🧱 Stack
- `n8n` (orquestación de workflows)
- `python-api` (render con Pillow + webhooks de deAPI)
- `nginx` (proxy + galería web)
- `postgres` (persistencia)

## 🖼️ Galería de resultados
- Web local: `http://localhost:8083`
- API índice: `http://localhost:8083/api/outputs-index`
- Imagen directa: `http://localhost:8083/api/outputs/<archivo>.png`

## 🌍 URL pública temporal (Cloudflare Tunnel)
- `https://collins-travis-discrimination-villages.trycloudflare.com`

## 📁 Estructura recomendada
- `api/` → backend FastAPI
- `web/` → frontend de galería
- `scripts/` → utilidades (smoke tests)
- `tests/payloads/` → payloads activos de prueba
- `tests/payloads/archive/` → payloads históricos
- `outputs/` → imágenes generadas (no versionar)

## ✅ Smoke test rápido
```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\smoke-test.ps1
```

Valida:
- Home web local
- Health API local
- Índice de outputs local
- Home pública vía tunnel
- Índice de outputs público

## 🔐 Seguridad (pendiente recomendada)
Se detectaron hallazgos con `pip-audit`:
- `cairosvg 2.7.1` → actualizar a `>=2.9.0`
- `requests 2.32.3` → actualizar a `>=2.32.4`
- `starlette 0.47.3` → actualizar a `>=0.49.1`
- `pip 25.0.1` → actualizar en imagen base

También se recomienda mover secretos de `docker-compose.yml` a `.env`.

## 🛠️ Cambios recientes
- Endpoint `GET /outputs-index` para listar imágenes por fecha.
- Galería web agrupada por fecha, con preview y apertura en nueva pestaña.
- Webhook `POST /webhook/deapi` con validación HMAC opcional.
- Limpieza inicial de payloads y orden de estructura de pruebas.
- `.gitignore` reforzado para evitar subir datos sensibles/runtime.
