import os
import json
import uuid
import hashlib
import hmac
import secrets
import requests
from urllib.parse import quote
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from markupsafe import escape
from sqlalchemy import inspect, text, func, or_
from flask import Flask, request, jsonify, render_template_string, redirect, url_for, session, abort

# 1. Import db, User, and Transaction directly from models.py
from models import db, User, Transaction, ServiceMarkup, PaymentFeeTier
from wallet_service import generate_payment_link

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
    process_education_pin
)
app = Flask(__name__)
secret_key = os.getenv("SECRET_KEY")
if not secret_key:
    raise RuntimeError("SECRET_KEY environment variable is required for secure sessions")
app.config['SECRET_KEY'] = secret_key

# --- CONFIGURATION ---
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is required")
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
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
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "").strip()

# 2. Bind the single db instance from models.py to app
db.init_app(app)


def ensure_database_schema():
    """Add model columns to existing deployments that predate the current schema."""
    inspector = inspect(db.engine)
    dialect = db.engine.dialect.name
    json_type = "JSONB" if dialect == "postgresql" else "JSON"
    required_columns = {
        "users": {
            "paystack_customer_code": "VARCHAR(100)",
            "dva_account_number": "VARCHAR(20)",
            "dva_bank_name": "VARCHAR(50)",
            "version_id": "INTEGER NOT NULL DEFAULT 1",
        },
        "transactions": {
            "meta_data": json_type,
        },
    }

    for table_name, columns in required_columns.items():
        existing = {column["name"] for column in inspector.get_columns(table_name)}
        for column_name, column_type in columns.items():
            if column_name not in existing:
                db.session.execute(
                    text(f'ALTER TABLE "{table_name}" ADD COLUMN "{column_name}" {column_type}')
                )
    db.session.commit()


SERVICE_TYPES = ("DATA", "AIRTIME", "CABLE", "ELECTRICITY", "BETTING", "EDU")


def seed_service_markups():
    defaults = {service_type: Decimal("50.00") if service_type == "DATA" else Decimal("0.00") for service_type in SERVICE_TYPES}
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


if ALLOW_DB_MUTATIONS:
    with app.app_context():
        db.create_all()
        ensure_database_schema()
        seed_service_markups()
        seed_payment_fee_tiers()

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
    normalized = str(phone_number).strip().replace(" ", "").replace("+", "")
    if normalized.startswith("234"):
        return normalized
    if normalized.startswith("0") and len(normalized) == 11:
        return "234" + normalized[1:]
    return normalized


def get_or_create_user(phone_number):
    normalized_phone = normalize_phone_number(phone_number)
    user = User.query.filter_by(whatsapp_id=normalized_phone).first() or User.query.filter_by(phone=normalized_phone).first()
    if not user:
        if not ALLOW_DB_MUTATIONS:
            return None
        user = User(phone=normalized_phone, whatsapp_id=normalized_phone, wallet_balance=Decimal("0.00"))
        db.session.add(user)
        db.session.commit()
    else:
        if user.whatsapp_id != normalized_phone:
            user.whatsapp_id = normalized_phone
        if user.phone != normalized_phone:
            user.phone = normalized_phone
        db.session.commit()
    return user


def get_markup(service_type):
    markup = ServiceMarkup.query.filter_by(service_type=service_type.upper()).first()
    return Decimal(str(markup.markup_amount)) if markup else Decimal("0.00")


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


def settle_transaction(user, result, amount, transaction_type, recipient, description):
    """Persist a successful provider result or refund the reserved wallet amount."""
    amount = Decimal(str(amount))
    if result.get("status") == "SUCCESS":
        tx = Transaction(
            user_id=user.id,
            reference=result["reference"],
            amount=amount,
            type=transaction_type,
            recipient=recipient,
            status="SUCCESS",
            description=description,
            meta_data=result.get("data", {}),
        )
        db.session.add(tx)
        db.session.commit()
        return True

    user.wallet_balance += amount
    db.session.commit()
    return False


def ensure_deposit_transaction(user, reference, amount, phone, status="PENDING", meta_data=None):
    """Create or update a payment transaction so it appears in dashboards immediately."""
    tx = Transaction.query.filter_by(reference=reference).first()
    if tx is None:
        tx = Transaction(
            user_id=user.id,
            reference=reference,
            amount=Decimal(str(amount or "0.00")).quantize(Decimal("0.01")),
            type="DEPOSIT",
            recipient=phone,
            status=status,
            description="Paystack wallet funding",
            meta_data=meta_data or {},
        )
        db.session.add(tx)
    else:
        tx.user_id = user.id
        tx.amount = Decimal(str(amount or tx.amount)).quantize(Decimal("0.01"))
        tx.recipient = phone or tx.recipient
        tx.status = status
        tx.description = tx.description or "Paystack wallet funding"
        if meta_data is not None:
            tx.meta_data = meta_data
    if isinstance(tx.meta_data, dict):
        tx.meta_data.setdefault("credited", status == "SUCCESS")
    db.session.commit()
    return tx


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


def require_admin_auth():
    auth = request.authorization
    if not ADMIN_USERNAME or not ADMIN_PASSWORD:
        return jsonify({"status": "error", "reason": "Admin credentials are not configured"}), 503
    if not auth or not hmac.compare_digest(auth.username, ADMIN_USERNAME) or not hmac.compare_digest(auth.password, ADMIN_PASSWORD):
        response = jsonify({"status": "error", "reason": "Authentication required"})
        response.status_code = 401
        response.headers["WWW-Authenticate"] = 'Basic realm="WAJ VTU Admin"'
        return response
    return None


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
        abort(403)


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

    ensure_deposit_transaction(user, result["reference"], result.get("net_amount", amount), phone, status="PENDING")
    return jsonify(result), 200


