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
from datetime import datetime, timedelta, timezone
import jwt
import bcrypt
import os
import random
import re
import uuid

# Import configuration
from config.settings import (
    MONGO_URI, SECRET_KEY, API_HOST, API_PORT,
    MAIL_SERVER, MAIL_PORT, MAIL_USE_TLS, MAIL_USE_SSL,
    MAIL_USERNAME, MAIL_PASSWORD, MAIL_DEFAULT_SENDER,
    VERIFICATION_CODE_EXPIRY_MINUTES, RESET_CODE_EXPIRY_MINUTES,
    JWT_ISSUER, JWT_AUDIENCE, ACCESS_TOKEN_EXPIRATION_MINUTES,
    ALLOW_AUTH_CODE_IN_RESPONSE, ENABLE_BACKGROUND_JOBS, BG_LOCK_TTL_SECONDS,
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
CORS(app)

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

try:
    client = MongoClient(MONGO_URI)
    db = client['users_db']
    users_collection = db['users']
    verification_codes = db['verification_codes']
    reset_codes = db['reset_codes']
    signals_collection = db['signals']  # Таамгууд хадгалах collection
    in_app_notifications = db['in_app_notifications']  # In-app мэдэгдлүүд
    runtime_locks = db['runtime_locks']  # Background task distributed locks
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
    try:
        runtime_locks.create_index('expires_at', expireAfterSeconds=0)
        runtime_locks.create_index('owner')
    except Exception as idx_err:
        print(f"[WARN] runtime_locks index: {idx_err}", flush=True)
    print("✓ MongoDB холбогдлоо", flush=True)
except Exception as e:
    print(f"✗ MongoDB холбогдох алдаа: {e}", flush=True)
    exit(1)

RUNTIME_OWNER_ID = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"


def acquire_runtime_lock(lock_name: str, ttl_seconds: int = BG_LOCK_TTL_SECONDS) -> bool:
    """Acquire or renew a short-lived distributed lock for background jobs."""
    ttl = max(30, int(ttl_seconds))
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(seconds=ttl)
    try:
        result = runtime_locks.find_one_and_update(
            {
                '_id': lock_name,
                '$or': [
                    {'expires_at': {'$lte': now}},
                    {'owner': RUNTIME_OWNER_ID},
                ],
            },
            {
                '$set': {
                    'owner': RUNTIME_OWNER_ID,
                    'updated_at': now,
                    'expires_at': expires_at,
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return bool(result and result.get('owner') == RUNTIME_OWNER_ID)
    except Exception as e:
        print(f"[WARN] Lock acquire failed ({lock_name}): {e}")
        return False

# ==================== SIGNAL GENERATORS ====================

signal_generator = None  # GBDT trained model

def load_signal_generator():
    global signal_generator
    
    try:
        signal_generator = get_signal_generator_gbdt()
        if signal_generator.is_loaded:
            print("✓ GBDT Signal Generator ачаалагдлаа (Trained Multi-TF Ensemble)")
            return True
        else:
            print("⚠ GBDT model file олдсонгүй")
            signal_generator = None
            return False
    except Exception as e:
        print(f"⚠ GBDT Signal Generator алдаа: {e}")
        signal_generator = None
        return False

# Load on startup in background thread (avoid blocking gunicorn bind)
threading.Thread(target=load_signal_generator, daemon=True).start()

# ==================== PRELOAD HISTORICAL DATA ====================

def preload_historical_data():
    """Backend эхлэхэд historical data урьдчилан татах"""
    try:
        print("📥 Preloading historical data...")
        df = get_twelvedata_dataframe(interval="1min", outputsize=500)
        if df is not None and len(df) >= 200:
            print(f"[OK] Historical data preloaded: {len(df)} bars")
            return True
        else:
            print(f"[WARN] Historical data preload: got {len(df) if df is not None else 0} bars")
    except Exception as e:
        print(f"[WARN] Historical data preload failed: {e}")
    return False

# Preload on startup in background thread (avoid blocking gunicorn bind)
threading.Thread(target=preload_historical_data, daemon=True).start()

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
    while True:
        if acquire_runtime_lock('jobs:news_updater'):
            news_cache.update()
        # Run every 30 minutes
        time.sleep(1800)

# ==================== NEWS NOTIFICATION SCHEDULER ====================
# Checks upcoming events every 2 minutes, sends notifications 10 min before event

def news_notification_scheduler():
    """10 минутын өмнө мэдээний мэдэгдэл илгээх scheduler"""
    print("[INFO] Starting news notification scheduler (10-min advance alerts)...")
    time.sleep(30)  # Wait for initial news cache to load
    
    while True:
        if not acquire_runtime_lock('jobs:news_notification_scheduler'):
            time.sleep(120)
            continue

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
        
        except Exception as e:
            print(f"[WARN] News notification scheduler error: {e}")
        
        time.sleep(120)  # Check every 2 minutes

# ==================== CONTINUOUS SIGNAL GENERATOR ====================
# Минут тутамд таамаглал гаргаж, итгэлцэл >= 0.9 бол DB-д хадгалж,
# хэрэглэгчийн босгоос дээш бол push notification илгээнэ.

# Supported currency pairs for continuous generation
SIGNAL_PAIRS = ["EUR/USD"]

# Minimum confidence to save signal to DB (default: 0.9 = 90%)
try:
    SAVE_CONFIDENCE_THRESHOLD = float(os.environ.get("SAVE_CONFIDENCE_THRESHOLD", "0.9"))
except Exception:
    SAVE_CONFIDENCE_THRESHOLD = 0.9
SAVE_CONFIDENCE_THRESHOLD = max(0.0, min(1.0, SAVE_CONFIDENCE_THRESHOLD))

# Cache to avoid duplicate signals within the same direction
_last_signal_cache = {}  # { pair: { signal, timestamp } }

# Signal endpoint response cache (per pair, 60s TTL)
_signal_response_cache = {}  # { pair: { "data": ..., "time": ... } }
SIGNAL_CACHE_TTL = 60  # seconds

def continuous_signal_generator():
    """
    Background thread: минут тутамд модел ажиллуулж таамаглал гаргана.
    - Итгэлцэл >= 90% бол MongoDB-д хадгална
    - Хэрэглэгч бүрийн signal_threshold-оос дээш бол push мэдэгдэл илгээнэ
    """
    print("[INFO] Starting continuous signal generator (every 60s)...")
    # Wait for signal generator to load
    for _ in range(60):
        if signal_generator is not None and signal_generator.is_loaded:
            break
        time.sleep(2)
    
    if signal_generator is None or not signal_generator.is_loaded:
        print("[ERROR] Continuous signal generator: model not loaded, stopping.")
        return
    
    print("[OK] Continuous signal generator active.")
    
    while True:
        if not acquire_runtime_lock('jobs:continuous_signal_generator'):
            time.sleep(15)
            continue

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
                conf_decimal = sig_conf / 100.0 if sig_conf > 1 else sig_conf

                # For logging: HOLD-д hold_confidence (HOLD-ийн магадлал) харуулна
                # BUY/SELL-д тухайн signal-ийн confidence харуулна
                if sig_type == 'HOLD':
                    hold_conf_pct = result.get('hold_confidence', sig_conf)
                    dir_signal = result.get('directional_signal', '')
                    print(f"[SIGNAL] {pair}: HOLD (hold={hold_conf_pct:.1f}%, lean={dir_signal} {sig_conf:.1f}%) (threshold: {SAVE_CONFIDENCE_THRESHOLD*100}%)")
                else:
                    print(f"[SIGNAL] {pair}: {sig_type} @ {sig_conf:.1f}% (threshold: {SAVE_CONFIDENCE_THRESHOLD*100}%)")

                # Only process BUY/SELL signals with confidence >= 0.9 (90%)
                if sig_type in ('BUY', 'SELL') and conf_decimal >= SAVE_CONFIDENCE_THRESHOLD:
                    # Check duplicate: skip if same signal type within last 5 minutes
                    cache_key = pair
                    last = _last_signal_cache.get(cache_key)
                    if last and last['signal'] == sig_type:
                        elapsed = (datetime.now(timezone.utc) - last['timestamp']).total_seconds()
                        if elapsed < 300:  # 5 minutes dedup
                            print(f"[SKIP] Duplicate {sig_type} for {pair} (last: {elapsed:.0f}s ago)")
                            continue

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
                        'models_agree': result.get('models_agree'),
                        'atr_pips': result.get('atr_pips'),
                        'reason': result.get('reason'),
                        'source': 'auto',  # Mark as auto-generated
                        'created_at': datetime.now(timezone.utc),
                        'status': 'active'
                    }
                    db_result = signals_collection.insert_one(signal_doc)
                    print(f"[DB] Signal saved: {sig_type} {pair} @ {sig_conf:.1f}% (ID: {db_result.inserted_id})")

                    # Update dedup cache
                    _last_signal_cache[cache_key] = {
                        'signal': sig_type,
                        'timestamp': datetime.now(timezone.utc)
                    }

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

        # Wait 60 seconds before next cycle
        time.sleep(60)


def start_background_workers():
    """Start background workers only when explicitly enabled."""
    if not ENABLE_BACKGROUND_JOBS:
        print("[INFO] Background workers disabled (ENABLE_BACKGROUND_JOBS=false)")
        return

    print(f"[INFO] Background workers enabled (owner={RUNTIME_OWNER_ID})")
    threading.Thread(target=news_updater_task, daemon=True).start()
    threading.Thread(target=news_notification_scheduler, daemon=True).start()
    threading.Thread(target=continuous_signal_generator, daemon=True).start()


start_background_workers()

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

def generate_token(user_id, email):
    now = datetime.now(timezone.utc)
    payload = {
        'user_id': str(user_id),
        'email': email,
        'iat': now,
        'iss': JWT_ISSUER,
        'aud': JWT_AUDIENCE,
        'exp': now + timedelta(minutes=ACCESS_TOKEN_EXPIRATION_MINUTES),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm='HS256')

def verify_token(token):
    try:
        payload = jwt.decode(
            token,
            SECRET_KEY,
            algorithms=['HS256'],
            issuer=JWT_ISSUER,
            audience=JWT_AUDIENCE,
            options={'require': ['exp', 'iat', 'iss', 'aud']},
        )
        return payload
    except Exception:
        return None


def _extract_bearer_token() -> str:
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        return auth_header.split(' ', 1)[1].strip()
    return ''

def token_required(f):
    from functools import wraps
    @wraps(f)
    def decorated(*args, **kwargs):
        token = _extract_bearer_token()
        if not token:
            return jsonify({'error': 'Token шаардлагатай'}), 401
        payload = verify_token(token)
        if not payload:
            return jsonify({'error': 'Token хүчингүй'}), 401
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
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')
    
    if not all([name, email, password]):
        return jsonify({'error': 'Бүх талбарыг бөглөнө үү'}), 400
    
    if not re.match(r'^[\w\.-]+@[\w\.-]+\.\w+$', email):
        return jsonify({'error': 'Имэйл хаяг буруу байна'}), 400
    
    if len(password) < 6:
        return jsonify({'error': 'Нууц үг хамгийн багадаа 6 тэмдэгт'}), 400
    
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
        'created_at': datetime.now(timezone.utc),
        'expires_at': datetime.now(timezone.utc) + timedelta(minutes=VERIFICATION_CODE_EXPIRY_MINUTES)
    })
    
    # Try sending email; fall back to demo_mode if sending fails for any reason
    email_sent = send_verification_email(email, code, name) if is_email_configured() else False
    if email_sent:
        return jsonify({
            'success': True,
            'message': 'Баталгаажуулах код илгээлээ',
            'email': email
        })

    response = {
        'success': True,
        'message': 'Код үүсгэлээ. Имэйл үйлчилгээ түр доголдсон тул дахин оролдоно уу.',
        'email': email,
    }
    if ALLOW_AUTH_CODE_IN_RESPONSE:
        print(f"[DEMO] Verification code for {email}: {code}")
        response['demo_mode'] = True
        response['verification_code'] = code
    return jsonify(response)

@app.route('/auth/verify-email', methods=['POST'])
def verify_email():
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
        'email_verified': True,
        'created_at': datetime.now(timezone.utc)
    }
    result = users_collection.insert_one(user)
    
    # Clean up
    verification_codes.delete_many({'email': email})
    
    # Generate token
    token = generate_token(result.inserted_id, email)
    
    return jsonify({
        'success': True,
        'token': token,
        'user': {
            'name': user['name'],
            'email': user['email'],
            'email_verified': True
        }
    })

