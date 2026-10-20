from io import BytesIO
from PIL import Image, ImageDraw, ImageFont
import os
import json
import uuid
import hashlib
import hmac
import secrets
import requests
import threading
from dotenv import load_dotenv
from urllib.parse import quote
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from markupsafe import escape
from sqlalchemy import inspect, text, func, or_
from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session, abort, Response
from werkzeug.middleware.proxy_fix import ProxyFix

from models import (
    db, User, Transaction, InboundMessage, SavedService, ServiceMarkup, PaymentFeeTier, AdminAuditLog,
    Referral, ReferralReward, MarketingContent, MarketingCampaign, MarketingMessage, MarketingSetting,
    SupportTicket
)
from wallet_service import generate_payment_link, get_payment_fee_percentage

# Import provider functions from the ClubKonnect adapter.
from provider import (
    fetch_data_variations,
    process_data_purchase,
    process_airtime_purchase,
    fetch_cable_plans,
    verify_smartcard,
    process_cable_tv,
    verify_meter,
    process_electricity_payment,
    verify_betting_account,
    process_betting_topup,
    fetch_education_packages,
    process_education_pin,
    fetch_account_balance,
    fetch_swiftbills_balance,
)
load_dotenv()
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
# Registered near the bottom of this file (after `db.init_app(app)` and the
# startup block below) to avoid a circular import — web.py imports `app`
# from this module, so it must exist before web.py is imported.
secret_key = os.getenv("SECRET_KEY")
if not secret_key:
    raise RuntimeError("SECRET_KEY environment variable is required for secure sessions")
app.config['SECRET_KEY'] = secret_key
app.config['DEBUG'] = os.getenv('FLASK_ENV', '').strip().lower() == 'development'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['SESSION_COOKIE_SECURE'] = os.getenv('FLASK_ENV', '').strip().lower() == 'production' or os.getenv('APP_BASE_URL', '').lower().startswith('https://')
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=30)
app.config['PREFERRED_URL_SCHEME'] = 'https'

# --- CONFIGURATION ---
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is required")
is_testing = os.getenv("IS_TESTING") == "true"
is_development = os.getenv("FLASK_ENV", "").strip().lower() == "development"
if is_testing and DATABASE_URL.startswith(("postgres://", "postgresql://")):
    raise RuntimeError("Tests cannot run against PostgreSQL; use a local SQLite DATABASE_URL")
if is_development and not is_testing:
    DATABASE_URL = "sqlite:///local.db"
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
if not DATABASE_URL.startswith(("postgresql://", "sqlite://")):
    raise RuntimeError("DATABASE_URL must use PostgreSQL in production or SQLite during local development/tests")
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_pre_ping': True,
    'pool_recycle': 300,
}
if DATABASE_URL.startswith("postgresql://"):
    # Supabase/Render pooler connections can reject reused prepared statements.
    # In Psycopg 3 the valid way to disable statement preparation is with
    # prepare_threshold=None, not prepared_statement_cache_size.
    app.config['SQLALCHEMY_ENGINE_OPTIONS']['connect_args'] = {
        'options': '-csearch_path=public',
        'prepare_threshold': None,
    }
ALLOW_DB_MUTATIONS = os.getenv("ALLOW_DB_MUTATIONS", "false").strip().lower() in {"1", "true", "yes", "on"}
BRIDGE_BASE_URL = os.getenv("BRIDGE_URL") or os.getenv(
    "NODE_BRIDGE_URL", "http://localhost:3000"
)
BRIDGE_URL = (
    BRIDGE_BASE_URL
    if BRIDGE_BASE_URL.rstrip("/").endswith("/api/sendText")
    else f"{BRIDGE_BASE_URL.rstrip('/')}/api/sendText"
)
BRIDGE_API_TOKEN = os.getenv("BRIDGE_API_TOKEN", "")
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "")
APP_BASE_URL = (os.getenv("APP_BASE_URL") or os.getenv("PUBLIC_BASE_URL") or "http://localhost:5000").rstrip("/")
PAYSTACK_CALLBACK_URL = os.getenv("PAYSTACK_CALLBACK_URL") or f"{APP_BASE_URL}/payments/paystack/callback"
META_API_TOKEN = os.getenv("META_API_TOKEN", "").strip()
META_PHONE_NUMBER_ID = os.getenv("META_PHONE_NUMBER_ID", "").strip()
META_API_VERSION = os.getenv("META_API_VERSION", "v20.0").strip()
META_VERIFY_TOKEN = os.getenv("META_VERIFY_TOKEN", "").strip()
WHATSAPP_BOT_PHONE = os.getenv("WHATSAPP_BOT_PHONE", "2348102314725").strip()
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()
ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", ADMIN_USERNAME).strip().lower()
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
RESEND_FROM_EMAIL = os.getenv("RESEND_FROM_EMAIL", "").strip()
app.config['ADMIN_EMAIL'] = ADMIN_EMAIL
app.config['RESEND_API_KEY'] = RESEND_API_KEY
app.config['RESEND_FROM_EMAIL'] = RESEND_FROM_EMAIL

# 2. Bind the single db instance from models.py to app
db.init_app(app)

from auth import auth_bp  # noqa: E402
app.register_blueprint(auth_bp)
# web.py imports `app` from this module, so it must be imported after `app`
# is defined above (deferred import avoids a circular-import error).
def _register_web_blueprint():
    from web import web_bp
    app.register_blueprint(web_bp)
_register_web_blueprint()

def _register_marketing_blueprint():
    from admin_marketing import marketing_admin_bp
    app.register_blueprint(marketing_admin_bp)
_register_marketing_blueprint()


@app.route("/sitemap.xml")
def sitemap():
    """Expose public pages to search engines without indexing private screens."""
    public_endpoints = ("web.home", "auth.login", "auth.signup", "auth.forgot_password")
    base_url = APP_BASE_URL
    urls = []
    for endpoint in public_endpoints:
        urls.append(f"  <url><loc>{escape(f'{base_url}{url_for(endpoint)}')}</loc></url>")
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(urls)
        + "\n</urlset>\n"
    )
    return Response(xml, mimetype="application/xml")


@app.route("/robots.txt")
def robots():
    return Response(
        f"User-agent: *\nAllow: /\nDisallow: /dashboard\nDisallow: /admin\nSitemap: {APP_BASE_URL}/sitemap.xml\n",
        mimetype="text/plain",
    )


@app.route("/manifest.webmanifest")
@app.route("/static/manifest.webmanifest")
def manifest():
    manifest_path = os.path.join(app.static_folder, "manifest.webmanifest")
    with open(manifest_path, "r", encoding="utf-8") as handle:
        return jsonify(json.load(handle))


def ensure_database_schema():
    """Add model columns to existing deployments that predate the current schema."""
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    if "admin_audit_logs" not in existing_tables:
        AdminAuditLog.__table__.create(bind=db.engine)

    marketing_table_map = {
        "referrals": Referral,
        "referral_rewards": ReferralReward,
        "marketing_content": MarketingContent,
        "marketing_campaigns": MarketingCampaign,
        "marketing_messages": MarketingMessage,
        "marketing_settings": MarketingSetting,
    }
    for tbl_name, tbl_model in marketing_table_map.items():
        if tbl_name not in existing_tables:
            tbl_model.__table__.create(bind=db.engine)

    dialect = db.engine.dialect.name
    json_type = "JSONB" if dialect == "postgresql" else "JSON"
    required_columns = {
        "users": {
            "paystack_customer_code": "VARCHAR(100)",
            "dva_account_number": "VARCHAR(20)",
            "dva_bank_name": "VARCHAR(50)",
            "version_id": "INTEGER NOT NULL DEFAULT 1",
            "email": "VARCHAR(120)",
            "password_hash": "VARCHAR(255)",
            "referral_code": "VARCHAR(30)",
            "referred_by_id": "INTEGER",
        },
        "transactions": {
            "meta_data": json_type,
            "provider_name": "VARCHAR(30)",
            "provider_reference": "VARCHAR(100)",
        },
    }

    for table_name, columns in required_columns.items():
        if table_name not in existing_tables:
            continue
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, column_type in columns.items():
            if column_name not in existing:
                db.session.execute(
                    text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {column_type}')
                )
    db.session.commit()

    # ORM-level unique=True on User.email doesn't add a DB constraint when the
    # column is added via ALTER TABLE above, so create the unique index
    # explicitly (partial index skips NULLs, since most users have no email).
    existing_indexes = {idx["name"] for idx in inspector.get_indexes("users")} if "users" in existing_tables else set()
    if "users" in existing_tables and "ix_users_email_unique" not in existing_indexes:
        where_clause = "WHERE email IS NOT NULL" if dialect == "postgresql" else ""
        db.session.execute(
            text(f'CREATE UNIQUE INDEX IF NOT EXISTS ix_users_email_unique ON "users" (email) {where_clause}')
        )
        db.session.commit()

    if "users" in existing_tables and "ix_users_referral_code" not in existing_indexes:
        where_clause = "WHERE referral_code IS NOT NULL" if dialect == "postgresql" else ""
        db.session.execute(
            text(f'CREATE UNIQUE INDEX IF NOT EXISTS ix_users_referral_code ON "users" (referral_code) {where_clause}')
        )
        db.session.commit()


SERVICE_TYPES = ("DATA_MTN", "DATA_AIRTEL", "DATA_GLO", "DATA_9MOBILE", "AIRTIME", "CABLE", "ELECTRICITY", "BETTING", "EDU")


def seed_service_markups():
    defaults = {
        "DATA_MTN": Decimal("5.00"), 
        "DATA_AIRTEL": Decimal("5.00"), 
        "DATA_GLO": Decimal("5.00"), 
        "DATA_9MOBILE": Decimal("5.00"), 
        "AIRTIME": Decimal("2.00"), 
        "CABLE": Decimal("2.00"), 
        "ELECTRICITY": Decimal("2.00"), 
        "BETTING": Decimal("2.00"), 
        "EDU": Decimal("5.00")
    }
    for service_type, markup_amount in defaults.items():
        if not ServiceMarkup.query.filter_by(service_type=service_type).first():
            db.session.add(ServiceMarkup(service_type=service_type, markup_amount=markup_amount))
    db.session.commit()


def seed_payment_fee_tiers():
    defaults = [
        {"label": "BELOW_1000", "min_amount": Decimal("0.00"), "max_amount": Decimal("999.99"), "fee_percentage": Decimal("2.50")},
        {"label": "1000_TO_20000", "min_amount": Decimal("1000.00"), "max_amount": Decimal("19999.99"), "fee_percentage": Decimal("1.50")},
        {"label": "ABOVE_20000", "min_amount": Decimal("20000.00"), "max_amount": None, "fee_percentage": Decimal("1.00")},
    ]
    for tier_data in defaults:
        if not PaymentFeeTier.query.filter_by(label=tier_data["label"]).first():
            db.session.add(PaymentFeeTier(**tier_data))
    db.session.commit()


def _masked_database_target():
    """Returns a safe-to-log description of the DB engine/host, no credentials."""
    try:
        url = db.engine.url
        host_part = f"{url.host}:{url.port}" if url.host else "(no host - likely SQLite/local file)"
        return f"driver={url.drivername} database={url.database} host={host_part}"
    except Exception as exc:
        return f"(unable to inspect database URL: {exc})"


with app.app_context():
    print(f"[startup] Connecting to database -> {_masked_database_target()}")

if ALLOW_DB_MUTATIONS:
    with app.app_context():
        # Create every currently declared model, including tables added after
        # the original deployment (such as scheduled_tasks and saved_services).
        db.create_all()
        ensure_database_schema()
        seed_service_markups()
        seed_payment_fee_tiers()
        try:
            from services.marketing_settings import seed_default_marketing_settings
            from services.referral_service import generate_unique_referral_code
            seed_default_marketing_settings()
            users_without_code = User.query.filter(
                (User.referral_code == None) | (User.referral_code == "")  # noqa: E711
            ).all()
            if users_without_code:
                for u in users_without_code:
                    u.referral_code = generate_unique_referral_code()
                db.session.commit()
        except Exception as seed_err:
            print(f"[startup] Marketing seeding warning: {seed_err}")
        existing_user_count = User.query.count()
        print(f"[startup] Existing users in database after create_all(): {existing_user_count}")

# --- STATE DEFINITIONS ---
STATES = {
    "IDLE": "IDLE",
    # Data Bundle Flow
    "AWAITING_DATA_NETWORK": "AWAITING_DATA_NETWORK",
    "AWAITING_DATA_CATEGORY": "AWAITING_DATA_CATEGORY",
    "AWAITING_DATA_PLAN": "AWAITING_DATA_PLAN",
    "AWAITING_DATA_NUMBER": "AWAITING_DATA_NUMBER",
    # Airtime Flow
    "AWAITING_AIRTIME_NETWORK": "AWAITING_AIRTIME_NETWORK",
    "AWAITING_AIRTIME_AMOUNT": "AWAITING_AIRTIME_AMOUNT",
    "AWAITING_AIRTIME_NUMBER": "AWAITING_AIRTIME_NUMBER",
    # Cable TV Flow
    "AWAITING_CABLE_PROVIDER": "AWAITING_CABLE_PROVIDER",
    "AWAITING_CABLE_CARD": "AWAITING_CABLE_CARD",
    "AWAITING_CABLE_PLAN": "AWAITING_CABLE_PLAN",
    # Electricity Flow
    "AWAITING_ELECTRICITY_DISCO": "AWAITING_ELECTRICITY_DISCO",
    "AWAITING_ELECTRICITY_METER_TYPE": "AWAITING_ELECTRICITY_METER_TYPE",
    "AWAITING_ELECTRICITY_METER": "AWAITING_ELECTRICITY_METER",
    "AWAITING_ELECTRICITY_AMOUNT": "AWAITING_ELECTRICITY_AMOUNT",
    # Betting Flow
    "AWAITING_BETTING_PLATFORM": "AWAITING_BETTING_PLATFORM",
    "AWAITING_BETTING_ACCOUNT": "AWAITING_BETTING_ACCOUNT",
    "AWAITING_BETTING_AMOUNT": "AWAITING_BETTING_AMOUNT",
    # Education Flow
    "AWAITING_EDUCATION_PACKAGE": "AWAITING_EDUCATION_PACKAGE",
    "AWAITING_EDUCATION_QUANTITY": "AWAITING_EDUCATION_QUANTITY",
    # Wallet funding flow
    "AWAITING_TOPUP_AMOUNT": "AWAITING_TOPUP_AMOUNT",
    "AWAITING_TOPUP_EMAIL": "AWAITING_TOPUP_EMAIL",
}


# --- HELPER UTILITIES ---
def normalize_phone_number(phone_number):
    if phone_number is None:
        return ""
    normalized = str(phone_number).strip().replace(" ", "")
    normalized = normalized.split("@", 1)[0].replace("+", "")
    if normalized.startswith("00"):
        normalized = normalized[2:]
    if normalized.startswith("234"):
        return normalized
    if normalized.startswith("0") and len(normalized) == 11:
        return "234" + normalized[1:]
    return normalized


def merge_user_accounts(primary_user, secondary_user):
    if primary_user is None or secondary_user is None or primary_user.id == secondary_user.id:
        return primary_user or secondary_user

    if primary_user.wallet_balance is None:
        primary_user.wallet_balance = Decimal("0.00")
    if secondary_user.wallet_balance is None:
        secondary_user.wallet_balance = Decimal("0.00")

    primary_user.wallet_balance += secondary_user.wallet_balance
    if not primary_user.email and secondary_user.email:
        primary_user.email = secondary_user.email
    if not primary_user.password_hash and secondary_user.password_hash:
        primary_user.password_hash = secondary_user.password_hash
    if not primary_user.name and secondary_user.name:
        primary_user.name = secondary_user.name

    if not primary_user.phone:
        primary_user.phone = secondary_user.phone
    preferred_phone = normalize_phone_number(primary_user.phone) or normalize_phone_number(secondary_user.phone)
    if preferred_phone:
        primary_user.phone = preferred_phone

    Transaction.query.filter_by(user_id=secondary_user.id).update({"user_id": primary_user.id})
    db.session.delete(secondary_user)
    db.session.flush()

    if not primary_user.whatsapp_id or str(primary_user.whatsapp_id).startswith("web_"):
        primary_user.whatsapp_id = normalize_phone_number(secondary_user.whatsapp_id) or normalize_phone_number(primary_user.phone) or primary_user.whatsapp_id
    else:
        primary_user.whatsapp_id = normalize_phone_number(primary_user.whatsapp_id) or primary_user.whatsapp_id

    db.session.commit()
    return primary_user


INJECTION_PATTERNS = (
    "<script",
    "</script",
    "<iframe",
    "<object",
    "<embed",
    "<svg",
    "<img",
    "javascript:",
    "vbscript:",
    "data:text/html",
    "onerror=",
    "onload=",
    "srcdoc",
    "alert(",
    "document.cookie",
    "eval(",
    "expression(",
)


