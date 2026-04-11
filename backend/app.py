# -*- coding: utf-8 -*-
"""
Forex Signal API
- MongoDB + JWT Authentication
- Yahoo Finance (yfinance) for live rates — no API key required
- GBDT Signal Generator (Multi-Timeframe Ensemble)
"""

import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from flask import Flask, request, jsonify
from flask_cors import CORS
from flask_mail import Mail, Message
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError
from datetime import datetime, timedelta, timezone
from werkzeug.middleware.proxy_fix import ProxyFix
import jwt
import bcrypt
import os
import random
import re
import logging
import hashlib
import uuid
import ipaddress
from queue import Queue, Empty, Full

try:
    from redis import Redis
except Exception:
    Redis = None

# Import configuration
from config.settings import (
    MONGO_URI, SECRET_KEY, API_HOST, API_PORT,
    MAIL_SERVER, MAIL_PORT, MAIL_USE_TLS, MAIL_USE_SSL,
    MAIL_USERNAME, MAIL_PASSWORD, MAIL_DEFAULT_SENDER,
    VERIFICATION_CODE_EXPIRY_MINUTES, RESET_CODE_EXPIRY_MINUTES,
    JWT_ISSUER, JWT_AUDIENCE,
    ACCESS_TOKEN_EXPIRATION_MINUTES, REFRESH_TOKEN_EXPIRATION_DAYS,
    RATE_LIMIT_REDIS_URL,
    POLICY_TERMS_VERSION, POLICY_PRIVACY_VERSION,
)
import threading
import time

# Import Yahoo Finance handler (real-time + historical forex data, no API key)
from utils.yfinance_handler import (
    get_twelvedata_live_rate,
    get_twelvedata_historical,
    get_twelvedata_dataframe,
    get_twelvedata_multitf,
    get_all_forex_rates
)

# Import Market Analyst (News & AI)
try:
    from utils.market_analyst import market_analyst
    print("[OK] market_analyst loaded", flush=True)
except Exception as _e:
    print(f"[CRITICAL] market_analyst import failed: {_e}", flush=True)
    import traceback; traceback.print_exc()
    raise

# Import GBDT Signal Generator (trained multi-timeframe model)
try:
    from ml.signal_generator_gbdt import get_signal_generator_gbdt
    print("[OK] signal_generator_gbdt loaded", flush=True)
except Exception as _e:
    print(f"[CRITICAL] signal_generator_gbdt import failed: {_e}", flush=True)
    import traceback; traceback.print_exc()
    raise

# Import Push Notification Service
from utils.push_notifications import push_service

app = Flask(__name__)

try:
    TRUSTED_PROXY_COUNT = int(os.environ.get('TRUSTED_PROXY_COUNT', '1'))
except Exception:
    TRUSTED_PROXY_COUNT = 1

if TRUSTED_PROXY_COUNT > 0:
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=TRUSTED_PROXY_COUNT,
        x_proto=1,
        x_host=1,
        x_port=1,
    )

# ==================== LOGGING & CORS ====================

LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s %(levelname)s %(name)s %(message)s'
)
logger = logging.getLogger('predictrix')

def _resolve_cors_origins():
    raw_origins = os.environ.get('CORS_ALLOWED_ORIGINS', '').strip()
    if raw_origins:
        origins = [origin.strip() for origin in raw_origins.split(',') if origin.strip()]
        if origins:
            return origins

    allow_all = os.environ.get('ALLOW_ALL_CORS', 'false').strip().lower() in ('1', 'true', 'yes', 'on')
    flask_env = os.environ.get('FLASK_ENV', '').strip().lower()
    if allow_all or flask_env in ('development', 'dev', 'local'):
        return '*'

    return [
        'https://predictrix.app',
        'https://www.predictrix.app',
        'http://localhost:19006',
        'http://127.0.0.1:19006',
        'exp://localhost:19000',
    ]

_cors_origins = _resolve_cors_origins()
CORS(app, resources={r"/*": {"origins": _cors_origins}})
logger.info('CORS configured')


def _public_error_response(message: str = 'Дотоод алдаа гарлаа. Дахин оролдоно уу.', status_code: int = 500):
    return jsonify({'success': False, 'error': message}), status_code


def _parse_int_query_param(name: str, default: int, minimum: int = 1, maximum: int | None = None):
    raw_value = request.args.get(name, default)
    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        return None, jsonify({'success': False, 'error': f'{name} must be an integer'}), 400

    if minimum is not None and value < minimum:
        value = minimum
    if maximum is not None and value > maximum:
        value = maximum

    return value, None, None


def _parse_float_query_param(name: str):
    raw_value = request.args.get(name)
    if raw_value in (None, ''):
        return None, None, None

    try:
        return float(raw_value), None, None
    except (TypeError, ValueError):
        return None, jsonify({'success': False, 'error': f'{name} must be numeric'}), 400

# ==================== BACKGROUND JOB MONITORING ====================

_background_jobs = {}
_background_jobs_lock = threading.Lock()
_BACKGROUND_JOB_STALE_SECONDS = {
    'signal_model_loader': 600,
    'historical_preload': 600,
    'news_updater': 3900,
    'news_scheduler': 360,
    'signal_generator': 180,
    'pair_analysis_preloader': 900,
    'pair_analysis_worker': 900,
}


def _env_bool(name: str, default: bool):
    raw_value = os.environ.get(name)
    if raw_value is None:
        return default

    normalized = str(raw_value).strip().lower()
    if normalized in ('1', 'true', 'yes', 'on'):
        return True
    if normalized in ('0', 'false', 'no', 'off'):
        return False
    return default


APP_PROCESS_ROLE = str(os.environ.get('APP_PROCESS_ROLE', 'all')).strip().lower() or 'all'
if APP_PROCESS_ROLE not in ('all', 'api', 'worker'):
    APP_PROCESS_ROLE = 'all'

BACKGROUND_WORKERS_ENABLED = _env_bool(
    'BACKGROUND_WORKERS_ENABLED',
    default=(APP_PROCESS_ROLE in ('all', 'worker'))
)

WORKER_INSTANCE_ID = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
job_locks_collection = None
analysis_jobs_collection = None