@app.route('/auth/resend-verification', methods=['POST'])
def resend_verification():
    data = request.get_json(silent=True) or {}
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

    response = {
        'success': True,
        'message': 'Код үүсгэлээ. Имэйл үйлчилгээ түр доголдсон тул дахин оролдоно уу.',
    }
    if ALLOW_AUTH_CODE_IN_RESPONSE:
        print(f"[DEMO] Resend verification code for {email}: {code}")
        response['demo_mode'] = True
        response['verification_code'] = code
    return jsonify(response)

@app.route('/auth/login', methods=['POST'])
def login():
    data = request.get_json(silent=True) or {}
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
    
    token = generate_token(user['_id'], email)
    
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
        'token': token,
        'user': {
            'name': user['name'],
            'email': user['email'],
            'email_verified': user.get('email_verified', False)
        }
    })


@app.route('/auth/verify', methods=['POST'])
def verify_auth_token():
    """Check whether a JWT token is valid (for mobile auth bootstrap)."""
    data = request.get_json(silent=True) or {}
    token = str(data.get('token', '')).strip() or _extract_bearer_token()
    if not token:
        return jsonify({'success': False, 'valid': False, 'error': 'Token шаардлагатай'}), 400

    payload = verify_token(token)
    if not payload:
        return jsonify({'success': True, 'valid': False}), 200

    user = users_collection.find_one({'email': payload.get('email')})
    if not user:
        return jsonify({'success': True, 'valid': False}), 200

    return jsonify({
        'success': True,
        'valid': True,
        'user': {
            'name': user.get('name', ''),
            'email': user.get('email', ''),
            'email_verified': user.get('email_verified', False),
        }
    }), 200

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