def contains_injection_pattern(value):
    if value is None:
        return False
    text = str(value)
    if len(text) > 2048:
        return True
    if "\x00" in text:
        return True
    if any(ord(ch) < 32 and ch not in "\r\n\t" for ch in text):
        return True
    lowered = text.lower()
    return any(pattern in lowered for pattern in INJECTION_PATTERNS)


def validate_request_tree(value, path="request"):
    if isinstance(value, dict):
        for key, item in value.items():
            validate_request_tree(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            validate_request_tree(item, f"{path}[{index}]")
        return
    if isinstance(value, str) and contains_injection_pattern(value):
        raise ValueError(f"Suspicious input detected at {path}")


@app.before_request
def reject_malicious_payloads():
    if request.method == "OPTIONS":
        return None

    try:
        if request.args:
            validate_request_tree(request.args.to_dict(flat=False), "query")
        if request.form:
            validate_request_tree(request.form.to_dict(flat=False), "form")
        if request.is_json:
            payload = request.get_json(silent=True)
            if payload is not None:
                validate_request_tree(payload, "json")
    except ValueError as exc:
        return jsonify({"status": "rejected", "reason": str(exc)}), 400

    return None


@app.before_request
def enforce_https_redirect():
    if app.testing:
        return None
    proto = request.headers.get("X-Forwarded-Proto", "")
    if proto.lower() == "http" and request.url.startswith("http://"):
        redirect_url = request.url.replace("http://", "https://", 1)
        return redirect(redirect_url, code=301)
    return None


@app.after_request
def add_security_headers(response):
    is_form_popup = request.path.startswith("/support/form")
    response.headers['X-Frame-Options'] = 'SAMEORIGIN' if is_form_popup else 'DENY'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    response.headers['Permissions-Policy'] = 'geolocation=(), camera=(), microphone=()'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    frame_ancestor = "'self'" if is_form_popup else "'none'"
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        "object-src 'none'; "
        "base-uri 'self'; "
        f"frame-ancestors {frame_ancestor}; "
        "form-action 'self' https://wajvtu.com.ng https://checkout.paystack.com; "
        "upgrade-insecure-requests"
    )
    return response


def generate_whatsapp_link_token(user):
    """Create a signed, time-limited link token for binding a WhatsApp number to a user account."""
    if user is None or getattr(user, "id", None) is None:
        return ""

    user_id = int(user.id)
    expiry = int((datetime.now(timezone.utc) + timedelta(minutes=30)).timestamp())
    nonce = secrets.token_urlsafe(18)
    payload = f"{user_id}:{expiry}:{nonce}"
    signature = hmac.new(
        app.config["SECRET_KEY"].encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}:{signature}"


def claim_whatsapp_link_token(phone_number, token):
    """Verify a link token and attach the supplied WhatsApp number to the user account."""
    normalized_phone = normalize_phone_number(phone_number)
    if not normalized_phone or not token:
        return False

    try:
        user_id_raw, expiry_raw, nonce, signature = str(token).strip().split(":", 3)
        user_id = int(user_id_raw)
        expiry = int(expiry_raw)
    except (TypeError, ValueError):
        return False

    now = int(datetime.now(timezone.utc).timestamp())
    if expiry < now:
        return False

    payload = f"{user_id}:{expiry}:{nonce}"
    expected_signature = hmac.new(
        app.config["SECRET_KEY"].encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, signature):
        return False

    user = User.query.get(user_id)
    if user is None:
        return False

    conflicting_user = User.query.filter_by(whatsapp_id=normalized_phone).first()
    if conflicting_user and conflicting_user.id != user.id:
        user = merge_user_accounts(user, conflicting_user)

    user.phone = normalize_phone_number(user.phone) or normalized_phone
    user.whatsapp_id = normalized_phone
    db.session.commit()
    return True


def get_or_create_user(phone_number):
    normalized_phone = normalize_phone_number(phone_number)
    if not normalized_phone:
        return None

    user = (
        User.query.filter_by(whatsapp_id=normalized_phone).first()
        or User.query.filter_by(phone=normalized_phone).first()
        or User.query.filter(User.whatsapp_id == str(phone_number).split("@", 1)[0]).first()
    )

    if not user:
        if not ALLOW_DB_MUTATIONS:
            return None
        user = User(phone=normalized_phone, whatsapp_id=normalized_phone, wallet_balance=Decimal("0.00"))
        db.session.add(user)
        db.session.commit()
    else:
        if user.phone != normalized_phone:
            user.phone = normalized_phone
        if user.whatsapp_id != normalized_phone:
            user.whatsapp_id = normalized_phone
        db.session.commit()

    duplicate_matches = User.query.filter(User.id != user.id).filter(
        (User.phone == normalized_phone) | (User.whatsapp_id == normalized_phone) | (User.whatsapp_id == str(phone_number).split("@", 1)[0])
    ).all()
    for duplicate in duplicate_matches:
        user = merge_user_accounts(user, duplicate)

    return user


def get_markup(service_type, base_amount=Decimal("0.00")):
    from decimal import ROUND_HALF_UP
    markup = ServiceMarkup.query.filter_by(service_type=service_type.upper()).first()
    percentage = Decimal(str(markup.markup_amount)) if markup else Decimal("0.00")
    raw_markup = base_amount * percentage / Decimal("100")
    total_amount = base_amount + raw_markup
    rounded_total = total_amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    return (rounded_total - base_amount).quantize(Decimal("0.01"))


def set_user_session(user, state, data):
    user.current_state = state
    user.state_data = json.loads(json.dumps(data))
    db.session.commit()


def get_user_session_data(user):
    try:
        data = user.state_data or {}
        return json.loads(json.dumps(data)) if isinstance(data, dict) else json.loads(data)
    except Exception:
        return {}


def settle_transaction(user, result, amount, transaction_type, recipient, description, meta_data=None):
    """Persist a successful provider result or refund the reserved wallet amount."""
    amount = Decimal(str(amount))
    provider = result.get("provider") or (result.get("data") or {}).get("provider") or "unknown"
    provider_reference = result.get("provider_reference") or (result.get("data") or {}).get("provider_reference")
    if result.get("status") == "SUCCESS":
        tx = Transaction(
            user_id=user.id,
            reference=result["reference"],
            amount=amount,
            type=transaction_type,
            recipient=recipient,
            provider_name=str(provider).lower()[:30],
            provider_reference=(provider_reference or result.get("reference") or "")[:100],
            status="SUCCESS",
            description=description,
            meta_data={**result.get("data", {}), **(meta_data or {})},
        )
        db.session.add(tx)
        db.session.commit()
        try:
            from services.referral_service import process_referral_reward_for_transaction
            process_referral_reward_for_transaction(tx)
        except Exception as ref_err:
            app.logger.error(f"Error checking referral reward for tx {tx.reference}: {ref_err}")
        return True

    user.wallet_balance += amount
    db.session.commit()
    return False


def reconcile_deposit_transaction(transaction):
    """Credit a successful deposit exactly once and mark it as reconciled."""
    if transaction is None or transaction.type != "DEPOSIT" or transaction.status != "SUCCESS":
        return False

    user = transaction.user
    if user is None:
        return False

    meta = transaction.meta_data or {}
    if meta.get("credited") is True:
        return False

    amount = Decimal(str(transaction.amount or "0.00")).quantize(Decimal("0.01"))
    user.wallet_balance += amount
    meta["credited"] = True
    meta["credited_at"] = datetime.now(timezone.utc).isoformat()
    transaction.meta_data = meta
    db.session.commit()
    return True


def reconcile_successful_deposit(user, tx, net_credit, paid_gross, data):
    """Apply the wallet credit once and mark the transaction as credited."""
    meta = dict(tx.meta_data or {})
    if meta.get("credited") is True:
        return False

    user.wallet_balance += Decimal(str(net_credit))
    meta.update({
        "gross_amount": str(paid_gross),
        "net_amount": str(net_credit),
        "paystack": data,
        "credited": True,
        "credited_at": datetime.now(timezone.utc).isoformat(),
    })
    tx.amount = Decimal(str(net_credit)).quantize(Decimal("0.01"))
    tx.type = "DEPOSIT"
    tx.recipient = user.phone
    tx.status = "SUCCESS"
    tx.description = f"Paystack deposit; gross paid NGN {paid_gross:,.2f}"
    tx.meta_data = meta
    db.session.commit()
    return True


def verify_paystack_signature(raw_body, signature):
    expected = hmac.new(
        PAYSTACK_SECRET_KEY.encode("utf-8"), raw_body, hashlib.sha512
    ).hexdigest()
    return bool(signature) and hmac.compare_digest(expected, signature)