@app.route("/payments/paystack/callback", methods=["GET"])
def paystack_callback():
    reference = request.args.get("reference") or request.args.get("trxref")
    if reference:
        transaction = Transaction.query.filter_by(reference=reference).first()
        if transaction:
            reconcile_deposit_transaction(transaction)
        if transaction and transaction.status == "SUCCESS":
            user_phone = normalize_phone_number((transaction.recipient or "").strip() or (transaction.user.phone if transaction.user else ""))
            whatsapp_link = f"https://wa.me/{user_phone}" if user_phone else "https://wa.me/"
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
            payload = {
                "messaging_product": "whatsapp",
                "to": recipient,
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


@app.route("/", methods=["GET"])
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

    # Meta WhatsApp Cloud API sends payloads under entry -> changes -> value.
    if "entry" in req_data:
        for entry in req_data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages") or []
                if not messages:
                    continue

                first_message = messages[0]
                sender = first_message.get("from") or first_message.get("sender")
                text = ""
                if first_message.get("type") == "text":
                    text = first_message.get("text", {}).get("body", "")
                if sender:
                    req_data = {
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
        return jsonify({"status": "ignored", "reason": "No sender specified"}), 200

    provider_phone = str(chat_id).split("@", 1)[0]

    user = get_or_create_user(chat_id)
    if user is None:
        return jsonify({"status": "ignored", "reason": "User creation is disabled"}), 200

    current_state = user.current_state or STATES["IDLE"]
    session_data = get_user_session_data(user)

    normalized_text = text.strip().lower().replace("*", "").replace("#", "")
    english_aliases = {
        "menu": "MENU",
        "main menu": "MENU",
        "home": "MENU",
        "cancel": "CANCEL",
        "back": "MENU",
        "help": "MENU",
        "buy data": "1",
        "data": "1",
        "data bundle": "1",
        "internet data": "1",
        "buy airtime": "2",
        "airtime": "2",
        "top up balance": "8",
        "top up": "8",
        "fund wallet": "8",
        "deposit": "8",
        "cable tv": "3",
        "tv subscription": "3",
        "dstv": "3",
        "gotv": "3",
        "startimes": "3",
        "electricity": "4",
        "pay electricity": "4",
        "electric bill": "4",
        "light bill": "4",
        "betting": "5",
        "betting top up": "5",
        "bet9ja": "5",
        "sportybet": "5",
        "betking": "5",
        "education": "6",
        "waec": "6",
        "jamb": "6",
        "pins": "6",
        "wallet": "7",
        "check wallet": "7",
        "balance": "7",
        "my balance": "7",
    }
    if normalized_text in english_aliases:
        text = english_aliases[normalized_text]

    def send_main_menu_response():
        main_menu = (
            "WAJ VTU\n"
            "Smart utility services\n\n"
            "Please select a service:\n\n"
            "1. Buy Data\n"
            "2. Buy Airtime\n"
            "3. Cable TV\n"
            "4. Pay Electricity\n"
            "5. Betting Top-up\n"
            "6. Education PINs\n"
            "7. Check Wallet\n"
            "8. Top Up Balance\n\n"
            f"Available balance: ₦{user.wallet_balance:,.2f}\n"
            "Reply with a number from 1 to 8."
        )
        send_whatsapp_message(chat_id, main_menu)

    if text.upper() in ["0", "MENU", "*MENU*", "CANCEL"] or normalized_text in ["menu", "main menu", "cancel", "home", "back", "help"]:
        set_user_session(user, STATES["IDLE"], {})
        send_main_menu_response()
        return jsonify({"status": "ok"}), 200

    if current_state == STATES["IDLE"]:
        if text == "1":
            set_user_session(user, STATES["AWAITING_DATA_NETWORK"], {})
            network_menu = (
                "Select mobile network\n\n"
                "1. MTN\n"
                "2. Airtel\n"
                "3. Glo\n"
                "4. 9mobile\n\n"
                "Reply with 1, 2, 3, or 4."
            )
            send_whatsapp_message(chat_id, network_menu)

        elif text == "2":
            set_user_session(user, STATES["AWAITING_AIRTIME_NETWORK"], {})
            airtime_menu = (
                "Select airtime network\n\n"
                "1. MTN\n"
                "2. Airtel\n"
                "3. Glo\n"
                "4. 9mobile\n\n"
                "Reply with 1, 2, 3, or 4."
            )
            send_whatsapp_message(chat_id, airtime_menu)

        elif text == "3":
            set_user_session(user, STATES["AWAITING_CABLE_PROVIDER"], {})
            send_whatsapp_message(
                chat_id,
                "Select cable provider\n\n"
                "1. DSTV\n"
                "2. GOTV\n"
                "3. STARTIMES\n\n"
                "Reply with 1, 2, or 3."
            )

        elif text == "4":
            set_user_session(user, STATES["AWAITING_ELECTRICITY_DISCO"], {})
            send_whatsapp_message(
                chat_id,
                "💡 *SELECT ELECTRICITY DISTRIBUTOR*\n"
                "────────────────────────\n"
                "1. IKEDC\n"
                "2. EKEDC\n"
                "3. AEDC\n"
                "4. IBEDC\n\n"
                "_Reply with 1, 2, 3, or 4_"
            )

        elif text == "5":
            set_user_session(user, STATES["AWAITING_BETTING_PLATFORM"], {})
            send_whatsapp_message(
                chat_id,
                "⚽ *SELECT BETTING PLATFORM*\n"
                "────────────────────────\n"
                "1. BET9JA\n"
                "2. SPORTYBET\n"
                "3. BETKING\n\n"
                "_Reply with 1, 2, or 3_"
            )

        elif text == "6":
            packages = fetch_education_packages()
            if not packages:
                send_whatsapp_message(
                    chat_id,
                    "❌ Education packages are unavailable right now. Please try again later."
                )
                return jsonify({"status": "error"}), 200
            session_data["education_packages"] = packages
            set_user_session(user, STATES["AWAITING_EDUCATION_PACKAGE"], session_data)
            package_menu = "🎓 *SELECT EDUCATION PIN*\n"
            for index, package in enumerate(packages, start=1):
                package_amount = Decimal(str(package["amount"])) + get_markup("EDU")
                package_menu += f"{index}. {package['name']} - ₦{package_amount:,.2f}\n"
            send_whatsapp_message(chat_id, package_menu + "\n_Reply with the package number_")

        elif text == "7":
            send_whatsapp_message(
                chat_id,
                f"💳 *WALLET BALANCE*\n"
                f"₦{user.wallet_balance:,.2f}\n\n"
                "Type *MENU* to view more services."
            )

        elif text == "8":
            set_user_session(user, STATES["AWAITING_TOPUP_AMOUNT"], {})
            send_whatsapp_message(
                chat_id,
                "💰 *TOP UP WALLET*\n"
                "────────────────────────\n"
                "Enter the amount you want to add to your wallet in Naira.\n"
                "Minimum amount: ₦100"
            )

        else:
            send_main_menu_response()

    elif current_state == STATES["AWAITING_TOPUP_AMOUNT"]:
        try:
            amount = Decimal(text.replace(",", "")).quantize(Decimal("0.01"))
        except Exception:
            amount = Decimal("0")

        if amount < Decimal("100.00"):
            send_whatsapp_message(chat_id, "❌ Please enter a valid amount of at least ₦100, for example: 1000")
        else:
            session_data["topup_amount"] = str(amount)
            set_user_session(user, STATES["AWAITING_TOPUP_EMAIL"], session_data)
            send_whatsapp_message(
                chat_id,
                "📧 Enter your email address to continue with the Paystack payment."
            )

    elif current_state == STATES["AWAITING_TOPUP_EMAIL"]:
        if "@" not in text or "." not in text.rsplit("@", 1)[-1]:
            send_whatsapp_message(chat_id, "❌ Please enter a valid email address.")
        else:
            amount = Decimal(session_data.get("topup_amount", "0"))
            result = generate_payment_link(text, amount, provider_phone, pass_fee_to_user=True)
            set_user_session(user, STATES["IDLE"], {})
            if result.get("status") == "SUCCESS":
                send_whatsapp_message(
                    chat_id,
                    f"✅ *PAYMENT LINK READY*\n"
                    f"Amount to credit: ₦{amount:,.2f}\n"
                    f"Amount to pay: ₦{result['gross_amount']:,.2f}\n\n"
                    f"Complete your payment here:\n{result['payment_url']}\n\n"
                    "Your wallet will be credited automatically after payment."
                )
            else:
                send_whatsapp_message(
                    chat_id,
                    f"❌ Unable to create the payment link: {result.get('reason', 'Please try again later.')}"
                )

    elif current_state == STATES["AWAITING_DATA_NETWORK"]:
        networks = {"1": "MTN", "2": "AIRTEL", "3": "GLO", "4": "9MOBILE"}
        if text not in networks:
            send_whatsapp_message(chat_id, "❌ Invalid selection. Please choose a valid network: 1 for MTN, 2 for AIRTEL, 3 for GLO, 4 for 9MOBILE.")
        else:
            network_name = networks[text]
            session_data["network"] = network_name
            send_whatsapp_message(chat_id, f"⏳ Loading {network_name} data plans for you...")

            variations = fetch_data_variations(network_name)
            if not variations:
                send_whatsapp_message(chat_id, "❌ Plans are unavailable right now. Type *MENU* to return to the main menu.")
                set_user_session(user, STATES["IDLE"], {})
                return jsonify({"status": "error"}), 200

            session_data["categorized_plans"] = categorize_data_plans(variations)

            set_user_session(user, STATES["AWAITING_DATA_CATEGORY"], session_data)
            category_menu = (
                f"📶 *{network_name} DATA CATEGORIES*\n"
                "────────────────────────\n"
                "1. ⚡ Daily Plans\n"
                "2. ⚡ 2-Day Plans\n"
                "3. 📅 Weekly Plans\n"
                "4. 🗓️ Monthly / SME / Corporate\n"
                "5. 🎉 Awoof & Promo Deals\n"
                "6. 📦 View All Plans\n\n"
                "_Reply with a category number from 1 to 6_"
            )
            send_whatsapp_message(chat_id, category_menu)

    elif current_state == STATES["AWAITING_DATA_CATEGORY"]:
        cat_map = {
            "1": "DAILY",
            "2": "TWO_DAYS",
            "3": "WEEKLY",
            "4": "MONTHLY",
            "5": "AWOOF",
            "6": "ALL"
        }
        if text not in cat_map:
            send_whatsapp_message(chat_id, "❌ Invalid option. Please choose a category from 1 to 6.")
        else:
            selected_cat = cat_map[text]
            network_name = session_data["network"]
            categorized_plans = session_data.get("categorized_plans", {})

            if selected_cat == "ALL":
                filtered_plans = [p for cat in categorized_plans.values() for p in cat]
            else:
                filtered_plans = categorized_plans.get(selected_cat, [])

            if not filtered_plans:
                send_whatsapp_message(chat_id, f"ℹ️ No plans found in this category. Showing all available {network_name} plans instead.")
                filtered_plans = [p for cat in categorized_plans.values() for p in cat]

            plan_menu = f"📊 *SELECT {network_name} DATA PLAN*\n────────────────────────\n"
            plans_map = {}
            for idx, plan in enumerate(filtered_plans, start=1):
                name = plan.get("name")
                cost = Decimal(str(plan.get("variation_amount"))) + get_markup("DATA")
                code = plan.get("variation_code")
                plans_map[str(idx)] = {"code": code, "amount": str(cost), "name": name}
                plan_menu += f"{idx}. {name} - ₦{cost:,.2f}\n"

            plan_menu += "\n_Reply with the plan number you want, e.g. 1_"
            session_data["plans_map"] = plans_map
            set_user_session(user, STATES["AWAITING_DATA_PLAN"], session_data)
            send_whatsapp_message(chat_id, plan_menu)

    elif current_state == STATES["AWAITING_DATA_PLAN"]:
        plans_map = session_data.get("plans_map", {})
        if text not in plans_map:
            send_whatsapp_message(chat_id, "❌ Invalid option. Please select a valid plan number from the list.")
        else:
            session_data["selected_plan"] = plans_map[text]
            set_user_session(user, STATES["AWAITING_DATA_NUMBER"], session_data)
            send_whatsapp_message(
                chat_id,
                f"📞 Enter the 11-digit phone number to receive *{plans_map[text]['name']}* for WAJ VTU:"
            )

    elif current_state == STATES["AWAITING_DATA_NUMBER"]:
        if len(text) != 11 or not text.isdigit():
            send_whatsapp_message(chat_id, "❌ Invalid phone number. Please enter a valid 11-digit phone number.")
        else:
            plan = session_data.get("selected_plan")
            if not plan:
                set_user_session(user, STATES["IDLE"], {})
                send_whatsapp_message(chat_id, "❌ Your plan selection expired. Type *MENU* and start again.")
                return jsonify({"status": "expired_session"}), 200

            recipient_phone = text
            network = session_data["network"]
            cost_decimal = Decimal(str(plan["amount"]))

            if user.wallet_balance < cost_decimal:
                send_whatsapp_message(
                    chat_id,
                    f"❌ Insufficient wallet balance!\n"
                    f"Plan Cost: ₦{cost_decimal:,.2f} | Balance: ₦{user.wallet_balance:,.2f}\n"
                    f"Type *MENU* to return to WAJ VTU services."
                )
                set_user_session(user, STATES["IDLE"], {})
                return jsonify({"status": "insufficient_balance"}), 200

            user.wallet_balance -= cost_decimal
            db.session.commit()

            send_whatsapp_message(chat_id, f"⏳ Processing {plan['name']} for {recipient_phone} via WAJ VTU...")
            result = process_data_purchase(recipient_phone, network, plan["code"], float(plan["amount"]))

            if result.get("status") == "SUCCESS":
                tx = Transaction(
                    user_id=user.id,
                    reference=result['reference'],
                    amount=cost_decimal,
                    type='DATA',
                    recipient=recipient_phone,
                    status='SUCCESS',
                    description=f"{network} {plan['name']} to {recipient_phone}"
                )
                db.session.add(tx)
                db.session.commit()
                send_whatsapp_message(
                    chat_id,
                    f"✅ *WAJ VTU DATA PURCHASE SUCCESSFUL!*\n"
                    f"────────────────────\n"
                    f"• *Ref:* `{result['reference']}`\n"
                    f"• *New Balance:* ₦{user.wallet_balance:,.2f}\n\n"
                    f"Thank you for choosing WAJ VTU.\n"
                    f"Type *MENU* for more services."
                )
            else:
                user.wallet_balance += cost_decimal
                db.session.commit()
                send_whatsapp_message(chat_id, f"❌ Purchase failed: {result.get('reason')}. Your wallet has been refunded.")

            set_user_session(user, STATES["IDLE"], {})

    elif current_state == STATES["AWAITING_AIRTIME_NETWORK"]:
        networks = {"1": "MTN", "2": "AIRTEL", "3": "GLO", "4": "9MOBILE"}
        if text not in networks:
            send_whatsapp_message(chat_id, "❌ Invalid selection. Reply with 1, 2, 3, or 4.")
        else:
            session_data["network"] = networks[text]
            set_user_session(user, STATES["AWAITING_AIRTIME_AMOUNT"], session_data)
            send_whatsapp_message(chat_id, f"💵 Enter the airtime amount for *{networks[text]}* (e.g. 500):")

    elif current_state == STATES["AWAITING_AIRTIME_AMOUNT"]:
        if not text.isdigit() or int(text) < 50:
            send_whatsapp_message(chat_id, "❌ Enter a valid amount of at least ₦50.")
        else:
            session_data["amount"] = text
            set_user_session(user, STATES["AWAITING_AIRTIME_NUMBER"], session_data)
            send_whatsapp_message(chat_id, f"📞 Enter the recipient 11-digit phone number for ₦{text} airtime:")

    elif current_state == STATES["AWAITING_AIRTIME_NUMBER"]:
        if len(text) != 11 or not text.isdigit():
            send_whatsapp_message(chat_id, "❌ Enter a valid 11-digit phone number.")
        else:
            recipient_phone = text
            amount_decimal = Decimal(session_data["amount"])
            charge_amount = amount_decimal + get_markup("AIRTIME")
            network = session_data["network"]

            if user.wallet_balance < charge_amount:
                send_whatsapp_message(
                    chat_id,
                    f"❌ Insufficient balance! Required: ₦{charge_amount:,.2f} | Balance: ₦{user.wallet_balance:,.2f}"
                )
                set_user_session(user, STATES["IDLE"], {})
                return jsonify({"status": "insufficient_balance"}), 200

            user.wallet_balance -= charge_amount
            db.session.commit()

            send_whatsapp_message(chat_id, f"⏳ Processing ₦{amount_decimal} {network} airtime via WAJ VTU...")
            result = process_airtime_purchase(recipient_phone, network, float(amount_decimal))

            if result.get("status") == "SUCCESS":
                tx = Transaction(
                    user_id=user.id,
                    reference=result['reference'],
                    amount=charge_amount,
                    type='AIRTIME',
                    recipient=recipient_phone,
                    status='SUCCESS',
                    description=f"{network} Airtime to {recipient_phone}"
                )
                db.session.add(tx)
                db.session.commit()
                send_whatsapp_message(
                    chat_id,
                    f"✅ *WAJ VTU AIRTIME SUCCESSFUL!*\n"
                    f"────────────────────\n"
                    f"• *Ref:* `{result['reference']}`\n"
                    f"• *New Balance:* ₦{user.wallet_balance:,.2f}\n\n"
                    f"Thank you for choosing WAJ VTU.\n"
                    f"Type *MENU* for more services."
                )
            else:
                user.wallet_balance += charge_amount
                db.session.commit()
                send_whatsapp_message(chat_id, f"❌ Purchase failed: {result.get('reason')}. Your wallet has been refunded.")

            set_user_session(user, STATES["IDLE"], {})

    elif current_state == STATES["AWAITING_CABLE_PROVIDER"]:
        providers = {"1": "DSTV", "2": "GOTV", "3": "STARTIMES"}
        if text not in providers:
            send_whatsapp_message(chat_id, "❌ Reply with 1 for DSTV, 2 for GOTV, or 3 for STARTIMES.")
        else:
            session_data["cable_provider"] = providers[text]
            set_user_session(user, STATES["AWAITING_CABLE_CARD"], session_data)
            send_whatsapp_message(chat_id, f"Enter your {providers[text]} smartcard / IUC number:")

    elif current_state == STATES["AWAITING_CABLE_CARD"]:
        if not text.isdigit() or len(text) < 8:
            send_whatsapp_message(chat_id, "❌ Enter a valid smartcard / IUC number.")
        else:
            provider = session_data["cable_provider"]
            send_whatsapp_message(chat_id, "⏳ Verifying your cable account...")
            verification = verify_smartcard(provider, text)
            if not verification.get("valid"):
                send_whatsapp_message(chat_id, f"❌ {verification.get('message', 'Account verification failed')}")
                set_user_session(user, STATES["IDLE"], {})
            else:
                session_data["smartcard"] = text
                session_data["cable_plans"] = fetch_cable_plans(provider)
                set_user_session(user, STATES["AWAITING_CABLE_PLAN"], session_data)
                plan_menu = f"📺 *{provider} PLANS*\n"
                for index, plan in enumerate(session_data["cable_plans"], start=1):
                    plan_amount = Decimal(str(plan["amount"])) + get_markup("CABLE")
                    plan_menu += f"{index}. {plan['name']} - ₦{plan_amount:,.2f}\n"
                send_whatsapp_message(chat_id, plan_menu + "\n_Reply with the plan number you want._")

    elif current_state == STATES["AWAITING_CABLE_PLAN"]:
        plans = session_data.get("cable_plans", [])
        if not text.isdigit() or not 1 <= int(text) <= len(plans):
            send_whatsapp_message(chat_id, "❌ Please select a valid cable plan number.")
        else:
            plan = plans[int(text) - 1]
            amount = Decimal(str(plan["amount"])) + get_markup("CABLE")
            if user.wallet_balance < amount:
                send_whatsapp_message(chat_id, "❌ Insufficient wallet balance.")
                set_user_session(user, STATES["IDLE"], {})
            else:
                user.wallet_balance -= amount
                db.session.commit()
                provider = session_data["cable_provider"]
                send_whatsapp_message(chat_id, "⏳ Processing your cable subscription via WAJ VTU...")
                result = process_cable_tv(provider, session_data["smartcard"], plan["code"], float(amount), provider_phone)
                success = settle_transaction(user, result, amount, "CABLE", session_data["smartcard"], f"{provider} {plan['name']}")
                if success:
                    send_whatsapp_message(
                        chat_id,
                        f"✅ *WAJ VTU CABLE SUBSCRIPTION SUCCESSFUL!*\n"
                        f"Ref: {result['reference']}\n"
                        f"New Balance: ₦{user.wallet_balance:,.2f}\n\n"
                        f"Thank you for choosing WAJ VTU."
                    )
                else:
                    send_whatsapp_message(chat_id, f"❌ {result.get('reason', 'Cable subscription failed')}. Your wallet has been refunded.")
                set_user_session(user, STATES["IDLE"], {})

    elif current_state == STATES["AWAITING_ELECTRICITY_DISCO"]:
        discos = {"1": "IKEDC", "2": "EKEDC", "3": "AEDC", "4": "IBEDC"}
        if text not in discos:
            send_whatsapp_message(chat_id, "❌ Reply with a valid electricity provider number.")
        else:
            session_data["disco"] = discos[text]
            set_user_session(user, STATES["AWAITING_ELECTRICITY_METER_TYPE"], session_data)
            send_whatsapp_message(chat_id, "Select meter type:\n1. Prepaid\n2. Postpaid")

    elif current_state == STATES["AWAITING_ELECTRICITY_METER_TYPE"]:
        meter_types = {"1": "PREPAID", "2": "POSTPAID"}
        if text not in meter_types:
            send_whatsapp_message(chat_id, "❌ Reply 1 for Prepaid or 2 for Postpaid.")
        else:
            session_data["meter_type"] = meter_types[text]
            set_user_session(user, STATES["AWAITING_ELECTRICITY_METER"], session_data)
            send_whatsapp_message(chat_id, "Enter your meter number:")

    elif current_state == STATES["AWAITING_ELECTRICITY_METER"]:
        if not text.isdigit() or len(text) < 8:
            send_whatsapp_message(chat_id, "❌ Enter a valid meter number.")
        else:
            send_whatsapp_message(chat_id, "⏳ Verifying your meter...")
            verification = verify_meter(session_data["disco"], text, session_data["meter_type"])
            if not verification.get("valid"):
                send_whatsapp_message(chat_id, f"❌ {verification.get('message', 'Meter verification failed')}")
                set_user_session(user, STATES["IDLE"], {})
            else:
                session_data["meter_number"] = text
                set_user_session(user, STATES["AWAITING_ELECTRICITY_AMOUNT"], session_data)
                send_whatsapp_message(chat_id, "Enter the electricity amount (minimum ₦500):")

    elif current_state == STATES["AWAITING_ELECTRICITY_AMOUNT"]:
        if not text.isdigit() or int(text) < 500:
            send_whatsapp_message(chat_id, "❌ Enter a valid amount of at least ₦500.")
        else:
            amount = Decimal(text)
            charge_amount = amount + get_markup("ELECTRICITY")
            if user.wallet_balance < charge_amount:
                send_whatsapp_message(chat_id, "❌ Insufficient wallet balance.")
                set_user_session(user, STATES["IDLE"], {})
            else:
                user.wallet_balance -= charge_amount
                db.session.commit()
                send_whatsapp_message(chat_id, "⏳ Processing your electricity payment via WAJ VTU...")
                result = process_electricity_payment(session_data["disco"], session_data["meter_number"], session_data["meter_type"], float(amount), provider_phone)
                success = settle_transaction(user, result, charge_amount, "ELECTRICITY", session_data["meter_number"], f"{session_data['disco']} electricity payment")
                if success:
                    send_whatsapp_message(
                        chat_id,
                        f"✅ *WAJ VTU ELECTRICITY PAYMENT SUCCESSFUL!*\n"
                        f"Ref: {result['reference']}\n"
                        f"Token: {result.get('token', 'Check provider account')}\n"
                        f"New Balance: ₦{user.wallet_balance:,.2f}\n\n"
                        f"Thank you for choosing WAJ VTU."
                    )
                else:
                    send_whatsapp_message(chat_id, f"❌ {result.get('reason', 'Electricity payment failed')}. Your wallet has been refunded.")
                set_user_session(user, STATES["IDLE"], {})

    elif current_state == STATES["AWAITING_BETTING_PLATFORM"]:
        platforms = {"1": "BET9JA", "2": "SPORTYBET", "3": "BETKING"}
        if text not in platforms:
            send_whatsapp_message(chat_id, "❌ Reply with a valid betting platform number.")
        else:
            session_data["platform"] = platforms[text]
            set_user_session(user, STATES["AWAITING_BETTING_ACCOUNT"], session_data)
            send_whatsapp_message(chat_id, "Enter your betting account ID:")

    elif current_state == STATES["AWAITING_BETTING_ACCOUNT"]:
        if len(text) < 4 or len(text) > 30:
            send_whatsapp_message(chat_id, "❌ Enter a valid betting account ID.")
        else:
            send_whatsapp_message(chat_id, "⏳ Verifying your betting account...")
            verification = verify_betting_account(session_data["platform"], text)
            if not verification.get("valid"):
                send_whatsapp_message(chat_id, f"❌ {verification.get('message', 'Betting account verification failed')}")
                set_user_session(user, STATES["IDLE"], {})
            else:
                session_data["betting_account"] = text
                set_user_session(user, STATES["AWAITING_BETTING_AMOUNT"], session_data)
                send_whatsapp_message(chat_id, "Enter top-up amount (minimum ₦100):")

    elif current_state == STATES["AWAITING_BETTING_AMOUNT"]:
        if not text.isdigit() or int(text) < 100:
            send_whatsapp_message(chat_id, "❌ Enter a valid amount of at least ₦100.")
        else:
            amount = Decimal(text)
            charge_amount = amount + get_markup("BETTING")
            if user.wallet_balance < charge_amount:
                send_whatsapp_message(chat_id, "❌ Insufficient wallet balance.")
                set_user_session(user, STATES["IDLE"], {})
            else:
                user.wallet_balance -= charge_amount
                db.session.commit()
                send_whatsapp_message(chat_id, "⏳ Processing your betting top-up via WAJ VTU...")
                result = process_betting_topup(session_data["platform"], session_data["betting_account"], float(amount), provider_phone)
                success = settle_transaction(user, result, charge_amount, "BETTING", session_data["betting_account"], f"{session_data['platform']} betting top-up")
                if success:
                    send_whatsapp_message(
                        chat_id,
                        f"✅ *WAJ VTU BETTING TOP-UP SUCCESSFUL!*\n"
                        f"Ref: {result['reference']}\n"
                        f"New Balance: ₦{user.wallet_balance:,.2f}\n\n"
                        f"Thank you for choosing WAJ VTU."
                    )
                else:
                    send_whatsapp_message(chat_id, f"❌ {result.get('reason', 'Betting top-up failed')}. Your wallet has been refunded.")
                set_user_session(user, STATES["IDLE"], {})

    elif current_state == STATES["AWAITING_EDUCATION_PACKAGE"]:
        packages = session_data.get("education_packages", [])
        if not text.isdigit() or not 1 <= int(text) <= len(packages):
            send_whatsapp_message(chat_id, "❌ Select a valid education package number:")
        else:
            session_data["education_package"] = packages[int(text) - 1]
            set_user_session(user, STATES["AWAITING_EDUCATION_QUANTITY"], session_data)
            send_whatsapp_message(chat_id, "How many PINs do you want? Enter a number from 1 to 5.")

    elif current_state == STATES["AWAITING_EDUCATION_QUANTITY"]:
        if not text.isdigit() or not 1 <= int(text) <= 5:
            send_whatsapp_message(chat_id, "❌ Enter a quantity from 1 to 5:")
        else:
            quantity = int(text)
            package = session_data["education_package"]
            amount = (Decimal(str(package["amount"])) + get_markup("EDU")) * quantity
            if user.wallet_balance < amount:
                send_whatsapp_message(chat_id, "❌ Insufficient wallet balance.")
                set_user_session(user, STATES["IDLE"], {})
            else:
                user.wallet_balance -= amount
                db.session.commit()
                send_whatsapp_message(chat_id, "⏳ Processing your education PIN order...")
                result = process_education_pin(package["code"], quantity, provider_phone)
                success = settle_transaction(user, result, amount, "EDU", chat_id, f"{package['name']} x{quantity}")
                if success:
                    pins = "\n".join(str(pin) for pin in result.get("pins", []))
                    send_whatsapp_message(
                        chat_id,
                        f"✅ *WAJ VTU EDUCATION PIN ORDER SUCCESSFUL!*\n"
                        f"Ref: {result['reference']}\n"
                        f"PINs:\n{pins}\n"
                        f"New Balance: ₦{user.wallet_balance:,.2f}\n\n"
                        f"Thank you for choosing WAJ VTU."
                    )
                else:
                    send_whatsapp_message(chat_id, f"❌ {result.get('reason', 'Education PIN order failed')}. Wallet refunded.")
                set_user_session(user, STATES["IDLE"], {})

    return jsonify({"status": "success"}), 200


# ==============================================================================
# --- ADMIN CONTROL PANEL ROUTES ---
# ==============================================================================

ADMIN_BASE_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>VTU Admin Control Panel</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
        body { background-color: #f1f5f9; color: #1e293b; }

        .navbar {
            background-color: #0f172a;
            padding: 0 30px;
            min-height: 60px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
        }
        .navbar .brand { color: #ffffff; font-size: 18px; font-weight: bold; text-decoration: none; }
        .navbar .nav-links { display: flex; flex-wrap: wrap; gap: 10px; list-style: none; margin: 0; padding: 0; }
        .navbar .nav-links a {
            color: #94a3b8;
            text-decoration: none;
            padding: 8px 16px;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 500;
            display: inline-block;
        }
        .navbar .nav-links a:hover, .navbar .nav-links a.active { background-color: #2563eb; color: #ffffff; }
        .nav-toggle {
            display: none;
            background: transparent;
            border: 1px solid rgba(255,255,255,0.25);
            color: white;
            font-size: 18px;
            padding: 8px 10px;
            border-radius: 6px;
            cursor: pointer;
        }

        .container { max-width: 1200px; margin: 30px auto; padding: 0 20px; }
        .section-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 18px; margin-bottom: 24px; }
        .card-grid { display: flex; gap: 20px; margin-bottom: 25px; }
        .card { background: white; padding: 20px; border-radius: 8px; flex: 1; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        .card h3 { font-size: 12px; color: #64748b; text-transform: uppercase; margin-bottom: 8px; }
        .card p { font-size: 24px; font-weight: bold; color: #0f172a; }

        table { width: 100%; background: white; border-collapse: collapse; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        th, td { padding: 12px 16px; text-align: left; border-bottom: 1px solid #e2e8f0; font-size: 14px; }
        th { background: #1e293b; color: white; font-weight: 600; }

        input, select, button { padding: 7px 12px; border-radius: 6px; border: 1px solid #cbd5e1; font-size: 13px; }
        button { background-color: #2563eb; color: white; border: none; font-weight: 600; cursor: pointer; }
        button:hover { background-color: #1d4ed8; }

        .badge-success { color: #16a34a; font-weight: bold; }
        .badge-failed { color: #dc2626; font-weight: bold; }

        @media (max-width: 768px) {
            .navbar {
                padding: 12px 16px;
                flex-wrap: wrap;
                gap: 12px;
            }
            .navbar .brand {
                font-size: 16px;
            }
            .nav-toggle {
                display: inline-flex;
                align-items: center;
                justify-content: center;
            }
            .navbar .nav-links {
                display: none;
                flex-direction: column;
                width: 100%;
                gap: 8px;
            }
            .navbar .nav-links.open {
                display: flex;
            }
            .navbar .nav-links a {
                width: 100%;
                text-align: left;
                padding: 8px 10px;
                font-size: 12px;
            }
            .container {
                margin: 20px auto;
                padding: 0 12px;
            }
            .card-grid,
            .section-grid {
                display: grid;
                grid-template-columns: 1fr;
                gap: 12px;
            }
            .card {
                padding: 16px;
            }
            .card p {
                font-size: 20px;
            }
            th, td {
                padding: 10px 8px;
                font-size: 12px;
                white-space: nowrap;
            }
            table {
                display: block;
                width: 100%;
                overflow-x: auto;
                -webkit-overflow-scrolling: touch;
            }
            input, select {
                width: 100%;
                min-width: 0;
            }
            form {
                width: 100%;
            }
        }
    </style>
</head>
<body>
    <nav class="navbar">
        <a href="/admin/dashboard" class="brand">⚙️ WAJ VTU Admin</a>
        <button class="nav-toggle" type="button" aria-label="Toggle navigation">☰</button>
        <ul class="nav-links" id="admin-nav-links">
            <li><a href="/admin/dashboard" class="{{ 'active' if active_page == 'dashboard' else '' }}">📊 Dashboard</a></li>
            <li><a href="/admin/transactions" class="{{ 'active' if active_page == 'transactions' else '' }}">💳 Transactions</a></li>
            <li><a href="/admin/users" class="{{ 'active' if active_page == 'users' else '' }}">👥 Customers</a></li>
            <li><a href="/admin/payments" class="{{ 'active' if active_page == 'payments' else '' }}">💰 Payments</a></li>
            <li><a href="/admin/ledger" class="{{ 'active' if active_page == 'ledger' else '' }}">📒 Wallet Ledger</a></li>
            <li><a href="/admin/whatsapp" class="{{ 'active' if active_page == 'whatsapp' else '' }}">📱 WhatsApp</a></li>
            <li><a href="/admin/services" class="{{ 'active' if active_page == 'services' else '' }}">🛒 Services</a></li>
            <li><a href="/admin/analytics" class="{{ 'active' if active_page == 'analytics' else '' }}">📈 Analytics</a></li>
            <li><a href="/admin/providers" class="{{ 'active' if active_page == 'providers' else '' }}">🔌 Providers</a></li>
            <li><a href="/admin/pricing" class="{{ 'active' if active_page == 'pricing' else '' }}">💵 Pricing</a></li>
            <li><a href="/admin/support" class="{{ 'active' if active_page == 'support' else '' }}">🎟️ Support</a></li>
            <li><a href="/admin/security" class="{{ 'active' if active_page == 'security' else '' }}">🔐 Security</a></li>
            <li><a href="/admin/settings" class="{{ 'active' if active_page == 'settings' else '' }}">⚙️ Settings</a></li>
        </ul>
    </nav>
    <div class="container">
        {{ body_content | safe }}
    </div>
    <script>
        const navToggle = document.querySelector('.nav-toggle');
        const navLinks = document.getElementById('admin-nav-links');
        if (navToggle && navLinks) {
            navToggle.addEventListener('click', () => {
                navLinks.classList.toggle('open');
            });
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
    total_revenue = sum((tx.amount for tx in successful_txs), Decimal("0.00"))
    today_sales = sum(
        (
            tx.amount
            for tx in successful_txs
            if normalize_datetime(tx.created_at) and normalize_datetime(tx.created_at) >= start_of_day
        ),
        Decimal("0.00"),
    )

    active_customers = db.session.query(User.id).join(Transaction).group_by(User.id).count()
    new_customers_today = User.query.filter(User.created_at >= start_of_day).count()
    new_customers_week = User.query.filter(User.created_at >= start_of_week).count()
    new_customers_month = User.query.filter(User.created_at >= start_of_month).count()

    top_customers = (
        db.session.query(User.phone, func.sum(Transaction.amount).label("total_spent"))
        .join(Transaction)
        .group_by(User.phone)
        .order_by(func.sum(Transaction.amount).desc())
        .limit(5)
        .all()
    )

    service_breakdown = (
        db.session.query(Transaction.type, func.count(Transaction.id).label("count"), func.sum(Transaction.amount).label("total_amount"))
        .group_by(Transaction.type)
        .order_by(func.sum(Transaction.amount).desc())
        .limit(5)
        .all()
    )

    recent_transactions = Transaction.query.order_by(Transaction.id.desc()).limit(10).all()

    tx_rows = ""
    for tx in recent_transactions:
        status_cls = "badge-success" if tx.status == "SUCCESS" else "badge-failed"
        tx_rows += f"""
        <tr>
            <td><code>{escape(tx.reference)}</code></td>
            <td>{escape(tx.user.phone if tx.user else '')}</td>
            <td>{escape(tx.type)}</td>
            <td>₦{tx.amount:,.2f}</td>
            <td>{escape(tx.recipient or '')}</td>
            <td class="{escape(status_cls)}">{escape(tx.status)}</td>
            <td>{escape(format_admin_datetime(tx.created_at))}</td>
        </tr>
        """

    customer_rows = ""
    for customer_phone, total_spent in top_customers:
        customer_rows += f"""
        <tr>
            <td>{escape(customer_phone)}</td>
            <td>₦{Decimal(total_spent or 0):,.2f}</td>
        </tr>
        """

    service_rows = ""
    for service_type, count, total_amount in service_breakdown:
        service_rows += f"""
        <tr>
            <td>{escape(service_type)}</td>
            <td>{count}</td>
            <td>₦{Decimal(total_amount or 0):,.2f}</td>
        </tr>
        """

    content = f"""
    <div class="card-grid">
        <div class="card"><h3>Total Revenue</h3><p>₦{total_revenue:,.2f}</p></div>
        <div class="card"><h3>Today's Sales</h3><p>₦{today_sales:,.2f}</p></div>
        <div class="card"><h3>Total Transactions</h3><p>{total_transactions}</p></div>
        <div class="card"><h3>Total Customers</h3><p>{total_users}</p></div>
    </div>
    <div class="card-grid">
        <div class="card"><h3>Successful</h3><p>{successful_transactions}</p></div>
        <div class="card"><h3>Failed</h3><p>{failed_transactions}</p></div>
        <div class="card"><h3>Pending</h3><p>{pending_transactions}</p></div>
        <div class="card"><h3>Active Customers</h3><p>{active_customers}</p></div>
    </div>
    <div class="card-grid">
        <div class="card"><h3>New Today</h3><p>{new_customers_today}</p></div>
        <div class="card"><h3>New This Week</h3><p>{new_customers_week}</p></div>
        <div class="card"><h3>New This Month</h3><p>{new_customers_month}</p></div>
        <div class="card"><h3>Revenue / Txn</h3><p>₦{(total_revenue / total_transactions if total_transactions else Decimal('0.00')):,.2f}</p></div>
    </div>

    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:15px;">
        <h3>Recent Activity Stream</h3>
        <a href="/admin/settings" style="background:#2563eb; color:#ffffff; padding:8px 14px; border-radius:6px; text-decoration:none; font-size:14px; font-weight:600;">Edit Service Pricing</a>
    </div>
    <table>
        <thead>
            <tr><th>Reference</th><th>Customer</th><th>Type</th><th>Amount</th><th>Recipient</th><th>Status</th><th>Timestamp</th></tr>
        </thead>
        <tbody>
            {tx_rows if tx_rows else '<tr><td colspan="7" style="text-align:center;">No transactions logged yet</td></tr>'}
        </tbody>
    </table>

    <div style="display:grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-top: 25px;">
        <div>
            <h3 style="margin-bottom: 12px;">Top Customers</h3>
            <table>
                <thead><tr><th>Phone</th><th>Total Spent</th></tr></thead>
                <tbody>{customer_rows if customer_rows else '<tr><td colspan="2" style="text-align:center;">No customer activity yet</td></tr>'}</tbody>
            </table>
        </div>
        <div>
            <h3 style="margin-bottom: 12px;">Service Breakdown</h3>
            <table>
                <thead><tr><th>Service</th><th>Count</th><th>Total</th></tr></thead>
                <tbody>{service_rows if service_rows else '<tr><td colspan="3" style="text-align:center;">No service activity yet</td></tr>'}</tbody>
            </table>
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
        user_rows += f"""
        <tr>
            <td>#{escape(u.id)}</td>
            <td><b>{escape(u.phone)}</b></td>
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
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;">
        <h2>👥 User Directory & Wallet Control</h2>
        <form method="GET" action="/admin/users" style="display:flex; gap:8px;">
            <input type="text" name="q" placeholder="Search phone number..." value="{escape(search_query)}">
            <button type="submit">Search</button>
        </form>
    </div>
    <table>
        <thead>
            <tr><th>User ID</th><th>Phone Number</th><th>Joined</th><th>Wallet Balance</th><th>Bot State</th><th>Manual Wallet Top-up</th></tr>
        </thead>
        <tbody>
            {user_rows if user_rows else '<tr><td colspan="6" style="text-align:center;">No users found</td></tr>'}
        </tbody>
    </table>
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

    failures = Transaction.query.filter(Transaction.status.in_(['FAILED', 'PENDING'])).order_by(Transaction.id.desc()).limit(15).all()
    support_rows = ""
    for tx in failures:
        support_rows += f"""
        <tr>
            <td>{escape(tx.reference)}</td>
            <td>{escape(tx.user.phone if tx.user else '')}</td>
            <td>{escape(tx.status)}</td>
            <td>{escape(tx.description or 'Awaiting review')}</td>
        </tr>
        """

    content = f"""
    <div class="section-grid">
        <div class="card"><h3>Failed Tickets</h3><p>{Transaction.query.filter_by(status='FAILED').count()}</p></div>
        <div class="card"><h3>Pending Review</h3><p>{Transaction.query.filter_by(status='PENDING').count()}</p></div>
        <div class="card"><h3>Resolved</h3><p>{Transaction.query.filter_by(status='SUCCESS').count()}</p></div>
    </div>
    <h2 style="margin-bottom:15px;">🎟️ Support Queue</h2>
    <table>
        <thead><tr><th>Reference</th><th>Customer</th><th>Status</th><th>Issue</th></tr></thead>
        <tbody>{support_rows if support_rows else '<tr><td colspan="4" style="text-align:center;">No support issues logged</td></tr>'}</tbody>
    </table>
    """
    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=content, active_page="support")


@app.route("/admin/security")
def admin_security():
    auth_error = require_admin_auth()
    if auth_error:
        return auth_error

    wallet_adjustments = Transaction.query.filter_by(type='WALLET_ADJUSTMENT').count()
    total_transactions = Transaction.query.count()
    rows = f"""
    <tr><td>Admin login activity</td><td>Tracked in application session</td><td>Enabled</td></tr>
    <tr><td>Manual wallet adjustments</td><td>{wallet_adjustments}</td><td>Auditable</td></tr>
    <tr><td>Transaction status changes</td><td>{total_transactions}</td><td>Recorded</td></tr>
    <tr><td>Pricing updates</td><td>{ServiceMarkup.query.count()}</td><td>Controlled</td></tr>
    """

    content = f"""
    <h2 style="margin-bottom:15px;">🔐 Security & Audit</h2>
    <table>
        <thead><tr><th>Audit Area</th><th>Count / Scope</th><th>Status</th></tr></thead>
        <tbody>{rows}</tbody>
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

        db.session.commit()

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
            <thead><tr><th>Service</th><th>Markup (₦)</th></tr></thead>
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
    validate_csrf_token()
    user = User.query.get_or_404(user_id)
    try:
        amount = Decimal(request.form.get("amount", "0"))
    except Exception:
        return redirect(url_for("admin_users"))
    action_type = request.form.get("action_type")

    if amount <= 0 or action_type not in {"CREDIT", "DEBIT"}:
        return redirect(url_for("admin_users"))
    if action_type == "DEBIT" and user.wallet_balance < amount:
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

    return redirect(url_for("admin_users"))


@app.route("/admin/transactions")
def admin_transactions():
    auth_error = require_admin_auth()
    if auth_error:
        return auth_error

    status_filter = request.args.get("status", "ALL").upper()
    search_query = request.args.get("q", "").strip()

    query = Transaction.query
    if status_filter in {"SUCCESS", "PENDING", "FAILED", "REVERSED"}:
        query = query.filter(Transaction.status == status_filter)
    if search_query:
        term = f"%{search_query}%"
        query = query.join(User, Transaction.user_id == User.id, isouter=True).filter(
            or_(
                Transaction.reference.ilike(term),
                Transaction.type.ilike(term),
                Transaction.recipient.ilike(term),
                Transaction.description.ilike(term),
                User.phone.ilike(term),
            )
        )

    transactions = query.order_by(Transaction.id.desc()).all()

    tx_rows = ""
    for tx in transactions:
        status_cls = "badge-success" if tx.status == "SUCCESS" else "badge-failed"
        tx_rows += f"""
        <tr>
            <td><code>{escape(tx.reference)}</code></td>
            <td>{escape(tx.user.phone if tx.user else f'#{tx.user_id}')}</td>
            <td>{escape(tx.type)}</td>
            <td>₦{tx.amount:,.2f}</td>
            <td>{escape(tx.recipient or '')}</td>
            <td class="{escape(status_cls)}">{escape(tx.status)}</td>
            <td>{escape(format_admin_datetime(tx.created_at))}</td>
            <td><small>{escape(tx.description or '')}</small></td>
        </tr>
        """

    statuses = ["ALL", "SUCCESS", "PENDING", "FAILED", "REVERSED"]
    q_param = quote(search_query)
    filter_buttons = "".join(
        f'<a href="/admin/transactions?status={status}&q={q_param}" style="{ "background:#2563eb; color:#fff;" if status == status_filter else "background:#e2e8f0; color:#0f172a;" } padding:6px 10px; border-radius:6px; text-decoration:none; font-size:12px; margin-right:8px;">{status}</a>'
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
            <button type="submit">Apply</button>
        </form>
    </div>
    <div style="margin-bottom:16px; display:flex; flex-wrap:wrap; gap:8px;">{filter_buttons}</div>
    <table>
        <thead>
            <tr><th>Reference</th><th>Customer</th><th>Type</th><th>Amount</th><th>Recipient</th><th>Status</th><th>Timestamp</th><th>Description</th></tr>
        </thead>
        <tbody>
            {tx_rows if tx_rows else '<tr><td colspan="8" style="text-align:center;">No transactions match the selected filter</td></tr>'}
        </tbody>
    </table>
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


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)