@app.route('/auth/update', methods=['PUT'])
@token_required
def update_profile(payload):
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()

    if not name:
        return jsonify({'error': 'Нэр хоосон байж болохгүй'}), 400
    if len(name) > 80:
        return jsonify({'error': 'Нэр хэт урт байна'}), 400

    result = users_collection.update_one(
        {'email': payload.get('email')},
        {'$set': {'name': name, 'updated_at': datetime.now(timezone.utc)}}
    )

    if result.matched_count == 0:
        return jsonify({'error': 'Хэрэглэгч олдсонгүй'}), 404

    user = users_collection.find_one({'email': payload.get('email')})
    return jsonify({
        'success': True,
        'message': 'Профайл амжилттай шинэчлэгдлээ',
        'user': {
            'name': user.get('name', ''),
            'email': user.get('email', ''),
            'email_verified': user.get('email_verified', False),
        }
    })


@app.route('/auth/change-password', methods=['PUT'])
@token_required
def change_password(payload):
    data = request.get_json(silent=True) or {}
    old_password = (data.get('oldPassword') or data.get('old_password') or '').strip()
    new_password = (data.get('newPassword') or data.get('new_password') or '').strip()

    if not old_password or not new_password:
        return jsonify({'error': 'oldPassword болон newPassword шаардлагатай'}), 400
    if len(new_password) < 6:
        return jsonify({'error': 'Шинэ нууц үг хамгийн багадаа 6 тэмдэгт'}), 400
    if old_password == new_password:
        return jsonify({'error': 'Шинэ нууц үг өмнөхтэй ижил байж болохгүй'}), 400

    user = users_collection.find_one({'email': payload.get('email')})
    if not user:
        return jsonify({'error': 'Хэрэглэгч олдсонгүй'}), 404

    stored_password = user.get('password', '')
    if isinstance(stored_password, str):
        stored_password = stored_password.encode()

    if not bcrypt.checkpw(old_password.encode(), stored_password):
        return jsonify({'error': 'Одоогийн нууц үг буруу байна'}), 400

    hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    users_collection.update_one(
        {'email': payload.get('email')},
        {'$set': {'password': hashed, 'password_updated_at': datetime.now(timezone.utc)}}
    )

    return jsonify({'success': True, 'message': 'Нууц үг амжилттай солигдлоо'})