def record_admin_audit(username, action, success, reason=None):
    username = (username or "unknown").strip()[:100]
    reason = (reason or ("Successful admin action" if success else "Admin action failed")).strip()[:255]
    ip_address = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
    ip_address = ip_address.split(",", 1)[0].strip() or "unknown"
    user_agent = (request.user_agent.string or "unknown")[:255]
    entry = AdminAuditLog(
        username=username,
        action=action,
        success=bool(success),
        reason=reason,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    db.session.add(entry)
    db.session.commit()


def require_admin_auth():
    website_user_id = session.get("user_id")
    if not website_user_id:
        return redirect(url_for("auth.login", next=request.path))

    website_user = db.session.get(User, website_user_id)
    if website_user and ADMIN_EMAIL and (website_user.email or "").strip().lower() == ADMIN_EMAIL:
        record_admin_audit(website_user.email, "login", True, "Successful website admin login")
        return None

    record_admin_audit(
        website_user.email if website_user else "unknown",
        "login",
        False,
        "Website user is not an administrator",
    )
    return jsonify({"status": "error", "reason": "Administrator access required"}), 403


def get_csrf_token():
    token = session.get("admin_csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        session["admin_csrf_token"] = token
    return token


def normalize_datetime(value):
    """Return a timezone-aware UTC datetime for comparisons and display."""
    if value is None:
        return None
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return None
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    try:
        return value.astimezone(timezone.utc)
    except Exception:
        return value


def format_admin_datetime(value):
    """Return a compact, human-readable timestamp suitable for admin tables."""
    value = normalize_datetime(value)
    if value is None:
        return "—"
    return value.strftime("%Y-%m-%d %H:%M:%S UTC")


def validate_csrf_token():
    submitted_token = request.form.get("csrf_token", "")
    expected_token = session.get("admin_csrf_token", "")
    if not expected_token or not submitted_token or not hmac.compare_digest(submitted_token, expected_token):
        record_admin_audit(ADMIN_USERNAME or "unknown", "csrf_failure", False, "Invalid admin CSRF token")
        abort(403)


def verify_paystack_transaction(reference):
    """Fetch the authoritative checkout result for browser callbacks."""
    try:
        response = requests.get(
            f"https://api.paystack.co/transaction/verify/{quote(reference, safe='')}",
            headers={"Authorization": f"Bearer {PAYSTACK_SECRET_KEY}"},
            timeout=15,
        )
        data = response.json()
        if response.status_code == 200 and data.get("status") and data.get("data", {}).get("status") == "success":
            return data["data"]
        app.logger.warning("Paystack verification failed for reference=%s: %s", reference, data.get("message"))
    except (requests.RequestException, ValueError) as exc:
        app.logger.error("Paystack verification error for reference=%s: %s", reference, exc)
    return None


@app.route("/payments/initialize", methods=["POST"])
def initialize_payment():
    payload = request.get_json() or {}
    phone = str(payload.get("phone", "")).strip()
    email = str(payload.get("email", "")).strip()
    try:
        amount = Decimal(str(payload.get("amount", "0")))
    except Exception:
        amount = Decimal("0")

    if not phone or not email or amount <= 0:
        return jsonify({"status": "FAILED", "reason": "phone, email, and a positive amount are required"}), 400

    user = get_or_create_user(phone)
    result = generate_payment_link(email, amount, phone, pass_fee_to_user=True)
    if result.get("status") != "SUCCESS":
        return jsonify(result), 502

    return jsonify(result), 200


@app.route("/payments/paystack/callback", methods=["GET"])
def paystack_callback():
    reference = request.args.get("reference") or request.args.get("trxref")
    if reference:
        verified_data = verify_paystack_transaction(reference)
        if verified_data:
            _handle_checkout_link_charge(verified_data)
        transaction = Transaction.query.filter_by(reference=reference).first()
        if transaction and transaction.status == "SUCCESS":
            payment_data = (transaction.meta_data or {}).get("paystack") or {}
            payment_source = (payment_data.get("metadata") or {}).get("payment_source")
            if payment_source == "website":
                return redirect(url_for("web.dashboard"), code=302)
            user_phone = normalize_phone_number((transaction.recipient or "").strip() or (transaction.user.phone if transaction.user else ""))
            payment_meta = dict(transaction.meta_data or {})
            if user_phone and not payment_meta.get("whatsapp_return_notified"):
                wallet_message = f"✅ Wallet funding successful. NGN {transaction.amount:,.2f} has been added to your WAJ VTU wallet."
                send_whatsapp_message(user_phone, wallet_message)
                payment_meta["whatsapp_return_notified"] = True
                transaction.meta_data = payment_meta
                db.session.commit()
            return_message = quote("Wallet funded successfully. You can continue your purchase here.")
            bot_phone = normalize_phone_number(WHATSAPP_BOT_PHONE)
            whatsapp_link = f"https://wa.me/{bot_phone}?text={return_message}" if bot_phone else "https://wa.me/"
            return redirect(whatsapp_link, code=302)
        return render_template_string("<h2>Payment is being processed</h2><p>Your transaction is still pending confirmation.</p><a href=\"/\">Refresh</a>")
    return render_template_string("<h2>Payment status unavailable</h2><p>The Paystack callback did not include a valid reference.</p>")


@app.route("/payments/paystack/webhook", methods=["POST"])
def paystack_webhook():
    raw_body = request.get_data()
    signature = request.headers.get("x-paystack-signature", "")
    if not verify_paystack_signature(raw_body, signature):
        return jsonify({"status": "error", "reason": "Invalid signature"}), 401

    event = request.get_json(silent=True) or {}
    if event.get("event") != "charge.success":
        return jsonify({"status": "ignored"}), 200

    data = event.get("data") or {}

    return _handle_checkout_link_charge(data)


def _handle_checkout_link_charge(data):
    reference = str(data.get("reference", "")).strip()
    metadata = data.get("metadata") or {}
    phone = str(metadata.get("phone_number", "")).strip()
    try:
        paid_gross = (Decimal(str(data.get("amount", 0))) / Decimal("100")).quantize(Decimal("0.01"))
        net_credit = Decimal(str(metadata.get("net_credit_amount", paid_gross))).quantize(Decimal("0.01"))
    except Exception:
        return jsonify({"status": "error", "reason": "Invalid payment amount"}), 400

    if not reference or not phone or paid_gross <= 0 or net_credit <= 0 or net_credit > paid_gross:
        return jsonify({"status": "error", "reason": "Invalid payment payload"}), 400

    normalized_phone = normalize_phone_number(phone)
    if not normalized_phone:
        return jsonify({"status": "error", "reason": "Missing or invalid phone number in Paystack metadata"}), 400

    user = User.query.filter_by(whatsapp_id=normalized_phone).first() or User.query.filter_by(phone=normalized_phone).first()
    if not user:
        user = User.query.filter_by(whatsapp_id=phone).first() or User.query.filter_by(phone=phone).first()
    if not user:
        if not ALLOW_DB_MUTATIONS:
            return jsonify({"status": "error", "reason": "User creation disabled"}), 400
        user = User(phone=normalized_phone, whatsapp_id=normalized_phone, wallet_balance=Decimal("0.00"))
        db.session.add(user)
        db.session.commit()
    else:
        if user.whatsapp_id != normalized_phone:
            user.whatsapp_id = normalized_phone
        if user.phone != normalized_phone:
            user.phone = normalized_phone
        db.session.commit()

    tx = Transaction.query.filter_by(reference=reference).first()
    if tx is None:
        tx = Transaction(
            user=user,
            reference=reference,
            amount=net_credit,
            type="DEPOSIT",
            recipient=phone,
            status="SUCCESS",
            description=f"Paystack deposit; gross paid NGN {paid_gross:,.2f}",
            meta_data={},
        )
        db.session.add(tx)
    else:
        if tx.status == "SUCCESS":
            if not (tx.meta_data or {}).get("credited"):
                reconcile_successful_deposit(user, tx, net_credit, paid_gross, data)
            return jsonify({"status": "ok", "duplicate": True, "credited_amount": str(net_credit)}), 200
        tx.amount = net_credit
        tx.type = "DEPOSIT"
        tx.recipient = phone
        tx.status = "SUCCESS"
        tx.description = f"Paystack deposit; gross paid NGN {paid_gross:,.2f}"

    reconcile_successful_deposit(user, tx, net_credit, paid_gross, data)
    return jsonify({"status": "ok", "credited_amount": str(net_credit)}), 200


def _format_meta_recipient(recipient):
    """Normalize Nigerian local numbers and bridge JIDs to an E.164 recipient."""
    value = str(recipient or "").split("@", 1)[0].strip()
    digits = "".join(character for character in value if character in "0123456789")
    if len(digits) == 11 and digits.startswith("0"):
        digits = "234" + digits[1:]
    elif len(digits) == 10 and digits[0] in "789":
        digits = "234" + digits
    if not 8 <= len(digits) <= 15 or digits.startswith("0"):
        return ""
    return f"+{digits}"


def send_whatsapp_message(recipient, text):
    """
    Sends outgoing WhatsApp messages through the official Meta Cloud API when configured,
    otherwise falls back to the local bridge service.
    """
    try:
        meta_api_token = os.getenv("META_API_TOKEN", META_API_TOKEN).strip()
        meta_phone_number_id = os.getenv("META_PHONE_NUMBER_ID", META_PHONE_NUMBER_ID).strip()
        meta_api_version = os.getenv("META_API_VERSION", META_API_VERSION).strip()

        if meta_api_token and meta_phone_number_id:
            meta_url = f"https://graph.facebook.com/{meta_api_version}/{meta_phone_number_id}/messages"
            meta_recipient = _format_meta_recipient(recipient)
            if not meta_recipient:
                print("Meta API message not sent: recipient is not a valid phone number")
                return False
            payload = {
                "messaging_product": "whatsapp",
                "to": meta_recipient,
                "type": "text",
                "text": {"body": text}
            }
            headers = {
                "Authorization": f"Bearer {meta_api_token}",
                "Content-Type": "application/json",
            }
            response = requests.post(meta_url, json=payload, headers=headers, timeout=20)
            if not response.ok:
                print(f"Meta API HTTP Error ({response.status_code}): {response.text}")
            return response.ok

        payload = {
            "chatId": recipient,
            "text": text
        }
        headers = {}
        if BRIDGE_API_TOKEN:
            headers["Authorization"] = f"Bearer {BRIDGE_API_TOKEN}"

        response = requests.post(BRIDGE_URL, json=payload, headers=headers, timeout=10)

        if not response.ok:
            print(f"Bridge HTTP Error ({response.status_code}): {response.text}")

        return response.ok
    except Exception as e:
        print(f"Failed to deliver WhatsApp message: {e}")
        return False


def send_whatsapp_interactive_url(recipient, body_text, button_title, url, header_text="WAJ VTU Support"):
    """
    Sends a WhatsApp message with an interactive CTA URL button (which triggers
    the native in-app browser popup inside WhatsApp), falling back to a formatted link.
    """
    try:
        meta_api_token = os.getenv("META_API_TOKEN", META_API_TOKEN).strip()
        meta_phone_number_id = os.getenv("META_PHONE_NUMBER_ID", META_PHONE_NUMBER_ID).strip()
        meta_api_version = os.getenv("META_API_VERSION", META_API_VERSION).strip()

        if meta_api_token and meta_phone_number_id:
            meta_url = f"https://graph.facebook.com/{meta_api_version}/{meta_phone_number_id}/messages"
            meta_recipient = _format_meta_recipient(recipient)
            if not meta_recipient:
                print("Meta CTA message not sent: recipient is not a valid phone number")
                return False
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": meta_recipient,
                "type": "interactive",
                "interactive": {
                    "type": "cta_url",
                    "header": {"type": "text", "text": header_text},
                    "body": {"text": body_text},
                    "action": {
                        "name": "cta_url",
                        "parameters": {
                            "display_text": button_title[:20],
                            "url": url
                        }
                    }
                }
            }
            headers = {
                "Authorization": f"Bearer {meta_api_token}",
                "Content-Type": "application/json",
            }
            response = requests.post(meta_url, json=payload, headers=headers, timeout=20)
            if response.ok:
                return True
            print(f"Meta CTA API HTTP Error ({response.status_code}): {response.text}")

        fallback_text = (
            f"*{header_text}*\n\n"
            f"{body_text}\n\n"
            f"👉 {url}\n\n"
            f"_(Tap the link to open the Support Form popup inside WhatsApp)_"
        )
        return send_whatsapp_message(recipient, fallback_text)
    except Exception as e:
        print(f"Failed to send interactive WhatsApp message: {e}")
        fallback_text = f"*{header_text}*\n\n{body_text}\n\n👉 {url}"
        return send_whatsapp_message(recipient, fallback_text)


def generate_receipt_png(transaction):
    """Render a successful transaction as a PNG image."""
    image = Image.new("RGB", (900, 650), "#f7f4ec")
    draw = ImageDraw.Draw(image)
    title_font = ImageFont.load_default(size=42)
    heading_font = ImageFont.load_default(size=28)
    body_font = ImageFont.load_default(size=24)
    small_font = ImageFont.load_default(size=20)
    draw.rectangle((40, 40, 860, 610), fill="#ffffff", outline="#d8c9a5", width=3)
    draw.text((80, 80), "WAJ VTU", fill="#1c2148", font=title_font)
    draw.text((80, 145), "PAYMENT RECEIPT", fill="#b7791f", font=heading_font)
    draw.line((80, 195, 820, 195), fill="#d8c9a5", width=2)

    rows = [
        ("Service", transaction.type),
        ("Amount", f"NGN {Decimal(str(transaction.amount)):,.2f}"),
        ("Recipient", transaction.recipient or "-"),
        ("Reference", transaction.reference),
        ("Status", transaction.status),
        ("Date", transaction.created_at.strftime("%Y-%m-%d %H:%M UTC")),
    ]
    y_position = 235
    for label, value in rows:
        draw.text((80, y_position), label, fill="#687080", font=body_font)
        draw.text((350, y_position), str(value)[:34], fill="#1c2148", font=body_font)
        y_position += 52
    draw.text((80, 565), "Thank you for using WAJ VTU", fill="#687080", font=small_font)

    image_buffer = BytesIO()
    image.save(image_buffer, format="PNG")
    image_buffer.seek(0)
    return image_buffer


def send_whatsapp_receipt(recipient, reference):
    """Generate and send a PNG receipt for a completed transaction."""
    meta_api_token = os.getenv("META_API_TOKEN", META_API_TOKEN).strip()
    meta_phone_number_id = os.getenv("META_PHONE_NUMBER_ID", META_PHONE_NUMBER_ID).strip()
    if not meta_api_token or not meta_phone_number_id:
        return False

    transaction = Transaction.query.filter_by(reference=reference, status="SUCCESS").first()
    if transaction is None:
        return False
    meta_recipient = _format_meta_recipient(recipient)
    if not meta_recipient:
        print("WhatsApp receipt not sent: recipient is not a valid phone number")
        return False
    image_buffer = generate_receipt_png(transaction)
    api_version = os.getenv("META_API_VERSION", META_API_VERSION).strip()
    base_url = f"https://graph.facebook.com/{api_version}/{meta_phone_number_id}"
    headers = {"Authorization": f"Bearer {meta_api_token}"}
    try:
        upload = requests.post(
            f"{base_url}/media",
            files={"file": ("waj-vtu-receipt.png", image_buffer, "image/png")},
            data={"messaging_product": "whatsapp", "type": "image/png"},
            headers=headers,
            timeout=20,
        )
        upload_data = upload.json()
        media_id = upload_data.get("id")
        if not upload.ok or not media_id:
            print(f"WhatsApp receipt upload failed: {upload.text}")
            return False

        response = requests.post(
            f"{base_url}/messages",
            json={
                "messaging_product": "whatsapp",
                "to": meta_recipient,
                "type": "image",
                "image": {"id": media_id, "caption": f"Receipt: {transaction.reference}"},
            },
            headers={**headers, "Content-Type": "application/json"},
            timeout=20,
        )
        if not response.ok:
            print(f"WhatsApp receipt send failed: {response.text}")
        return response.ok
    except (requests.RequestException, ValueError) as exc:
        print(f"WhatsApp receipt error: {exc}")
        return False


def categorize_data_plans(plans):
    categorized = {
        "DAILY": [],
        "TWO_DAYS": [],
        "WEEKLY": [],
        "MONTHLY": [],
        "AWOOF": [],
        "OTHERS": []
    }

    for plan in plans:
        name = plan.get("name", "").upper()

        if any(k in name for k in ["AWOOF", "PROMO", "NIGHT", "SOCIAL", "INSTAGRAM", "TIKTOK", "YOUTUBE"]):
            categorized["AWOOF"].append(plan)
        elif any(k in name for k in ["2 DAYS", "2DAY", "48HRS", "48 HRS", "2 DAYS VALIDITY"]):
            categorized["TWO_DAYS"].append(plan)
        elif any(k in name for k in ["1 DAY", "1DAY", "DAILY", "24HRS", "24 HRS", "1DAY VALIDITY", "DAY"]):
            categorized["DAILY"].append(plan)
        elif any(k in name for k in ["7 DAYS", "7DAYS", "WEEKLY", "14 DAYS", "14DAYS"]):
            categorized["WEEKLY"].append(plan)
        elif any(k in name for k in ["30 DAYS", "30DAYS", "MONTHLY", "SME", "CORPORATE", "CG"]):
            categorized["MONTHLY"].append(plan)
        else:
            categorized["OTHERS"].append(plan)

    return categorized


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint for Render monitoring."""
    return jsonify({
        "status": "online",
        "service": "WhatsApp VTU Platform",
        "build": "clubkonnect",
    }), 200


@app.route("/webhook", methods=["GET"])
def meta_webhook_verification():
    """Handle Facebook/Meta webhook verification requests."""
    mode = request.args.get("hub.mode")
    token = request.args.get("hub.verify_token")
    challenge = request.args.get("hub.challenge")

    if mode == "subscribe" and token == META_VERIFY_TOKEN and challenge:
        return challenge, 200

    return jsonify({"status": "error", "reason": "Forbidden"}), 403


# --- MAIN WEBHOOK ENDPOINT ---
@app.route("/webhook", methods=["POST"])
def whatsapp_webhook():
    auth_header = request.headers.get("Authorization")
    if auth_header and BRIDGE_API_TOKEN and auth_header != f"Bearer {BRIDGE_API_TOKEN}":
        return jsonify({"status": "error", "reason": "Unauthorized"}), 401

    req_data = request.get_json() or {}

    if not isinstance(req_data, dict):
        return jsonify({"status": "ignored"}), 200

    has_message = False
    sender = None
    message_type = None
    has_status_event = False

    for entry in req_data.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            if "statuses" in value:
                has_status_event = True
                continue
            messages = value.get("messages") or []
            if not messages:
                continue
            has_message = True
            first_message = messages[0]
            sender = first_message.get("from") or first_message.get("sender")
            message_type = first_message.get("type")
            if sender:
                break
        if sender:
            break

    if not has_message:
        return jsonify({"status": "ignored"}), 200

    if message_type != "text":
        threading.Thread(
            target=send_whatsapp_message,
            args=(sender, "I can only process text messages. Please type your request."),
        ).start()
        return jsonify({"status": "ignored", "reason": "text_only"}), 200

    if not ALLOW_DB_MUTATIONS and get_or_create_user(sender) is None:
        return jsonify({"status": "ignored"}), 200

    # Spawn background thread for processing to avoid Meta timeout
    thread = threading.Thread(target=process_webhook_payload, args=(req_data,))
    thread.start()

    return jsonify({"status": "success"}), 200

def process_webhook_payload(req_data):
    with app.app_context():
        message_id = req_data.get("message_id") or req_data.get("id")
        if "entry" in req_data:
            for entry in req_data.get("entry", []):
                for change in entry.get("changes", []):
                    value = change.get("value", {})
                    messages = value.get("messages") or []
                    if not messages:
                        continue

                    first_message = messages[0]
                    m_type = first_message.get("type")
                    if m_type != "text":
                        return
                    message_id = first_message.get("id") or message_id
                    sender = first_message.get("from") or first_message.get("sender")
                    text = first_message.get("text", {}).get("body", "")
                    if sender:
                        req_data = {
                            **req_data,
                            "sender": sender,
                            "message": text,
                            "body": text,
                            "from": sender,
                            "text": text,
                        }
                        break
                if "sender" in req_data:
                    break

        chat_id = req_data.get("sender") or req_data.get("from") or req_data.get("phone")
        text = str(req_data.get("message") or req_data.get("text") or req_data.get("body") or "").strip()

        if not chat_id:
            # Check if this is just a status update (read/delivered/sent) before logging
            is_status = False
            for entry in req_data.get('entry', []):
                for change in entry.get('changes', []):
                    if 'statuses' in change.get('value', {}):
                        is_status = True
            
            if not is_status:
                print(f"[webhook] No sender extracted from payload: {req_data}", flush=True)
            return

        if message_id:
            existing_message = InboundMessage.query.filter_by(
                provider="whatsapp", message_id=str(message_id)
            ).first()
            if existing_message:
                return
            try:
                db.session.add(InboundMessage(
                    provider="whatsapp",
                    message_id=str(message_id),
                    sender=str(chat_id),
                ))
                db.session.commit()
            except Exception:
                db.session.rollback()
                if InboundMessage.query.filter_by(provider="whatsapp", message_id=str(message_id)).first():
                    return
                raise

        provider_phone = str(chat_id).split("@", 1)[0]

        user = get_or_create_user(chat_id)
        if user is None:
            return

        # Let the AI Chat Agent handle everything in a background thread!
        # This is CRITICAL so the webhook instantly returns 200 OK to WhatsApp
        import threading
        
        def run_agent_in_background(user_id, message_text, chat_id_str, provider_phone_str):
            with app.app_context():
                user_obj = User.query.get(user_id)
                if user_obj:
                    import chat_agent
                    chat_agent.handle_chat_message(app, db, user_obj, message_text, chat_id_str, provider_phone_str)
                    
        threading.Thread(target=run_agent_in_background, args=(user.id, text, chat_id, provider_phone)).start()

ADMIN_BASE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>WAJ VTU Admin Control Center</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Space+Grotesk:wght@500;600;700&family=Inter:wght@400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --admin-ink: #09090b;
            --admin-dark-bg: #111113;
            --admin-sidebar-border: #222226;
            --admin-amber: #ffc400;
            --admin-amber-hover: #e5b000;
            --admin-bg: #f8fafc;
            --admin-card: #ffffff;
            --admin-muted: #64748b;
            --admin-border: #e2e8f0;
            --admin-border-subtle: #f1f5f9;
            --admin-success: #16a34a;
            --admin-success-bg: #dcfce7;
            --admin-alert: #dc2626;
            --admin-alert-bg: #fee2e2;
            --admin-pending: #d97706;
            --admin-pending-bg: #fef3c7;
            --radius-sm: 8px;
            --radius-md: 12px;
            --radius-lg: 16px;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif; }
        html, body { width: 100%; min-width: 0; height: 100%; }
        body { background-color: var(--admin-bg); color: var(--admin-ink); -webkit-font-smoothing: antialiased; }

        a { color: inherit; text-decoration: none; }

        /* Admin App Shell */
        .admin-shell {
            display: flex;
            min-height: 100vh;
            width: 100%;
        }

        /* Fixed Sidebar */
        .admin-sidebar {
            width: 260px;
            background-color: var(--admin-ink);
            border-right: 1px solid var(--admin-sidebar-border);
            position: fixed;
            top: 0;
            bottom: 0;
            left: 0;
            z-index: 100;
            display: flex;
            flex-direction: column;
            overflow-y: auto;
            scrollbar-width: thin;
            scrollbar-color: #27272a transparent;
        }

        .sidebar-brand-box {
            padding: 22px 20px 18px;
            border-bottom: 1px solid var(--admin-sidebar-border);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .sidebar-brand {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 18px;
            font-weight: 700;
            color: #ffffff;
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .sidebar-brand-icon {
            width: 30px;
            height: 30px;
            border-radius: 8px;
            background: var(--admin-amber);
            color: var(--admin-ink);
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 15px;
            font-weight: 800;
        }

        .sidebar-brand span { color: var(--admin-amber); }

        .sidebar-brand-tag {
            font-size: 10px;
            font-weight: 700;
            padding: 2px 7px;
            border-radius: 999px;
            background: rgba(255, 196, 0, 0.15);
            color: var(--admin-amber);
            border: 1px solid rgba(255, 196, 0, 0.3);
            text-transform: uppercase;
        }

        .sidebar-menu {
            padding: 16px 12px;
            flex: 1;
            display: flex;
            flex-direction: column;
            gap: 18px;
        }

        .sidebar-group {
            display: flex;
            flex-direction: column;
            gap: 2px;
        }

        .sidebar-group-title {
            font-size: 10.5px;
            font-weight: 700;
            letter-spacing: 0.08em;
            color: #71717a;
            padding: 4px 12px 6px;
            text-transform: uppercase;
        }

        .sidebar-link {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 12px;
            border-radius: var(--radius-sm);
            font-size: 13px;
            font-weight: 500;
            color: #a1a1aa;
            transition: all 0.15s ease;
        }

        .sidebar-link:hover {
            color: #ffffff;
            background: rgba(255, 255, 255, 0.06);
        }

        .sidebar-link.active {
            color: #09090b;
            background: var(--admin-amber);
            font-weight: 700;
        }

        .sidebar-link.active .sidebar-icon {
            color: #09090b;
        }

        .sidebar-icon {
            font-size: 15px;
            width: 20px;
            text-align: center;
            flex-shrink: 0;
        }

        .sidebar-footer {
            padding: 14px 16px;
            border-top: 1px solid var(--admin-sidebar-border);
            background: #050506;
            display: flex;
            align-items: center;
            justify-content: space-between;
        }

        .sidebar-user-pill {
            display: flex;
            align-items: center;
            gap: 8px;
            min-width: 0;
        }

        .sidebar-user-avatar {
            width: 28px;
            height: 28px;
            border-radius: 50%;
            background: #27272a;
            color: var(--admin-amber);
            font-size: 12px;
            font-weight: 700;
            display: flex;
            align-items: center;
            justify-content: center;
            flex-shrink: 0;
        }

        .sidebar-user-name {
            font-size: 12px;
            color: #d4d4d8;
            font-weight: 600;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
            max-width: 120px;
        }

        .sidebar-logout-btn {
            font-size: 12px;
            font-weight: 600;
            color: #f87171;
            padding: 4px 8px;
            border-radius: 6px;
            background: rgba(239, 68, 68, 0.12);
            transition: background 0.15s ease;
        }

        .sidebar-logout-btn:hover {
            background: rgba(239, 68, 68, 0.25);
        }

        /* Main Workspace Pane */
        .admin-main-wrap {
            margin-left: 260px;
            flex: 1;
            display: flex;
            flex-direction: column;
            min-height: 100vh;
            background: var(--admin-bg);
            width: calc(100% - 260px);
        }

        /* Top Header Bar */
        .admin-topbar {
            height: 64px;
            background: #ffffff;
            border-bottom: 1px solid var(--admin-border);
            padding: 0 32px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            position: sticky;
            top: 0;
            z-index: 40;
        }

        .admin-topbar-left {
            display: flex;
            align-items: center;
            gap: 14px;
        }

        .admin-mobile-toggle {
            display: none;
            background: transparent;
            border: 1px solid var(--admin-border);
            border-radius: 6px;
            width: 36px;
            height: 36px;
            font-size: 18px;
            color: var(--admin-ink);
            align-items: center;
            justify-content: center;
            cursor: pointer;
        }

        .admin-topbar-title {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 15px;
            font-weight: 700;
            color: var(--admin-ink);
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .admin-status-badge {
            display: inline-flex;
            align-items: center;
            gap: 5px;
            padding: 3px 8px;
            border-radius: 999px;
            background: #ecfdf5;
            color: #059669;
            font-size: 11px;
            font-weight: 600;
            border: 1px solid #a7f3d0;
        }

        .admin-status-dot {
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: #10b981;
        }

        .admin-topbar-right {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .topbar-portal-link {
            font-size: 12px;
            font-weight: 600;
            color: var(--admin-muted);
            background: var(--admin-border-subtle);
            border: 1px solid var(--admin-border);
            padding: 6px 12px;
            border-radius: 6px;
            display: inline-flex;
            align-items: center;
            gap: 5px;
            transition: all 0.15s ease;
        }

        .topbar-portal-link:hover {
            color: var(--admin-ink);
            background: #e2e8f0;
        }

        /* Container & Content Elements */
        .admin-container {
            max-width: 1360px;
            margin: 0 auto;
            padding: 28px 32px 48px;
            width: 100%;
        }

        /* Page Headers */
        .admin-page-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
            margin-bottom: 24px;
        }

        .admin-page-title {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 24px;
            font-weight: 700;
            color: var(--admin-ink);
            letter-spacing: -0.4px;
        }

        .admin-page-desc {
            font-size: 13px;
            color: var(--admin-muted);
            margin-top: 3px;
        }

        .admin-page-actions {
            display: flex;
            gap: 8px;
            flex-wrap: wrap;
        }

        .btn-admin-action {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 8px 14px;
            border-radius: var(--radius-sm);
            font-size: 13px;
            font-weight: 600;
            background: #ffffff;
            color: var(--admin-ink);
            border: 1px solid var(--admin-border);
            cursor: pointer;
            transition: all 0.15s ease;
        }

        .btn-admin-action:hover {
            background: #f1f5f9;
            border-color: #cbd5e1;
        }

        .btn-admin-action.primary {
            background: var(--admin-amber);
            color: var(--admin-ink);
            border-color: var(--admin-amber);
            font-weight: 700;
        }

        .btn-admin-action.primary:hover {
            background: var(--admin-amber-hover);
        }

        /* Metric Grid & Cards */
        .admin-grid-4 {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 16px;
            margin-bottom: 24px;
        }

        .section-grid, .card-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(210px, 1fr));
            gap: 16px;
            margin-bottom: 24px;
        }

        .admin-stat-card, .card {
            background: var(--admin-card);
            border: 1px solid var(--admin-border);
            border-radius: var(--radius-md);
            padding: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.03);
            display: flex;
            flex-direction: column;
            justify-content: space-between;
        }

        .stat-card-kicker, .card h3 {
            font-size: 11px;
            font-weight: 700;
            color: var(--admin-muted);
            text-transform: uppercase;
            letter-spacing: 0.06em;
            margin-bottom: 8px;
        }

        .stat-card-val, .card p {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 26px;
            font-weight: 700;
            color: var(--admin-ink);
            letter-spacing: -0.4px;
        }

        .stat-card-sub, .card small {
            display: block;
            color: #94a3b8;
            font-size: 12px;
            margin-top: 8px;
            line-height: 1.4;
        }

        /* Two-Column Section */
        .admin-two-col {
            display: grid;
            grid-template-columns: 1.4fr 1fr;
            gap: 20px;
            margin-bottom: 24px;
            align-items: start;
        }

        .admin-panel-card {
            background: #ffffff;
            border: 1px solid var(--admin-border);
            border-radius: var(--radius-md);
            box-shadow: 0 1px 3px rgba(0,0,0,0.03);
            overflow: hidden;
        }

        .admin-panel-header {
            padding: 16px 20px;
            border-bottom: 1px solid var(--admin-border);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .admin-panel-title {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 15px;
            font-weight: 700;
            color: var(--admin-ink);
        }

        .admin-panel-link {
            font-size: 12px;
            font-weight: 600;
            color: #2563eb;
        }

        .admin-panel-link:hover { text-decoration: underline; }

        .dashboard-heading {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 17px;
            font-weight: 700;
            color: var(--admin-ink);
            margin: 28px 0 14px;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        /* Tables */
        .table-wrap {
            width: 100%;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
        }

        table {
            width: 100%;
            background: var(--admin-card);
            border-collapse: collapse;
            font-size: 13px;
            text-align: left;
        }

        th {
            background: #f8fafc;
            color: #475569;
            font-weight: 600;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            padding: 12px 16px;
            border-bottom: 1px solid var(--admin-border);
            white-space: nowrap;
        }

        td {
            padding: 12px 16px;
            border-bottom: 1px solid var(--admin-border-subtle);
            color: var(--admin-ink);
            vertical-align: middle;
            font-size: 13px;
        }

        tr:last-child td { border-bottom: none; }
        tr:hover td { background-color: #fafafa; }

        /* Status & Badges */
        .badge-success { color: #16a34a; font-weight: 700; display: inline-block; padding: 2px 8px; background: #dcfce7; border-radius: 999px; font-size: 11px; }
        .badge-failed { color: #dc2626; font-weight: 700; display: inline-block; padding: 2px 8px; background: #fee2e2; border-radius: 999px; font-size: 11px; }
        .badge-pending { color: #d97706; font-weight: 700; display: inline-block; padding: 2px 8px; background: #fef3c7; border-radius: 999px; font-size: 11px; }

        .account-badge { display:inline-block; padding:3px 8px; border-radius:999px; font-size:11px; font-weight:700; }
        .account-badge.website { background:#fef3c7; color:#92400e; }
        .account-badge.whatsapp { background:#dcfce7; color:#166534; }
        .account-badge.linked { background:#dbeafe; color:#1e40af; }

        /* Form elements */
        input, select {
            padding: 9px 12px;
            border-radius: var(--radius-sm);
            border: 1px solid var(--admin-border);
            font-size: 13px;
            background: #ffffff;
            color: var(--admin-ink);
        }

        input:focus, select:focus {
            outline: none;
            border-color: var(--admin-amber);
            box-shadow: 0 0 0 3px rgba(255, 196, 0, 0.25);
        }

        button {
            padding: 9px 14px;
            border-radius: var(--radius-sm);
            border: none;
            background-color: var(--admin-amber);
            color: var(--admin-ink);
            font-weight: 700;
            font-size: 13px;
            cursor: pointer;
            transition: background 0.15s ease;
        }

        button:hover { background-color: var(--admin-amber-hover); }

        /* Clickable Marketing Overview Cards */
        .marketing-action-grid {
            display: grid;
            grid-template-columns: repeat(4, 1fr);
            gap: 16px;
            margin-bottom: 24px;
        }

        .mkt-card-tile {
            background: #ffffff;
            border: 1px solid var(--admin-border);
            border-radius: var(--radius-md);
            padding: 18px 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.03);
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            gap: 12px;
            transition: transform 0.15s ease, border-color 0.15s ease, box-shadow 0.15s ease;
            position: relative;
        }

        .mkt-card-tile:hover {
            transform: translateY(-2px);
            border-color: var(--admin-amber);
            box-shadow: 0 8px 16px rgba(0,0,0,0.06);
        }

        .mkt-tile-top {
            display: flex;
            align-items: center;
            gap: 10px;
        }

        .mkt-tile-icon {
            font-size: 20px;
        }

        .mkt-tile-title {
            font-size: 13px;
            font-weight: 700;
            color: var(--admin-ink);
        }

        .mkt-tile-metric {
            font-family: 'Space Grotesk', sans-serif;
            font-size: 20px;
            font-weight: 700;
            color: var(--admin-ink);
        }

        .mkt-tile-sub {
            font-size: 12px;
            color: var(--admin-muted);
            line-height: 1.4;
        }

        .mkt-tile-arrow {
            font-size: 12px;
            font-weight: 700;
            color: #b45309;
            display: inline-flex;
            align-items: center;
            gap: 4px;
        }

        /* Universal Mobile-Smooth Sub-Navbars, Action Bars, and Filter Bars */
        .admin-subnav-bar, .admin-action-bar, .admin-page-actions, .admin-filter-bar {
            display: flex;
            align-items: center;
            gap: 8px;
            overflow-x: auto;
            -webkit-overflow-scrolling: touch;
            scrollbar-width: none;
            white-space: nowrap;
            padding: 2px 2px 8px;
            margin-bottom: 20px;
            width: 100%;
        }

        .admin-subnav-bar::-webkit-scrollbar,
        .admin-action-bar::-webkit-scrollbar,
        .admin-page-actions::-webkit-scrollbar,
        .admin-filter-bar::-webkit-scrollbar {
            display: none;
        }

        .admin-subnav-item, .admin-action-pill, .btn-admin-action {
            flex-shrink: 0;
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 8px 14px;
            border-radius: 8px;
            font-size: 13px;
            font-weight: 600;
            background: #ffffff;
            color: var(--admin-ink);
            border: 1px solid var(--admin-border);
            text-decoration: none;
            transition: all 0.15s ease;
            white-space: nowrap;
        }

        .admin-subnav-item:hover, .admin-action-pill:hover, .btn-admin-action:hover {
            color: var(--admin-ink);
            border-color: #cbd5e1;
            background: #f8fafc;
        }

        .admin-subnav-item.active, .admin-action-pill.highlighted, .btn-admin-action.primary {
            background: var(--admin-amber);
            color: #09090b;
            border-color: var(--admin-amber);
            font-weight: 700;
            box-shadow: 0 2px 6px rgba(255, 196, 0, 0.25);
        }

        .admin-filter-pill {
            flex-shrink: 0;
            display: inline-flex;
            align-items: center;
            padding: 6px 12px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 600;
            text-decoration: none;
            background: #ffffff;
            color: var(--admin-muted);
            border: 1px solid var(--admin-border);
            transition: all 0.15s ease;
            white-space: nowrap;
        }

        .admin-filter-pill:hover {
            background: #f8fafc;
            color: var(--admin-ink);
        }

        .admin-filter-pill.active {
            background: #09090b;
            color: #ffffff;
            border-color: #09090b;
        }

        .sidebar-close-btn {
            display: none;
            background: rgba(255, 255, 255, 0.12);
            border: none;
            color: #ffffff;
            width: 30px;
            height: 30px;
            border-radius: 6px;
            font-size: 15px;
            cursor: pointer;
            align-items: center;
            justify-content: center;
            transition: background 0.15s ease;
        }
        .sidebar-close-btn:hover {
            background: rgba(255, 255, 255, 0.25);
        }

        /* Mobile Backdrop */
        .admin-sidebar-backdrop {
            display: none;
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.5);
            z-index: 95;
        }

        /* Responsive Breakpoints */
        @media (max-width: 1024px) {
            .admin-grid-4, .marketing-action-grid { grid-template-columns: repeat(2, 1fr); }
            .admin-two-col { grid-template-columns: 1fr; }
        }

        @media (max-width: 768px) {
            .admin-sidebar {
                transform: translateX(-100%);
                transition: transform 0.25s cubic-bezier(0.4, 0, 0.2, 1);
                width: 280px;
                max-width: 85vw;
                box-shadow: 4px 0 24px rgba(0,0,0,0.4);
            }
            .admin-sidebar.open {
                transform: translateX(0);
            }
            .admin-sidebar-backdrop.open {
                display: block;
            }
            .sidebar-close-btn {
                display: inline-flex;
            }
            .admin-main-wrap {
                margin-left: 0;
                width: 100%;
                min-width: 0;
            }
            .admin-topbar {
                height: 56px;
                padding: 0 12px;
                gap: 8px;
            }
            .admin-topbar-left {
                gap: 8px;
                min-width: 0;
                flex: 1;
            }
            .admin-mobile-toggle {
                display: inline-flex;
                width: 36px;
                height: 36px;
                flex-shrink: 0;
            }
            .admin-topbar-title {
                font-size: 14px;
                min-width: 0;
            }
            .admin-brand-name {
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }
            .status-badge-text {
                display: none;
            }
            .admin-status-badge {
                padding: 4px 6px;
                gap: 0;
            }
            .admin-topbar-right {
                gap: 6px;
                flex-shrink: 0;
            }
            .topbar-portal-link {
                padding: 5px 9px;
                font-size: 11.5px;
                white-space: nowrap;
                flex-shrink: 0;
            }
            .portal-link-text {
                display: none;
            }
            .admin-container {
                padding: 14px 12px 40px;
            }
            .admin-page-header {
                flex-direction: column;
                align-items: flex-start;
                gap: 12px;
                margin-bottom: 14px;
            }
            /* On mobile, navigation is handled exclusively via the toggle drawer navbar; hide duplicate main-page navbars */
            .admin-subnav-bar,
            .admin-action-bar,
            .admin-page-actions {
                display: none !important;
            }
            .admin-grid-4, .marketing-action-grid {
                grid-template-columns: 1fr;
            }
            .table-wrap, table {
                display: block;
                width: 100%;
                overflow-x: auto;
            }
            th, td {
                padding: 10px 12px;
                white-space: nowrap;
            }
        }
    </style>
</head>
<body>
    <div class="admin-shell">
        <div class="admin-sidebar-backdrop" id="admin-sidebar-backdrop" onclick="closeAdminSidebar()"></div>
        <aside class="admin-sidebar" id="admin-sidebar">
            <div class="sidebar-brand-box">
                <a href="/admin/dashboard" class="sidebar-brand">
                    <span class="sidebar-brand-icon">⚡</span>
                    WAJ <span>VTU</span>
                </a>
                <div style="display: flex; align-items: center; gap: 8px;">
                    <span class="sidebar-brand-tag">Admin</span>
                    <button class="sidebar-close-btn" type="button" aria-label="Close sidebar" onclick="closeAdminSidebar()">✕</button>
                </div>
            </div>

            <nav class="sidebar-menu">
                <!-- Group 1: MAIN -->
                <div class="sidebar-group">
                    <div class="sidebar-group-title">MAIN</div>
                    <a href="/admin/dashboard" class="sidebar-link {{ 'active' if active_page == 'dashboard' else '' }}">
                        <span class="sidebar-icon">📊</span> Dashboard
                    </a>
                    <a href="/admin/transactions" class="sidebar-link {{ 'active' if active_page == 'transactions' else '' }}">
                        <span class="sidebar-icon">💳</span> Transactions
                    </a>
                    <a href="/admin/users" class="sidebar-link {{ 'active' if active_page == 'users' else '' }}">
                        <span class="sidebar-icon">👥</span> Customers
                    </a>
                </div>

                <!-- Group 2: SERVICES -->
                <div class="sidebar-group">
                    <div class="sidebar-group-title">SERVICES</div>
                    <a href="/admin/services" class="sidebar-link {{ 'active' if active_page == 'services' else '' }}">
                        <span class="sidebar-icon">🛒</span> Services Overview
                    </a>
                    <a href="/admin/transactions?q=AIRTIME" class="sidebar-link">
                        <span class="sidebar-icon">📱</span> Airtime
                    </a>
                    <a href="/admin/transactions?q=DATA" class="sidebar-link">
                        <span class="sidebar-icon">📶</span> Data
                    </a>
                    <a href="/admin/transactions?q=CABLE" class="sidebar-link">
                        <span class="sidebar-icon">📺</span> Cable TV
                    </a>
                    <a href="/admin/transactions?q=ELECTRICITY" class="sidebar-link">
                        <span class="sidebar-icon">⚡</span> Electricity
                    </a>
                    <a href="/admin/transactions?q=EDU" class="sidebar-link">
                        <span class="sidebar-icon">🎓</span> Education
                    </a>
                </div>

                <!-- Group 3: MARKETING -->
                <div class="sidebar-group">
                    <div class="sidebar-group-title">MARKETING</div>
                    <a href="/admin/marketing" class="sidebar-link {{ 'active' if active_page == 'marketing' else '' }}">
                        <span class="sidebar-icon">🚀</span> Marketing Overview
                    </a>
                    <a href="/admin/marketing/referrals" class="sidebar-link">
                        <span class="sidebar-icon">🤝</span> Referrals
                    </a>
                    <a href="/admin/marketing/daily-deals" class="sidebar-link">
                        <span class="sidebar-icon">🏷️</span> Daily Deals
                    </a>
                    <a href="/admin/marketing/reengagement" class="sidebar-link">
                        <span class="sidebar-icon">🔁</span> Re-engagement
                    </a>
                    <a href="/admin/marketing/campaigns" class="sidebar-link">
                        <span class="sidebar-icon">📬</span> Campaigns
                    </a>
                    <a href="/admin/marketing/settings" class="sidebar-link">
                        <span class="sidebar-icon">⚙️</span> Marketing Settings
                    </a>
                </div>

                <!-- Group 4: FINANCE -->
                <div class="sidebar-group">
                    <div class="sidebar-group-title">FINANCE</div>
                    <a href="/admin/payments" class="sidebar-link {{ 'active' if active_page == 'payments' else '' }}">
                        <span class="sidebar-icon">💰</span> Wallet / Deposits
                    </a>
                    <a href="/admin/ledger" class="sidebar-link {{ 'active' if active_page == 'ledger' else '' }}">
                        <span class="sidebar-icon">📒</span> Wallet Ledger
                    </a>
                    <a href="/admin/pricing" class="sidebar-link {{ 'active' if active_page == 'pricing' else '' }}">
                        <span class="sidebar-icon">💵</span> Fees &amp; Markups
                    </a>
                </div>

                <!-- Group 5: SYSTEM -->
                <div class="sidebar-group">
                    <div class="sidebar-group-title">SYSTEM</div>
                    <a href="/admin/whatsapp" class="sidebar-link {{ 'active' if active_page == 'whatsapp' else '' }}">
                        <span class="sidebar-icon">📱</span> WhatsApp Bot
                    </a>
                    <a href="/admin/settings" class="sidebar-link {{ 'active' if active_page == 'settings' else '' }}">
                        <span class="sidebar-icon">⚙️</span> Settings &amp; Tasks
                    </a>
                    <a href="/admin/security" class="sidebar-link {{ 'active' if active_page == 'security' else '' }}">
                        <span class="sidebar-icon">🔐</span> Audit Logs
                    </a>
                    <a href="/admin/analytics" class="sidebar-link {{ 'active' if active_page == 'analytics' else '' }}">
                        <span class="sidebar-icon">📈</span> Analytics
                    </a>
                    <a href="/admin/providers" class="sidebar-link {{ 'active' if active_page == 'providers' else '' }}">
                        <span class="sidebar-icon">🔌</span> Providers
                    </a>
                    <a href="/admin/support" class="sidebar-link {{ 'active' if active_page == 'support' else '' }}">
                        <span class="sidebar-icon">🎟️</span> Support
                    </a>
                </div>
            </nav>

            <div class="sidebar-footer">
                <div class="sidebar-user-pill">
                    <span class="sidebar-user-avatar">⚙</span>
                    <span class="sidebar-user-name">Admin Portal</span>
                </div>
                <a href="/logout" class="sidebar-logout-btn" title="Log out">Logout</a>
            </div>
        </aside>

        <div class="admin-main-wrap">
            <header class="admin-topbar">
                <div class="admin-topbar-left">
                    <button class="admin-mobile-toggle" type="button" aria-label="Open sidebar" onclick="toggleAdminSidebar()">☰</button>
                    <div class="admin-topbar-title">
                        <span class="admin-brand-name">WAJ VTU Admin</span>
                        <span class="admin-status-badge" title="System operational"><span class="admin-status-dot"></span><span class="status-badge-text"> Operational</span></span>
                    </div>
                </div>
                <div class="admin-topbar-right">
                    <a href="/dashboard" class="topbar-portal-link" target="_blank" rel="noopener" title="Open Customer Site">
                        <span>↗</span> <span class="portal-link-text">Customer Site</span>
                    </a>
                    <a href="/logout" class="topbar-portal-link" style="color:#ef4444;" title="Log out">
                        Logout
                    </a>
                </div>
            </header>

            <main class="admin-container">
                {{ body_content | safe }}
            </main>
        </div>
    </div>

    <script>
        function toggleAdminSidebar() {
            const s = document.getElementById('admin-sidebar');
            const b = document.getElementById('admin-sidebar-backdrop');
            s.classList.toggle('open');
            b.classList.toggle('open');
        }
        function closeAdminSidebar() {
            document.getElementById('admin-sidebar')?.classList.remove('open');
            document.getElementById('admin-sidebar-backdrop')?.classList.remove('open');
        }
    </script>
</body>
</html>
"""


@app.route("/admin/dashboard")
def admin_dashboard():
    auth_error = require_admin_auth()
    if auth_error:
        return auth_error

    now = datetime.now(timezone.utc)
    start_of_day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    start_of_week = start_of_day - timedelta(days=now.weekday())
    start_of_month = datetime(now.year, now.month, 1, tzinfo=timezone.utc)

    total_users = User.query.count()
    total_transactions = Transaction.query.count()
    successful_transactions = Transaction.query.filter_by(status="SUCCESS").count()
    failed_transactions = Transaction.query.filter_by(status="FAILED").count()
    pending_transactions = Transaction.query.filter_by(status="PENDING").count()

    successful_txs = Transaction.query.filter_by(status="SUCCESS").all()
    total_inflow = sum((tx.amount for tx in successful_txs if tx.type == "DEPOSIT"), Decimal("0.00"))
    total_outflow = sum((tx.amount for tx in successful_txs if tx.type != "DEPOSIT"), Decimal("0.00"))
    
    markup_rates = {
        row.service_type: Decimal(str(row.markup_amount))
        for row in ServiceMarkup.query.all()
    }
    data_rates = [rate for service, rate in markup_rates.items() if service.startswith("DATA_")]
    default_data_rate = sum(data_rates, Decimal("0.00")) / len(data_rates) if data_rates else Decimal("0.00")

    def estimated_markup(tx):
        if tx.type == "DEPOSIT":
            return Decimal("0.00")
        stored_markup = (tx.meta_data or {}).get("markup_amount") if isinstance(tx.meta_data, dict) else None
        if stored_markup is not None:
            return Decimal(str(stored_markup))
        rate = markup_rates.get(tx.type, default_data_rate if tx.type == "DATA" else Decimal("0.00"))
        if rate <= 0:
            return Decimal("0.00")
        return (Decimal(str(tx.amount)) * rate / (Decimal("100.00") + rate)).quantize(Decimal("0.01"))

    def api_discount(tx):
        if not isinstance(tx.meta_data, dict):
            return Decimal("0.00")
        return Decimal(str(tx.meta_data.get("api_discount_amount", "0.00")))

    total_markup_earned = sum((estimated_markup(tx) for tx in successful_txs), Decimal("0.00"))
    total_api_discount = sum((api_discount(tx) for tx in successful_txs), Decimal("0.00"))
    today_markup_earned = sum(
        (
            estimated_markup(tx)
            for tx in successful_txs
            if normalize_datetime(tx.created_at) and normalize_datetime(tx.created_at) >= start_of_day
        ),
        Decimal("0.00"),
    )
    provider_balance_result = fetch_account_balance()
    provider_balance = provider_balance_result.get("balance")
    swiftbills_balance_result = fetch_swiftbills_balance()
    swiftbills_balance = swiftbills_balance_result.get("balance")
    
    today_inflow = sum(
        (
            tx.amount
            for tx in successful_txs
            if tx.type == "DEPOSIT" and normalize_datetime(tx.created_at) and normalize_datetime(tx.created_at) >= start_of_day
        ),
        Decimal("0.00"),
    )
    today_outflow = sum(
        (
            tx.amount
            for tx in successful_txs
            if tx.type != "DEPOSIT" and normalize_datetime(tx.created_at) and normalize_datetime(tx.created_at) >= start_of_day
        ),
        Decimal("0.00"),
    )
    today_tx_count = sum(
        1
        for tx in successful_txs
        if normalize_datetime(tx.created_at) and normalize_datetime(tx.created_at) >= start_of_day
    )
    total_user_balances = db.session.query(func.sum(User.wallet_balance)).scalar() or Decimal("0.00")

    active_customers = db.session.query(User.id).join(Transaction).group_by(User.id).count()
    new_customers_today = User.query.filter(User.created_at >= start_of_day).count()
    new_customers_week = User.query.filter(User.created_at >= start_of_week).count()
    new_customers_month = User.query.filter(User.created_at >= start_of_month).count()

    top_customers = (
        db.session.query(User.phone, func.sum(Transaction.amount).label("total_spent"))
        .join(Transaction)
        .filter(Transaction.type != "DEPOSIT", Transaction.status == "SUCCESS")
        .group_by(User.phone)
        .order_by(func.sum(Transaction.amount).desc())
        .limit(5)
        .all()
    )

    service_breakdown = (
        db.session.query(Transaction.type, func.count(Transaction.id).label("count"), func.sum(Transaction.amount).label("total_amount"))
        .filter(Transaction.type != "DEPOSIT", Transaction.status == "SUCCESS")
        .group_by(Transaction.type)
        .order_by(func.sum(Transaction.amount).desc())
        .limit(6)
        .all()
    )

    provider_summary = (
        db.session.query(
            Transaction.provider_name,
            func.count(Transaction.id).label("count"),
            func.sum(Transaction.amount).label("total_amount"),
        )
        .filter(Transaction.status == "SUCCESS")
        .group_by(Transaction.provider_name)
        .order_by(func.count(Transaction.id).desc())
        .all()
    )

    recent_transactions = Transaction.query.order_by(Transaction.id.desc()).limit(10).all()

    tx_rows = ""
    for tx in recent_transactions:
        status_cls = {
            "SUCCESS": "badge-success",
            "PENDING": "badge-pending",
        }.get(tx.status, "badge-failed")
        tx_rows += f"""
        <tr>
            <td><code>{escape(tx.reference)}</code></td>
            <td>{escape(tx.user.phone if tx.user else '')}</td>
            <td><strong>{escape(tx.type)}</strong></td>
            <td>₦{tx.amount:,.2f}</td>
            <td>{escape(tx.recipient or '—')}</td>
            <td><span class="{escape(status_cls)}">{escape(tx.status)}</span></td>
            <td>{escape(format_admin_datetime(tx.created_at))}</td>
        </tr>
        """

    customer_rows = ""
    for customer_phone, total_spent in top_customers:
        customer_rows += f"""
        <tr>
            <td><strong>{escape(customer_phone or 'Anonymous')}</strong></td>
            <td style="font-family:'Space Grotesk',sans-serif; font-weight:700;">₦{Decimal(total_spent or 0):,.2f}</td>
        </tr>
        """

    service_rows = ""
    for service_type, count, total_amount in service_breakdown:
        service_rows += f"""
        <tr>
            <td><strong>{escape(service_type)}</strong></td>
            <td>{count} sales</td>
            <td style="font-family:'Space Grotesk',sans-serif; font-weight:700;">₦{Decimal(total_amount or 0):,.2f}</td>
        </tr>
        """

    provider_cards = ""
    for provider_name, count, total_amount in provider_summary:
        display_name = (provider_name or "unknown").replace("_", " ").title()
        provider_cards += f"""
        <div class="card">
            <h3>{escape(display_name)}</h3>
            <p>{count}</p>
            <small>Successful sales · ₦{Decimal(total_amount or 0):,.2f}</small>
        </div>
        """
    if not provider_cards:
        provider_cards = '<div class="card"><h3>No Provider Data</h3><p>0</p><small>No successful provider transactions yet</small></div>'

    # Marketing metrics for dashboard overview cards
    from services.referral_service import get_admin_referral_metrics
    from services.deal_service import generate_daily_deal, get_lagos_today
    from services.reengagement_service import scan_inactive_segments

    ref_metrics = get_admin_referral_metrics()
    today_deal, _ = generate_daily_deal(target_date=get_lagos_today())
    deal_metric_str = f"₦{today_deal.price:,.0f}" if today_deal and getattr(today_deal, "price", None) is not None else "Active"
    deal_sub_str = f"{getattr(today_deal, 'network', '')} {getattr(today_deal, 'data_size', '')}".strip() if today_deal else "Daily special data discounts"
    if not deal_sub_str:
        deal_sub_str = "Daily special data discounts"
    seg_data = scan_inactive_segments()
    inactive_total = (
        seg_data.get("counts", {}).get("7_days", 0)
        + seg_data.get("counts", {}).get("14_days", 0)
        + seg_data.get("counts", {}).get("30_days", 0)
    )
    pending_marketing_actions = MarketingMessage.query.filter(
        MarketingMessage.status.in_(["DRAFT", "QUEUED"])
    ).count()

    content = f"""
    <!-- 1. TOP HEADER & ACTIONS -->
    <div class="admin-page-header">
        <div>
            <h1 class="admin-page-title">Admin Dashboard</h1>
            <p class="admin-page-desc">Comprehensive platform health, real-time volume, and operational control.</p>
        </div>
        <div class="admin-page-actions">
            <a href="/admin/transactions" class="btn-admin-action"><span>💳</span> Transactions</a>
            <a href="/admin/users" class="btn-admin-action"><span>👥</span> Customers</a>
            <a href="/admin/marketing" class="btn-admin-action primary"><span>🚀</span> Marketing Hub</a>
            <a href="/admin/settings" class="btn-admin-action"><span>⚙️</span> Settings</a>
        </div>
    </div>

    <!-- 2. STATISTICS: 4-CARD DESKTOP GRID -->
    <div class="admin-grid-4">
        <div class="admin-stat-card">
            <div class="stat-card-kicker">TOTAL CUSTOMERS</div>
            <div class="stat-card-val">{total_users:,}</div>
            <div class="stat-card-sub">{active_customers:,} active · {new_customers_today} joined today</div>
        </div>
        <div class="admin-stat-card">
            <div class="stat-card-kicker">TODAY'S TRANSACTIONS</div>
            <div class="stat-card-val">{today_tx_count:,}</div>
            <div class="stat-card-sub">{successful_transactions:,} successful · {pending_transactions} pending</div>
        </div>
        <div class="admin-stat-card">
            <div class="stat-card-kicker">TODAY'S REVENUE / INFLOW</div>
            <div class="stat-card-val">₦{today_inflow:,.2f}</div>
            <div class="stat-card-sub">Lifetime inflow: ₦{total_inflow:,.2f}</div>
        </div>
        <div class="admin-stat-card">
            <div class="stat-card-kicker">WALLET LIABILITIES & BALANCE</div>
            <div class="stat-card-val">₦{total_user_balances:,.2f}</div>
            <div class="stat-card-sub">User balances · Provider: {'₦{:,.2f}'.format(provider_balance) if provider_balance is not None else 'Live'}</div>
        </div>
    </div>

    <!-- 3. TWO-COLUMN LAYOUT: RECENT TRANSACTIONS + RECENT ACTIVITY -->
    <div class="admin-two-col">
        <!-- LEFT: Recent Transactions -->
        <div class="admin-panel-card">
            <div class="admin-panel-header">
                <span class="admin-panel-title">💳 Recent Transactions Stream</span>
                <a href="/admin/transactions" class="admin-panel-link">View all transactions →</a>
            </div>
            <div class="table-wrap">
                <table>
                    <thead>
                        <tr><th>Reference</th><th>Customer</th><th>Type</th><th>Amount</th><th>Recipient</th><th>Status</th><th>Timestamp</th></tr>
                    </thead>
                    <tbody>
                        {tx_rows if tx_rows else '<tr><td colspan="7" style="text-align:center;">No transactions logged yet</td></tr>'}
                    </tbody>
                </table>
            </div>
        </div>

        <!-- RIGHT: Recent Customers & Growth -->
        <div class="admin-panel-card">
            <div class="admin-panel-header">
                <span class="admin-panel-title">👥 Top Customers by Spend</span>
                <a href="/admin/users" class="admin-panel-link">Manage Users →</a>
            </div>
            <div class="table-wrap">
                <table>
                    <thead><tr><th>Customer Phone</th><th>Total Spent</th></tr></thead>
                    <tbody>{customer_rows if customer_rows else '<tr><td colspan="2" style="text-align:center;">No customer activity yet</td></tr>'}</tbody>
                </table>
            </div>
            <div style="padding: 16px 20px; border-top: 1px solid var(--admin-border); background: #fafafa;">
                <div style="font-size: 11px; font-weight: 700; color: var(--admin-muted); text-transform: uppercase; margin-bottom: 8px;">Customer Registration Growth</div>
                <div style="display: flex; justify-content: space-between; font-size: 12px; color: var(--admin-ink);">
                    <span>Today: <strong>{new_customers_today}</strong></span>
                    <span>This Week: <strong>{new_customers_week}</strong></span>
                    <span>This Month: <strong>{new_customers_month}</strong></span>
                </div>
            </div>
        </div>
    </div>

    <!-- 4. MARKETING OVERVIEW: COMPACT CLICKABLE CARDS -->
    <h2 class="dashboard-heading">🚀 Marketing Automation Overview</h2>
    <div class="marketing-action-grid">
        <a href="/admin/marketing/referrals" class="mkt-card-tile">
            <div class="mkt-tile-top">
                <span class="mkt-tile-icon">🤝</span>
                <span class="mkt-tile-title">Referral Program</span>
            </div>
            <div class="mkt-tile-metric">{ref_metrics['total_referrals']}</div>
            <div class="mkt-tile-sub">{ref_metrics['successful_referrals']} successful · ₦{ref_metrics['total_rewards_paid']:,.2f} rewards paid</div>
            <div class="mkt-tile-arrow">Manage Referrals →</div>
        </a>

        <a href="/admin/marketing/daily-deals" class="mkt-card-tile">
            <div class="mkt-tile-top">
                <span class="mkt-tile-icon">🏷️</span>
                <span class="mkt-tile-title">Today's Data Deal</span>
            </div>
            <div class="mkt-tile-metric">{deal_metric_str}</div>
            <div class="mkt-tile-sub">{deal_sub_str}</div>
            <div class="mkt-tile-arrow">View Daily Deals →</div>
        </a>

        <a href="/admin/marketing/reengagement" class="mkt-card-tile">
            <div class="mkt-tile-top">
                <span class="mkt-tile-icon">🔁</span>
                <span class="mkt-tile-title">Customer Re-engagement</span>
            </div>
            <div class="mkt-tile-metric">{inactive_total}</div>
            <div class="mkt-tile-sub">Inactive customers across 7d, 14d, 30d segments</div>
            <div class="mkt-tile-arrow">Launch Campaign →</div>
        </a>

        <a href="/admin/marketing/campaigns" class="mkt-card-tile">
            <div class="mkt-tile-top">
                <span class="mkt-tile-icon">📬</span>
                <span class="mkt-tile-title">Marketing Actions</span>
            </div>
            <div class="mkt-tile-metric">{pending_marketing_actions}</div>
            <div class="mkt-tile-sub">Pending / Draft messages awaiting dispatch</div>
            <div class="mkt-tile-arrow">Review Queue →</div>
        </a>
    </div>

    <!-- 5. BUSINESS & OPERATIONAL OVERVIEW -->
    <h2 class="dashboard-heading">💼 Business &amp; Provider Overview</h2>
    <div class="card-grid">
        <div class="card">
            <h3>ClubKonnect Balance</h3>
            <p>{'₦{:,.2f}'.format(provider_balance) if provider_balance is not None else 'Unavailable'}</p>
            <small>{escape(provider_balance_result.get('reason', 'Live provider balance'))}</small>
        </div>
        <div class="card">
            <h3>SwiftBills Balance</h3>
            <p>{'₦{:,.2f}'.format(swiftbills_balance) if swiftbills_balance is not None else 'Unavailable'}</p>
            <small>{escape(swiftbills_balance_result.get('reason', 'Live SwiftBills balance'))}</small>
        </div>
        <div class="card">
            <h3>Estimated Markup Earned</h3>
            <p>₦{total_markup_earned:,.2f}</p>
            <small>Total customer charges minus provider base costs</small>
        </div>
        <div class="card">
            <h3>API Discounts Saved</h3>
            <p>₦{total_api_discount:,.2f}</p>
            <small>Provider discounts captured on eligible plans</small>
        </div>
        <div class="card">
            <h3>Today's Markup Earned</h3>
            <p>₦{today_markup_earned:,.2f}</p>
            <small>Based on successful sales settled today</small>
        </div>
        <div class="card">
            <h3>Cash Flow Outflow</h3>
            <p>₦{today_outflow:,.2f}</p>
            <small>Today's service costs · Lifetime: ₦{total_outflow:,.2f}</small>
        </div>
    </div>

    <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 24px;">
        <div class="admin-panel-card">
            <div class="admin-panel-header">
                <span class="admin-panel-title">🛒 Service Sales Breakdown</span>
                <a href="/admin/services" class="admin-panel-link">All Services →</a>
            </div>
            <div class="table-wrap">
                <table>
                    <thead><tr><th>Service</th><th>Sales Count</th><th>Total Volume</th></tr></thead>
                    <tbody>{service_rows if service_rows else '<tr><td colspan="3" style="text-align:center;">No service sales yet</td></tr>'}</tbody>
                </table>
            </div>
        </div>

        <div class="admin-panel-card">
            <div class="admin-panel-header">
                <span class="admin-panel-title">🔌 Provider Volume Summary</span>
                <a href="/admin/providers" class="admin-panel-link">Manage Providers →</a>
            </div>
            <div class="card-grid" style="padding:16px; margin-bottom:0;">
                {provider_cards}
            </div>
        </div>
    </div>
    """

    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=content, active_page="dashboard")


