"""
Backend Configuration
Predictrix Flask backend + GBDT signal generator
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Project root directory
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Load environment variables from config/.env
ENV_PATH = Path(__file__).resolve().parent / '.env'


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


# Initial load: never override process env.
load_dotenv(ENV_PATH, override=False)

# Runtime behavior flags (can now come from process env or .env)
ALLOW_LOCAL_DOTENV = _env_bool('ALLOW_LOCAL_DOTENV', False)
STRICT_RUNTIME_SECRETS = _env_bool('STRICT_RUNTIME_SECRETS', True)

# Optional second-pass override for local-only workflows.
if ALLOW_LOCAL_DOTENV:
    load_dotenv(ENV_PATH, override=True)


def _required_secret(name: str) -> str:
    value = (os.getenv(name) or '').strip()
    if not value:
        raise ValueError(f"{name} байхгүй байна! backend/config/.env файлыг шалгана уу.")
    if STRICT_RUNTIME_SECRETS and value.upper() == 'CHANGE_ME':
        raise ValueError(f"{name} нь placeholder утгатай байна. Runtime secret оруулна уу.")
    return value

# MongoDB Configuration
MONGO_URI = _required_secret('MONGO_URI')

# JWT Configuration
SECRET_KEY = _required_secret('SECRET_KEY')
JWT_ISSUER = os.getenv('JWT_ISSUER', 'predictrix-api')
JWT_AUDIENCE = os.getenv('JWT_AUDIENCE', 'predictrix-mobile')
ACCESS_TOKEN_EXPIRATION_MINUTES = int(os.getenv('ACCESS_TOKEN_EXPIRATION_MINUTES', 60))
REFRESH_TOKEN_EXPIRATION_DAYS = int(os.getenv('REFRESH_TOKEN_EXPIRATION_DAYS', 30))

# Backward-compatible legacy value
JWT_EXPIRATION_DAYS = max(1, ACCESS_TOKEN_EXPIRATION_MINUTES // (60 * 24))

# Auth/runtime hardening flags
ALLOW_AUTH_CODE_IN_RESPONSE = _env_bool('ALLOW_AUTH_CODE_IN_RESPONSE', False)
ENABLE_BACKGROUND_JOBS = _env_bool('ENABLE_BACKGROUND_JOBS', True)
BG_LOCK_TTL_SECONDS = int(os.getenv('BG_LOCK_TTL_SECONDS', 240))

# Email Configuration (Flask-Mail)
MAIL_SERVER = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
MAIL_PORT = int(os.getenv('MAIL_PORT', 587))
MAIL_USE_TLS = _env_bool('MAIL_USE_TLS', True)
MAIL_USE_SSL = _env_bool('MAIL_USE_SSL', False)
MAIL_USERNAME = os.getenv('MAIL_USERNAME')
MAIL_PASSWORD = os.getenv('MAIL_PASSWORD')
MAIL_DEFAULT_SENDER = os.getenv('MAIL_DEFAULT_SENDER', MAIL_USERNAME)

# Email verification settings
VERIFICATION_CODE_EXPIRY_MINUTES = int(os.getenv('VERIFICATION_CODE_EXPIRY_MINUTES', 10))
RESET_CODE_EXPIRY_MINUTES = int(os.getenv('RESET_CODE_EXPIRY_MINUTES', 10))

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
DEBUG_MODE = _env_bool('DEBUG', True)

# Optional explicit model override for promotion workflows
ACTIVE_GBDT_MODEL_PATH = (os.getenv('ACTIVE_GBDT_MODEL_PATH') or '').strip()

# Data directories
DATA_DIR = BASE_DIR / 'data'
MODELS_DIR = BASE_DIR / 'models'

# Data source: Yahoo Finance (yfinance) — no API key required

SUPPORTED_PAIR = "EUR_USD"

print(f"[OK] Configuration loaded from: {ENV_PATH}")
print(f"[INFO] Using Yahoo Finance (yfinance) for forex data — no API key required")
