# Configuration & Secrets Reference

Predictrix reads **all** configuration from environment variables — no secrets
are hard-coded in source.

- **Local dev:** put values in `backend/config/.env` (auto-loaded when
  `ALLOW_LOCAL_DOTENV` is true — the default in dev). Mobile values go in
  `mobile_app/.env`.
- **Production:** values come from the platform secret manager (Fly.io
  `fly secrets`, Azure App Settings). Local `.env` loading is disabled in
  production by design.

Templates: [`backend/config/.env.example`](../backend/config/.env.example) ·
[`mobile_app/.env.example`](../mobile_app/.env.example)

**Legend** — **Required (prod):** startup fails without it when
`STRICT_RUNTIME_SECRETS` / production mode is on. **Optional:** has a safe default.

---

## Core secrets

| Variable | Required | Purpose | How to obtain / set |
|---|---|---|---|
| `MONGO_URI` | **Required (prod)** | MongoDB connection string. Dev falls back to `mongodb://localhost:27017/users_db`. | MongoDB Atlas free cluster, or a local `mongod`. |
| `SECRET_KEY` | **Required (prod)** | Flask / JWT signing secret. Dev falls back to an insecure key. | `python -c "import secrets; print(secrets.token_hex(32))"` |

## Email (Flask-Mail) — required for email verification & password reset

| Variable | Required | Purpose | Default |
|---|---|---|---|
| `MAIL_USERNAME` | For email features | SMTP account (e.g. a Gmail address). | — |
| `MAIL_PASSWORD` | For email features | SMTP password / app password. Gmail: enable 2FA → create an **App Password**. | — |
| `MAIL_SERVER` | Optional | SMTP host. | `smtp.gmail.com` |
| `MAIL_PORT` | Optional | SMTP port. | `587` |
| `MAIL_USE_TLS` | Optional | Use STARTTLS. | `True` |
| `MAIL_USE_SSL` | Optional | Use SSL. | `False` |
| `MAIL_DEFAULT_SENDER` | Optional | From address. | = `MAIL_USERNAME` |

## AI / LLM (Google Gemini) — OPTIONAL (market-analysis narrative)

| Variable | Required | Purpose | Default |
|---|---|---|---|
| `GEMINI_API_KEY_1` … `GEMINI_API_KEY_21` | Optional | Rotation pool of Gemini keys for the market-analysis text. With none set, LLM analysis is skipped. Get keys from **Google AI Studio**; add as many as you have (1–21). | none |
| `GEMINI_SAFETY_MODE` | Optional | `strict` / `balanced`. | `strict` (prod) / `balanced` (dev) |
| `ALLOW_EXTERNAL_LLM_FALLBACK` | Optional | Allow a non-Gemini fallback provider. | `false` |
| `POLLINATIONS_FAIL_THRESHOLD` | Optional | Fallback failures before cooldown. | `3` |
| `POLLINATIONS_COOLDOWN_SECONDS` | Optional | Fallback cooldown. | `120` |
| `AI_PROMPT_MAX_CHARS` | Optional | Max prompt size. | `12000` |

## External market data — OPTIONAL

| Variable | Required | Purpose | Default |
|---|---|---|---|
| `ALPHAVANTAGE_API_KEY` | Optional | Secondary data source. The **primary** source is Yahoo Finance (`yfinance`), which needs **no key**. | none |

## JWT / session lifecycle

| Variable | Default |
|---|---|
| `JWT_ISSUER` | `predictrix-api` |
| `JWT_AUDIENCE` | `predictrix-mobile` |
| `ACCESS_TOKEN_EXPIRATION_MINUTES` | `60` (min 5) |
| `REFRESH_TOKEN_EXPIRATION_DAYS` | `30` (min 1) |

## Runtime / operations

| Variable | Purpose | Default |
|---|---|---|
| `APP_ENV` / `ENVIRONMENT` | Set `production` / `staging` to force production mode. | unset (dev) |
| `DEBUG` | Flask debug mode. | `true` dev / `false` prod |
| `ALLOW_LOCAL_DOTENV` | Load `config/.env` locally. | `true` dev / `false` prod |
| `STRICT_RUNTIME_SECRETS` | Fail fast if required secrets are missing. | `false` dev / `true` prod |
| `API_HOST` / `API_PORT` | Bind address. | `0.0.0.0` / `5000` |
| `LOG_LEVEL` | Log verbosity. | `INFO` |
| `APP_PROCESS_ROLE` | `api` / `worker` / `all`. | `all` |
| `BACKGROUND_WORKERS_ENABLED` | Toggle the scheduler inside the API process. | — |
| `MODEL_CONTRACT_REQUIRED` | Fail if the model manifest/contract is missing. | `true` prod / `false` dev |
| `GIT_COMMIT` | Stamped into the model manifest. | — |

## Rate limiting, proxy & CORS

| Variable | Purpose | Default |
|---|---|---|
| `RATE_LIMIT_REDIS_URL` | Redis URL for distributed rate limiting (multi-instance). | none (in-memory) |
| `AUTH_RATE_LIMIT_MAX_REQUESTS` | Auth requests per window. | `20` |
| `AUTH_RATE_LIMIT_WINDOW_SECONDS` | Auth rate-limit window. | `60` |
| `TRUSTED_PROXY_COUNT` | Proxy hops trusted for client-IP resolution. | `1` |
| `CORS_ALLOWED_ORIGINS` | Comma-separated production allowlist. | none |
| `ALLOW_ALL_CORS` | Allow every origin (dev only; set by `start-local.ps1`). | `false` |
| `ANALYSIS_CIRCUIT_FAIL_THRESHOLD` | Market-analysis circuit breaker trips. | `3` |
| `ANALYSIS_CIRCUIT_COOLDOWN_SECONDS` | Circuit breaker cooldown. | `180` |

## Feature flags / governance

| Variable | Purpose | Default |
|---|---|---|
| `ALLOW_DEMO_AUTH_CODES` | Return OTP codes in API responses for local testing. **Never enable in production.** | `false` |
| `ALLOW_TEST_NOTIFICATION_ENDPOINT` | Enable `/notifications/test` outside dev. | `false` |
| `POLICY_TERMS_VERSION` | Terms-of-service consent version. | `2026-04-04` |
| `POLICY_PRIVACY_VERSION` | Privacy-policy consent version. | `2026-04-04` |

## Platform-injected (do NOT set by hand)

| Variable | Meaning |
|---|---|
| `FLY_APP_NAME` | Present on Fly.io — used to auto-detect production. |
| `WEBSITE_SITE_NAME` | Present on Azure — used to auto-detect production. |

---

## Mobile app (Expo) — `mobile_app/.env`

> **Note:** Any `EXPO_PUBLIC_*` value is **bundled into the app binary** and is
> **not secret**. Never put private keys here.

| Variable | When | Purpose | Default |
|---|---|---|---|
| `EXPO_PUBLIC_API_BASE_URL` | Release builds | Backend URL the production app calls. | `https://predictrix-api.fly.dev` |
| `EXPO_PUBLIC_LOCAL_API_HOST` | Dev (physical device) | Your PC's LAN IP so a real device can reach the local backend. | emulator uses `10.0.2.2` |

## ML training — `model & backtest result/`

| Variable | Purpose | Default |
|---|---|---|
| `MULTI_SEED_COUNT` | Number of training seeds. | `3` |
| `WF_EMBARGO_MINUTES` | Walk-forward embargo window. | `60` |
| `GIT_COMMIT` | Manifest traceability stamp. | — |