@app.route("/admin/users", methods=["GET"])
def admin_users():
    auth_error = require_admin_auth()
    if auth_error:
        return auth_error
    search_query = request.args.get("q", "").strip()
    csrf_token = get_csrf_token()
    if search_query:
        users = User.query.filter(User.phone.contains(search_query)).all()
    else:
        users = User.query.order_by(User.id.desc()).all()

    user_rows = ""
    for u in users:
        has_website_login = bool(u.email and u.password_hash)
        has_whatsapp_identity = bool(u.whatsapp_id and not str(u.whatsapp_id).startswith("web_"))
        if has_website_login and has_whatsapp_identity:
            account_type = "Linked"
            account_class = "linked"
        elif has_website_login:
            account_type = "Website"
            account_class = "website"
        else:
            account_type = "WhatsApp"
            account_class = "whatsapp"
        user_rows += f"""
        <tr>
            <td>#{escape(u.id)}</td>
            <td><b>{escape(u.phone)}</b></td>
            <td><span class="account-badge {account_class}">{account_type}</span><br><small>{escape(u.email or 'WhatsApp only')}</small></td>
            <td>{escape(format_admin_datetime(u.created_at))}</td>
            <td>₦{u.wallet_balance:,.2f}</td>
            <td><code>{escape(u.current_state)}</code></td>
            <td>
                <form method="POST" action="/admin/user/{u.id}/fund" style="display:flex; gap:6px;">
                    <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
                    <input type="number" step="0.01" name="amount" placeholder="Amount" required style="width:100px;">
                    <select name="action_type">
                        <option value="CREDIT">+ Credit</option>
                        <option value="DEBIT">- Debit</option>
                    </select>
                    <button type="submit">Update Wallet</button>
                </form>
            </td>
        </tr>
        """

    content = f"""
    <div style="display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:20px;">
        <h2 style="margin:0;">👥 User Directory & Wallet Control</h2>
        <form method="GET" action="/admin/users" style="display:flex; gap:8px; flex-wrap:wrap;">
            <input type="text" name="q" placeholder="Search phone number..." value="{escape(search_query)}">
            <button type="submit">Search</button>
        </form>
    </div>
    <div class="table-wrap">
        <table>
            <thead>
                <tr><th>User ID</th><th>Phone Number</th><th>Account Source</th><th>Joined</th><th>Wallet Balance</th><th>Bot State</th><th>Manual Wallet Top-up</th></tr>
            </thead>
            <tbody>
                {user_rows if user_rows else '<tr><td colspan="7" style="text-align:center;">No users found</td></tr>'}
            </tbody>
        </table>
    </div>
    """

    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=content, active_page="users")


