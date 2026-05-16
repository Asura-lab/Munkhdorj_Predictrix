"""
Backend Configuration
Predictrix Flask backend + GBDT signal generator
"""

import os
from pathlib import Path

# Project root directory
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load environment variables from config/.env only when explicitly allowed.
ENV_PATH = Path(__file__).resolve().parent / '.env'


def _as_bool(value: str, default: bool = False) -> bool:
    raw = str(value if value is not None else default).strip().lower()
    return raw in ('1', 'true', 'yes', 'on')


def _is_production_runtime() -> bool:
    explicit_env = str(os.getenv('APP_ENV', os.getenv('ENVIRONMENT', ''))).strip().lower()
    if explicit_env in ('prod', 'production', 'staging'):
        return True

    if os.getenv('FLY_APP_NAME') or os.getenv('WEBSITE_SITE_NAME'):
        return True

    debug_raw = os.getenv('DEBUG')
    if debug_raw is not None and str(debug_raw).strip().lower() in ('0', 'false', 'no', 'off'):
        return True

    return False


PRODUCTION_RUNTIME = _is_production_runtime()

# In development, auto-load .env unless explicitly disabled.
# In production, .env loading is always blocked.
_dev_dotenv_default = 'false' if PRODUCTION_RUNTIME else 'true'
_allow_dotenv_requested = _as_bool(os.getenv('ALLOW_LOCAL_DOTENV', _dev_dotenv_default))
ALLOW_LOCAL_DOTENV = False

if _allow_dotenv_requested and PRODUCTION_RUNTIME:
    print('[WARN] ALLOW_LOCAL_DOTENV is ignored in production runtime.')
elif _allow_dotenv_requested and ENV_PATH.exists():
    ALLOW_LOCAL_DOTENV = True
    # Load .env into os.environ without overriding already-set variables.
    with open(ENV_PATH, encoding='utf-8-sig') as _dotenv_file:
        for _line in _dotenv_file:
            _line = _line.strip()
            if not _line or _line.startswith('#') or '=' not in _line:
                continue
            _k, _, _v = _line.partition('=')
            _k = _k.strip()
            if _k and _k not in os.environ:
                os.environ[_k] = _v.strip()
    print(f'[OK] Local .env loaded from {ENV_PATH}')
elif _allow_dotenv_requested:
    print(f'[WARN] ALLOW_LOCAL_DOTENV=true but .env not found at {ENV_PATH}')

# Force STRICT_RUNTIME_SECRETS=false in local dev so missing secrets use fallbacks.
# The .env may set it to true (for production parity), but locally we override it.
if not PRODUCTION_RUNTIME:
    os.environ.setdefault('STRICT_RUNTIME_SECRETS', 'false')

strict_default = 'true' if PRODUCTION_RUNTIME else 'false'
STRICT_RUNTIME_SECRETS = _as_bool(os.getenv('STRICT_RUNTIME_SECRETS', strict_default))

# MongoDB Configuration
MONGO_URI = os.getenv('MONGO_URI')
if not MONGO_URI:
    if STRICT_RUNTIME_SECRETS or PRODUCTION_RUNTIME:
        raise ValueError("MONGO_URI байхгүй байна! Secret manager эсвэл runtime environment-оос өгнө үү.")
    MONGO_URI = 'mongodb://localhost:27017/users_db'
    print("[WARN] MONGO_URI not set. Using local fallback URI for development/testing.")

# JWT Configuration
SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY:
    if STRICT_RUNTIME_SECRETS or PRODUCTION_RUNTIME:
        raise ValueError("SECRET_KEY байхгүй байна! Secret manager эсвэл runtime environment-оос өгнө үү.")
    SECRET_KEY = 'dev-insecure-secret-key-change-me'
    print("[WARN] SECRET_KEY not set. Using local fallback key for development/testing.")

JWT_ISSUER = os.getenv('JWT_ISSUER', 'predictrix-api').strip() or 'predictrix-api'
JWT_AUDIENCE = os.getenv('JWT_AUDIENCE', 'predictrix-mobile').strip() or 'predictrix-mobile'

try:
    ACCESS_TOKEN_EXPIRATION_MINUTES = int(os.getenv('ACCESS_TOKEN_EXPIRATION_MINUTES', '60'))
except Exception:
    ACCESS_TOKEN_EXPIRATION_MINUTES = 60
ACCESS_TOKEN_EXPIRATION_MINUTES = max(5, ACCESS_TOKEN_EXPIRATION_MINUTES)

try:
    REFRESH_TOKEN_EXPIRATION_DAYS = int(os.getenv('REFRESH_TOKEN_EXPIRATION_DAYS', '30'))