@app.route('/auth/forgot-password', methods=['POST'])
def forgot_password():
    data = request.get_json(silent=True) or {}
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

    response = {
        'success': True,
        'message': 'Код үүсгэлээ. Имэйл үйлчилгээ түр доголдсон тул дахин оролдоно уу.',
    }
    if ALLOW_AUTH_CODE_IN_RESPONSE:
        print(f"[DEMO] Reset code for {email}: {code}")
        response['demo_mode'] = True
        response['reset_code'] = code
    return jsonify(response)


@app.route('/auth/verify-reset-code', methods=['POST'])
def verify_reset_code():
    data = request.get_json(silent=True) or {}
    email = data.get('email', '').strip().lower()
    code = data.get('code', '').strip()

    if not email or not code:
        return jsonify({'error': 'email болон code шаардлагатай'}), 400

    record = reset_codes.find_one({
        'email': email,
        'code': code,
        'expires_at': {'$gt': datetime.now(timezone.utc)}
    })
    if not record:
        return jsonify({'error': 'Код буруу эсвэл хугацаа дууссан'}), 400

    return jsonify({'success': True, 'message': 'Код хүчинтэй'})

@app.route('/auth/reset-password', methods=['POST'])
def reset_password():
    data = request.get_json(silent=True) or {}
    email = data.get('email', '').strip().lower()
    code = data.get('code', '').strip()
    new_password = data.get('new_password', '')

    if not email or not code or not new_password:
        return jsonify({'error': 'email, code, new_password шаардлагатай'}), 400
    
    if len(new_password) < 6:
        return jsonify({'error': 'Нууц үг хамгийн багадаа 6 тэмдэгт'}), 400
    
    record = reset_codes.find_one({
        'email': email,
        'code': code,
        'expires_at': {'$gt': datetime.now(timezone.utc)}
    })
    
    if not record:
        return jsonify({'error': 'Код буруу эсвэл хугацаа дууссан'}), 400
    
    user = users_collection.find_one({'email': email})
    if not user:
        return jsonify({'error': 'Хэрэглэгч олдсонгүй'}), 404

    hashed = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()
    users_collection.update_one(
        {'email': email},
        {'$set': {'password': hashed, 'password_updated_at': datetime.now(timezone.utc)}}
    )
    reset_codes.delete_many({'email': email})
    
    return jsonify({'success': True, 'message': 'Нууц үг амжилттай солигдлоо'})