@app.route("/admin/payments")
def admin_payments():
    auth_error = require_admin_auth()
    if auth_error:
        return auth_error

    success_txs = Transaction.query.filter_by(status="SUCCESS").all()
    total_received = sum((tx.amount for tx in success_txs), Decimal("0.00"))
    wallet_adjustments = Transaction.query.filter_by(type="WALLET_ADJUSTMENT").order_by(Transaction.id.desc()).limit(10).all()

    payment_rows = ""
    for tx in wallet_adjustments:
        payment_rows += f"""
        <tr>
            <td><code>{escape(tx.reference)}</code></td>
            <td>{escape(tx.user.phone if tx.user else '')}</td>
            <td>₦{tx.amount:,.2f}</td>
            <td>{escape(tx.status)}</td>
            <td>{escape(format_admin_datetime(tx.created_at))}</td>
        </tr>
        """

    content = f"""
    <div class="section-grid">
        <div class="card"><h3>Total Received</h3><p>₦{total_received:,.2f}</p></div>
        <div class="card"><h3>Successful Payments</h3><p>{len(success_txs)}</p></div>
        <div class="card"><h3>Wallet Adjustments</h3><p>{Transaction.query.filter_by(type='WALLET_ADJUSTMENT').count()}</p></div>
        <div class="card"><h3>Pending</h3><p>{Transaction.query.filter_by(status='PENDING').count()}</p></div>
    </div>
    <h2 style="margin-bottom:15px;">💰 Payment Overview</h2>
    <table>
        <thead><tr><th>Reference</th><th>Customer</th><th>Amount</th><th>Status</th><th>Timestamp</th></tr></thead>
        <tbody>{payment_rows if payment_rows else '<tr><td colspan="5" style="text-align:center;">No payment activity found</td></tr>'}</tbody>
    </table>
    """
    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=content, active_page="payments")