def _acquire_worker_lock(job_name: str, ttl_seconds: int):
    if job_locks_collection is None:
        return True

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=max(int(ttl_seconds or 60), 30))

    try:
        doc = job_locks_collection.find_one_and_update(
            {
                '_id': job_name,
                '$or': [
                    {'expires_at': {'$lte': now}},
                    {'owner_id': WORKER_INSTANCE_ID},
                ],
            },
            {
                '$set': {
                    'owner_id': WORKER_INSTANCE_ID,
                    'updated_at': now,
                    'expires_at': expires_at,
                },
                '$setOnInsert': {
                    'created_at': now,
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return bool(doc and doc.get('owner_id') == WORKER_INSTANCE_ID)
    except Exception as lock_err:
        logger.warning(f'Worker lock acquire fallback for {job_name}: {lock_err}')
        return True


def _renew_worker_lock(job_name: str, ttl_seconds: int):
    if job_locks_collection is None:
        return True

    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=max(int(ttl_seconds or 60), 30))

    try:
        result = job_locks_collection.update_one(
            {
                '_id': job_name,
                'owner_id': WORKER_INSTANCE_ID,
            },
            {
                '$set': {
                    'updated_at': now,
                    'expires_at': expires_at,
                },
            },
        )
        return result.matched_count > 0
    except Exception as lock_err:
        logger.warning(f'Worker lock renew fallback for {job_name}: {lock_err}')
        return True


def _release_worker_lock(job_name: str):
    if job_locks_collection is None:
        return

    try:
        job_locks_collection.delete_one({'_id': job_name, 'owner_id': WORKER_INSTANCE_ID})
    except Exception as lock_err:
        logger.warning(f'Worker lock release failed for {job_name}: {lock_err}')


def _start_background_job(job_name: str, target, lock_ttl_seconds: int = 300):
    if not BACKGROUND_WORKERS_ENABLED:
        update_background_job_state(job_name, 'disabled', f'background disabled (role={APP_PROCESS_ROLE})')
        logger.info(f'Skipping background job {job_name} (role={APP_PROCESS_ROLE})')
        return False

    def _runner():
        if not _acquire_worker_lock(job_name, lock_ttl_seconds):
            update_background_job_state(job_name, 'skipped', 'lock held by another worker')
            logger.info(f'Skipping background job {job_name} (lock held)')
            return

        try:
            target()
        finally:
            _release_worker_lock(job_name)

    threading.Thread(target=_runner, daemon=True).start()
    return True

def update_background_job_state(name: str, status: str, message: str = ''):
    with _background_jobs_lock:
        _background_jobs[name] = {
            'status': status,
            'message': message,
            'updated_at': datetime.now(timezone.utc),
        }

def get_background_job_health():
    now = datetime.now(timezone.utc)
    snapshot = {}
    overall = 'healthy'

    with _background_jobs_lock:
        for name, state in _background_jobs.items():
            updated_at = state.get('updated_at')
            age_seconds = None
            if isinstance(updated_at, datetime):
                age_seconds = int((now - updated_at).total_seconds())

            stale_after = _BACKGROUND_JOB_STALE_SECONDS.get(name, 300)
            is_stale = age_seconds is None or age_seconds > stale_after
            effective_status = 'stale' if is_stale else state.get('status', 'unknown')

            if effective_status in ('error', 'stale'):
                overall = 'degraded'

            snapshot[name] = {
                'status': effective_status,
                'age_seconds': age_seconds,
                'message': state.get('message', ''),
                'updated_at': updated_at.isoformat() if isinstance(updated_at, datetime) else None,
            }

    return overall, snapshot

# ==================== DISTRIBUTED RATE LIMIT ====================
# Redis-backed limiter with in-memory fallback.
_auth_rate_limit_buckets = {}
_auth_rate_limit_lock = threading.Lock()
_rate_limit_redis = None
_rate_limit_backend = 'memory'
_rate_limit_backend_warning_logged = False

try:
    AUTH_RATE_LIMIT_WINDOW_SECONDS = int(os.environ.get('AUTH_RATE_LIMIT_WINDOW_SECONDS', '60'))
except Exception:
    AUTH_RATE_LIMIT_WINDOW_SECONDS = 60

try:
    AUTH_RATE_LIMIT_MAX_REQUESTS = int(os.environ.get('AUTH_RATE_LIMIT_MAX_REQUESTS', '20'))
except Exception:
    AUTH_RATE_LIMIT_MAX_REQUESTS = 20

AUTH_RATE_LIMIT_WINDOW_SECONDS = max(10, AUTH_RATE_LIMIT_WINDOW_SECONDS)
AUTH_RATE_LIMIT_MAX_REQUESTS = max(1, AUTH_RATE_LIMIT_MAX_REQUESTS)

PUBLIC_RATE_LIMIT_DEFAULTS = {
    'rates_live': (60, 60),
    'rates_specific': (90, 60),
    'signal': (24, 60),
    'predict': (16, 60),
    'signals_history': (30, 60),
    'signals_stats': (30, 60),
    'signals_latest': (30, 60),
    'api_news_analyze': (20, 60),
    'api_market_analysis': (25, 60),
}


def _initialize_rate_limit_backend():
    global _rate_limit_redis, _rate_limit_backend

    if not RATE_LIMIT_REDIS_URL:
        logger.info('Rate limit backend: memory (RATE_LIMIT_REDIS_URL not set)')
        return

    if Redis is None:
        logger.warning('Rate limit backend fallback to memory (redis package unavailable)')
        return

    try:
        client = Redis.from_url(
            RATE_LIMIT_REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
            health_check_interval=30,
        )
        client.ping()
        _rate_limit_redis = client
        _rate_limit_backend = 'redis'
        logger.info('Rate limit backend: redis')
    except Exception as redis_err:
        _rate_limit_redis = None
        _rate_limit_backend = 'memory'
        logger.warning(f'Rate limit backend fallback to memory: {redis_err}')


def _normalize_ip(value):
    candidate = str(value or '').strip()
    if not candidate:
        return None

    try:
        return str(ipaddress.ip_address(candidate))
    except Exception:
        return None

def _get_client_ip():
    for candidate in (request.access_route or []):
        normalized = _normalize_ip(candidate)
        if normalized:
            return normalized

    remote = _normalize_ip(request.remote_addr)
    return remote or 'unknown'


def _auth_rate_limited_memory(scope: str, limit: int, window: int):
    now = time.time()
    key = f"{scope}:{_get_client_ip()}"

    with _auth_rate_limit_lock:
        bucket = _auth_rate_limit_buckets.get(key, [])
        bucket = [ts for ts in bucket if now - ts <= window]

        if len(bucket) >= limit:
            _auth_rate_limit_buckets[key] = bucket
            retry_after = max(1, int(window - (now - bucket[0])))
            return True, retry_after

        bucket.append(now)
        _auth_rate_limit_buckets[key] = bucket

    return False, 0


def _auth_rate_limited_redis(scope: str, limit: int, window: int):
    global _rate_limit_backend_warning_logged

    if _rate_limit_redis is None:
        return None

    key = f"ratelimit:{scope}:{_get_client_ip()}"
    try:
        pipe = _rate_limit_redis.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        count, ttl = pipe.execute()

        if count == 1 or ttl is None or ttl < 0:
            _rate_limit_redis.expire(key, window)
            ttl = window

        if count > limit:
            retry_after = max(1, int(ttl if ttl and ttl > 0 else window))
            return True, retry_after

        return False, 0
    except Exception as redis_err:
        if not _rate_limit_backend_warning_logged:
            logger.warning(f'Redis rate limit unavailable, falling back to memory: {redis_err}')
            _rate_limit_backend_warning_logged = True
        return None

def _auth_rate_limited(scope: str, max_requests: int = None, window_seconds: int = None):
    limit = max_requests or AUTH_RATE_LIMIT_MAX_REQUESTS
    window = window_seconds or AUTH_RATE_LIMIT_WINDOW_SECONDS

    redis_result = _auth_rate_limited_redis(scope, limit, window)
    if redis_result is not None:
        return redis_result

    return _auth_rate_limited_memory(scope, limit, window)

def enforce_auth_rate_limit(scope: str, max_requests: int = None, window_seconds: int = None):
    limited, retry_after = _auth_rate_limited(scope, max_requests=max_requests, window_seconds=window_seconds)
    if not limited:
        return None

    return jsonify({
        'error': 'Хэт олон хүсэлт илгээгдлээ. Түр хүлээгээд дахин оролдоно уу.',
        'retry_after': retry_after,
    }), 429

def enforce_public_rate_limit(scope: str, max_requests: int = None, window_seconds: int = None):
    default_limit, default_window = PUBLIC_RATE_LIMIT_DEFAULTS.get(scope, (30, 60))
    limit = max_requests if max_requests is not None else default_limit
    window = window_seconds if window_seconds is not None else default_window

    limited, retry_after = _auth_rate_limited(scope, max_requests=limit, window_seconds=window)
    if not limited:
        return None

    return jsonify({
        'error': 'Хэт олон хүсэлт илгээгдлээ. Түр хүлээгээд дахин оролдоно уу.',
        'retry_after': retry_after,
    }), 429


_initialize_rate_limit_backend()

# Flask-Mail configuration
app.config['MAIL_SERVER'] = MAIL_SERVER
app.config['MAIL_PORT'] = MAIL_PORT
app.config['MAIL_USE_TLS'] = MAIL_USE_TLS
app.config['MAIL_USE_SSL'] = MAIL_USE_SSL
app.config['MAIL_USERNAME'] = MAIL_USERNAME
app.config['MAIL_PASSWORD'] = MAIL_PASSWORD
app.config['MAIL_DEFAULT_SENDER'] = MAIL_DEFAULT_SENDER
app.config['MAIL_TIMEOUT'] = 10  # 10s SMTP connection timeout to prevent worker hangs

mail = Mail(app)

# ==================== DATABASE SETUP ====================

def _ensure_index(collection, keys, name: str, **kwargs):
    try:
        collection.create_index(keys, name=name, **kwargs)
    except Exception as idx_err:
        print(f"[WARN] index {name}: {idx_err}", flush=True)


def _drop_non_unique_email_indexes(collection, keep_name: str):
    try:
        for idx in collection.list_indexes():
            key = idx.get('key')
            key_items = list(key.items()) if key is not None else []
            if key_items == [('email', 1)] and not idx.get('unique', False):
                idx_name = idx.get('name')
                if idx_name and idx_name != keep_name:
                    collection.drop_index(idx_name)
                    print(f"[INFO] Dropped non-unique email index: {idx_name}", flush=True)
    except Exception as drop_err:
        print(f"[WARN] drop non-unique email index failed: {drop_err}", flush=True)

try:
    client = MongoClient(
        MONGO_URI,
        serverSelectionTimeoutMS=5000,
        connectTimeoutMS=10000,
        socketTimeoutMS=20000,
        maxPoolSize=100,
        retryWrites=True,
    )
    db = client['users_db']
    users_collection = db['users']
    verification_codes = db['verification_codes']
    reset_codes = db['reset_codes']
    refresh_tokens_collection = db['refresh_tokens']
    signals_collection = db['signals']  # Таамгууд хадгалах collection
    in_app_notifications = db['in_app_notifications']  # In-app мэдэгдлүүд
    job_locks_collection = db['job_locks']
    analysis_jobs_collection = db['analysis_jobs']

    # Reliability indexes for auth flows and query performance.
    _drop_non_unique_email_indexes(users_collection, keep_name='uniq_users_email')
    _ensure_index(users_collection, 'email', name='uniq_users_email', unique=True)
    _ensure_index(verification_codes, 'email', name='uniq_verification_email', unique=True)
    _ensure_index(verification_codes, 'expires_at', name='ttl_verification_expires', expireAfterSeconds=0)
    _ensure_index(reset_codes, 'email', name='uniq_reset_email', unique=True)
    _ensure_index(reset_codes, 'expires_at', name='ttl_reset_expires', expireAfterSeconds=0)
    _ensure_index(refresh_tokens_collection, 'token_hash', name='uniq_refresh_token_hash', unique=True)
    _ensure_index(refresh_tokens_collection, 'jti', name='uniq_refresh_jti', unique=True)
    _ensure_index(refresh_tokens_collection, 'user_id', name='idx_refresh_user_id')
    _ensure_index(refresh_tokens_collection, 'expires_at', name='ttl_refresh_expires', expireAfterSeconds=0)
    _ensure_index(signals_collection, [('pair', 1), ('created_at', -1)], name='idx_signals_pair_created')
    _ensure_index(signals_collection, [('pair', 1), ('source', 1), ('created_at', -1)], name='idx_signals_pair_source_created')
    _ensure_index(signals_collection, [('run_id', 1), ('created_at', -1)], name='idx_signals_run_id_created')
    _ensure_index(signals_collection, [('model_version', 1), ('created_at', -1)], name='idx_signals_model_version_created')
    _ensure_index(job_locks_collection, 'expires_at', name='ttl_job_locks_expires', expireAfterSeconds=0)
    _ensure_index(analysis_jobs_collection, [('status', 1), ('created_ts', 1)], name='idx_analysis_jobs_status_created')
    _ensure_index(analysis_jobs_collection, [('pair', 1), ('status', 1), ('updated_ts', -1)], name='idx_analysis_jobs_pair_status_updated')
    _ensure_index(
        analysis_jobs_collection,
        [('pair', 1), ('status', 1)],
        name='uniq_analysis_jobs_pair_active',
        unique=True,
        partialFilterExpression={'status': {'$in': ['queued', 'running']}},
    )
    _ensure_index(analysis_jobs_collection, 'expires_at', name='ttl_analysis_jobs_expires', expireAfterSeconds=0)

    # Migration: drop conflicting old index if it exists
    try:
        existing = {i['name'] for i in in_app_notifications.list_indexes()}
        if 'idx_created_desc' in existing:
            in_app_notifications.drop_index('idx_created_desc')
            print("[INFO] Dropped old idx_created_desc index", flush=True)
    except Exception as drop_err:
        print(f"[WARN] drop idx_created_desc: {drop_err}", flush=True)
    # TTL index: auto-delete via expires_at field (news=20min, others=7days)
    try:
        # Drop old created_at TTL index if exists
        in_app_notifications.drop_index('created_at_1')
    except Exception:
        pass
    try:
        in_app_notifications.create_index('expires_at', expireAfterSeconds=0)
    except Exception as idx_err:
        print(f"[WARN] in_app_notifications TTL index: {idx_err}", flush=True)
    print("✓ MongoDB холбогдлоо", flush=True)
except Exception as e:
    print(f"✗ MongoDB холбогдох алдаа: {e}", flush=True)
    exit(1)

# ==================== SIGNAL GENERATORS ====================

signal_generator = None  # GBDT trained model

def load_signal_generator():
    global signal_generator
    update_background_job_state('signal_model_loader', 'starting', 'Loading GBDT model')
    
    try:
        signal_generator = get_signal_generator_gbdt()
        if signal_generator.is_loaded:
            print("✓ GBDT Signal Generator ачаалагдлаа (Trained Multi-TF Ensemble)")
            update_background_job_state('signal_model_loader', 'ok', 'GBDT model loaded')
            return True
        else:
            print("⚠ GBDT model file олдсонгүй")
            signal_generator = None
            update_background_job_state('signal_model_loader', 'error', 'GBDT model file not found')
            return False
    except Exception as e:
        print(f"⚠ GBDT Signal Generator алдаа: {e}")
        signal_generator = None
        update_background_job_state('signal_model_loader', 'error', str(e))
        return False

# Load on startup in background thread (avoid blocking gunicorn bind)
_start_background_job('signal_model_loader', load_signal_generator, lock_ttl_seconds=600)

# ==================== PRELOAD HISTORICAL DATA ====================

def preload_historical_data():
    """Backend эхлэхэд historical data урьдчилан татах"""
    update_background_job_state('historical_preload', 'starting', 'Preloading historical data')
    try:
        print("📥 Preloading historical data...")
        df = get_twelvedata_dataframe(interval="1min", outputsize=500)
        if df is not None and len(df) >= 200:
            print(f"[OK] Historical data preloaded: {len(df)} bars")
            update_background_job_state('historical_preload', 'ok', f'Loaded {len(df)} bars')
            return True
        else:
            print(f"[WARN] Historical data preload: got {len(df) if df is not None else 0} bars")
            update_background_job_state('historical_preload', 'error', 'Insufficient historical bars')
    except Exception as e:
        print(f"[WARN] Historical data preload failed: {e}")
        update_background_job_state('historical_preload', 'error', str(e))
    return False

# Preload on startup in background thread (avoid blocking gunicorn bind)
_start_background_job('historical_preload', preload_historical_data, lock_ttl_seconds=600)

# ==================== NEWS CACHE SYSTEM ====================

class NewsCache:
    def __init__(self):
        self.cache = {
            'history': None,
            'upcoming': None,
            'outlook': None,
            'latest': None
        }
        self.last_updated = None
        self.lock = threading.Lock()

    def update(self):
        """Update all news categories in cache"""
        print("[INFO] Updating news cache...")
        try:
            # Fetch latest data
            history = market_analyst.get_news_history()
            upcoming = market_analyst.get_upcoming_news()
            outlook = market_analyst.get_market_outlook()
            latest = market_analyst.get_latest_news()

            # Push dispatch is handled by the dedicated news scheduler below.
            # Avoid sending/marking events during cache refresh, which can suppress
            # proper 10-minute advance alerts.

            with self.lock:
                old_upcoming = self.cache.get('upcoming')
                self.cache['history'] = history
                self.cache['upcoming'] = upcoming
                self.cache['outlook'] = outlook
                self.cache['latest'] = latest
                self.last_updated = datetime.now()
            print("[OK] News cache updated successfully")
        except Exception as e:
            print(f"[ERROR] News cache update failed: {e}")

    def _check_and_notify_news(self, upcoming):
        """Томоохон мэдээ илэрвэл push notification илгээх (impact шүүлтүүртэй)"""
        if not upcoming:
            return
        
        # upcoming нь dict эсвэл list байж болно
        events = []
        if isinstance(upcoming, dict):
            events = upcoming.get('events', upcoming.get('data', []))
        elif isinstance(upcoming, list):
            events = upcoming
        
        if not events or not isinstance(events, list):
            return
        
        for event in events:
            if not isinstance(event, dict):
                continue
            raw_impact = str(event.get('impact', '')).lower()
            # Map to standardized impact
            if raw_impact in ('high', 'red', '3', 'critical'):
                impact = 'high'
            elif raw_impact in ('medium', 'orange', '2', 'yellow'):
                impact = 'medium'
            else:
                impact = 'low'

            # Only push for high and medium (low is not useful)
            if impact in ('high', 'medium'):
                event_title = event.get('title', event.get('event', 'Economic News'))
                event_key = f"{event.get('date', '')}_{event_title}_{event.get('currency', '')}"
                
                # Skip if already notified
                if push_service.is_event_notified(event_key):
                    continue
                    
                push_service.mark_event_notified(event_key)

                # Save in-app notification (always, regardless of push permission)
                currency = event.get('currency', event.get('country', 'USD'))
                impact_emoji = "\U0001f534" if impact == "high" else "\U0001f7e1"
                save_in_app_notification(
                    ntype='news',
                    title=f"{impact_emoji} {currency} - News Alert",
                    body=event_title,
                    data={'impact': impact, 'currency': currency}
                )

                threading.Thread(
                    target=push_service.send_news_notification,
                    args=({
                        'title': event_title,
                        'impact': impact,
                        'currency': currency,
                        'description': event.get('forecast', event.get('description', '')),
                    },),
                    daemon=True
                ).start()

    def get(self, key):
        """Get data from cache"""
        with self.lock:
            return self.cache.get(key)
        
    def is_ready(self):
        with self.lock:
            return self.last_updated is not None

news_cache = NewsCache()

def news_updater_task():
    """Background task to update news every 30 minutes"""
    print("[INFO] Starting background news updater...")
    update_background_job_state('news_updater', 'starting', 'News updater started')
    if not _renew_worker_lock('news_updater', 3900):
        update_background_job_state('news_updater', 'error', 'Worker lock unavailable at start')
        return

    # Initial update
    try:
        news_cache.update()
        update_background_job_state('news_updater', 'ok', 'Initial cache update complete')
    except Exception as e:
        update_background_job_state('news_updater', 'error', f'Initial update failed: {e}')
    
    while True:
        if not _renew_worker_lock('news_updater', 3900):
            update_background_job_state('news_updater', 'error', 'Worker lock lost')
            return

        # Sleep for 30 minutes (1800 seconds)
        time.sleep(1800)
        try:
            news_cache.update()
            update_background_job_state('news_updater', 'ok', 'Periodic cache update complete')
        except Exception as e:
            print(f"[WARN] News updater loop error: {e}")
            update_background_job_state('news_updater', 'error', str(e))

# Start background updater
_start_background_job('news_updater', news_updater_task, lock_ttl_seconds=3900)

# ==================== NEWS NOTIFICATION SCHEDULER ====================
# Checks upcoming events every 2 minutes, sends notifications 10 min before event

def news_notification_scheduler():
    """10 минутын өмнө мэдээний мэдэгдэл илгээх scheduler"""
    print("[INFO] Starting news notification scheduler (10-min advance alerts)...")
    update_background_job_state('news_scheduler', 'starting', 'Scheduler starting')
    if not _renew_worker_lock('news_scheduler', 360):
        update_background_job_state('news_scheduler', 'error', 'Worker lock unavailable at start')
        return

    time.sleep(30)  # Wait for initial news cache to load
    
    while True:
        if not _renew_worker_lock('news_scheduler', 360):
            update_background_job_state('news_scheduler', 'error', 'Worker lock lost')
            return

        try:
            upcoming = news_cache.get('upcoming')
            if upcoming:
                events = []
                if isinstance(upcoming, dict):
                    events = upcoming.get('events', upcoming.get('data', []))
                elif isinstance(upcoming, list):
                    events = upcoming
                
                now = datetime.now(timezone.utc)
                
                for event in events:
                    if not isinstance(event, dict):
                        continue
                    
                    # Parse event time
                    date_str = event.get('date', '')
                    if not date_str:
                        continue
                    
                    try:
                        # Handle raw TradingView date (before _format_event) or formatted date
                        raw = event.get('raw', {})
                        raw_date = raw.get('date', date_str) if raw else date_str
                        
                        if 'T' in str(raw_date):
                            event_time = datetime.fromisoformat(str(raw_date).replace('Z', '+00:00'))
                        else:
                            # Try parsing formatted "YYYY-MM-DD HH:MM"
                            event_time = datetime.strptime(str(raw_date), "%Y-%m-%d %H:%M")
                            event_time = event_time.replace(tzinfo=timezone.utc)
                    except (ValueError, TypeError):
                        continue
                    
                    # Check if event is within a practical notification window.
                    # A wider range helps recover from brief downtime/cache lag.
                    diff_minutes = (event_time - now).total_seconds() / 60
                    if 0 <= diff_minutes <= 20:
                        raw_impact = str(event.get('impact', event.get('sentiment', ''))).lower()
                        if raw_impact in ('high', 'red', '3', 'critical'):
                            impact = 'high'
                        elif raw_impact in ('medium', 'orange', '2', 'yellow'):
                            impact = 'medium'
                        else:
                            impact = 'low'
                        
                        event_title = event.get('title', event.get('event_name', 'Economic News'))
                        event_key = f"sched_{date_str}_{event_title}"
                        
                        if push_service.is_event_notified(event_key):
                            continue

                        time_str = event_time.strftime("%H:%M UTC")
                        currency = event.get('currency', 'USD')

                        send_result = push_service.send_news_notification({
                            'title': event_title,
                            'impact': impact,
                            'currency': currency,
                            'description': event.get('forecast', event.get('description', '')),
                            'event_time': time_str,
                        })

                        sent_count = 0
                        total_count = 0
                        if isinstance(send_result, dict):
                            try:
                                sent_count = int(send_result.get('sent', 0))
                            except Exception:
                                sent_count = 0
                            try:
                                total_count = int(send_result.get('total', 0))
                            except Exception:
                                total_count = 0

                        # Mark as notified only on full send success.
                        if (
                            isinstance(send_result, dict)
                            and send_result.get('success')
                            and total_count > 0
                            and sent_count >= total_count
                        ):
                            push_service.mark_event_notified(event_key)

                            impact_emoji = "\U0001f534" if impact == "high" else "\U0001f7e1" if impact == "medium" else "\U0001f7e2"
                            save_in_app_notification(
                                ntype='news',
                                title=f"{impact_emoji} {currency} - News Alert",
                                body=f"\u23f0 {time_str}\n{event_title}",
                                data={'impact': impact, 'currency': currency, 'event_time': time_str}
                            )

                            print(f"[INFO] Scheduled news notification: {event_title} at {time_str} ({impact}) sent={sent_count}/{total_count}")
                        else:
                            print(f"[INFO] News notification skipped/retry later: {event_title} ({impact}) result={send_result}")

            update_background_job_state('news_scheduler', 'ok', 'Scheduler loop complete')
        
        except Exception as e:
            print(f"[WARN] News notification scheduler error: {e}")
            update_background_job_state('news_scheduler', 'error', str(e))
        
        time.sleep(120)  # Check every 2 minutes

# Start news notification scheduler
_start_background_job('news_scheduler', news_notification_scheduler, lock_ttl_seconds=360)

# ==================== CONTINUOUS SIGNAL GENERATOR ====================
# Минут тутамд таамаглал гаргаж, BUY/SELL дохиог DB-д шууд хадгалж,
# хэрэглэгчийн босгоос дээш үед push notification илгээнэ.

# Supported currency pairs for continuous generation
TRADING_SCOPE_PAIR = "EUR/USD"
SIGNAL_PAIRS = [TRADING_SCOPE_PAIR]

# Signal endpoint response cache (pair+confidence key, 60s TTL)
_signal_response_cache = {}  # { "pair|conf": { "data": ..., "time": ... } }
SIGNAL_CACHE_TTL = 60  # seconds

def continuous_signal_generator():
    """
    Background thread: минут тутамд модел ажиллуулж таамаглал гаргана.
    - BUY/SELL дохио гармагц MongoDB-д хадгална
    - Хэрэглэгч бүрийн signal_threshold-оос дээш бол push мэдэгдэл илгээнэ
    """
    print("[INFO] Starting continuous signal generator (every 60s)...")
    update_background_job_state('signal_generator', 'starting', 'Waiting for model readiness')
    if not _renew_worker_lock('signal_generator', 300):
        update_background_job_state('signal_generator', 'error', 'Worker lock unavailable at start')
        return

    # Wait for signal generator to load
    for _ in range(60):
        if signal_generator is not None and signal_generator.is_loaded:
            break
        time.sleep(2)
    
    if signal_generator is None or not signal_generator.is_loaded:
        print("[ERROR] Continuous signal generator: model not loaded, stopping.")
        update_background_job_state('signal_generator', 'error', 'Model not loaded')
        return
    
    print("[OK] Continuous signal generator active.")
    update_background_job_state('signal_generator', 'ok', 'Signal generator active')
    
    while True:
        if not _renew_worker_lock('signal_generator', 300):
            update_background_job_state('signal_generator', 'error', 'Worker lock lost')
            return

        for pair in SIGNAL_PAIRS:
            try:
                # Check if market is closed
                now = datetime.now()
                if now.weekday() >= 5 or (now.weekday() == 0 and now.hour < 8):
                    continue  # Skip during weekends

                # Fetch multi-timeframe data
                multi_tf = get_twelvedata_multitf(symbol=pair, base_bars=5000)
                if multi_tf is None or "1min" not in multi_tf:
                    print(f"[WARN] Continuous signal: no data for {pair}")
                    continue

                df = multi_tf["1min"]
                if len(df) < 100:
                    print(f"[WARN] Continuous signal: insufficient data for {pair} ({len(df)} bars)")
                    continue

                # Generate signal with NO minimum confidence filter (we filter after)
                result = signal_generator.generate_signal(
                    df_1min=df,
                    multi_tf_data=multi_tf,
                    min_confidence=0.0,  # No filter - we decide based on output
                    symbol=pair.replace('/', '')
                )

                sig_type = result.get('signal', 'HOLD').upper()
                sig_conf = result.get('confidence', 0)  # This is 0-100 percentage

                # For logging: HOLD-д hold_confidence (HOLD-ийн магадлал) харуулна
                # BUY/SELL-д тухайн signal-ийн confidence харуулна
                if sig_type == 'HOLD':
                    hold_conf_pct = result.get('hold_confidence', sig_conf)
                    dir_signal = result.get('directional_signal', '')
                    print(f"[SIGNAL] {pair}: HOLD (hold={hold_conf_pct:.1f}%, lean={dir_signal} {sig_conf:.1f}%)")
                else:
                    print(f"[SIGNAL] {pair}: {sig_type} @ {sig_conf:.1f}%")

                model_provenance = _signal_provenance_from_result(result)

                # Save every generated actionable signal immediately.
                if sig_type in ('BUY', 'SELL'):
                    # Save to MongoDB
                    signal_doc = {
                        'pair': pair.replace('/', '_'),
                        'signal': sig_type,
                        'confidence': float(sig_conf),
                        'entry_price': result.get('entry_price'),
                        'stop_loss': result.get('stop_loss'),
                        'take_profit': result.get('take_profit'),
                        'sl_pips': result.get('sl_pips'),
                        'tp_pips': result.get('tp_pips'),
                        'risk_reward': result.get('risk_reward'),
                        'model_probabilities': result.get('model_probabilities'),
                        'model_version': result.get('model_version'),
                        'model_provenance': model_provenance,
                        'run_id': model_provenance.get('run_id'),
                        'models_agree': result.get('models_agree'),
                        'atr_pips': result.get('atr_pips'),
                        'reason': result.get('reason'),
                        'source': 'auto',  # Mark as auto-generated
                        'created_at': datetime.now(timezone.utc),
                        'status': 'active'
                    }
                    db_result = signals_collection.insert_one(signal_doc)
                    print(f"[DB] Signal saved: {sig_type} {pair} @ {sig_conf:.1f}% (ID: {db_result.inserted_id})")

                    # Save in-app notification (always, regardless of push permission)
                    emoji = "\U0001f4c8" if sig_type == "BUY" else "\U0001f4c9"
                    conf_pct = f"{sig_conf:.1f}%"
                    entry_price = result.get('entry_price', 'N/A')
                    save_in_app_notification(
                        ntype='signal',
                        title=f"{emoji} {sig_type} Signal - {pair}",
                        body=f"Confidence: {conf_pct} | Entry: {entry_price}",
                        data={
                            'signal_type': sig_type,
                            'pair': pair,
                            'confidence': sig_conf,
                            'entry_price': entry_price,
                            'stop_loss': result.get('stop_loss'),
                            'take_profit': result.get('take_profit'),
                        }
                    )

                    # Send push notification per user threshold
                    try:
                        threading.Thread(
                            target=push_service.send_signal_notification,
                            args=({
                                'signal_type': sig_type,
                                'pair': pair,
                                'confidence': sig_conf,
                                'entry_price': result.get('entry_price'),
                                'sl': result.get('stop_loss'),
                                'tp': result.get('take_profit'),
                            },),
                            daemon=True
                        ).start()
                    except Exception as notif_err:
                        print(f"[WARN] Signal push notification error: {notif_err}")

            except Exception as e:
                print(f"[ERROR] Continuous signal error for {pair}: {e}")
                import traceback
                traceback.print_exc()
                update_background_job_state('signal_generator', 'error', f'{pair}: {e}')

        update_background_job_state('signal_generator', 'ok', 'Signal generation loop complete')
        # Wait 60 seconds before next cycle
        time.sleep(60)

# Start continuous signal generator
_start_background_job('signal_generator', continuous_signal_generator, lock_ttl_seconds=300)

# ==================== IN-APP NOTIFICATION HELPERS ====================

def save_in_app_notification(ntype: str, title: str, body: str, data: dict = None):
    """
    In-app мэдэгдэл хадгалах (push зөвшөөрөлгүй ч харагдана).
    ntype: 'signal' | 'news' | 'security' | 'system'
    """
    try:
        now = datetime.now(timezone.utc)
        ttl_minutes = 20 if ntype == 'news' else 7 * 24 * 60
        doc = {
            'type': ntype,
            'title': title,
            'body': body,
            'data': data or {},
            'created_at': now,
            'expires_at': now + timedelta(minutes=ttl_minutes),
            'read_by': [],
        }
        in_app_notifications.insert_one(doc)
    except Exception as e:
        print(f"[WARN] Save in-app notification failed: {e}")

# ==================== AUTH HELPERS ====================

def _token_hash(token: str):
    return hashlib.sha256(str(token).encode('utf-8')).hexdigest()


def _normalize_exp_to_datetime(exp_value):
    if isinstance(exp_value, datetime):
        return exp_value
    if isinstance(exp_value, (int, float)):
        return datetime.fromtimestamp(exp_value, tz=timezone.utc)
    return datetime.now(timezone.utc) + timedelta(days=REFRESH_TOKEN_EXPIRATION_DAYS)


def generate_token(user_id, email, token_type='access', expires_delta=None, jti=None):
    now = datetime.now(timezone.utc)
    if expires_delta is None:
        if token_type == 'refresh':
            expires_delta = timedelta(days=REFRESH_TOKEN_EXPIRATION_DAYS)
        else:
            expires_delta = timedelta(minutes=ACCESS_TOKEN_EXPIRATION_MINUTES)

    payload = {
        'user_id': str(user_id),
        'email': str(email).strip().lower(),
        'type': token_type,
        'jti': jti or str(uuid.uuid4()),
        'iss': JWT_ISSUER,
        'aud': JWT_AUDIENCE,
        'iat': now,
        'nbf': now,
        'exp': now + expires_delta,
    }
    return jwt.encode(payload, SECRET_KEY, algorithm='HS256')


def _store_refresh_token(user_id, refresh_token, token_payload):
    try:
        if refresh_tokens_collection is None:
            return

        now = datetime.now(timezone.utc)
        refresh_tokens_collection.update_one(
            {'token_hash': _token_hash(refresh_token)},
            {
                '$set': {
                    'user_id': str(user_id),
                    'jti': token_payload.get('jti'),
                    'issued_at': now,
                    'expires_at': _normalize_exp_to_datetime(token_payload.get('exp')),
                    'revoked': False,
                    'revoked_at': None,
                    'last_used_at': now,
                }
            },
            upsert=True,
        )
    except Exception as refresh_store_err:
        logger.warning(f'Failed to store refresh token: {refresh_store_err}')


def _issue_token_pair(user_id, email):
    access_token = generate_token(user_id, email, token_type='access')
    refresh_token = generate_token(user_id, email, token_type='refresh')

    refresh_payload = verify_token(refresh_token, expected_type='refresh', log_failures=False)
    if refresh_payload:
        _store_refresh_token(user_id, refresh_token, refresh_payload)

    return access_token, refresh_token


def _revoke_refresh_token(refresh_token):
    try:
        if not refresh_token or refresh_tokens_collection is None:
            return False

        result = refresh_tokens_collection.update_one(
            {
                'token_hash': _token_hash(refresh_token),
                'revoked': {'$ne': True},
            },
            {
                '$set': {
                    'revoked': True,
                    'revoked_at': datetime.now(timezone.utc),
                }
            },
        )
        return result.modified_count > 0
    except Exception as revoke_err:
        logger.warning(f'Failed to revoke refresh token: {revoke_err}')
        return False


def _revoke_user_refresh_tokens(user_id):
    try:
        if not user_id or refresh_tokens_collection is None:
            return 0

        result = refresh_tokens_collection.update_many(
            {
                'user_id': str(user_id),
                'revoked': {'$ne': True},
            },
            {
                '$set': {
                    'revoked': True,
                    'revoked_at': datetime.now(timezone.utc),
                }
            },
        )
        return int(result.modified_count)
    except Exception as revoke_err:
        logger.warning(f'Failed to revoke user refresh tokens: {revoke_err}')
        return 0


def _is_refresh_token_active(refresh_token):
    try:
        if not refresh_token or refresh_tokens_collection is None:
            return False

        token_hash = _token_hash(refresh_token)
        now = datetime.now(timezone.utc)

        doc = refresh_tokens_collection.find_one({
            'token_hash': token_hash,
            'revoked': {'$ne': True},
            'expires_at': {'$gt': now},
        })
        if not doc:
            return False

        refresh_tokens_collection.update_one(
            {'_id': doc['_id']},
            {'$set': {'last_used_at': now}}
        )
        return True
    except Exception as refresh_lookup_err:
        logger.warning(f'Failed to validate refresh token: {refresh_lookup_err}')
        return False


def verify_token(token, expected_type='access', log_failures=True):
    try:
        if not token:
            return None

        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=['HS256'],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
            options={'require': ['exp', 'email', 'user_id', 'jti', 'type', 'iss', 'aud']}
        )

        if expected_type and payload.get('type') != expected_type:
            if log_failures:
                logger.warning(f"Invalid token type: expected={expected_type}, got={payload.get('type')}")
            return None

        return payload
    except jwt.ExpiredSignatureError:
        if log_failures:
            logger.info('Auth token expired')
        return None
    except jwt.InvalidTokenError as token_err:
        if log_failures:
            logger.warning(f'Invalid auth token: {token_err}')
        return None
    except Exception as token_err:
        if log_failures:
            logger.warning(f'Token verification failed: {token_err}')
        return None

def _extract_bearer_token(auth_header: str):
    raw = str(auth_header or '').strip()
    if not raw:
        return None

    parts = raw.split()
    if len(parts) != 2 or parts[0].lower() != 'bearer':
        return None

    return parts[1].strip() or None

def get_auth_payload_from_request(invalid_message='Token буруу'):
    token = _extract_bearer_token(request.headers.get('Authorization', ''))
    if not token:
        return None, (jsonify({'error': 'Token шаардлагатай'}), 401)

    payload = verify_token(token, expected_type='access')
    if not payload:
        return None, (jsonify({'error': invalid_message}), 401)

    return payload, None

def token_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        payload, auth_error = get_auth_payload_from_request(invalid_message='Token хүчингүй')
        if auth_error:
            return auth_error
        return f(payload, *args, **kwargs)
    return decorated

def generate_verification_code():
    return str(random.randint(100000, 999999))

def send_verification_email(email, code, name=""):
    """Send verification email synchronously. Returns True on success, False on failure."""
    try:
        msg = Message(
            'Predictrix - Баталгаажуулах код',
            recipients=[email]
        )
        msg.html = f"""
        <h2>Сайн байна уу{', ' + name if name else ''}!</h2>
        <p>Таны баталгаажуулах код: <strong style="font-size: 24px; color: #1a237e;">{code}</strong></p>
        <p>Код {VERIFICATION_CODE_EXPIRY_MINUTES} минутын дотор хүчинтэй.</p>
        """
        mail.send(msg)
        return True
    except Exception as e:
        print(f"Email илгээх алдаа: {e}")
        return False

def send_verification_email_async(email, code, name=""):
    """Send verification email in a background thread to avoid blocking the worker."""
    def _send():
        with app.app_context():
            send_verification_email(email, code, name)
    t = threading.Thread(target=_send, daemon=True)
    t.start()

def is_email_configured():
    """Check whether SMTP credentials are available."""
    return bool(MAIL_USERNAME and MAIL_PASSWORD)

def allow_demo_auth_codes():
    """Allow returning OTP codes in API responses only when explicitly enabled."""
    raw = os.environ.get('ALLOW_DEMO_AUTH_CODES', 'false').strip().lower()
    return raw in ('1', 'true', 'yes', 'on')

def allow_test_notification_endpoint():
    """Allow /notifications/test in dev or when explicitly enabled."""
    raw = os.environ.get('ALLOW_TEST_NOTIFICATION_ENDPOINT', 'false').strip().lower()
    if raw in ('1', 'true', 'yes', 'on'):
        return True

    flask_env = os.environ.get('FLASK_ENV', '').strip().lower()
    return flask_env in ('development', 'dev', 'local')

def normalize_trading_pair(raw_pair, default_pair=TRADING_SCOPE_PAIR):
    pair = (raw_pair or default_pair)
    pair = str(pair).strip().upper().replace('_', '/')
    if '/' not in pair and len(pair) == 6:
        pair = f"{pair[:3]}/{pair[3:]}"
    return pair

def enforce_trading_scope(raw_pair, allow_market=False):
    pair = normalize_trading_pair(raw_pair)

    if allow_market and pair == 'MARKET':
        return pair, None

    if pair != TRADING_SCOPE_PAIR:
        return pair, (jsonify({
            'success': False,
            'error': f'Одоогийн моделийн хүрээнд зөвхөн {TRADING_SCOPE_PAIR} дэмжигдэнэ.',
            'supported_pairs': [TRADING_SCOPE_PAIR]
        }), 400)

    return pair, None


def _parse_iso_datetime(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _normalize_consent_payload(data):
    consent = data.get('consent') if isinstance(data, dict) else None
    consent = consent if isinstance(consent, dict) else {}

    accepted = bool(consent.get('accepted'))
    terms_version = str(consent.get('terms_version', '')).strip()
    privacy_version = str(consent.get('privacy_version', '')).strip()
    locale = str(consent.get('locale', 'mn')).strip().lower() or 'mn'
    accepted_at = _parse_iso_datetime(consent.get('accepted_at')) or datetime.now(timezone.utc)

    if not accepted:
        return None, 'Үйлчилгээний нөхцөл ба нууцлалын бодлогыг зөвшөөрөх шаардлагатай'
    if not terms_version or not privacy_version:
        return None, 'consent.terms_version болон consent.privacy_version шаардлагатай'
    if locale not in ('mn', 'en'):
        return None, 'consent.locale нь mn эсвэл en байх ёстой'

    if terms_version != POLICY_TERMS_VERSION or privacy_version != POLICY_PRIVACY_VERSION:
        return None, 'Policy version хоцрогдсон байна. Аппаа шинэчлээд дахин оролдоно уу'

    return {
        'accepted': True,
        'terms_version': terms_version,
        'privacy_version': privacy_version,
        'locale': locale,
        'accepted_at': accepted_at,
        'evidence': {
            'ip': _get_client_ip(),
            'user_agent': str(request.headers.get('User-Agent', ''))[:300],
            'recorded_at': datetime.now(timezone.utc),
        }
    }, None


def _signal_provenance_from_result(result):
    provenance = result.get('model_provenance') if isinstance(result, dict) else None
    if not isinstance(provenance, dict):
        return {}
    return {
        'schema_version': provenance.get('schema_version'),
        'model_version': provenance.get('model_version') or result.get('model_version'),
        'run_id': provenance.get('run_id'),
        'seed': provenance.get('seed'),
        'dataset_hash': provenance.get('dataset_hash'),
        'commit_id': provenance.get('commit_id'),
        'feature_schema_hash': provenance.get('feature_schema_hash'),
        'model_file_sha256': provenance.get('model_file_sha256'),
        'trained_at_utc': provenance.get('trained_at_utc'),
    }

def send_reset_email_async(email, code):
    """Send password reset email in a background thread."""
    def _send():
        with app.app_context():
            try:
                msg = Message('Predictrix - Нууц үг сэргээх', recipients=[email])
                msg.html = f"""
                <h2>Нууц үг сэргээх</h2>
                <p>Код: <strong style="font-size: 24px;">{code}</strong></p>
                <p>Код {RESET_CODE_EXPIRY_MINUTES} минутын дотор хүчинтэй.</p>
                """
                mail.send(msg)
            except Exception as e:
                print(f"Reset email алдаа: {e}")
    threading.Thread(target=_send, daemon=True).start()

# ==================== AUTH ENDPOINTS ====================

@app.route('/auth/register', methods=['POST'])
def register():
    limit_result = enforce_auth_rate_limit('auth_register', max_requests=10, window_seconds=60)
    if limit_result:
        return limit_result

    data = request.json or {}
    name = data.get('name', '').strip()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    consent_payload, consent_error = _normalize_consent_payload(data or {})
    
    if not all([name, email, password]):
        return jsonify({'error': 'Бүх талбарыг бөглөнө үү'}), 400
    
    if not re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', email):
        return jsonify({'error': 'Имэйл хаяг буруу байна'}), 400
    
    if len(password) < 12:
        return jsonify({'error': 'Нууц үг хамгийн багадаа 12 тэмдэгт'}), 400

    if consent_error:
        return jsonify({'error': consent_error}), 400
    
    if users_collection.find_one({'email': email}):
        return jsonify({'error': 'Энэ имэйл бүртгэлтэй байна'}), 400
    
    # Generate verification code
    code = generate_verification_code()
    
    # Save to verification_codes collection
    verification_codes.delete_many({'email': email})
    verification_codes.insert_one({
        'email': email,
        'code': code,
        'name': name,
        'password': bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode(),
        'consent': consent_payload,
        'created_at': datetime.now(timezone.utc),
        'expires_at': datetime.now(timezone.utc) + timedelta(minutes=VERIFICATION_CODE_EXPIRY_MINUTES)
    })
    
    # Try sending email; fall back to demo_mode if sending fails for any reason
    email_sent = send_verification_email(email, code, name) if is_email_configured() else False
    if email_sent:
        return jsonify({
            'success': True,
            'message': 'Баталгаажуулах код илгээлээ',
            'email': email,
            'policy_versions': {
                'terms': POLICY_TERMS_VERSION,
                'privacy': POLICY_PRIVACY_VERSION,
            }
        })
    else:
        if allow_demo_auth_codes():
            print(f"[DEMO] Verification code for {email}: {code}")
            return jsonify({
                'success': True,
                'message': 'Demo горим: код апп дотор харагдана',
                'email': email,
                'demo_mode': True,
                'verification_code': code,
                'policy_versions': {
                    'terms': POLICY_TERMS_VERSION,
                    'privacy': POLICY_PRIVACY_VERSION,
                }
            })

        # Security-first default: never expose OTP in API response.
        return jsonify({
            'success': True,
            'message': 'Баталгаажуулах код илгээлээ. Имэйлээ шалгана уу.',
            'email': email,
            'policy_versions': {
                'terms': POLICY_TERMS_VERSION,
                'privacy': POLICY_PRIVACY_VERSION,
            }
        })

@app.route('/auth/verify-email', methods=['POST'])
def verify_email():
    limit_result = enforce_auth_rate_limit('auth_verify_email', max_requests=12, window_seconds=60)
    if limit_result:
        return limit_result

    data = request.json
    email = data.get('email', '').strip().lower()
    code = data.get('code', '').strip()
    
    record = verification_codes.find_one({
        'email': email,
        'code': code,
        'expires_at': {'$gt': datetime.now(timezone.utc)}
    })
    
    if not record:
        return jsonify({'error': 'Код буруу эсвэл хугацаа дууссан'}), 400
    
    # Create user
    user = {
        'name': record['name'],
        'email': email,
        'password': record['password'],
        'consent': record.get('consent') or {},
        'policy_terms_version': (record.get('consent') or {}).get('terms_version'),
        'policy_privacy_version': (record.get('consent') or {}).get('privacy_version'),
        'email_verified': True,
        'created_at': datetime.now(timezone.utc)
    }
    result = users_collection.insert_one(user)
    
    # Clean up
    verification_codes.delete_many({'email': email})
    
    # Generate access + refresh tokens
    access_token, refresh_token = _issue_token_pair(result.inserted_id, email)
    
    return jsonify({
        'success': True,
        'token': access_token,
        'refresh_token': refresh_token,
        'user': {
            'name': user['name'],
            'email': user['email'],
            'email_verified': True
        }
    })

@app.route('/auth/resend-verification', methods=['POST'])
def resend_verification():
    limit_result = enforce_auth_rate_limit('auth_resend_verification', max_requests=6, window_seconds=60)
    if limit_result:
        return limit_result

    data = request.json
    email = data.get('email', '').strip().lower()
    
    record = verification_codes.find_one({'email': email})
    if not record:
        return jsonify({'error': 'Бүртгэл олдсонгүй'}), 404
    
    code = generate_verification_code()
    verification_codes.update_one(
        {'email': email},
        {'$set': {
            'code': code,
            'created_at': datetime.now(timezone.utc),
            'expires_at': datetime.now(timezone.utc) + timedelta(minutes=VERIFICATION_CODE_EXPIRY_MINUTES)
        }}
    )

    resent = send_verification_email(email, code, record.get('name', '')) if is_email_configured() else False
    if resent:
        return jsonify({'success': True, 'message': 'Код дахин илгээлээ'})
    else:
        if allow_demo_auth_codes():
            print(f"[DEMO] Resend verification code for {email}: {code}")
            return jsonify({
                'success': True,
                'message': 'Demo горим: код апп дотор харагдана',
                'demo_mode': True,
                'verification_code': code
            })

        return jsonify({'success': True, 'message': 'Код дахин илгээлээ'})

@app.route('/auth/login', methods=['POST'])
def login():
    limit_result = enforce_auth_rate_limit('auth_login', max_requests=10, window_seconds=60)
    if limit_result:
        return limit_result

    data = request.json
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    device_id = data.get('device_id', '')
    platform_name = data.get('platform', 'Unknown')
    
    user = users_collection.find_one({'email': email})
    if not user:
        return jsonify({'error': 'Имэйл эсвэл нууц үг буруу'}), 401
    
    # Password bytes эсвэл string байж болно
    stored_password = user['password']
    if isinstance(stored_password, str):
        stored_password = stored_password.encode()
    
    if not bcrypt.checkpw(password.encode(), stored_password):
        return jsonify({'error': 'Имэйл эсвэл нууц үг буруу'}), 401
    
    access_token, refresh_token = _issue_token_pair(user['_id'], email)
    
    # Security alert: detect login from new device
    try:
        user_id_str = str(user['_id'])
        existing_device = push_service.get_user_device(user_id_str)
        if (existing_device and existing_device.get('device_id') 
                and device_id and existing_device['device_id'] != device_id):
            # Different device detected — send security alert
            client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
            login_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
            threading.Thread(
                target=push_service.send_security_alert,
                args=(user_id_str, {
                    'ip': str(client_ip).split(',')[0].strip() if client_ip else 'Unknown',
                    'platform': platform_name,
                    'device_name': data.get('device_name', platform_name),
                    'login_time': login_time,
                }),
                daemon=True
            ).start()
    except Exception as sec_err:
        print(f"[WARN] Security alert check failed: {sec_err}")
    
    return jsonify({
        'success': True,
        'token': access_token,
        'refresh_token': refresh_token,
        'user': {
            'name': user['name'],
            'email': user['email'],
            'email_verified': user.get('email_verified', False)
        }
    })


@app.route('/auth/refresh', methods=['POST'])
def refresh_auth_token():
    limit_result = enforce_auth_rate_limit('auth_refresh', max_requests=20, window_seconds=60)
    if limit_result:
        return limit_result

    data = request.get_json(silent=True) or {}
    refresh_token = str(data.get('refresh_token', '')).strip()

    if not refresh_token:
        refresh_token = _extract_bearer_token(request.headers.get('Authorization', ''))

    if not refresh_token:
        return jsonify({'error': 'Refresh token шаардлагатай'}), 400

    payload = verify_token(refresh_token, expected_type='refresh')
    if not payload:
        return jsonify({'error': 'Refresh token буруу эсвэл хугацаа дууссан'}), 401

    if not _is_refresh_token_active(refresh_token):
        return jsonify({'error': 'Refresh token хүчингүй болсон'}), 401

    _revoke_refresh_token(refresh_token)
    access_token, next_refresh_token = _issue_token_pair(payload['user_id'], payload['email'])

    return jsonify({
        'success': True,
        'token': access_token,
        'refresh_token': next_refresh_token,
    })


@app.route('/auth/logout', methods=['POST'])
@token_required
def logout(payload):
    data = request.get_json(silent=True) or {}
    refresh_token = str(data.get('refresh_token', '')).strip()
    all_devices = bool(data.get('all_devices', False))

    revoked_count = 0
    if all_devices or not refresh_token:
        revoked_count = _revoke_user_refresh_tokens(payload['user_id'])
    else:
        revoked_count = 1 if _revoke_refresh_token(refresh_token) else 0

    return jsonify({
        'success': True,
        'message': 'Session гаргалт амжилттай боллоо',
        'revoked_refresh_tokens': revoked_count,
    })

@app.route('/auth/me', methods=['GET'])
@token_required
def get_me(payload):
    user = users_collection.find_one({'email': payload['email']})
    if not user:
        return jsonify({'error': 'Хэрэглэгч олдсонгүй'}), 404
    
    return jsonify({
        'success': True,
        'user': {
            'name': user['name'],
            'email': user['email'],
            'email_verified': user.get('email_verified', False)
        }
    })

@app.route('/auth/forgot-password', methods=['POST'])
def forgot_password():
    limit_result = enforce_auth_rate_limit('auth_forgot_password', max_requests=6, window_seconds=60)
    if limit_result:
        return limit_result

    data = request.json
    email = data.get('email', '').strip().lower()
    
    user = users_collection.find_one({'email': email})
    if not user:
        return jsonify({'success': True, 'message': 'Хэрэв бүртгэлтэй бол код илгээлээ'})
    
    code = generate_verification_code()
    reset_codes.delete_many({'email': email})
    reset_codes.insert_one({
        'email': email,
        'code': code,
        'created_at': datetime.now(timezone.utc),
        'expires_at': datetime.now(timezone.utc) + timedelta(minutes=RESET_CODE_EXPIRY_MINUTES)
    })
    
    reset_sent = False
    if is_email_configured():
        try:
            msg = Message('Predictrix - Нууц үг сэргээх', recipients=[email])
            msg.html = f"""
            <h2>Нууц үг сэргээх</h2>
            <p>Код: <strong style="font-size: 24px;">{code}</strong></p>
            <p>Код {RESET_CODE_EXPIRY_MINUTES} минутын дотор хүчинтэй.</p>
            """
            mail.send(msg)
            reset_sent = True
        except Exception as e:
            print(f"Reset email алдаа: {e}")

    if reset_sent:
        return jsonify({'success': True, 'message': 'Код илгээлээ'})

    if allow_demo_auth_codes():
        print(f"[DEMO] Reset code for {email}: {code}")
        return jsonify({
            'success': True,
            'message': 'Demo горим: код апп дотор харагдана',
            'demo_mode': True,
            'reset_code': code
        })

    return jsonify({'success': True, 'message': 'Хэрэв бүртгэлтэй бол код илгээлээ'})

@app.route('/auth/verify-reset-code', methods=['POST'])
def verify_reset_code():
    """Нууц үг сэргээх кодыг эхлээд тусад нь шалгах endpoint (mobile compatibility)."""
    limit_result = enforce_auth_rate_limit('auth_verify_reset_code', max_requests=12, window_seconds=60)
    if limit_result:
        return limit_result

    data = request.json or {}
    email = data.get('email', '').strip().lower()
    code = data.get('code', '').strip()

    if not email or not code:
        return jsonify({'error': 'Имэйл болон код шаардлагатай'}), 400

    record = reset_codes.find_one({
        'email': email,
        'code': code,
        'expires_at': {'$gt': datetime.now(timezone.utc)}
    })

    if not record:
        return jsonify({'error': 'Код буруу эсвэл хугацаа дууссан'}), 400

    return jsonify({'success': True, 'message': 'Код баталгаажлаа'})

@app.route('/auth/reset-password', methods=['POST'])
def reset_password():
    limit_result = enforce_auth_rate_limit('auth_reset_password', max_requests=8, window_seconds=60)
    if limit_result:
        return limit_result

    data = request.json
    email = data.get('email', '').strip().lower()
    code = data.get('code', '').strip()
    new_password = data.get('new_password', '')
    
    if len(new_password) < 6:
        return jsonify({'error': 'Нууц үг хамгийн багадаа 12 тэмдэгт'}), 400
    
    record = reset_codes.find_one({
        'email': email,
        'code': code,
        'expires_at': {'$gt': datetime.now(timezone.utc)}
    })
    
    if not record:
        return jsonify({'error': 'Код буруу эсвэл хугацаа дууссан'}), 400
    
    hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    users_collection.update_one({'email': email}, {'$set': {'password': hashed}})
    reset_codes.delete_many({'email': email})

    user_doc = users_collection.find_one({'email': email}, {'_id': 1})
    if user_doc and user_doc.get('_id'):
        _revoke_user_refresh_tokens(user_doc['_id'])
    
    return jsonify({'success': True, 'message': 'Нууц үг амжилттай солигдлоо'})

@app.route('/auth/update', methods=['PUT'])
@token_required
def update_profile(payload):
    """Хэрэглэгчийн профайл мэдээллээс нэрийг шинэчлэх endpoint (mobile compatibility)."""
    data = request.json or {}
    name = str(data.get('name', '')).strip()

    if not name:
        return jsonify({'error': 'Нэр хоосон байж болохгүй'}), 400

    users_collection.update_one(
        {'email': payload['email']},
        {'$set': {'name': name, 'updated_at': datetime.now(timezone.utc)}}
    )

    return jsonify({
        'success': True,
        'message': 'Профайл шинэчлэгдлээ',
        'user': {
            'name': name,
            'email': payload['email'],
        }
    })

@app.route('/auth/change-password', methods=['PUT'])
@token_required
def change_password(payload):
    """Хуучин нууц үгээр баталгаажуулж шинэ нууц үг тохируулах endpoint (mobile compatibility)."""
    data = request.json or {}
    old_password = str(data.get('oldPassword', data.get('old_password', '')))
    new_password = str(data.get('newPassword', data.get('new_password', '')))

    if not old_password or not new_password:
        return jsonify({'error': 'Хуучин болон шинэ нууц үг шаардлагатай'}), 400

    if len(new_password) < 6:
        return jsonify({'error': 'Шинэ нууц үг хамгийн багадаа 12 тэмдэгт'}), 400

    user = users_collection.find_one({'email': payload['email']})
    if not user:
        return jsonify({'error': 'Хэрэглэгч олдсонгүй'}), 404

    stored_password = user.get('password', '')
    if isinstance(stored_password, str):
        stored_password = stored_password.encode()

    if not bcrypt.checkpw(old_password.encode(), stored_password):
        return jsonify({'error': 'Хуучин нууц үг буруу байна'}), 400

    hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    users_collection.update_one(
        {'email': payload['email']},
        {'$set': {'password': hashed, 'updated_at': datetime.now(timezone.utc)}}
    )
    _revoke_user_refresh_tokens(user.get('_id'))

    return jsonify({'success': True, 'message': 'Нууц үг амжилттай солигдлоо'})

# ==================== PUSH NOTIFICATION ENDPOINTS ====================

@app.route('/notifications/register', methods=['POST'])
def register_push_token():
    """Push notification token бүртгэх"""
    payload, auth_error = get_auth_payload_from_request()
    if auth_error:
        return auth_error
    
    data = request.json or {}
    push_token = data.get('push_token', '').strip()
    platform = data.get('platform', 'unknown')
    device_id = data.get('device_id', '')
    
    if not push_token:
        return jsonify({'error': 'Push token шаардлагатай'}), 400

    if not re.match(r'^(Exponent|Expo)PushToken\[[^\]]+\]$', push_token):
        return jsonify({'error': 'Push token формат буруу байна'}), 400
    
    success = push_service.register_token(
        payload['user_id'], push_token, platform, device_id
    )
    
    if success:
        return jsonify({'success': True, 'message': 'Push token бүртгэгдлээ'})
    return jsonify({'error': 'Push token бүртгэж чадсангүй'}), 500

@app.route('/notifications/unregister', methods=['POST'])
def unregister_push_token():
    """Push notification token устгах"""
    payload, auth_error = get_auth_payload_from_request()
    if auth_error:
        return auth_error
    
    push_service.unregister_token(payload['user_id'])
    return jsonify({'success': True, 'message': 'Push token устгагдлаа'})

@app.route('/notifications/preferences', methods=['GET'])
def get_notification_preferences():
    """Мэдэгдлийн тохиргоо авах"""
    payload, auth_error = get_auth_payload_from_request()
    if auth_error:
        return auth_error
    
    prefs = push_service.get_preferences(payload['user_id'])
    return jsonify({'success': True, 'preferences': prefs})

@app.route('/notifications/preferences', methods=['PUT'])
def update_notification_preferences():
    """Мэдэгдлийн тохиргоо шинэчлэх"""
    payload, auth_error = get_auth_payload_from_request()
    if auth_error:
        return auth_error
    
    data = request.json or {}

    old_impact_filter = None
    new_impact_filter = None

    if 'news_impact_filter' in data:
        raw_filter = str(data.get('news_impact_filter', '')).strip().lower()
        if raw_filter not in ('high', 'medium', 'all'):
            return jsonify({'error': 'news_impact_filter утга буруу байна'}), 400

        data['news_impact_filter'] = raw_filter
        new_impact_filter = raw_filter

        try:
            old_doc = push_service.push_tokens.find_one(
                {'user_id': payload['user_id']},
                {'news_impact_filter': 1}
            )
            old_impact_filter = str((old_doc or {}).get('news_impact_filter', 'high')).lower()
        except Exception:
            old_impact_filter = 'high'

    success = push_service.update_preferences(payload['user_id'], data)
    
    if success:
        # IMPORTANT: when news impact filter changes, only show news notifications
        # created after this moment (prevents historical backfill in the UI).
        if (
            new_impact_filter is not None
            and old_impact_filter is not None
            and new_impact_filter != old_impact_filter
        ):
            try:
                push_service.push_tokens.update_one(
                    {'user_id': payload['user_id']},
                    {'$set': {'news_filter_updated_at': datetime.now(timezone.utc)}}
                )
            except Exception as cutoff_err:
                print(f"[WARN] Failed to persist news_filter_updated_at: {cutoff_err}")

        return jsonify({'success': True, 'message': 'Тохиргоо хадгалагдлаа'})
    return jsonify({'error': 'Тохиргоо хадгалж чадсангүй'}), 500


def _parse_datetime_safe(value):
    if isinstance(value, datetime):
        return value

    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00'))
        except Exception:
            return None

    return None


def _resolve_news_visibility(user_id: str):
    prefs_doc = push_service.push_tokens.find_one(
        {'user_id': user_id},
        {'news_impact_filter': 1, 'news_filter_updated_at': 1}
    )

    impact_filter = str((prefs_doc or {}).get('news_impact_filter', 'high')).lower()
    if impact_filter == 'all':
        allowed_impacts = ['high', 'medium', 'low']
    elif impact_filter == 'medium':
        allowed_impacts = ['high', 'medium']
    else:
        allowed_impacts = ['high']

    filter_updated_at = _parse_datetime_safe((prefs_doc or {}).get('news_filter_updated_at'))
    return allowed_impacts, filter_updated_at


def _build_news_query(allowed_impacts, filter_updated_at):
    news_clause = {'type': 'news', 'data.impact': {'$in': allowed_impacts}}
    if filter_updated_at:
        news_clause['created_at'] = {'$gte': filter_updated_at}
    return news_clause

@app.route('/notifications/test', methods=['POST'])
def test_push_notification():
    """Тест мэдэгдэл илгээх (debug зорилгоор)"""
    if not allow_test_notification_endpoint():
        return jsonify({'error': 'Тест мэдэгдлийн endpoint production горимд хаалттай байна'}), 403

    payload, auth_error = get_auth_payload_from_request()
    if auth_error:
        return auth_error
    
    # Send a test notification to this user only
    doc = push_service.push_tokens.find_one({"user_id": payload['user_id']})
    if not doc or not doc.get('push_token'):
        return jsonify({'error': 'Push token бүртгэгдээгүй'}), 404
    
    from utils.push_notifications import EXPO_PUSH_URL
    import requests as req
    result = req.post(
        EXPO_PUSH_URL,
        json=[{
            "to": doc['push_token'],
            "title": "🔔 Predictrix Test",
            "body": "Push notification амжилттай ажиллаж байна!",
            "sound": "default",
            "data": {"type": "test"}
        }],
        headers={"Content-Type": "application/json"},
        timeout=10
    )
    
    return jsonify({
        'success': True,
        'message': 'Тест мэдэгдэл илгээгдлээ',
        'expo_response': result.json() if result.status_code == 200 else result.text
    })

@app.route('/notifications/in-app', methods=['GET'])
def get_in_app_notifications():
    """
    In-app мэдэгдлүүдийг авах (push зөвшөөрөлгүй ч ажиллана).
    Auth шаардлагатай.
    Query params: limit (default 20), type (optional: signal/news/system)
    """
    payload, auth_error = get_auth_payload_from_request()
    if auth_error:
        return auth_error

    limit, limit_error, limit_status = _parse_int_query_param('limit', 20, minimum=1, maximum=50)
    if limit_error:
        return limit_error
    ntype = request.args.get('type', None)

    user_id = payload['user_id']

    # Resolve user's news visibility (impact filter + filter update cutoff)
    allowed_impacts, filter_updated_at = _resolve_news_visibility(user_id)

    # Build query: non-news always shown; news filtered by impact preference
    news_clause = _build_news_query(allowed_impacts, filter_updated_at)
    non_news_clause = {'type': {'$ne': 'news'}}
    query = {'$or': [non_news_clause, news_clause]}
    if ntype:
        if ntype == 'news':
            query = news_clause
        else:
            query = {'type': ntype}

    try:
        docs = list(
            in_app_notifications.find(query, {'_id': 1, 'type': 1, 'title': 1, 'body': 1, 'data': 1, 'created_at': 1, 'read_by': 1})
            .sort('created_at', -1)
            .limit(limit)
        )
        # Convert datetime to ISO string and compute is_read per user
        for doc in docs:
            if 'created_at' in doc:
                doc['created_at'] = doc['created_at'].isoformat()
            read_by = doc.get('read_by', [])
            doc['is_read'] = user_id in [str(r) for r in read_by]
            doc['_id'] = str(doc['_id'])
        return jsonify({'success': True, 'notifications': docs, 'count': len(docs)})
    except Exception as e:
        logger.exception('In-app notifications fetch failed')
        return _public_error_response()


@app.route('/notifications/in-app/unread-count', methods=['GET'])
def get_unread_notification_count():
    """Уншаагүй мэдэгдлийн тоо буцаах."""
    payload, auth_error = get_auth_payload_from_request()
    if auth_error:
        return auth_error

    user_id = payload['user_id']
    try:
        # Resolve user's news visibility (impact filter + filter update cutoff)
        allowed_impacts, filter_updated_at = _resolve_news_visibility(user_id)
        news_clause = _build_news_query(allowed_impacts, filter_updated_at)

        count = in_app_notifications.count_documents({
            'read_by': {'$nin': [user_id]},
            '$or': [
                {'type': {'$ne': 'news'}},
                news_clause
            ]
        })
        return jsonify({'success': True, 'unread_count': count})
    except Exception as e:
        logger.exception('Unread notification count failed')
        return _public_error_response()


@app.route('/notifications/in-app/mark-read', methods=['POST'])
def mark_notifications_read():
    """Мэдэгдлүүдийг уншсан гэж тэмдэглэх.
    Body: { ids: ['id1','id2',...] }  — хоосон бол бүгдийг тэмдэглэнэ.
    """
    payload, auth_error = get_auth_payload_from_request()
    if auth_error:
        return auth_error

    user_id = payload['user_id']
    body = request.get_json(silent=True) or {}
    ids = body.get('ids', [])

    try:
        from bson import ObjectId
        if ids:
            object_ids = [ObjectId(i) for i in ids if i]
            query_filter = {'_id': {'$in': object_ids}}
        else:
            query_filter = {}

        result = in_app_notifications.update_many(
            {**query_filter, 'read_by': {'$nin': [user_id]}},
            {'$addToSet': {'read_by': user_id}}
        )
        return jsonify({'success': True, 'modified': result.modified_count})
    except Exception as e:
        logger.exception('In-app mark-read failed')
        return _public_error_response()

# ==================== LIVE RATES (Yahoo Finance) ====================

@app.route('/rates/live', methods=['GET'])
def get_live_rates():
    """
    Get live rates for all 20 forex pairs from Yahoo Finance (yfinance)
    Returns rate, change, and change_percent for each pair
    """
    try:
        limit_result = enforce_public_rate_limit('rates_live')
        if limit_result:
            return limit_result

        result = get_all_forex_rates()
        
        if result and result.get('success'):
            return jsonify({
                'success': True,
                'source': 'twelvedata',
                'rates': result.get('rates', {}),
                'timestamp': result.get('time', datetime.now(timezone.utc).isoformat()),
                'cached': result.get('cached', False),
                'count': result.get('count', 0)
            })
        elif result.get('error') == 'rate_limited':
            return jsonify({
                'success': False,
                'error': 'rate_limited',
                'message': 'Rate limited. Please try again later.',
                'next_update_in': result.get('next_update_in', 60)
            }), 429
        else:
            return jsonify({
                'success': False,
                'error': result.get('error', 'Failed to fetch rates')
            }), 503
            
    except Exception as e:
        logger.exception('Live rates failed')
        return _public_error_response()

@app.route('/rates/specific', methods=['GET'])
def get_specific_rate():
    """Get specific currency pair rate"""
    pair, pair_error = enforce_trading_scope(request.args.get('pair', 'EUR_USD'))
    if pair_error:
        return pair_error
    
    try:
        result = get_twelvedata_live_rate()
        
        if result and result.get('success'):
            return jsonify({
                'success': True,
                'pair': pair.replace('/', '_'),
                'rate': result.get('rate', 0),
                'bid': result.get('bid'),
                'ask': result.get('ask'),
                'timestamp': result.get('time')
            })
        else:
            return jsonify({'success': False, 'error': 'Rate олдсонгүй'}), 404
            
    except Exception as e:
        logger.exception('Specific rate failed')
        return _public_error_response()

# ==================== SIGNAL GENERATOR ENDPOINTS ====================

@app.route('/signal', methods=['GET'])
def get_signal():
    """
    Signal Generator Endpoint (GBDT Multi-Timeframe Ensemble)
    Query params:
        min_confidence: Minimum confidence threshold (default: 60)
        pair: Currency pair (default: EUR/USD)
    """
    try:
        limit_result = enforce_public_rate_limit('signal')
        if limit_result:
            return limit_result

        if signal_generator is None or not signal_generator.is_loaded:
            return jsonify({
                'success': False,
                'error': 'Signal Generator ачаалагдаагүй'
            }), 500

        min_confidence, min_confidence_error, _min_conf_status = _parse_float_query_param('min_confidence')
        if min_confidence_error:
            return min_confidence_error
        if min_confidence is None:
            min_confidence = 60.0
        pair, pair_error = enforce_trading_scope(request.args.get('pair', 'EUR/USD'))
        if pair_error:
            return pair_error
        conf_threshold = min_confidence / 100.0 if min_confidence > 1 else min_confidence
        conf_threshold = max(0.0, min(1.0, conf_threshold))
        cache_key = f"{pair}|{conf_threshold:.4f}"

        # Check signal response cache (60s TTL)
        cached = _signal_response_cache.get(cache_key)
        if cached and (time.time() - cached['time']) < SIGNAL_CACHE_TTL:
            cached_data = cached['data'].copy()
            cached_data['cached'] = True
            return jsonify(cached_data)

        multi_tf = get_twelvedata_multitf(symbol=pair, base_bars=5000)

        if multi_tf is None or "1min" not in multi_tf:
            return jsonify({
                'success': False,
                'error': 'rate_limited',
                'message': f'Rate limited or no data for {pair}.',
                'data_count': 0,
                'required': 100
            }), 429

        df = multi_tf["1min"]

        if len(df) < 100:
            return jsonify({
                'success': False,
                'error': 'rate_limited',
                'message': f'Not enough data: {len(df)} bars (need 100+)',
                'data_count': len(df),
                'required': 100
            }), 429

        data_from = df['time'].iloc[0].isoformat() if hasattr(df['time'].iloc[0], 'isoformat') else str(df['time'].iloc[0])
        data_to   = df['time'].iloc[-1].isoformat() if hasattr(df['time'].iloc[-1], 'isoformat') else str(df['time'].iloc[-1])

        now = datetime.now()
        market_closed = now.weekday() >= 5 or (now.weekday() == 0 and now.hour < 8)

        signal = signal_generator.generate_signal(
            df_1min=df,
            multi_tf_data=multi_tf,
            min_confidence=conf_threshold,
            symbol=pair.replace('/', '')
        )

        saved_signal_id = None
        sig_type = str(signal.get('signal', '')).upper().strip()
        sig_conf = signal.get('confidence')

        # Ensure model-generated BUY/SELL prediction is persisted immediately.
        if sig_type in ('BUY', 'SELL') and sig_conf is not None:
            try:
                signal_doc = {
                    'pair': pair.replace('/', '_'),
                    'signal': sig_type,
                    'confidence': float(sig_conf),
                    'entry_price': signal.get('entry_price'),
                    'stop_loss': signal.get('stop_loss'),
                    'take_profit': signal.get('take_profit'),
                    'sl_pips': signal.get('sl_pips'),
                    'tp_pips': signal.get('tp_pips'),
                    'risk_reward': signal.get('risk_reward'),
                    'model_probabilities': signal.get('model_probabilities'),
                    'models_agree': signal.get('models_agree'),
                    'atr_pips': signal.get('atr_pips'),
                    'reason': signal.get('reason'),
                    'source': 'auto',
                    'created_at': datetime.now(timezone.utc),
                    'status': 'active'
                }
                db_result = signals_collection.insert_one(signal_doc)
                saved_signal_id = str(db_result.inserted_id)
            except Exception as save_err:
                print(f"[WARN] /signal immediate save failed for {pair}: {save_err}")

        # Push notification хэрэггүй — continuous_signal_generator background-д хариуцна

        tf_info = {tf: len(tf_df) for tf, tf_df in multi_tf.items()}

        response_data = {
            'success': True,
            'pair': pair.replace('/', '_'),
            'data_info': {
                'from': data_from,
                'to': data_to,
                'bars': len(df),
                'timeframes': tf_info,
                'market_closed': market_closed,
                'note': 'Market хаалттай үед сүүлийн арилжааны дата' if market_closed else None
            },
            'saved_signal_id': saved_signal_id,
            **signal
        }

        # Cache the response
        _signal_response_cache[cache_key] = {'data': response_data, 'time': time.time()}

        return jsonify(response_data)

    except Exception as e:
        print(f"Signal error: {e}")
        import traceback
        traceback.print_exc()
        logger.exception('Signal endpoint failed')
        return _public_error_response()

@app.route('/signal/demo', methods=['GET'])
def get_signal_demo():
    """Demo endpoint intentionally disabled to avoid ambiguous behavior in production."""
    return jsonify({
        'success': False,
        'error': 'disabled',
        'message': '/signal/demo endpoint is disabled. Use /signal with pair and min_confidence.'
    }), 410

# ==================== PREDICT (Signal wrapper) ====================

@app.route('/predict', methods=['POST'])
def predict():
    """Main prediction endpoint - GBDT Multi-Timeframe Ensemble"""
    try:
        limit_result = enforce_public_rate_limit('predict')
        if limit_result:
            return limit_result

        data = request.json or {}
        pair, pair_error = enforce_trading_scope(data.get('pair', 'EUR_USD'))
        if pair_error:
            return pair_error

        if signal_generator is None or not signal_generator.is_loaded:
            return jsonify({
                'success': False,
                'error': 'Signal Generator ачаалагдаагүй'
            }), 500

        multi_tf = get_twelvedata_multitf(symbol=pair, base_bars=5000)

        if multi_tf is None or "1min" not in multi_tf or len(multi_tf["1min"]) < 100:
            return jsonify({
                'success': False,
                'predictions': {pair: {'signal': 'HOLD', 'confidence': 0}}
            })

        signal = signal_generator.generate_signal(
            df_1min=multi_tf["1min"],
            multi_tf_data=multi_tf,
            min_confidence=0.60,
            symbol=pair.replace('/', '')
        )

        return jsonify({
            'success': True,
            'predictions': {
                pair: {
                    'signal': signal.get('signal', 'HOLD'),
                    'confidence': signal.get('confidence', 0),
                    'entry_price': signal.get('entry_price'),
                    'stop_loss': signal.get('stop_loss'),
                    'take_profit': signal.get('take_profit'),
                    'sl_pips': signal.get('sl_pips'),
                    'tp_pips': signal.get('tp_pips'),
                    'risk_reward': signal.get('risk_reward')
                }
            }
        })

    except Exception as e:
        print(f"Predict error: {e}")
        logger.exception('Predict failed')
        return _public_error_response()

# ==================== DATA COMPATIBILITY CHECK ====================

@app.route('/signal/check', methods=['GET'])
def check_signal_data():
    """
    Check if API data is compatible with the loaded GBDT model.
    Returns feature compatibility info and data quality metrics.
    """
    try:
        result = {
            'model_type': 'GBDT (Multi-TF Ensemble)',
            'model_loaded': signal_generator is not None and signal_generator.is_loaded,
        }

        if not result['model_loaded']:
            result['error'] = 'Model not loaded'
            return jsonify(result)

        result['expected_features'] = signal_generator.feature_cols
        result['feature_count'] = len(signal_generator.feature_cols)
        result['models'] = list(signal_generator.models.keys())
        result['has_calibrator'] = signal_generator.calibrator is not None

        pair, pair_error = enforce_trading_scope(request.args.get('pair', 'EUR/USD'))
        if pair_error:
            return pair_error
        multi_tf = get_twelvedata_multitf(symbol=pair, base_bars=5000)

        if multi_tf is not None and "1min" in multi_tf:
            from ml.signal_generator_gbdt import build_features_from_data

            tf_info = {tf: len(df) for tf, df in multi_tf.items()}
            result['data_available'] = True
            result['timeframe_bars'] = tf_info

            try:
                df_features = build_features_from_data(multi_tf)
                compat = signal_generator.check_features(df_features)
                result['feature_check'] = compat
                result['data_rows_after_features'] = len(df_features)

                if compat['compatible']:
                    result['status'] = 'COMPATIBLE'
                    result['message'] = 'API data is fully compatible with the model'
                else:
                    result['status'] = 'INCOMPATIBLE'
                    result['message'] = f"Missing {len(compat['missing_features'])} features"
            except Exception as e:
                result['feature_check_error'] = 'Feature compatibility check failed'
                result['status'] = 'ERROR'
        else:
            result['data_available'] = False
            result['status'] = 'NO_DATA'
            result['message'] = 'Could not fetch API data (rate limited?)'

        return jsonify(result)

    except Exception as e:
        logger.exception('API compatibility check failed')
        return _public_error_response()

# ==================== SIGNAL STORAGE ====================

def _to_optional_float(value, field_name: str):
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        raise ValueError(f'{field_name} тоон утга байх ёстой')


def _trusted_signal_filter_for_pair(pair_under: str):
    pair_slash = pair_under.replace('_', '/')
    return {
        'pair': {'$in': [pair_under, pair_slash]},
        'source': {'$in': ['auto', None]},
    }

@app.route('/signal/save', methods=['POST'])
@token_required
def save_signal(payload):
    """
    Таамаг хадгалах endpoint
    Request body:
        - pair: Валютын хослол (EUR_USD)
        - signal: BUY/SELL/HOLD
        - confidence: Итгэлцэл (0-100)
        - entry_price: Орох үнэ
        - stop_loss: Stop loss үнэ
        - take_profit: Take profit үнэ
        - sl_pips, tp_pips: Pip утгууд
        - risk_reward: Risk/Reward ratio
        - model_probabilities: Модел бүрийн таамаг
        - models_agree: Модел санал нийлсэн эсэх
        - atr_pips: ATR volatility
    """
    try:
        data = request.json
        
        if not data:
            return jsonify({'success': False, 'error': 'Data шаардлагатай'}), 400
        
        # Required fields
        signal_type = str(data.get('signal', '')).strip().upper()
        confidence_raw = data.get('confidence')
        normalized_pair, pair_error = enforce_trading_scope(data.get('pair', 'EUR_USD'))
        if pair_error:
            return pair_error

        pair = normalized_pair.replace('/', '_')  # Normalize: EUR/USD → EUR_USD
        
        if not signal_type or confidence_raw is None:
            return jsonify({'success': False, 'error': 'signal, confidence шаардлагатай'}), 400

        if signal_type not in {'BUY', 'SELL', 'HOLD'}:
            return jsonify({'success': False, 'error': 'signal must be BUY, SELL, or HOLD'}), 400

        try:
            confidence_value = float(confidence_raw)
        except (TypeError, ValueError):
            return jsonify({'success': False, 'error': 'confidence must be a number'}), 400

        # Canonical confidence unit: always store as 0-100 percentage.
        if 0.0 <= confidence_value <= 1.0:
            confidence_value *= 100.0
        confidence_value = max(0.0, min(100.0, confidence_value))

        source = str(data.get('source', 'manual')).strip().lower()
        if source not in {'auto', 'manual'}:
            source = 'manual'

        # Manual submissions are isolated from public analytics to prevent poisoning.
        manual_note = str(data.get('reason', '') or '').strip()
        if len(manual_note) > 300:
            manual_note = manual_note[:300]

        entry_price = _to_optional_float(data.get('entry_price'), 'entry_price')
        stop_loss = _to_optional_float(data.get('stop_loss'), 'stop_loss')
        take_profit = _to_optional_float(data.get('take_profit'), 'take_profit')
        sl_pips = _to_optional_float(data.get('sl_pips'), 'sl_pips')
        tp_pips = _to_optional_float(data.get('tp_pips'), 'tp_pips')
        risk_reward = _to_optional_float(data.get('risk_reward'), 'risk_reward')
        atr_pips = _to_optional_float(data.get('atr_pips'), 'atr_pips')
        manual_provenance = data.get('model_provenance') if isinstance(data.get('model_provenance'), dict) else {}
        if not manual_provenance and signal_generator is not None and signal_generator.is_loaded:
            try:
                manual_provenance = signal_generator.get_model_provenance()
            except Exception:
                manual_provenance = {}
        
        # Create signal document
        signal_doc = {
            'pair': pair,
            'signal': signal_type,
            'confidence': confidence_value,
            'entry_price': entry_price,
            'stop_loss': stop_loss,
            'take_profit': take_profit,
            'sl_pips': sl_pips,
            'tp_pips': tp_pips,
            'risk_reward': risk_reward,
            'model_probabilities': data.get('model_probabilities'),
            'models_agree': data.get('models_agree'),
            'atr_pips': atr_pips,
            'reason': manual_note,
            'model_version': data.get('model_version') or manual_provenance.get('model_version'),
            'model_provenance': manual_provenance,
            'run_id': manual_provenance.get('run_id'),
            'source': source,
            'visibility': 'private',
            'submitted_by_user_id': str(payload.get('user_id', '')),
            'submitted_by_email': str(payload.get('email', '')).strip().lower(),
            'created_at': datetime.now(timezone.utc),
            'status': 'active'  # active, closed, expired
        }
        
        # Insert to MongoDB
        result = signals_collection.insert_one(signal_doc)
        
        print(f"✓ Signal хадгалагдлаа: {signal_type} @ {confidence_value:.2f}% (ID: {result.inserted_id})")
        
        return jsonify({
            'success': True,
            'message': 'Signal амжилттай хадгалагдлаа',
            'signal_id': str(result.inserted_id)
        })
        
    except ValueError as validation_err:
        return jsonify({'success': False, 'error': str(validation_err)}), 400
    except Exception as e:
        print(f"Signal хадгалах алдаа: {e}")
        logger.exception('Signal save failed')
        return _public_error_response()


@app.route('/signals/history', methods=['GET'])
def get_signals_history():
    """
    Таамгийн түүх авах endpoint
    Query params:
        - pair: Валютын хослол (optional, default: EUR_USD)
        - limit: Хэдэн signal авах (optional, default: 50)
        - signal_type: BUY/SELL/HOLD (optional)
        - min_confidence: Хамгийн бага итгэлцэл (optional)
    """
    try:
        limit_result = enforce_public_rate_limit('signals_history')
        if limit_result:
            return limit_result

        normalized_pair, pair_error = enforce_trading_scope(request.args.get('pair', 'EUR_USD'))
        if pair_error:
            return pair_error

        pair = normalized_pair.replace('/', '_')
        limit, limit_error, _limit_status = _parse_int_query_param('limit', 50, minimum=1, maximum=200)
        if limit_error:
            return limit_error
        signal_type = request.args.get('signal_type')
        min_confidence, min_confidence_error, _min_conf_status = _parse_float_query_param('min_confidence')
        if min_confidence_error:
            return min_confidence_error
        
        # Build query (trusted auto-generated signals only)
        query = _trusted_signal_filter_for_pair(pair)
        
        if signal_type:
            query['signal'] = signal_type
        
        if min_confidence is not None:
            query['confidence'] = {'$gte': min_confidence}
        
        # Get signals sorted by created_at (newest first)
        signals = list(signals_collection.find(query)
                      .sort('created_at', -1)
                      .limit(limit))
        
        # Convert ObjectId to string and datetime to ISO string
        for sig in signals:
            sig['_id'] = str(sig['_id'])
            if sig.get('created_at'):
                sig['created_at'] = sig['created_at'].isoformat()
        
        return jsonify({
            'success': True,
            'count': len(signals),
            'signals': signals
        })
        
    except Exception as e:
        print(f"Signal history алдаа: {e}")
        logger.exception('Signal history failed')
        return _public_error_response()


@app.route('/signals/stats', methods=['GET'])
def get_signals_stats():
    """
    Таамгийн статистик
    """
    try:
        limit_result = enforce_public_rate_limit('signals_stats')
        if limit_result:
            return limit_result

        normalized_pair, pair_error = enforce_trading_scope(request.args.get('pair', 'EUR_USD'))
        if pair_error:
            return pair_error

        pair = normalized_pair.replace('/', '_')
        
        # Count by signal type
        trusted_pair_query = _trusted_signal_filter_for_pair(pair)
        buy_count = signals_collection.count_documents({**trusted_pair_query, 'signal': 'BUY'})
        sell_count = signals_collection.count_documents({**trusted_pair_query, 'signal': 'SELL'})
        hold_count = signals_collection.count_documents({**trusted_pair_query, 'signal': 'HOLD'})
        total_count = buy_count + sell_count + hold_count
        
        # Average confidence
        pipeline = [
            {'$match': trusted_pair_query},
            {'$group': {
                '_id': None,
                'avg_confidence': {'$avg': '$confidence'},
                'max_confidence': {'$max': '$confidence'},
                'min_confidence': {'$min': '$confidence'}
            }}
        ]
        
        stats_result = list(signals_collection.aggregate(pipeline))
        avg_stats = stats_result[0] if stats_result else {}
        
        # Last signal
        last_signal = signals_collection.find_one(
            trusted_pair_query,
            sort=[('created_at', -1)]
        )
        
        if last_signal:
            last_signal['_id'] = str(last_signal['_id'])
            if last_signal.get('created_at'):
                last_signal['created_at'] = last_signal['created_at'].isoformat()
        
        return jsonify({
            'success': True,
            'pair': pair,
            'stats': {
                'total_signals': total_count,
                'buy_count': buy_count,
                'sell_count': sell_count,
                'hold_count': hold_count,
                'avg_confidence': round(avg_stats.get('avg_confidence', 0), 2),
                'max_confidence': round(avg_stats.get('max_confidence', 0), 2),
                'min_confidence': round(avg_stats.get('min_confidence', 0), 2)
            },
            'last_signal': last_signal
        })
        
    except Exception as e:
        print(f"Signal stats алдаа: {e}")
        logger.exception('Signal stats failed')
        return _public_error_response()


@app.route('/signals/latest', methods=['GET'])
def get_latest_signal():
    """
    Сүүлийн сигнал(ууд) авах endpoint (auto + manual)
    Query params:
        - pair: Валютын хослол (default: EUR_USD)
        - limit: Хэдэн сигнал авах (default: 1, max: 20)
        - min_confidence: Хамгийн бага итгэлцэл (optional, 0-1 эсвэл 0-100)
    """
    try:
        limit_result = enforce_public_rate_limit('signals_latest')
        if limit_result:
            return limit_result

        normalized_pair, pair_error = enforce_trading_scope(request.args.get('pair', 'EUR_USD'))
        if pair_error:
            return pair_error

        pair_normalized = normalized_pair.upper().strip()
        pair_slash = pair_normalized.replace('_', '/')
        pair_under = pair_normalized.replace('/', '_')
        pair_compact = pair_slash.replace('/', '')

        limit, limit_error, _limit_status = _parse_int_query_param('limit', 1, minimum=1, maximum=20)
        if limit_error:
            return limit_error

        min_confidence, min_confidence_error, _min_conf_status = _parse_float_query_param('min_confidence')
        if min_confidence_error:
            return min_confidence_error

        query = {
            'pair': {'$in': [pair_under, pair_slash, pair_compact]},
            'signal': {'$in': ['BUY', 'SELL']},
        }

        if min_confidence is not None:
            min_conf = min_confidence
            if 0.0 <= min_conf <= 1.0:
                min_conf *= 100.0
            min_conf = max(0.0, min(100.0, min_conf))
            query['confidence'] = {'$gte': min_conf}

        # Return all BUY/SELL signals (auto + manual), sorted by newest.
        results = list(signals_collection.find(
            query,
            sort=[('created_at', -1)]
        ).limit(limit))

        for s in results:
            s['_id'] = str(s['_id'])
            if s.get('created_at'):
                s['created_at'] = s['created_at'].isoformat()

        if limit == 1:
            # Backward-compatible: return single signal object
            return jsonify({
                'success': True,
                'signal': results[0] if results else None,
                'message': 'Одоогоор сигнал байхгүй байна' if not results else None
            })
        else:
            return jsonify({
                'success': True,
                'signals': results,
                'count': len(results)
            })

    except Exception as e:
        print(f"Latest signal алдаа: {e}")
        logger.exception('Latest signal failed')
        return _public_error_response()


# ==================== NEWS & AI ANALYSIS ====================

ANALYSIS_TTL_SECONDS = 6 * 60 * 60
PRELOADED_ANALYSIS_PAIRS = [TRADING_SCOPE_PAIR]
ANALYSIS_REFRESH_CHECK_SECONDS = 60
ANALYSIS_JOB_TTL_SECONDS = 15 * 60
ANALYSIS_POLL_AFTER_SECONDS = 2

try:
    ANALYSIS_CIRCUIT_FAIL_THRESHOLD = int(os.environ.get('ANALYSIS_CIRCUIT_FAIL_THRESHOLD', '3'))
except Exception:
    ANALYSIS_CIRCUIT_FAIL_THRESHOLD = 3

try:
    ANALYSIS_CIRCUIT_COOLDOWN_SECONDS = int(os.environ.get('ANALYSIS_CIRCUIT_COOLDOWN_SECONDS', '180'))
except Exception:
    ANALYSIS_CIRCUIT_COOLDOWN_SECONDS = 180

ANALYSIS_CIRCUIT_FAIL_THRESHOLD = max(1, ANALYSIS_CIRCUIT_FAIL_THRESHOLD)
ANALYSIS_CIRCUIT_COOLDOWN_SECONDS = max(30, ANALYSIS_CIRCUIT_COOLDOWN_SECONDS)

_analysis_cache = {}  # { pair: { "data": ..., "created_ts": ... } }
_analysis_jobs = {}  # { job_id: {status, pair, ...} }
_analysis_pair_jobs = {}  # { pair: job_id }
_analysis_queue = Queue(maxsize=128)
_analysis_pending_pairs = set()
_analysis_refreshing_pairs = set()
_analysis_lock = threading.Lock()
_analysis_circuit = {
    'failures': 0,
    'open_until': 0.0,
    'last_error': '',
}


def normalize_analysis_pair(raw_pair):
    pair = (raw_pair or "EUR/USD").strip().upper().replace("_", "/")
    if "/" not in pair and len(pair) == 6:
        pair = f"{pair[:3]}/{pair[3:]}"
    return pair


def _normalize_analysis_outlook(pair, insight):
    if not isinstance(insight, dict):
        return insight

    normalized = insight.copy()
    normalizer = getattr(market_analyst, '_normalize_outlook_by_pair', None)
    if callable(normalizer):
        try:
            normalized['outlook'] = normalizer(
                pair,
                normalized.get('outlook'),
                technical_signal=normalized.get('signal')
            )
        except Exception as e:
            print(f"[WARN] outlook normalization failed for {pair}: {e}")
    return normalized


def _attach_analysis_guardrails(payload):
    if not isinstance(payload, dict):
        return payload

    guarded = payload.copy()
    confidence_raw = guarded.get('confidence', guarded.get('confidence_score', 50))
    try:
        confidence_value = float(confidence_raw)
    except Exception:
        confidence_value = 50.0

    if confidence_value <= 1:
        confidence_value *= 100.0

    if confidence_value >= 90:
        uncertainty = 'low'
    elif confidence_value >= 75:
        uncertainty = 'medium'
    else:
        uncertainty = 'high'

    guarded.setdefault('uncertainty_level', uncertainty)
    guarded.setdefault(
        'actionability',
        'wait_for_confirmation' if uncertainty == 'high' else 'review_then_execute_with_risk_controls'
    )
    guarded.setdefault('human_oversight_required', True)
    guarded.setdefault(
        'oversight_note',
        'This output is educational. Validate with independent sources and pre-defined risk limits before acting.'
    )
    return guarded


def _parse_analysis_created_at(value):
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        except Exception:
            return None
    else:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _build_analysis_entry(insight, pair):
    normalized_insight = _attach_analysis_guardrails(_normalize_analysis_outlook(pair, insight))
    created_dt = _parse_analysis_created_at(normalized_insight.get('created_at'))
    created_ts = created_dt.timestamp() if created_dt else time.time()
    created_at = (created_dt.isoformat() if created_dt else datetime.now(timezone.utc).isoformat())

    if not normalized_insight.get('created_at'):
        normalized_insight['created_at'] = created_at

    return {
        'data': normalized_insight,
        'created_ts': created_ts,
        'created_at': created_at,
    }


def _is_analysis_fresh(entry):
    return (time.time() - entry['created_ts']) < ANALYSIS_TTL_SECONDS


def _get_latest_analysis_from_db(pair):
    insights_collection = getattr(market_analyst, 'insights_collection', None)
    if insights_collection is None:
        return None

    doc = insights_collection.find_one(
        {'pair': pair, 'error': {'$ne': True}},
        sort=[('_id', -1)]
    )
    if not doc:
        return None

    object_id = doc.get('_id')
    if not doc.get('created_at') and hasattr(object_id, 'generation_time'):
        doc['created_at'] = object_id.generation_time.astimezone(timezone.utc).isoformat()
    elif isinstance(doc.get('created_at'), datetime):
        doc['created_at'] = doc['created_at'].isoformat()

    doc['_id'] = str(object_id)
    return doc


def _get_cached_analysis(pair):
    with _analysis_lock:
        cached = _analysis_cache.get(pair)
    if cached:
        return cached

    mem_cached = market_analyst._insight_cache.get(pair)
    if mem_cached and mem_cached.get('data'):
        entry = _build_analysis_entry(mem_cached['data'], pair)
        with _analysis_lock:
            _analysis_cache[pair] = entry
        return entry

    db_cached = _get_latest_analysis_from_db(pair)
    if db_cached:
        entry = _build_analysis_entry(db_cached, pair)
        with _analysis_lock:
            _analysis_cache[pair] = entry
        return entry

    return None


def _build_mock_signal_for_pair(pair):
    mock_signal = {
        'signal': 'NEUTRAL',
        'confidence': 50.0
    }

    if pair == 'MARKET':
        return mock_signal

    try:
        df = get_twelvedata_dataframe(symbol=pair, interval='15min', outputsize=100)
        if df is not None and not df.empty:
            close_price = df['close'].iloc[-1]
            open_price = df['open'].iloc[-1]
            mock_signal['signal'] = 'BUY' if close_price > open_price else 'SELL'
            mock_signal['confidence'] = 75.0
    except Exception as e:
        print(f"[WARN] mock signal build failed for {pair}: {e}")

    return mock_signal


def _generate_pair_analysis(pair):
    signal_payload = _build_mock_signal_for_pair(pair)
    insight = market_analyst.generate_ai_insight(signal_payload, pair=pair)

    if insight and not insight.get('error'):
        insight = _normalize_analysis_outlook(pair, insight)
        entry = _build_analysis_entry(insight, pair)
        with _analysis_lock:
            _analysis_cache[pair] = entry

    return insight


def _analysis_now_parts(now_ts=None):
    timestamp = float(now_ts if now_ts is not None else time.time())
    iso = datetime.fromtimestamp(timestamp, timezone.utc).isoformat()
    return timestamp, iso


def _analysis_job_expires_at(now_ts: float):
    return datetime.fromtimestamp(now_ts + ANALYSIS_JOB_TTL_SECONDS, timezone.utc)


def _analysis_jobs_persistence_available():
    return analysis_jobs_collection is not None


def _normalize_analysis_job_doc(doc):
    if not isinstance(doc, dict):
        return None

    job = dict(doc)
    raw_id = job.get('_id') or job.get('job_id')
    if not raw_id:
        return None

    job['job_id'] = str(raw_id)
    if '_id' in job:
        job['_id'] = str(raw_id)

    created_at = job.get('created_at')
    if isinstance(created_at, datetime):
        created_at = created_at.astimezone(timezone.utc).isoformat()
        job['created_at'] = created_at

    updated_at = job.get('updated_at')
    if isinstance(updated_at, datetime):
        updated_at = updated_at.astimezone(timezone.utc).isoformat()
        job['updated_at'] = updated_at

    result_created_at = job.get('result_created_at')
    if isinstance(result_created_at, datetime):
        job['result_created_at'] = result_created_at.astimezone(timezone.utc).isoformat()

    job['pair'] = normalize_analysis_pair(job.get('pair'))
    return job


def _sync_analysis_job_local(job):
    normalized_job = _normalize_analysis_job_doc(job)
    if not normalized_job:
        return None

    job_id = normalized_job['job_id']
    pair = normalized_job.get('pair')

    with _analysis_lock:
        _analysis_jobs[job_id] = normalized_job
        status = str(normalized_job.get('status', '')).lower()
        if pair and status in ('queued', 'running'):
            _analysis_pair_jobs[pair] = job_id
        elif pair and _analysis_pair_jobs.get(pair) == job_id:
            _analysis_pair_jobs.pop(pair, None)

    return dict(normalized_job)


def _get_active_analysis_job_for_pair_from_db(pair):
    if not _analysis_jobs_persistence_available():
        return None

    try:
        doc = analysis_jobs_collection.find_one(
            {
                'pair': pair,
                'status': {'$in': ['queued', 'running']},
            },
            sort=[('updated_ts', -1)],
        )
        return _normalize_analysis_job_doc(doc)
    except Exception as e:
        logger.warning(f'analysis job lookup failed for {pair}: {e}')
        return None


def _load_analysis_job_from_db(job_id: str):
    if not _analysis_jobs_persistence_available() or not job_id:
        return None

    try:
        doc = analysis_jobs_collection.find_one({'_id': str(job_id)})
        if not doc:
            return None
        return _sync_analysis_job_local(doc)
    except Exception as e:
        logger.warning(f'analysis job load failed ({job_id}): {e}')
        return None


def _persist_analysis_job(job):
    if not _analysis_jobs_persistence_available() or not isinstance(job, dict):
        return

    try:
        analysis_jobs_collection.update_one(
            {'_id': str(job.get('job_id'))},
            {'$set': {
                'pair': normalize_analysis_pair(job.get('pair')),
                'status': str(job.get('status', 'queued')).lower(),
                'created_at': job.get('created_at'),
                'created_ts': float(job.get('created_ts', time.time())),
                'updated_at': job.get('updated_at'),
                'updated_ts': float(job.get('updated_ts', time.time())),
                'retry_after': int(job.get('retry_after') or ANALYSIS_POLL_AFTER_SECONDS),
                'error': job.get('error'),
                'result_created_at': job.get('result_created_at'),
                'expires_at': _analysis_job_expires_at(float(job.get('updated_ts', time.time()))),
            }},
            upsert=True,
        )
    except Exception as e:
        logger.warning(f'analysis job persist failed ({job.get("job_id")}): {e}')


def _cleanup_analysis_jobs_locked(now_ts=None):
    now_ts = float(now_ts if now_ts is not None else time.time())
    expired_ids = []

    for job_id, job in _analysis_jobs.items():
        if job.get('status') in ('queued', 'running'):
            continue

        updated_ts = float(job.get('updated_ts', job.get('created_ts', now_ts)))
        if now_ts - updated_ts > ANALYSIS_JOB_TTL_SECONDS:
            expired_ids.append(job_id)

    for job_id in expired_ids:
        pair = _analysis_jobs.get(job_id, {}).get('pair')
        if pair and _analysis_pair_jobs.get(pair) == job_id:
            _analysis_pair_jobs.pop(pair, None)
        _analysis_jobs.pop(job_id, None)


def _is_analysis_circuit_open():
    now_ts = time.time()
    with _analysis_lock:
        open_until = float(_analysis_circuit.get('open_until', 0.0) or 0.0)
        if open_until <= now_ts:
            if _analysis_circuit.get('open_until'):
                _analysis_circuit['open_until'] = 0.0
            return False, 0

        return True, max(1, int(open_until - now_ts))


def _record_analysis_success():
    with _analysis_lock:
        _analysis_circuit['failures'] = 0
        _analysis_circuit['open_until'] = 0.0
        _analysis_circuit['last_error'] = ''


def _record_analysis_failure(error_message: str):
    now_ts = time.time()
    with _analysis_lock:
        existing_open_until = float(_analysis_circuit.get('open_until', 0.0) or 0.0)
        if existing_open_until > now_ts:
            return max(1, int(existing_open_until - now_ts))

        next_failures = int(_analysis_circuit.get('failures', 0)) + 1
        _analysis_circuit['failures'] = next_failures
        _analysis_circuit['last_error'] = str(error_message or '')

        if next_failures >= ANALYSIS_CIRCUIT_FAIL_THRESHOLD:
            open_until = now_ts + ANALYSIS_CIRCUIT_COOLDOWN_SECONDS
            _analysis_circuit['failures'] = 0
            _analysis_circuit['open_until'] = open_until
            return ANALYSIS_CIRCUIT_COOLDOWN_SECONDS

    return 0


def _enqueue_pair_analysis_job(pair, force=False):
    normalized_pair = normalize_analysis_pair(pair)
    now_ts, now_iso = _analysis_now_parts()

    if not force:
        current = _get_cached_analysis(normalized_pair)
        if current and _is_analysis_fresh(current):
            return None

    with _analysis_lock:
        _cleanup_analysis_jobs_locked(now_ts)

        existing_job_id = _analysis_pair_jobs.get(normalized_pair)
        if existing_job_id:
            existing_job = _analysis_jobs.get(existing_job_id)
            if existing_job and existing_job.get('status') in ('queued', 'running'):
                return existing_job_id

    existing_db_job = _get_active_analysis_job_for_pair_from_db(normalized_pair)
    if existing_db_job:
        _sync_analysis_job_local(existing_db_job)
        return existing_db_job['job_id']

    job_id = str(uuid.uuid4())
    job_doc = {
        '_id': job_id,
        'job_id': job_id,
        'pair': normalized_pair,
        'status': 'queued',
        'created_at': now_iso,
        'created_ts': now_ts,
        'updated_at': now_iso,
        'updated_ts': now_ts,
        'retry_after': ANALYSIS_POLL_AFTER_SECONDS,
        'error': None,
        'result_created_at': None,
        'worker_id': None,
        'expires_at': _analysis_job_expires_at(now_ts),
    }

    if _analysis_jobs_persistence_available():
        try:
            analysis_jobs_collection.insert_one(job_doc)
        except DuplicateKeyError:
            existing_db_job = _get_active_analysis_job_for_pair_from_db(normalized_pair)
            if existing_db_job:
                _sync_analysis_job_local(existing_db_job)
                return existing_db_job['job_id']
        except Exception as db_err:
            logger.warning(f'analysis enqueue DB error for {normalized_pair}: {db_err}')

    _sync_analysis_job_local(job_doc)
    with _analysis_lock:
        _analysis_pending_pairs.add(normalized_pair)

    # In memory-only mode, wake up local worker queue immediately.
    if not _analysis_jobs_persistence_available() and BACKGROUND_WORKERS_ENABLED:
        try:
            _analysis_queue.put_nowait(job_id)
        except Full:
            _set_analysis_job_state(
                job_id,
                'failed',
                error='Analysis queue is full',
                retry_after=ANALYSIS_POLL_AFTER_SECONDS,
            )
    return job_id


def _get_analysis_job(job_id: str):
    with _analysis_lock:
        _cleanup_analysis_jobs_locked()
        job = _analysis_jobs.get(job_id)
        if job:
            return dict(job)

    return _load_analysis_job_from_db(job_id)


def _set_analysis_job_state(job_id: str, status: str, **extra):
    now_ts, now_iso = _analysis_now_parts()
    payload = {
        'status': status,
        'updated_at': now_iso,
        'updated_ts': now_ts,
        **extra,
    }

    job = None
    with _analysis_lock:
        job = _analysis_jobs.get(job_id)

    if not job:
        loaded = _load_analysis_job_from_db(job_id)
        job = loaded if loaded else None
    if not job:
        return None

    with _analysis_lock:
        for key, value in payload.items():
            job[key] = value

        pair = job.get('pair')
        if pair and status in ('queued', 'running'):
            _analysis_pair_jobs[pair] = job_id
        elif pair and _analysis_pair_jobs.get(pair) == job_id:
            _analysis_pair_jobs.pop(pair, None)

        snapshot = dict(job)

    _persist_analysis_job(snapshot)
    return snapshot


def _claim_next_analysis_job_from_db():
    if not _analysis_jobs_persistence_available():
        return None

    now_ts, now_iso = _analysis_now_parts()
    stale_running_before = now_ts - ANALYSIS_JOB_TTL_SECONDS
    claim_query = {
        '$or': [
            {'status': 'queued'},
            {
                'status': 'running',
                'updated_ts': {'$lte': stale_running_before},
            },
        ]
    }

    claim_update = {
        '$set': {
            'status': 'running',
            'updated_at': now_iso,
            'updated_ts': now_ts,
            'retry_after': ANALYSIS_POLL_AFTER_SECONDS,
            'worker_id': WORKER_INSTANCE_ID,
            'expires_at': _analysis_job_expires_at(now_ts),
        }
    }

    try:
        claimed = analysis_jobs_collection.find_one_and_update(
            claim_query,
            claim_update,
            sort=[('created_ts', 1)],
            return_document=ReturnDocument.AFTER,
        )
        if not claimed:
            return None
        return _sync_analysis_job_local(claimed)
    except Exception as db_err:
        logger.warning(f'analysis claim failed: {db_err}')
        return None


def _analysis_worker_task():
    print('[INFO] Starting pair analysis queue worker...')
    update_background_job_state('pair_analysis_worker', 'starting', 'Analysis queue worker started')

    while True:
        if not _renew_worker_lock('pair_analysis_worker', 900):
            update_background_job_state('pair_analysis_worker', 'error', 'Worker lock lost')
            return

        job_id = None
        pair = None

        if _analysis_jobs_persistence_available():
            claimed_job = _claim_next_analysis_job_from_db()
            if not claimed_job:
                time.sleep(1)
                continue
            job_id = claimed_job.get('job_id')
            pair = claimed_job.get('pair')
        else:
            try:
                job_id = _analysis_queue.get(timeout=1)
            except Empty:
                continue

        try:
            if not pair:
                job = _get_analysis_job(job_id)
                if not job:
                    continue
                pair = normalize_analysis_pair(job.get('pair'))

            _set_analysis_job_state(
                job_id,
                'running',
                retry_after=ANALYSIS_POLL_AFTER_SECONDS,
                worker_id=WORKER_INSTANCE_ID,
            )
            if pair:
                with _analysis_lock:
                    _analysis_pending_pairs.discard(pair)
                    _analysis_refreshing_pairs.add(pair)

            circuit_open, retry_after = _is_analysis_circuit_open()
            if circuit_open:
                _set_analysis_job_state(
                    job_id,
                    'failed',
                    error='Analysis circuit breaker is open',
                    retry_after=retry_after,
                )
                update_background_job_state('pair_analysis_worker', 'error', f'Circuit breaker open ({retry_after}s)')
                continue

            insight = _generate_pair_analysis(pair)
            if insight and not insight.get('error'):
                _record_analysis_success()
                created_at = insight.get('created_at') or datetime.now(timezone.utc).isoformat()
                _set_analysis_job_state(
                    job_id,
                    'completed',
                    error=None,
                    retry_after=0,
                    result_created_at=created_at,
                )
                update_background_job_state('pair_analysis_worker', 'ok', f'Analysis refreshed for {pair}')
            else:
                raise RuntimeError('Analysis generation returned empty/error response')

        except Exception as analysis_err:
            retry_after = _record_analysis_failure(str(analysis_err))
            _set_analysis_job_state(
                job_id,
                'failed',
                error=str(analysis_err),
                retry_after=retry_after,
            )
            pair_label = pair or 'unknown'
            update_background_job_state('pair_analysis_worker', 'error', f'{pair_label}: {analysis_err}')
        finally:
            if pair:
                with _analysis_lock:
                    _analysis_refreshing_pairs.discard(pair)
                    if _analysis_pair_jobs.get(pair) == job_id:
                        _analysis_pair_jobs.pop(pair, None)
            if not _analysis_jobs_persistence_available():
                _analysis_queue.task_done()


def _schedule_pair_refresh(pair, force=False, return_job_id=False):
    pair = normalize_analysis_pair(pair)

    if not BACKGROUND_WORKERS_ENABLED and not _analysis_jobs_persistence_available():
        if return_job_id:
            return None
        return False

    job_id = _enqueue_pair_analysis_job(pair, force=force)

    if return_job_id:
        return job_id
    return bool(job_id)


def pair_analysis_preloader_task():
    """Top 3 pair анализыг startup дээр бэлэн болгоод 6 цаг тутам шинэчилнэ."""
    print("[INFO] Starting pair analysis preloader (6h TTL)...")
    update_background_job_state('pair_analysis_preloader', 'starting', 'Preloading top pairs')
    if not _renew_worker_lock('pair_analysis_preloader', 900):
        update_background_job_state('pair_analysis_preloader', 'error', 'Worker lock unavailable at start')
        return

    for pair in PRELOADED_ANALYSIS_PAIRS:
        cached = _get_cached_analysis(pair)
        if cached:
            age = int(time.time() - cached['created_ts'])
            print(f"[OK] Preloaded {pair} analysis from cache/DB (age={age}s)")
        else:
            print(f"[INFO] No existing analysis for {pair}; scheduling initial generation")
            _schedule_pair_refresh(pair, force=True)

    while True:
        if not _renew_worker_lock('pair_analysis_preloader', 900):
            update_background_job_state('pair_analysis_preloader', 'error', 'Worker lock lost')
            return

        try:
            for pair in PRELOADED_ANALYSIS_PAIRS:
                cached = _get_cached_analysis(pair)
                if not cached or not _is_analysis_fresh(cached):
                    _schedule_pair_refresh(pair, force=True)

            update_background_job_state('pair_analysis_preloader', 'ok', 'Preloader refresh check complete')
        except Exception as e:
            print(f"[WARN] pair_analysis_preloader_task error: {e}")
            update_background_job_state('pair_analysis_preloader', 'error', str(e))

        time.sleep(ANALYSIS_REFRESH_CHECK_SECONDS)


_start_background_job('pair_analysis_worker', _analysis_worker_task, lock_ttl_seconds=900)
_start_background_job('pair_analysis_preloader', pair_analysis_preloader_task, lock_ttl_seconds=900)

@app.route('/api/news', methods=['GET'])
def get_news():
    """Мэдээний жагсаалт авах (History, Upcoming, Outlook) - Cached"""
    try:
        news_type = request.args.get('type', 'latest')

        # Backward compatibility: mobile may pass "past", backend canonical is "history"
        if news_type == 'past':
            news_type = 'history'
        
        # Determine cache key
        cache_key = 'latest'
        if news_type in ['history', 'upcoming', 'outlook']:
            cache_key = news_type
            
        # Try to get from cache
        cached_data = news_cache.get(cache_key)
        
        if cached_data:
            return jsonify({
                "status": "success",
                "data": cached_data,
                "cached": True
            }), 200
            
        # Fallback to direct fetch if cache is empty
        if news_type == 'history':
            data = market_analyst.get_news_history()
        elif news_type == 'upcoming':
            data = market_analyst.get_upcoming_news()
        elif news_type == 'outlook':
            data = market_analyst.get_market_outlook()
        else:
            # Default to latest
            data = market_analyst.get_latest_news()
        
        return jsonify({
            "status": "success",
            "data": data,
            "cached": False
        }), 200
    except Exception as e:
        print(f"Error in latest news: {e}")
        traceback.print_exc()
        return _public_error_response('Failed to load latest news')

@app.route('/api/news/analyze', methods=['POST'])
def analyze_news_event():
    """Specific news event analysis using AI"""
    try:
        limit_result = enforce_public_rate_limit('api_news_analyze', max_requests=20, window_seconds=60)
        if limit_result:
            return limit_result

        event_data = request.json
        if not event_data:
            return jsonify({"status": "error", "message": "No data provided"}), 400
            
        analysis = market_analyst.analyze_specific_event(event_data)
        analysis = _attach_analysis_guardrails(analysis)
        
        return jsonify({
            "status": "success",
            "analysis": analysis
        }), 200
    except Exception as e:
        print(f"Error in news analysis: {e}")
        traceback.print_exc()
        return _public_error_response('Failed to analyze news event')

import traceback

@app.route('/api/market-analysis', methods=['GET'])
def get_market_analysis():
    """AI зах зээлийн дүгнэлт авах (6h shared cache + stale-while-refresh)."""
    try:
        limit_result = enforce_public_rate_limit('api_market_analysis', max_requests=25, window_seconds=60)
        if limit_result:
            return limit_result

        pair, pair_error = enforce_trading_scope(request.args.get('pair', 'EUR/USD'), allow_market=True)
        if pair_error:
            return pair_error

        pair = normalize_analysis_pair(pair)
        print(f"Analyzing pair: {pair}")

        if pair == "MARKET":
            market_signal = {
                "signal": "NEUTRAL",
                "confidence": 50.0
            }
            insight = market_analyst.generate_ai_insight(market_signal, pair=pair)
            insight = _normalize_analysis_outlook(pair, insight)
            insight = _attach_analysis_guardrails(insight)
            return jsonify({
                "status": "success",
                "data": insight,
                "cached": False,
                "analysis_source": "market-direct",
                "generated_at": datetime.now(timezone.utc).isoformat()
            }), 200

        cached = _get_cached_analysis(pair)
        if cached and _is_analysis_fresh(cached):
            age = int(time.time() - cached['created_ts'])
            print(f"[CACHE HIT] /api/market-analysis for {pair} (age={age}s)")
            return jsonify({
                "status": "success",
                "data": cached['data'],
                "cached": True,
                "analysis_source": "cache-fresh",
                "generated_at": cached.get('created_at')
            }), 200

        if cached:
            age = int(time.time() - cached['created_ts'])
            print(f"[STALE CACHE] /api/market-analysis for {pair} (age={age}s) -> refreshing in background")
            _schedule_pair_refresh(pair, force=True)
            return jsonify({
                "status": "success",
                "data": cached['data'],
                "cached": True,
                "stale": True,
                "analysis_source": "cache-stale",
                "generated_at": cached.get('created_at')
            }), 200

        circuit_open, retry_after = _is_analysis_circuit_open()
        if circuit_open:
            return jsonify({
                "status": "error",
                "message": "Analysis service is cooling down",
                "retry_after": retry_after,
            }), 503

        print(f"[CACHE MISS] /api/market-analysis for {pair} -> queued async generation")
        job_id = _schedule_pair_refresh(pair, force=True, return_job_id=True)
        if job_id:
            return jsonify({
                "status": "pending",
                "job_id": job_id,
                "pair": pair,
                "analysis_source": "queued",
                "poll_after_seconds": ANALYSIS_POLL_AFTER_SECONDS,
                "message": "Analysis queued",
            }), 202

        if not BACKGROUND_WORKERS_ENABLED:
            return jsonify({
                "status": "error",
                "message": "Analysis worker unavailable in current process role",
                "role": APP_PROCESS_ROLE,
            }), 503

        fallback = _get_cached_analysis(pair)
        if fallback:
            return jsonify({
                "status": "success",
                "data": fallback['data'],
                "cached": True,
                "stale": True,
                "analysis_source": "cache-fallback",
                "generated_at": fallback.get('created_at')
            }), 200

        return jsonify({"status": "error", "message": "Analysis unavailable"}), 503
    except Exception as e:
        print(f"Error in analysis: {e}")
        traceback.print_exc()
        return _public_error_response('Market analysis failed')


@app.route('/api/market-analysis/status/<job_id>', methods=['GET'])
def get_market_analysis_status(job_id):
    """Async market analysis job polling endpoint."""
    try:
        limit_result = enforce_public_rate_limit('api_market_analysis', max_requests=40, window_seconds=60)
        if limit_result:
            return limit_result

        job = _get_analysis_job(job_id)
        if not job:
            return jsonify({"status": "error", "message": "Analysis job not found"}), 404

        pair = normalize_analysis_pair(job.get('pair'))
        job_status = str(job.get('status', '')).lower()

        if job_status in ('queued', 'running'):
            return jsonify({
                "status": "pending",
                "job_id": job_id,
                "pair": pair,
                "job_status": job_status,
                "analysis_source": "queued",
                "poll_after_seconds": int(job.get('retry_after') or ANALYSIS_POLL_AFTER_SECONDS),
            }), 202

        if job_status == 'completed':
            cached = _get_cached_analysis(pair)
            if cached:
                return jsonify({
                    "status": "success",
                    "data": cached['data'],
                    "cached": False,
                    "analysis_source": "fresh-generated",
                    "generated_at": cached.get('created_at') or job.get('result_created_at'),
                    "job_id": job_id,
                }), 200

            return jsonify({
                "status": "error",
                "message": "Analysis completed but cached result is unavailable",
                "job_id": job_id,
            }), 503

        fallback = _get_cached_analysis(pair)
        if fallback:
            return jsonify({
                "status": "success",
                "data": fallback['data'],
                "cached": True,
                "stale": True,
                "analysis_source": "cache-fallback",
                "generated_at": fallback.get('created_at'),
                "job_id": job_id,
            }), 200

        return jsonify({
            "status": "error",
            "message": job.get('error') or 'Analysis job failed',
            "job_id": job_id,
            "retry_after": int(job.get('retry_after') or ANALYSIS_POLL_AFTER_SECONDS),
        }), 503
    except Exception as e:
        print(f"Error in analysis status: {e}")
        traceback.print_exc()
        return _public_error_response('Analysis status lookup failed')


# ==================== HEALTH CHECK ====================

@app.route('/health', methods=['GET'])
def health():
    return jsonify({
        'status': 'ok',
        'service': 'predictrix-api',
        'role': APP_PROCESS_ROLE,
        'timestamp': datetime.now(timezone.utc).isoformat()
    })


@app.route('/health/details', methods=['GET'])
@token_required
def health_details(payload):
    try:
        client.server_info()
        user_count = users_collection.count_documents({})
        jobs_status, jobs_snapshot = get_background_job_health()
        status = 'healthy' if jobs_status == 'healthy' else 'degraded'
        
        return jsonify({
            'status': status,
            'database': 'connected',
            'users_count': user_count,
            'signal_generator': 'GBDT loaded' if (signal_generator and signal_generator.is_loaded) else 'not loaded',
            'background_jobs_status': jobs_status,
            'background_jobs': jobs_snapshot,
            'rate_limit_backend': _rate_limit_backend,
            'role': APP_PROCESS_ROLE,
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        logger.exception('Health details failed')
        return jsonify({'status': 'unhealthy', 'error': 'Internal health check failure'}), 500

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        'name': 'Predictrix API',
        'version': '1.0',
        'model': 'GBDT (Multi-TF Ensemble)',
        'status': 'running',
        'endpoints': {
            'auth': ['/auth/register', '/auth/login', '/auth/verify-email', '/auth/refresh', '/auth/logout', '/auth/me'],
            'notifications': ['/notifications/register', '/notifications/unregister', '/notifications/preferences', '/notifications/test'],
            'rates': ['/rates/live', '/rates/specific'],
            'signal': ['/signal', '/signal/demo', '/predict'],
            'system': ['/health', '/health/details']
        }
    })

# ==================== MAIN ====================

if __name__ == '__main__':
    PORT = 5000
    
    print("=" * 60)
    print("PREDICTRIX API v1.0")
    print("=" * 60)
    mongo_status = "Connected" if (getattr(market_analyst, 'db', None) is not None) else "Offline (no connection)"
    print(f"✓ MongoDB: {mongo_status}")
    print(f"✓ GBDT Signal Generator: {'Loaded (Multi-TF Ensemble)' if (signal_generator and signal_generator.is_loaded) else 'Not loaded'}")
    print(f"✓ Yahoo Finance (yfinance): Enabled — no API key required")
    print(f"✓ Port: {PORT}")
    print(f"\n[+] API Endpoints:")
    print(f"  POST /auth/register, /auth/login")
    print(f"  GET  /rates/live, /rates/specific")
    print(f"  GET  /signal, /signal/demo")
    print(f"  POST /predict")
    print(f"  GET  /health")
    print("=" * 60)
    
    # Use waitress for production-ready server (more stable on Windows)
    from waitress import serve
    print(f"\n[+] Server starting with Waitress on http://0.0.0.0:{PORT}")
    serve(app, host='0.0.0.0', port=PORT, threads=4)