# ==================== PUSH NOTIFICATION ENDPOINTS ====================

@app.route('/notifications/register', methods=['POST'])
def register_push_token():
    """Push notification token бүртгэх"""
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return jsonify({'error': 'Token шаардлагатай'}), 401
    
    payload = verify_token(auth.split(' ')[1])
    if not payload:
        return jsonify({'error': 'Token буруу'}), 401
    
    data = request.json or {}
    push_token = (
        data.get('push_token')
        or data.get('expo_push_token')
        or data.get('token')
        or ''
    ).strip()
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
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return jsonify({'error': 'Token шаардлагатай'}), 401
    
    payload = verify_token(auth.split(' ')[1])
    if not payload:
        return jsonify({'error': 'Token буруу'}), 401
    
    push_service.unregister_token(payload['user_id'])
    return jsonify({'success': True, 'message': 'Push token устгагдлаа'})

@app.route('/notifications/preferences', methods=['GET'])
def get_notification_preferences():
    """Мэдэгдлийн тохиргоо авах"""
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return jsonify({'error': 'Token шаардлагатай'}), 401
    
    payload = verify_token(auth.split(' ')[1])
    if not payload:
        return jsonify({'error': 'Token буруу'}), 401
    
    prefs = push_service.get_preferences(payload['user_id'])
    return jsonify({'success': True, 'preferences': prefs})

@app.route('/notifications/preferences', methods=['PUT'])
def update_notification_preferences():
    """Мэдэгдлийн тохиргоо шинэчлэх"""
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return jsonify({'error': 'Token шаардлагатай'}), 401
    
    payload = verify_token(auth.split(' ')[1])
    if not payload:
        return jsonify({'error': 'Token буруу'}), 401
    
    data = request.json or {}
    success = push_service.update_preferences(payload['user_id'], data)
    
    if success:
        return jsonify({'success': True, 'message': 'Тохиргоо хадгалагдлаа'})
    return jsonify({'error': 'Тохиргоо хадгалж чадсангүй'}), 500