@app.route("/admin/whatsapp")
def admin_whatsapp():
    auth_error = require_admin_auth()
    if auth_error:
        return auth_error

    total_users = User.query.count()
    active_customers = db.session.query(User.id).join(Transaction).group_by(User.id).count()
    service_counts = db.session.query(Transaction.type, func.count(Transaction.id).label("count")).group_by(Transaction.type).order_by(func.count(Transaction.id).desc()).first()
    recent_users = User.query.order_by(User.created_at.desc()).limit(10).all()

    user_rows = ""
    for user in recent_users:
        user_rows += f"""
        <tr>
            <td>{escape(user.phone)}</td>
            <td>{escape(user.current_state)}</td>
            <td>{escape(format_admin_datetime(user.created_at))}</td>
        </tr>
        """

    content = f"""
    <div class="section-grid">
        <div class="card"><h3>Total WhatsApp Users</h3><p>{total_users}</p></div>
        <div class="card"><h3>Active Conversations</h3><p>{active_customers}</p></div>
        <div class="card"><h3>Completed Orders</h3><p>{Transaction.query.filter_by(status='SUCCESS').count()}</p></div>
        <div class="card"><h3>Most Requested</h3><p>{escape(service_counts[0]) if service_counts else 'N/A'}</p></div>
    </div>
    <h2 style="margin-bottom:15px;">📱 WhatsApp Customer Funnel</h2>
    <table>
        <thead><tr><th>Phone</th><th>Current State</th><th>Joined</th></tr></thead>
        <tbody>{user_rows if user_rows else '<tr><td colspan="3" style="text-align:center;">No WhatsApp users found</td></tr>'}</tbody>
    </table>
    """
    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=content, active_page="whatsapp")


@app.route("/admin/services")
def admin_services():
    auth_error = require_admin_auth()
    if auth_error:
        return auth_error

    breakdown = (
        db.session.query(Transaction.type, func.count(Transaction.id).label("count"), func.sum(Transaction.amount).label("total_amount"))
        .group_by(Transaction.type)
        .order_by(func.sum(Transaction.amount).desc())
        .all()
    )

    service_rows = ""
    for service_type, count, total_amount in breakdown:
        service_rows += f"""
        <tr>
            <td>{escape(service_type)}</td>
            <td>{count}</td>
            <td>₦{Decimal(total_amount or 0):,.2f}</td>
        </tr>
        """

    content = f"""
    <h2 style="margin-bottom:15px;">🛒 Service Performance</h2>
    <table>
        <thead><tr><th>Service</th><th>Transactions</th><th>Total Value</th></tr></thead>
        <tbody>{service_rows if service_rows else '<tr><td colspan="3" style="text-align:center;">No service activity yet</td></tr>'}</tbody>
    </table>
    """
    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=content, active_page="services")


@app.route("/admin/analytics")
def admin_analytics():
    auth_error = require_admin_auth()
    if auth_error:
        return auth_error

    now = datetime.now(timezone.utc)
    start_of_day = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
    total_revenue = sum((tx.amount for tx in Transaction.query.filter_by(status='SUCCESS').all()), Decimal('0.00'))
    today_sales = sum((tx.amount for tx in Transaction.query.filter_by(status='SUCCESS').all() if normalize_datetime(tx.created_at) and normalize_datetime(tx.created_at) >= start_of_day), Decimal('0.00'))
    failed = Transaction.query.filter_by(status='FAILED').count()
    pending = Transaction.query.filter_by(status='PENDING').count()
    active_customers = db.session.query(User.id).join(Transaction).group_by(User.id).count()

    top_customers = (
        db.session.query(User.phone, func.sum(Transaction.amount).label("total_spent"))
        .join(Transaction)
        .group_by(User.phone)
        .order_by(func.sum(Transaction.amount).desc())
        .limit(5)
        .all()
    )

    customer_rows = ""
    for phone, total_spent in top_customers:
        customer_rows += f"""
        <tr>
            <td>{escape(phone)}</td>
            <td>₦{Decimal(total_spent or 0):,.2f}</td>
        </tr>
        """

    content = f"""
    <div class="section-grid">
        <div class="card"><h3>Total Revenue</h3><p>₦{total_revenue:,.2f}</p></div>
        <div class="card"><h3>Today's Sales</h3><p>₦{today_sales:,.2f}</p></div>
        <div class="card"><h3>Failed</h3><p>{failed}</p></div>
        <div class="card"><h3>Pending</h3><p>{pending}</p></div>
        <div class="card"><h3>Active Customers</h3><p>{active_customers}</p></div>
        <div class="card"><h3>Total Transactions</h3><p>{Transaction.query.count()}</p></div>
    </div>
    <h2 style="margin-bottom:15px;">📈 Customer Value Tracker</h2>
    <table>
        <thead><tr><th>Customer</th><th>Total Spent</th></tr></thead>
        <tbody>{customer_rows if customer_rows else '<tr><td colspan="2" style="text-align:center;">No customer analytics yet</td></tr>'}</tbody>
    </table>
    """
    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=content, active_page="analytics")