except Exception:
    REFRESH_TOKEN_EXPIRATION_DAYS = 30
REFRESH_TOKEN_EXPIRATION_DAYS = max(1, REFRESH_TOKEN_EXPIRATION_DAYS)

# Consent and policy governance
POLICY_TERMS_VERSION = os.getenv('POLICY_TERMS_VERSION', '2026-04-04')
POLICY_PRIVACY_VERSION = os.getenv('POLICY_PRIVACY_VERSION', '2026-04-04')

# Model governance (fail-fast in production by default)
MODEL_CONTRACT_REQUIRED = _as_bool(
    os.getenv('MODEL_CONTRACT_REQUIRED', 'true' if PRODUCTION_RUNTIME else 'false')
)

# LLM governance
ALLOW_EXTERNAL_LLM_FALLBACK = _as_bool(
    os.getenv('ALLOW_EXTERNAL_LLM_FALLBACK', 'false')
)
GEMINI_SAFETY_MODE = os.getenv('GEMINI_SAFETY_MODE', 'strict' if PRODUCTION_RUNTIME else 'balanced').strip().lower()

# Backward compatibility for older imports.
JWT_EXPIRATION_DAYS = max(1, ACCESS_TOKEN_EXPIRATION_MINUTES // (24 * 60))

# Distributed rate limit backend (Redis)
RATE_LIMIT_REDIS_URL = os.getenv('RATE_LIMIT_REDIS_URL', '').strip()

# Email Configuration (Flask-Mail)
MAIL_SERVER = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
MAIL_PORT = int(os.getenv('MAIL_PORT', 587))
MAIL_USE_TLS = os.getenv('MAIL_USE_TLS', 'True').lower() == 'true'
MAIL_USE_SSL = os.getenv('MAIL_USE_SSL', 'False').lower() == 'true'
MAIL_USERNAME = os.getenv('MAIL_USERNAME')
MAIL_PASSWORD = os.getenv('MAIL_PASSWORD')
MAIL_DEFAULT_SENDER = os.getenv('MAIL_DEFAULT_SENDER', MAIL_USERNAME)

# Email verification settings
VERIFICATION_CODE_EXPIRY_MINUTES = 10
RESET_CODE_EXPIRY_MINUTES = 10

# AI Configuration - All 21 Gemini API keys
GEMINI_API_KEYS = [
    os.getenv('GEMINI_API_KEY_1'),
    os.getenv('GEMINI_API_KEY_2'),
    os.getenv('GEMINI_API_KEY_3'),
    os.getenv('GEMINI_API_KEY_4'),
    os.getenv('GEMINI_API_KEY_5'),
    os.getenv('GEMINI_API_KEY_6'),
    os.getenv('GEMINI_API_KEY_7'),
    os.getenv('GEMINI_API_KEY_8'),
    os.getenv('GEMINI_API_KEY_9'),
    os.getenv('GEMINI_API_KEY_10'),
    os.getenv('GEMINI_API_KEY_11'),
    os.getenv('GEMINI_API_KEY_12'),
    os.getenv('GEMINI_API_KEY_13'),
    os.getenv('GEMINI_API_KEY_14'),
    os.getenv('GEMINI_API_KEY_15'),
    os.getenv('GEMINI_API_KEY_16'),
    os.getenv('GEMINI_API_KEY_17'),
    os.getenv('GEMINI_API_KEY_18'),
    os.getenv('GEMINI_API_KEY_19'),
    os.getenv('GEMINI_API_KEY_20'),
    os.getenv('GEMINI_API_KEY_21'),
]
# Filter out None values
GEMINI_API_KEYS = [key for key in GEMINI_API_KEYS if key]

# API Configuration
API_HOST = os.getenv('API_HOST', '0.0.0.0')
API_PORT = int(os.getenv('API_PORT', 5000))
DEBUG_MODE = _as_bool(os.getenv('DEBUG', 'false' if PRODUCTION_RUNTIME else 'true'))

# Data directories
DATA_DIR = BASE_DIR / 'data'
MODELS_DIR = BASE_DIR / 'models'

# Data source: Yahoo Finance (yfinance) — no API key required

SUPPORTED_PAIR = "EUR_USD"

_mongo_type = "Atlas (cloud)" if MONGO_URI.startswith("mongodb+srv") else "Local (localhost)"
print("[OK] Configuration loaded from runtime environment variables")
print(f"[INFO] Production runtime mode: {PRODUCTION_RUNTIME}")
print(f"[INFO] Strict runtime secrets: {STRICT_RUNTIME_SECRETS}")
print(f"[INFO] MongoDB: {_mongo_type}")
print(f"[INFO] Using Yahoo Finance (yfinance) for forex data — no API key required")