@app.route('/notifications/test', methods=['POST'])
def test_push_notification():
    """Тест мэдэгдэл илгээх (debug зорилгоор)"""
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return jsonify({'error': 'Token шаардлагатай'}), 401
    
    payload = verify_token(auth.split(' ')[1])
    if not payload:
        return jsonify({'error': 'Token буруу'}), 401
    
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
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return jsonify({'error': 'Token шаардлагатай'}), 401

    payload = verify_token(auth.split(' ')[1])
    if not payload:
        return jsonify({'error': 'Token буруу'}), 401

    limit = min(int(request.args.get('limit', 20)), 50)
    ntype = request.args.get('type', None)

    user_id = payload['user_id']

    # Resolve user's news_impact_filter preference
    prefs_doc = push_service.push_tokens.find_one({'user_id': user_id}, {'news_impact_filter': 1})
    impact_filter = (prefs_doc or {}).get('news_impact_filter', 'high')
    if impact_filter == 'all':
        allowed_impacts = ['high', 'medium', 'low']
    elif impact_filter == 'medium':
        allowed_impacts = ['high', 'medium']
    else:  # 'high'
        allowed_impacts = ['high']

    # Build query: non-news always shown; news filtered by impact preference
    news_clause = {'type': 'news', 'data.impact': {'$in': allowed_impacts}}
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
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/notifications/in-app/unread-count', methods=['GET'])
def get_unread_notification_count():
    """Уншаагүй мэдэгдлийн тоо буцаах."""
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return jsonify({'error': 'Token шаардлагатай'}), 401
    payload = verify_token(auth.split(' ')[1])
    if not payload:
        return jsonify({'error': 'Token буруу'}), 401

    user_id = payload['user_id']
    try:
        # Resolve user's news_impact_filter preference
        prefs_doc = push_service.push_tokens.find_one({'user_id': user_id}, {'news_impact_filter': 1})
        impact_filter = (prefs_doc or {}).get('news_impact_filter', 'high')
        if impact_filter == 'all':
            allowed_impacts = ['high', 'medium', 'low']
        elif impact_filter == 'medium':
            allowed_impacts = ['high', 'medium']
        else:  # 'high'
            allowed_impacts = ['high']

        count = in_app_notifications.count_documents({
            'read_by': {'$nin': [user_id]},
            '$or': [
                {'type': {'$ne': 'news'}},
                {'type': 'news', 'data.impact': {'$in': allowed_impacts}}
            ]
        })
        return jsonify({'success': True, 'unread_count': count})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/notifications/in-app/mark-read', methods=['POST'])
def mark_notifications_read():
    """Мэдэгдлүүдийг уншсан гэж тэмдэглэх.
    Body: { ids: ['id1','id2',...] }  — хоосон бол бүгдийг тэмдэглэнэ.
    """
    auth = request.headers.get('Authorization', '')
    if not auth.startswith('Bearer '):
        return jsonify({'error': 'Token шаардлагатай'}), 401
    payload = verify_token(auth.split(' ')[1])
    if not payload:
        return jsonify({'error': 'Token буруу'}), 401

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
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== LIVE RATES (Yahoo Finance) ====================