@app.route("/admin/providers")
def admin_providers():
    auth_error = require_admin_auth()
    if auth_error:
        return auth_error

    provider_rows = ""
    for service_type, count, total_amount in (
        db.session.query(Transaction.type, func.count(Transaction.id).label("count"), func.sum(Transaction.amount).label("total_amount"))
        .group_by(Transaction.type)
        .order_by(func.count(Transaction.id).desc())
        .all()
    ):
        provider_rows += f"""
        <tr>
            <td>{escape(service_type)}</td>
            <td>{count}</td>
            <td>₦{Decimal(total_amount or 0):,.2f}</td>
            <td>Healthy</td>
        </tr>
        """

    content = f"""
    <div class="section-grid">
        <div class="card"><h3>Provider Health</h3><p>Healthy</p></div>
        <div class="card"><h3>Successful Requests</h3><p>{Transaction.query.filter_by(status='SUCCESS').count()}</p></div>
        <div class="card"><h3>Failed Requests</h3><p>{Transaction.query.filter_by(status='FAILED').count()}</p></div>
        <div class="card"><h3>Pending Requests</h3><p>{Transaction.query.filter_by(status='PENDING').count()}</p></div>
    </div>
    <h2 style="margin-bottom:15px;">🔌 Provider / API Monitor</h2>
    <table>
        <thead><tr><th>Provider</th><th>Requests</th><th>Volume</th><th>Status</th></tr></thead>
        <tbody>{provider_rows if provider_rows else '<tr><td colspan="4" style="text-align:center;">No provider activity yet</td></tr>'}</tbody>
    </table>
    """
    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=content, active_page="providers")


@app.route("/admin/pricing")
def admin_pricing_redirect():
    return redirect(url_for("admin_settings"))


@app.route("/admin/support")
def admin_support():
    auth_error = require_admin_auth()
    if auth_error:
        return auth_error

    csrf_token = get_csrf_token()
    status_filter = request.args.get("status", "ALL").strip().upper()
    search_query = request.args.get("q", "").strip()
    flash_msg = request.args.get("msg", "").strip()

    # Query metrics
    total_count = SupportTicket.query.count()
    open_count = SupportTicket.query.filter_by(status="OPEN").count()
    in_progress_count = SupportTicket.query.filter_by(status="IN_PROGRESS").count()
    resolved_count = SupportTicket.query.filter_by(status="RESOLVED").count()

    query = SupportTicket.query
    if status_filter in ["OPEN", "IN_PROGRESS", "RESOLVED"]:
        query = query.filter_by(status=status_filter)
    if search_query:
        search_pattern = f"%{search_query}%"
        query = query.filter(
            or_(
                SupportTicket.ticket_code.ilike(search_pattern),
                SupportTicket.customer_phone.ilike(search_pattern),
                SupportTicket.reference.ilike(search_pattern),
                SupportTicket.recipient.ilike(search_pattern),
                SupportTicket.description.ilike(search_pattern),
                SupportTicket.category.ilike(search_pattern),
            )
        )

    tickets = query.order_by(SupportTicket.created_at.desc()).limit(50).all()

    ticket_cards = ""
    for tkt in tickets:
        status_bg = "#fef3c7"
        status_color = "#b45309"
        status_border = "#fde68a"
        if tkt.status == "IN_PROGRESS":
            status_bg = "#e0f2fe"
            status_color = "#0369a1"
            status_border = "#bae6fd"
        elif tkt.status == "RESOLVED":
            status_bg = "#dcfce7"
            status_color = "#15803d"
            status_border = "#bbf7d0"

        # Customer account info
        user_info = "Guest / Unlinked"
        if tkt.user:
            user_info = f"{escape(tkt.user.name or 'User')} (Bal: ₦{tkt.user.wallet_balance:,.2f})"

        # Clean history notes rendering
        notes_html = ""
        if tkt.admin_notes:
            notes_lines = tkt.admin_notes.strip().split("\n\n")
            rendered_notes = "".join(
                f'<div style="background:#f8fafc; border-left:3px solid #0284c7; padding:6px 10px; margin-top:6px; border-radius:4px; font-size:12px; color:#334155;">{escape(note)}</div>'
                for note in notes_lines if note.strip()
            )
            notes_html = f"""
            <div style="margin-top:12px; border-top:1px dashed #e2e8f0; padding-top:10px;">
                <div style="font-size:11px; font-weight:700; color:#64748b; text-transform:uppercase;">Communication & Resolution History:</div>
                {rendered_notes}
            </div>
            """

        action_forms = ""
        if tkt.status != "RESOLVED":
            action_forms = f"""
            <div style="margin-top:14px; background:#fafafa; border:1px solid #e5e7eb; border-radius:8px; padding:12px;">
                <form method="POST" action="/admin/support/reply/{escape(tkt.ticket_code)}" style="display:flex; flex-direction:column; gap:8px;">
                    <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
                    <label style="font-size:12px; font-weight:700; color:#1f2937;">Send Official WhatsApp Response:</label>
                    <textarea name="reply_text" rows="2" placeholder="Type reply here... It lands directly in customer's WhatsApp chat" required style="width:100%; border:1px solid #d1d5db; border-radius:6px; padding:8px 10px; font-size:13px; font-family:inherit; resize:vertical;"></textarea>
                    <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:8px;">
                        <button type="submit" style="background:#16a34a; color:#fff; border:none; padding:8px 14px; border-radius:6px; font-weight:600; font-size:12px; cursor:pointer;">
                            💬 Send to WhatsApp ↗
                        </button>
                    </div>
                </form>
                <div style="margin-top:10px; padding-top:8px; border-top:1px solid #f3f4f6; display:flex; justify-content:flex-end;">
                    <form method="POST" action="/admin/support/resolve/{escape(tkt.ticket_code)}">
                        <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
                        <button type="submit" onclick="return confirm('Resolve ticket and resume automated AI for this user?');" style="background:#0f172a; color:#f8fafc; border:1px solid #334155; padding:6px 12px; border-radius:6px; font-weight:600; font-size:12px; cursor:pointer;">
                            ✓ Mark Resolved & Resume AI
                        </button>
                    </form>
                </div>
            </div>
            """
        else:
            action_forms = f"""
            <div style="margin-top:12px; display:flex; align-items:center; justify-content:space-between; background:#f0fdf4; border:1px solid #bbf7d0; padding:8px 12px; border-radius:6px; font-size:12px; color:#166534;">
                <span><b>✓ Case Closed:</b> Resolved on {escape(format_admin_datetime(tkt.resolved_at))}</span>
                <span style="font-weight:700;">AI Bot Active</span>
            </div>
            """

        ticket_cards += f"""
        <div style="background:#ffffff; border:1px solid #e2e8f0; border-radius:10px; padding:16px; margin-bottom:16px; box-shadow:0 1px 3px rgba(0,0,0,0.03);">
            <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:10px; margin-bottom:10px;">
                <div>
                    <span style="font-family:'Space Grotesk',monospace; font-weight:700; font-size:15px; color:#0f172a;">{escape(tkt.ticket_code)}</span>
                    <span style="display:inline-block; margin-left:8px; padding:2px 8px; border-radius:4px; font-size:11px; font-weight:700; background:{status_bg}; color:{status_color}; border:1px solid {status_border};">{escape(tkt.status)}</span>
                    <div style="font-size:12px; color:#64748b; margin-top:2px;">Submitted: {escape(format_admin_datetime(tkt.created_at))}</div>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:13px; font-weight:700; color:#0f172a;">📱 {escape(tkt.customer_phone)}</div>
                    <div style="font-size:11px; color:#64748b;">{user_info}</div>
                </div>
            </div>

            <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:10px; background:#f8fafc; border:1px solid #e2e8f0; border-radius:6px; padding:10px 12px; font-size:12px; margin-bottom:12px;">
                <div><b>Service:</b> <span style="text-transform:uppercase;">{escape(tkt.service_type or 'VTU')}</span></div>
                <div><b>Category:</b> {escape(tkt.category or 'Dispute')}</div>
                <div><b>Reference:</b> <code>{escape(tkt.reference or 'None')}</code></div>
                <div><b>Target / Recipient:</b> <code>{escape(tkt.recipient or 'None')}</code></div>
            </div>

            <div style="font-size:13px; color:#1e293b; line-height:1.5; background:#fff; border:1px solid #e5e7eb; border-radius:6px; padding:10px 12px;">
                <b>Customer Complaint:</b><br>
                {escape(tkt.description)}
            </div>

            {notes_html}
            {action_forms}
        </div>
        """

    flash_banner = ""
    if flash_msg:
        flash_banner = f"""
        <div style="background:#dcfce7; border:1px solid #86efac; color:#15803d; padding:12px 16px; border-radius:8px; margin-bottom:16px; font-size:13px; font-weight:600; display:flex; align-items:center; gap:8px;">
            <span>✓</span> {escape(flash_msg)}
        </div>
        """

    content = f"""
    {flash_banner}
    <div style="background: #0f172a; border: 1px solid #1e293b; border-radius: 12px; padding: 14px 18px; margin-bottom: 20px; display: flex; align-items: center; justify-content: space-between; gap: 16px; flex-wrap: wrap;">
        <div style="display: flex; align-items: center; gap: 12px;">
            <span style="font-size: 26px;">🛡️</span>
            <div>
                <div style="font-weight: 700; color: #f8fafc; font-size: 14px;">Official WhatsApp Support Desk Relay</div>
                <div style="color: #94a3b8; font-size: 12px;">Replies sent here are delivered straight to the user's WhatsApp from the official business line. Your personal phone number is never exposed.</div>
            </div>
        </div>
        <div>
            <span style="background: rgba(16, 185, 129, 0.15); color: #10b981; border: 1px solid rgba(16, 185, 129, 0.3); padding: 4px 10px; border-radius: 6px; font-size: 11px; font-weight: 700;">● Cloud API Active</span>
        </div>
    </div>

    <div class="section-grid" style="margin-bottom:20px;">
        <div class="card"><h3>Total Tickets</h3><p>{total_count}</p></div>
        <div class="card" style="border-top:3px solid #f59e0b;"><h3>Open / Action Required</h3><p style="color:#d97706;">{open_count}</p></div>
        <div class="card" style="border-top:3px solid #0284c7;"><h3>In Progress</h3><p style="color:#0284c7;">{in_progress_count}</p></div>
        <div class="card" style="border-top:3px solid #10b981;"><h3>Resolved</h3><p style="color:#10b981;">{resolved_count}</p></div>
    </div>

    <div style="display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:16px;">
        <div style="display:flex; gap:6px; flex-wrap:wrap;">
            <a href="/admin/support?status=ALL" style="padding:6px 12px; border-radius:6px; font-size:12px; font-weight:700; text-decoration:none; background:{'#0f172a' if status_filter == 'ALL' else '#f1f5f9'}; color:{'#fff' if status_filter == 'ALL' else '#475569'};">All ({total_count})</a>
            <a href="/admin/support?status=OPEN" style="padding:6px 12px; border-radius:6px; font-size:12px; font-weight:700; text-decoration:none; background:{'#0f172a' if status_filter == 'OPEN' else '#fef3c7'}; color:{'#fff' if status_filter == 'OPEN' else '#b45309'};">Open ({open_count})</a>
            <a href="/admin/support?status=IN_PROGRESS" style="padding:6px 12px; border-radius:6px; font-size:12px; font-weight:700; text-decoration:none; background:{'#0f172a' if status_filter == 'IN_PROGRESS' else '#e0f2fe'}; color:{'#fff' if status_filter == 'IN_PROGRESS' else '#0369a1'};">In Progress ({in_progress_count})</a>
            <a href="/admin/support?status=RESOLVED" style="padding:6px 12px; border-radius:6px; font-size:12px; font-weight:700; text-decoration:none; background:{'#0f172a' if status_filter == 'RESOLVED' else '#dcfce7'}; color:{'#fff' if status_filter == 'RESOLVED' else '#15803d'};">Resolved ({resolved_count})</a>
        </div>
        <form method="GET" action="/admin/support" style="display:flex; gap:8px;">
            <input type="hidden" name="status" value="{escape(status_filter)}">
            <input type="text" name="q" placeholder="Search phone, ticket, ref..." value="{escape(search_query)}" style="padding:6px 10px; border:1px solid #cbd5e1; border-radius:6px; font-size:12px; width:200px;">
            <button type="submit" style="padding:6px 12px; border-radius:6px; font-size:12px; font-weight:600; cursor:pointer;">Search</button>
        </form>
    </div>

    <div>
        {ticket_cards if ticket_cards else '<div style="background:#fff; border:1px solid #e2e8f0; border-radius:8px; padding:32px; text-align:center; color:#64748b;">No support tickets found for this filter.</div>'}
    </div>
    """

    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=content, active_page="support")


@app.route("/admin/support/reply/<ticket_code>", methods=["POST"])
def admin_support_reply(ticket_code):
    auth_error = require_admin_auth()
    if auth_error:
        return auth_error

    validate_csrf_token()

    ticket = SupportTicket.query.filter_by(ticket_code=ticket_code).first()
    if not ticket:
        abort(404)

    reply_text = request.form.get("reply_text", "").strip()
    if not reply_text:
        return redirect(url_for("admin_support", msg="Reply text cannot be empty"))

    # Deliver directly to customer's WhatsApp chat
    wa_message = (
        f"🎫 *WAJ VTU Support Desk*\n"
        f"Ticket: *{ticket.ticket_code}*\n"
        f"Ref: {ticket.reference or 'N/A'}\n\n"
        f"{reply_text}\n\n"
        f"🛡️ *Security Reminder:* WAJ VTU staff will NEVER ask for your Transaction PIN, password, or bank OTP."
    )
    send_whatsapp_message(ticket.customer_phone, wa_message)

    # Record in ticket notes & set status to IN_PROGRESS
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    admin_name = session.get("username", "Admin")
    reply_entry = f"[{now_str} {admin_name}]: {reply_text}"
    ticket.admin_notes = (ticket.admin_notes + "\n\n" + reply_entry) if ticket.admin_notes else reply_entry
    ticket.status = "IN_PROGRESS"
    ticket.updated_at = datetime.now(timezone.utc)
    db.session.commit()

    record_admin_audit(admin_name, "support_reply", True, f"Sent WhatsApp reply to {ticket.customer_phone} for {ticket.ticket_code}")

    return redirect(url_for("admin_support", msg=f"Reply sent to {ticket.customer_phone} via WhatsApp successfully"))


@app.route("/admin/support/resolve/<ticket_code>", methods=["POST"])
def admin_support_resolve(ticket_code):
    auth_error = require_admin_auth()
    if auth_error:
        return auth_error

    validate_csrf_token()

    ticket = SupportTicket.query.filter_by(ticket_code=ticket_code).first()
    if not ticket:
        abort(404)

    ticket.status = "RESOLVED"
    ticket.resolved_at = datetime.now(timezone.utc)
    ticket.updated_at = datetime.now(timezone.utc)

    # Un-escalate customer user record so AI assistant resumes
    clean_phone = ticket.customer_phone.replace("+", "").strip()
    user = ticket.user
    if not user:
        user = User.query.filter((User.phone == clean_phone) | (User.whatsapp_id == clean_phone)).first()
        if not user and len(clean_phone) == 11 and clean_phone.startswith("0"):
            intl = "234" + clean_phone[1:]
            user = User.query.filter((User.phone == intl) | (User.whatsapp_id == intl)).first()
    if user:
        user.is_escalated = False

    # Notify customer on WhatsApp
    wa_message = (
        f"✅ *WAJ VTU Support — Issue Resolved*\n"
        f"Ticket: *{ticket.ticket_code}*\n\n"
        f"Your dispute has been marked as resolved by our administration team.\n"
        f"Your automated WhatsApp assistant is now fully active! If you need any further assistance, simply send us a message here.\n\n"
        f"Thank you for choosing WAJ VTU!"
    )
    send_whatsapp_message(ticket.customer_phone, wa_message)

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    admin_name = session.get("username", "Admin")
    resolve_entry = f"[{now_str} {admin_name}]: Ticket marked as RESOLVED and AI un-escalated."
    ticket.admin_notes = (ticket.admin_notes + "\n\n" + resolve_entry) if ticket.admin_notes else resolve_entry
    db.session.commit()

    record_admin_audit(admin_name, "support_resolve", True, f"Resolved ticket {ticket.ticket_code} for {ticket.customer_phone}")

    return redirect(url_for("admin_support", msg=f"Ticket {ticket.ticket_code} marked as resolved and customer notified on WhatsApp"))


@app.route("/admin/security")
def admin_security():
    auth_error = require_admin_auth()
    if auth_error:
        return auth_error

    wallet_adjustments = Transaction.query.filter_by(type='WALLET_ADJUSTMENT').count()
    total_transactions = Transaction.query.count()
    recent_audits = AdminAuditLog.query.order_by(AdminAuditLog.created_at.desc()).limit(10).all()
    rows = f"""
    <tr><td>Admin login activity</td><td>{AdminAuditLog.query.filter_by(action='login').count()} recorded logins</td><td>Enabled</td></tr>
    <tr><td>Manual wallet adjustments</td><td>{wallet_adjustments}</td><td>Auditable</td></tr>
    <tr><td>Transaction status changes</td><td>{total_transactions}</td><td>Recorded</td></tr>
    <tr><td>Pricing updates</td><td>{ServiceMarkup.query.count()}</td><td>Controlled</td></tr>
    """
    audit_rows = "".join(
        f"""
        <tr>
            <td>{escape(log.username)}</td>
            <td>{escape(log.action)}</td>
            <td>{'Success' if log.success else 'Failure'}</td>
            <td>{escape(log.reason or '')}</td>
            <td>{escape(log.ip_address or '')}</td>
            <td>{escape(format_admin_datetime(log.created_at))}</td>
        </tr>
        """ for log in recent_audits
    )

    content = f"""
    <h2 style="margin-bottom:15px;">🔐 Security & Audit</h2>
    <table>
        <thead><tr><th>Audit Area</th><th>Count / Scope</th><th>Status</th></tr></thead>
        <tbody>{rows}</tbody>
    </table>

    <h3 style="margin:30px 0 12px;">Recent Admin Audit Trail</h3>
    <table>
        <thead><tr><th>Username</th><th>Action</th><th>Result</th><th>Reason</th><th>IP Address</th><th>Timestamp</th></tr></thead>
        <tbody>{audit_rows if audit_rows else '<tr><td colspan="6" style="text-align:center;">No admin activity logged yet</td></tr>'}</tbody>
    </table>
    """
    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=content, active_page="security")


@app.route("/admin/settings", methods=["GET", "POST"])
def admin_settings():
    auth_error = require_admin_auth()
    if auth_error:
        return auth_error

    if request.method == "POST":
        validate_csrf_token()

    errors = []
    if request.method == "POST":
        for service_type in SERVICE_TYPES:
            raw_value = request.form.get(service_type, "").strip()
            try:
                markup_amount = Decimal(raw_value)
                if not markup_amount.is_finite() or markup_amount < 0:
                    raise ValueError
                markup_amount = markup_amount.quantize(Decimal("0.01"))
            except (InvalidOperation, ValueError):
                errors.append(f"{service_type}: enter a non-negative number.")
                continue

            markup = ServiceMarkup.query.filter_by(service_type=service_type).first()
            if markup is None:
                markup = ServiceMarkup(service_type=service_type)
                db.session.add(markup)
            markup.markup_amount = markup_amount

        payment_tiers = {
            "BELOW_1000": request.form.get("BELOW_1000", "").strip(),
            "1000_TO_20000": request.form.get("1000_TO_20000", "").strip(),
            "ABOVE_20000": request.form.get("ABOVE_20000", "").strip(),
        }
        for label, raw_value in payment_tiers.items():
            try:
                fee_pct = Decimal(raw_value)
                if not fee_pct.is_finite() or fee_pct < 0:
                    raise ValueError
                fee_pct = fee_pct.quantize(Decimal("0.01"))
            except (InvalidOperation, ValueError):
                errors.append(f"{label}: enter a non-negative percentage.")
                continue

            tier = PaymentFeeTier.query.filter_by(label=label).first()
            if tier is None:
                tier = PaymentFeeTier(label=label)
                db.session.add(tier)
            tier.fee_percentage = fee_pct

        if errors:
            record_admin_audit(ADMIN_USERNAME, "pricing_update", False, "; ".join(errors[:5]))
        else:
            db.session.commit()
            record_admin_audit(ADMIN_USERNAME, "pricing_update", True, "Service pricing and fee tiers updated")

    csrf_token = get_csrf_token()
    markups = {markup.service_type: markup.markup_amount for markup in ServiceMarkup.query.all()}
    payment_tiers = {tier.label: tier for tier in PaymentFeeTier.query.order_by(PaymentFeeTier.min_amount.asc()).all()}
    error_html = "".join(f"<p style=\"color:#dc2626; margin-bottom:8px;\">{escape(error)}</p>" for error in errors)
    rows = ""
    for service_type in SERVICE_TYPES:
        value = escape(str(markups.get(service_type, Decimal("0.00"))))
        label = escape(service_type)
        rows += f"""
        <tr>
            <td><b>{label}</b></td>
            <td><input type="number" min="0" step="0.01" name="{label}" value="{value}" required></td>
        </tr>
        """

    fee_rows = ""
    preview_rows = ""
    fee_configs = [
        ("BELOW_1000", "Below 1000", "2.50", Decimal("500.00")),
        ("1000_TO_20000", "More than 1000", "1.50", Decimal("5000.00")),
        ("ABOVE_20000", "More than 20,000", "1.00", Decimal("25000.00")),
    ]
    for label, display_name, default_value, sample_amount in fee_configs:
        value = payment_tiers.get(label, PaymentFeeTier(label=label, fee_percentage=Decimal(default_value))).fee_percentage
        gross_amount = (sample_amount / (Decimal("1.00") - (value / Decimal("100")))).quantize(Decimal("0.01"))
        fee_rows += f"""
        <tr>
            <td><b>{escape(display_name)}</b></td>
            <td><input type="number" min="0" step="0.01" name="{label}" value="{escape(str(value))}" required></td>
        </tr>
        """
        preview_rows += f"""
        <tr>
            <td>{escape(display_name)}</td>
            <td>₦{sample_amount:,.2f}</td>
            <td>₦{gross_amount:,.2f}</td>
        </tr>
        """

    content = f"""
    <h2 style="margin-bottom:20px;">Service Markups</h2>
    {error_html}
    <form method="POST" action="/admin/settings">
        <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
        <table>
            <thead><tr><th>Service</th><th>Markup (%)</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>
        <button type="submit" style="margin-top:15px;">Save Markups</button>
    </form>

    <h2 style="margin:30px 0 20px;">Paystack Fee Tiers</h2>
    <form method="POST" action="/admin/settings">
        <input type="hidden" name="csrf_token" value="{escape(csrf_token)}">
        <table>
            <thead><tr><th>Tier</th><th>Fee %</th></tr></thead>
            <tbody>{fee_rows}</tbody>
        </table>
        <button type="submit" style="margin-top:15px;">Save Paystack Tiers</button>
    </form>

    <h3 style="margin:30px 0 12px;">Fee Preview</h3>
    <table>
        <thead><tr><th>Tier</th><th>Wallet Credit</th><th>Customer Pays</th></tr></thead>
        <tbody>{preview_rows}</tbody>
    </table>
    """
    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=content, active_page="settings")


@app.route("/admin/user/<int:user_id>/fund", methods=["POST"])
def admin_fund_wallet(user_id):
    auth_error = require_admin_auth()
    if auth_error:
        return auth_error
    try:
        validate_csrf_token()
    except Exception:
        record_admin_audit(ADMIN_USERNAME or "unknown", "wallet_adjustment", False, "Invalid admin CSRF token for wallet adjustment")
        raise
    user = User.query.get_or_404(user_id)
    try:
        amount = Decimal(request.form.get("amount", "0"))
    except Exception:
        return redirect(url_for("admin_users"))
    action_type = request.form.get("action_type")

    if amount <= 0 or action_type not in {"CREDIT", "DEBIT"}:
        record_admin_audit(ADMIN_USERNAME, "wallet_adjustment", False, f"Invalid wallet adjustment request for user #{user.id}")
        return redirect(url_for("admin_users"))
    if action_type == "DEBIT" and user.wallet_balance < amount:
        record_admin_audit(ADMIN_USERNAME, "wallet_adjustment", False, f"Insufficient wallet balance for user #{user.id}")
        return redirect(url_for("admin_users"))

    if action_type == "CREDIT":
        user.wallet_balance += amount
        desc = f"Admin Deposit (+₦{amount:,.2f})"
    else:
        user.wallet_balance -= amount
        desc = f"Admin Deduction (-₦{amount:,.2f})"

    tx = Transaction(
        user_id=user.id,
        reference=f"ADM_{uuid.uuid4().hex[:8].upper()}",
        amount=amount,
        type="WALLET_ADJUSTMENT",
        recipient=user.phone,
        status="SUCCESS",
        description=desc
    )
    db.session.add(tx)
    db.session.commit()
    record_admin_audit(ADMIN_USERNAME, "wallet_adjustment", True, f"{action_type} wallet for user #{user.id} by {amount:,.2f}")

    return redirect(url_for("admin_users"))


@app.route("/admin/transactions")
def admin_transactions():
    auth_error = require_admin_auth()
    if auth_error:
        return auth_error

    status_filter = request.args.get("status", "ALL").upper()
    provider_filter = (request.args.get("provider", "ALL") or "ALL").strip().lower()
    search_query = request.args.get("q", "").strip()

    query = Transaction.query
    if status_filter in {"SUCCESS", "PENDING", "FAILED", "REVERSED"}:
        query = query.filter(Transaction.status == status_filter)
    if provider_filter and provider_filter != "all":
        query = query.filter(Transaction.provider_name == provider_filter)
    if search_query:
        term = f"%{search_query}%"
        query = query.join(User, Transaction.user_id == User.id, isouter=True).filter(
            or_(
                Transaction.reference.ilike(term),
                Transaction.type.ilike(term),
                Transaction.recipient.ilike(term),
                Transaction.description.ilike(term),
                Transaction.provider_name.ilike(term),
                Transaction.provider_reference.ilike(term),
                User.phone.ilike(term),
            )
        )

    transactions = query.order_by(Transaction.id.desc()).all()

    tx_rows = ""
    for tx in transactions:
        status_cls = {
            "SUCCESS": "badge-success",
            "PENDING": "badge-pending",
        }.get(tx.status, "badge-failed")
        tx_rows += f"""
        <tr>
            <td><code>{escape(tx.reference)}</code></td>
            <td>{escape(tx.user.phone if tx.user else f'#{tx.user_id}')}</td>
            <td>{escape(tx.type)}</td>
            <td>₦{tx.amount:,.2f}</td>
            <td>{escape(tx.recipient or '')}</td>
            <td>{escape(tx.provider_name or 'unknown')}</td>
            <td>{escape(tx.provider_reference or '')}</td>
            <td class="{escape(status_cls)}">{escape(tx.status)}</td>
            <td>{escape(format_admin_datetime(tx.created_at))}</td>
            <td><small>{escape(tx.description or '')}</small></td>
        </tr>
        """

    statuses = ["ALL", "SUCCESS", "PENDING", "FAILED", "REVERSED"]
    providers = ["ALL", "clubkonnect", "swiftbills", "mock"]
    q_param = quote(search_query)
    provider_param = quote(provider_filter)
    filter_buttons = "".join(
        f'<a href="/admin/transactions?status={status}&provider={provider_param}&q={q_param}" class="admin-filter-pill {"active" if status == status_filter else ""}">{status}</a>'
        for status in statuses
    )

    content = f"""
    <div style="display:flex; justify-content:space-between; align-items:center; gap:12px; flex-wrap:wrap; margin-bottom:20px;">
        <h2 style="margin:0;">💳 System Transaction Ledger</h2>
        <form method="GET" action="/admin/transactions" style="display:flex; gap:8px; flex-wrap:wrap; align-items:center;">
            <input type="text" name="q" placeholder="Search phone, ref, service..." value="{escape(search_query)}" style="min-width:220px;">
            <select name="status">
                <option value="ALL" {'selected' if status_filter == 'ALL' else ''}>All</option>
                <option value="SUCCESS" {'selected' if status_filter == 'SUCCESS' else ''}>Successful</option>
                <option value="PENDING" {'selected' if status_filter == 'PENDING' else ''}>Pending</option>
                <option value="FAILED" {'selected' if status_filter == 'FAILED' else ''}>Failed</option>
                <option value="REVERSED" {'selected' if status_filter == 'REVERSED' else ''}>Reversed</option>
            </select>
            <select name="provider">
                <option value="ALL" {'selected' if provider_filter == 'all' else ''}>All providers</option>
                <option value="clubkonnect" {'selected' if provider_filter == 'clubkonnect' else ''}>ClubKonnect</option>
                <option value="swiftbills" {'selected' if provider_filter == 'swiftbills' else ''}>SwiftBills</option>
                <option value="mock" {'selected' if provider_filter == 'mock' else ''}>Mock</option>
            </select>
            <button type="submit">Apply</button>
        </form>
    </div>
    <div class="admin-filter-bar">{filter_buttons}</div>
    <div class="table-wrap">
        <table>
            <thead>
                <tr><th>Reference</th><th>Customer</th><th>Type</th><th>Amount</th><th>Recipient</th><th>Provider</th><th>Provider Ref</th><th>Status</th><th>Timestamp</th><th>Description</th></tr>
            </thead>
            <tbody>
                {tx_rows if tx_rows else '<tr><td colspan="10" style="text-align:center;">No transactions match the selected filter</td></tr>'}
            </tbody>
        </table>
    </div>
    """

    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=content, active_page="transactions")


@app.route("/admin/ledger")
def admin_ledger():
    auth_error = require_admin_auth()
    if auth_error:
        return auth_error

    ledger_entries = Transaction.query.order_by(Transaction.id.desc()).all()
    ledger_rows = ""
    for tx in ledger_entries:
        direction = "+" if tx.status == "SUCCESS" else "-"
        ledger_rows += f"""
        <tr>
            <td><code>{escape(tx.reference)}</code></td>
            <td>{escape(tx.user.phone if tx.user else f'#{tx.user_id}')}</td>
            <td>{escape(tx.type)}</td>
            <td>{direction}₦{tx.amount:,.2f}</td>
            <td>{escape(tx.status)}</td>
            <td>{escape(tx.description or '')}</td>
            <td>{escape(format_admin_datetime(tx.created_at))}</td>
        </tr>
        """

    content = f"""
    <h2 style="margin-bottom:20px;">📒 Wallet & Ledger Activity</h2>
    <table>
        <thead>
            <tr><th>Reference</th><th>Customer</th><th>Type</th><th>Movement</th><th>Status</th><th>Note</th><th>Timestamp</th></tr>
        </thead>
        <tbody>
            {ledger_rows if ledger_rows else '<tr><td colspan="7" style="text-align:center;">No ledger activity yet</td></tr>'}
        </tbody>
    </table>
    """
    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=content, active_page="ledger")



# ==============================================================================
# --- CRON ROUTES ---
# ==============================================================================

@app.route("/api/cron/run_scheduled_tasks", methods=["GET", "POST"])
def run_scheduled_tasks():
    from models import ScheduledTask
    from datetime import datetime, timedelta
    from chat_agent import execute_tool
    
    now = datetime.utcnow()
    tasks = ScheduledTask.query.filter(ScheduledTask.is_active == True, ScheduledTask.next_run <= now).all()
    
    executed = 0
    for task in tasks:
        user = User.query.get(task.user_id)
        if not user:
            continue
            
        provider_phone = "AdminCron"
        result = execute_tool(app, db, user, provider_phone, task.tool_name, task.tool_kwargs)
        
        # Send WhatsApp message
        message = f"🔄 *Scheduled Task Executed*\nTask: {task.tool_name}\nResult: {result.get('message', 'Processed')}"
        if result.get("status") != "success":
            message = f"⚠️ *Scheduled Task Failed*\nTask: {task.tool_name}\nReason: {result.get('message', 'Failed')}"
            
        send_whatsapp_message(user.whatsapp_id if "@" in user.whatsapp_id else f"{user.phone}@c.us", message)
        
        # Update next run
        if task.frequency == "daily":
            task.next_run = now + timedelta(days=1)
        elif task.frequency == "weekly":
            task.next_run = now + timedelta(days=7)
        else:
            task.next_run = now + timedelta(days=30)
            
        executed += 1
        
    db.session.commit()
    return jsonify({"status": "success", "executed_tasks": executed}), 200


@app.route("/api/cron/daily-deal", methods=["GET", "POST"])
def cron_daily_deal():
    """Generates today's deal of the day idempotently."""
    from services.deal_service import generate_daily_deal, get_lagos_today
    from services.marketing_settings import get_setting_bool
    if not get_setting_bool("DAILY_DEAL_ENABLED", True):
        return jsonify({"status": "disabled", "message": "Daily deals generator is disabled"}), 200

    deal, created = generate_daily_deal(target_date=get_lagos_today())
    return jsonify({
        "status": "success",
        "action": "created" if created else "already_exists",
        "deal_id": deal.id if deal else None,
        "network": deal.network if deal else None,
        "plan": deal.plan_name if deal else None,
        "price": str(deal.price) if deal else None,
    }), 200


@app.route("/api/cron/reengagement-scan", methods=["GET", "POST"])
def cron_reengagement_scan():
    """Performs customer inactivity scan across 7, 14, and 30 day segments."""
    from services.reengagement_service import scan_inactive_segments
    summary = scan_inactive_segments()
    return jsonify({
        "status": "success",
        "counts": summary.get("counts", {})
    }), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=app.config['DEBUG'])