@app.route('/rates/live', methods=['GET'])
def get_live_rates():
    """
    Get live rates for all 20 forex pairs from Yahoo Finance (yfinance)
    Returns rate, change, and change_percent for each pair
    """
    try:
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
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/rates/specific', methods=['GET'])
def get_specific_rate():
    """Get specific currency pair rate"""
    pair = request.args.get('pair', 'EUR_USD')
    
    try:
        result = get_twelvedata_live_rate()
        
        if result and result.get('success'):
            return jsonify({
                'success': True,
                'pair': pair,
                'rate': result.get('rate', 0),
                'bid': result.get('bid'),
                'ask': result.get('ask'),
                'timestamp': result.get('time')
            })
        else:
            return jsonify({'success': False, 'error': 'Rate олдсонгүй'}), 404
            
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

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
        if signal_generator is None or not signal_generator.is_loaded:
            return jsonify({
                'success': False,
                'error': 'Signal Generator ачаалагдаагүй'
            }), 500

        min_confidence = float(request.args.get('min_confidence', 60))
        pair = request.args.get('pair', 'EUR/USD').replace('_', '/')

        # Check signal response cache (60s TTL)
        cached = _signal_response_cache.get(pair)
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

        conf_threshold = min_confidence / 100.0 if min_confidence > 1 else min_confidence
        signal = signal_generator.generate_signal(
            df_1min=df,
            multi_tf_data=multi_tf,
            min_confidence=conf_threshold,
            symbol=pair.replace('/', '')
        )

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
            **signal
        }

        # Cache the response
        _signal_response_cache[pair] = {'data': response_data, 'time': time.time()}

        return jsonify(response_data)

    except Exception as e:
        print(f"Signal error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/signal/demo', methods=['GET'])
def get_signal_demo():
    """Demo signal with test data"""
    try:
        if signal_generator is None or not signal_generator.is_loaded:
            return jsonify({
                'success': False,
                'error': 'Signal Generator ачаалагдаагүй'
            }), 500
        
        min_confidence = float(request.args.get('min_confidence', 85))
        
        import pandas as pd
        test_file = Path(__file__).parent.parent / 'data' / 'EUR_USD_test.csv'
        
        if not test_file.exists():
            return jsonify({'success': False, 'error': 'Test data олдсонгүй'}), 404
        
        df = pd.read_csv(test_file)
        df.columns = df.columns.str.lower()
        df = df.tail(500).reset_index(drop=True)
        
        signal = signal_generator.generate_signal(df, min_confidence)
        
        return jsonify({
            'success': True,
            'pair': 'EUR_USD',
            'demo': True,
            **signal
        })
        
    except Exception as e:
        print(f"Signal V2 demo error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ==================== PREDICT (Signal wrapper) ====================

@app.route('/predict', methods=['POST'])
def predict():
    """Main prediction endpoint - GBDT Multi-Timeframe Ensemble"""
    try:
        data = request.json or {}
        pair = data.get('pair', 'EUR_USD').replace('_', '/')

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
        return jsonify({'success': False, 'error': str(e)}), 500

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

        pair = request.args.get('pair', 'EUR/USD').replace('_', '/')
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
                result['feature_check_error'] = str(e)
                result['status'] = 'ERROR'
        else:
            result['data_available'] = False
            result['status'] = 'NO_DATA'
            result['message'] = 'Could not fetch API data (rate limited?)'

        return jsonify(result)

    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ==================== SIGNAL STORAGE ====================

@app.route('/signal/save', methods=['POST'])
def save_signal():
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
        signal_type = data.get('signal')
        confidence = data.get('confidence')
        pair = data.get('pair', 'EUR_USD').replace('/', '_')  # Normalize: EUR/USD → EUR_USD
        
        if not signal_type or confidence is None:
            return jsonify({'success': False, 'error': 'signal, confidence шаардлагатай'}), 400
        
        # Create signal document
        signal_doc = {
            'pair': pair,
            'signal': signal_type,
            'confidence': float(confidence),
            'entry_price': data.get('entry_price'),
            'stop_loss': data.get('stop_loss'),
            'take_profit': data.get('take_profit'),
            'sl_pips': data.get('sl_pips'),
            'tp_pips': data.get('tp_pips'),
            'risk_reward': data.get('risk_reward'),
            'model_probabilities': data.get('model_probabilities'),
            'models_agree': data.get('models_agree'),
            'atr_pips': data.get('atr_pips'),
            'reason': data.get('reason'),
            'created_at': datetime.now(timezone.utc),
            'status': 'active'  # active, closed, expired
        }
        
        # Insert to MongoDB
        result = signals_collection.insert_one(signal_doc)
        
        print(f"✓ Signal хадгалагдлаа: {signal_type} @ {confidence}% (ID: {result.inserted_id})")
        
        return jsonify({
            'success': True,
            'message': 'Signal амжилттай хадгалагдлаа',
            'signal_id': str(result.inserted_id)
        })
        
    except Exception as e:
        print(f"Signal хадгалах алдаа: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


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
        pair = request.args.get('pair', 'EUR_USD')
        limit = int(request.args.get('limit', 50))
        signal_type = request.args.get('signal_type')
        min_confidence = request.args.get('min_confidence')
        
        # Build query
        query = {'pair': pair}
        
        if signal_type:
            query['signal'] = signal_type
        
        if min_confidence:
            query['confidence'] = {'$gte': float(min_confidence)}
        
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
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/signals/stats', methods=['GET'])
def get_signals_stats():
    """
    Таамгийн статистик
    """
    try:
        pair = request.args.get('pair', 'EUR_USD')
        
        # Count by signal type
        buy_count = signals_collection.count_documents({'pair': pair, 'signal': 'BUY'})
        sell_count = signals_collection.count_documents({'pair': pair, 'signal': 'SELL'})
        hold_count = signals_collection.count_documents({'pair': pair, 'signal': 'HOLD'})
        total_count = buy_count + sell_count + hold_count
        
        # Average confidence
        pipeline = [
            {'$match': {'pair': pair}},
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
            {'pair': pair},
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
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/signals/latest', methods=['GET'])
def get_latest_signal():
    """
    Сүүлийн auto-generated сигнал(ууд) авах endpoint
    Query params:
        - pair: Валютын хослол (default: EUR_USD)
        - limit: Хэдэн сигнал авах (default: 1, max: 20)
    """
    try:
        pair = request.args.get('pair', 'EUR_USD')
        limit = min(int(request.args.get('limit', 1)), 20)

        # Support both EUR_USD and EUR/USD formats in DB
        pair_slash = pair.replace('_', '/')
        pair_under = pair.replace('/', '_')
        query = {'pair': {'$in': [pair_under, pair_slash]}, 'signal': {'$in': ['BUY', 'SELL']}}

        # Return all BUY/SELL signals (auto + manual), sorted by newest
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
        return jsonify({'success': False, 'error': str(e)}), 500


# ==================== NEWS & AI ANALYSIS ====================

@app.route('/api/news', methods=['GET'])
def get_news():
    """Мэдээний жагсаалт авах (History, Upcoming, Outlook) - Cached"""
    try:
        news_type = request.args.get('type', 'latest')
        
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
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/news/analyze', methods=['POST'])
def analyze_news_event():
    """Specific news event analysis using AI"""
    try:
        event_data = request.json
        if not event_data:
            return jsonify({"status": "error", "message": "No data provided"}), 400
            
        analysis = market_analyst.analyze_specific_event(event_data)
        
        return jsonify({
            "status": "success",
            "analysis": analysis
        }), 200
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

import traceback

@app.route('/api/market-analysis', methods=['GET'])
def get_market_analysis():
    """AI зах зээлийн дүгнэлт авах (with in-memory cache)"""
    try:
        pair = request.args.get('pair', 'EUR/USD')
        print(f"Analyzing pair: {pair}")
        
        # Check if market_analyst already has a valid cache for this pair
        # If so, skip the TwelveData fetch entirely
        cached = market_analyst._insight_cache.get(pair)
        cache_ttl = market_analyst.cache_duration_market if pair == "MARKET" else market_analyst.cache_duration_pair
        if cached and (time.time() - cached["time"]) < cache_ttl:
            print(f"[CACHE HIT] /api/market-analysis for {pair}")
            return jsonify({
                "status": "success",
                "data": cached["data"]
            }), 200
        
        mock_signal = {
            "signal": "NEUTRAL",
            "confidence": 50.0
        }
        
        if pair != "MARKET":
            # 1. Одоогийн ханш болон дохиог авах
            try:
                df = get_twelvedata_dataframe(symbol=pair, interval="15min", outputsize=100)
                
                if df is not None and not df.empty:
                    # Simple trend check for demo
                    close = df['close'].iloc[-1]
                    open_p = df['open'].iloc[-1]
                    mock_signal["signal"] = "BUY" if close > open_p else "SELL"
                    mock_signal["confidence"] = 75.0
            except Exception as e:
                print(f"Error fetching data for {pair}: {e}")
                traceback.print_exc()
                # Continue with mock signal if data fetch fails
        
        insight = market_analyst.generate_ai_insight(mock_signal, pair=pair)
        
        return jsonify({
            "status": "success",
            "data": insight
        }), 200
    except Exception as e:
        print(f"Error in analysis: {e}")
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500


# ==================== HEALTH CHECK ====================

@app.route('/health', methods=['GET'])
def health():
    try:
        client.server_info()
        user_count = users_collection.count_documents({})
        
        return jsonify({
            'status': 'healthy',
            'database': 'connected',
            'users_count': user_count,
            'signal_generator': 'GBDT loaded' if (signal_generator and signal_generator.is_loaded) else 'not loaded',
            'timestamp': datetime.now(timezone.utc).isoformat()
        })
    except Exception as e:
        return jsonify({'status': 'unhealthy', 'error': str(e)}), 500

@app.route('/', methods=['GET'])
def index():
    return jsonify({
        'name': 'Predictrix API',
        'version': '1.0',
        'model': 'GBDT (Multi-TF Ensemble)',
        'status': 'running',
        'endpoints': {
            'auth': ['/auth/register', '/auth/login', '/auth/verify-email', '/auth/me'],
            'notifications': ['/notifications/register', '/notifications/unregister', '/notifications/preferences', '/notifications/test'],
            'rates': ['/rates/live', '/rates/specific'],
            'signal': ['/signal', '/signal/demo', '/predict'],
            'system': ['/health']
        }
    })

# ==================== MAIN ====================

if __name__ == '__main__':
    PORT = API_PORT
    
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
    print(f"\n[+] Server starting with Waitress on http://{API_HOST}:{PORT}")
    serve(app, host=API_HOST, port=PORT, threads=4)
