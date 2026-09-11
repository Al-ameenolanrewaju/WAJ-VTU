import os
import json
import uuid
import hashlib
import hmac
import requests
from decimal import Decimal
from sqlalchemy import inspect, text
from flask import Flask, request, jsonify, render_template_string, redirect, url_for

# 1. Import db, User, and Transaction directly from models.py
from models import db, User, Transaction
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

# --- CONFIGURATION ---
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL", "sqlite:///vtu_bot.db")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
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


with app.app_context():
    db.create_all()
    ensure_database_schema()

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
}


# --- HELPER UTILITIES ---
def get_or_create_user(phone_number):
    user = User.query.filter_by(whatsapp_id=phone_number).first()
    if not user:
        user = User(phone=phone_number, whatsapp_id=phone_number, wallet_balance=Decimal("0.00"))
        db.session.add(user)
        db.session.commit()
    return user


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


def verify_paystack_signature(raw_body, signature):
    expected = hmac.new(
        PAYSTACK_SECRET_KEY.encode("utf-8"), raw_body, hashlib.sha512
    ).hexdigest()
    return bool(signature) and hmac.compare_digest(expected, signature)


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

    if Transaction.query.filter_by(reference=reference).first():
        return jsonify({"status": "ok", "duplicate": True}), 200

    user = User.query.filter_by(whatsapp_id=phone).first()
    if not user:
        user = User.query.filter_by(phone=phone).first()
    if not user:
        user = User(phone=phone, whatsapp_id=phone, wallet_balance=Decimal("0.00"))
        db.session.add(user)

    user.wallet_balance += net_credit
    db.session.add(Transaction(
        user=user,
        reference=reference,
        amount=net_credit,
        type="DEPOSIT",
        recipient=phone,
        status="SUCCESS",
        description=f"Paystack deposit; gross paid NGN {paid_gross:,.2f}",
        meta_data={"gross_amount": str(paid_gross), "net_amount": str(net_credit), "paystack": data},
    ))
    db.session.commit()
    return jsonify({"status": "ok", "credited_amount": str(net_credit)}), 200


def send_whatsapp_message(recipient, text):
    """
    Sends outgoing message to the WhatsApp Bridge service (Node.js/Baileys).
    """
    try:
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
        print(f"Failed to deliver WhatsApp message to bridge: {e}")
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


# --- MAIN WEBHOOK ENDPOINT ---
@app.route("/webhook", methods=["POST"])
def whatsapp_webhook():
    req_data = request.get_json() or {}

    chat_id = req_data.get("sender") or req_data.get("from") or req_data.get("phone")
    text = (req_data.get("message") or req_data.get("text") or req_data.get("body") or "").strip()

    if not chat_id:
        return jsonify({"status": "error", "reason": "No sender specified"}), 400

    provider_phone = str(chat_id).split("@", 1)[0]

    user = get_or_create_user(chat_id)
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
        "top up": "2",
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
            "✨ *WELCOME TO WAJ VTU*\n"
            "────────────────────────\n"
            "Choose a service below:\n\n"
            "1. 📶 Buy Data\n"
            "2. 📱 Buy Airtime\n"
            "3. 📺 Cable TV\n"
            "4. 💡 Pay Electricity\n"
            "5. ⚽ Betting Top-up\n"
            "6. 🎓 Education PINs\n"
            "7. 💳 Check Wallet\n\n"
            f"💰 *Available Balance:* ₦{user.wallet_balance:,.2f}\n"
            "_Reply with a number from 1 to 7_"
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
                "📶 *SELECT MOBILE NETWORK*\n"
                "────────────────────────\n"
                "1. MTN\n"
                "2. AIRTEL\n"
                "3. GLO\n"
                "4. 9MOBILE\n\n"
                "_Choose a network by replying with 1, 2, 3, or 4_"
            )
            send_whatsapp_message(chat_id, network_menu)

        elif text == "2":
            set_user_session(user, STATES["AWAITING_AIRTIME_NETWORK"], {})
            airtime_menu = (
                "📱 *SELECT AIRTIME NETWORK*\n"
                "────────────────────────\n"
                "1. MTN\n"
                "2. AIRTEL\n"
                "3. GLO\n"
                "4. 9MOBILE\n\n"
                "_Choose a network by replying with 1, 2, 3, or 4_"
            )
            send_whatsapp_message(chat_id, airtime_menu)

        elif text == "3":
            set_user_session(user, STATES["AWAITING_CABLE_PROVIDER"], {})
            send_whatsapp_message(
                chat_id,
                "📺 *SELECT CABLE PROVIDER*\n"
                "────────────────────────\n"
                "1. DSTV\n"
                "2. GOTV\n"
                "3. STARTIMES\n\n"
                "_Reply with 1, 2, or 3_"
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
            session_data["education_packages"] = packages
            set_user_session(user, STATES["AWAITING_EDUCATION_PACKAGE"], session_data)
            package_menu = "🎓 *SELECT EDUCATION PIN*\n"
            for index, package in enumerate(packages, start=1):
                package_menu += f"{index}. {package['name']} - ₦{package['amount']:,.2f}\n"
            send_whatsapp_message(chat_id, package_menu + "\n_Reply with the package number_")

        elif text == "7":
            send_whatsapp_message(
                chat_id,
                f"💳 *WALLET BALANCE*\n"
                f"₦{user.wallet_balance:,.2f}\n\n"
                "Type *MENU* to view more services."
            )

        else:
            send_main_menu_response()

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
                cost = float(plan.get("variation_amount")) + 50.00
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
            network = session_data["network"]

            if user.wallet_balance < amount_decimal:
                send_whatsapp_message(
                    chat_id,
                    f"❌ Insufficient balance! Required: ₦{amount_decimal:,.2f} | Balance: ₦{user.wallet_balance:,.2f}"
                )
                set_user_session(user, STATES["IDLE"], {})
                return jsonify({"status": "insufficient_balance"}), 200

            user.wallet_balance -= amount_decimal
            db.session.commit()

            send_whatsapp_message(chat_id, f"⏳ Processing ₦{amount_decimal} {network} airtime via WAJ VTU...")
            result = process_airtime_purchase(recipient_phone, network, float(amount_decimal))

            if result.get("status") == "SUCCESS":
                tx = Transaction(
                    user_id=user.id,
                    reference=result['reference'],
                    amount=amount_decimal,
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
                user.wallet_balance += amount_decimal
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
                    plan_menu += f"{index}. {plan['name']} - ₦{plan['amount']:,.2f}\n"
                send_whatsapp_message(chat_id, plan_menu + "\n_Reply with the plan number you want._")

    elif current_state == STATES["AWAITING_CABLE_PLAN"]:
        plans = session_data.get("cable_plans", [])
        if not text.isdigit() or not 1 <= int(text) <= len(plans):
            send_whatsapp_message(chat_id, "❌ Please select a valid cable plan number.")
        else:
            plan = plans[int(text) - 1]
            amount = Decimal(str(plan["amount"]))
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
            if user.wallet_balance < amount:
                send_whatsapp_message(chat_id, "❌ Insufficient wallet balance.")
                set_user_session(user, STATES["IDLE"], {})
            else:
                user.wallet_balance -= amount
                db.session.commit()
                send_whatsapp_message(chat_id, "⏳ Processing your electricity payment via WAJ VTU...")
                result = process_electricity_payment(session_data["disco"], session_data["meter_number"], session_data["meter_type"], float(amount), provider_phone)
                success = settle_transaction(user, result, amount, "ELECTRICITY", session_data["meter_number"], f"{session_data['disco']} electricity payment")
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
            if user.wallet_balance < amount:
                send_whatsapp_message(chat_id, "❌ Insufficient wallet balance.")
                set_user_session(user, STATES["IDLE"], {})
            else:
                user.wallet_balance -= amount
                db.session.commit()
                send_whatsapp_message(chat_id, "⏳ Processing your betting top-up via WAJ VTU...")
                result = process_betting_topup(session_data["platform"], session_data["betting_account"], float(amount), provider_phone)
                success = settle_transaction(user, result, amount, "BETTING", session_data["betting_account"], f"{session_data['platform']} betting top-up")
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
            amount = Decimal(str(package["amount"])) * quantity
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
            height: 60px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1);
        }
        .navbar .brand { color: #ffffff; font-size: 18px; font-weight: bold; text-decoration: none; }
        .navbar .nav-links { display: flex; gap: 10px; list-style: none; }
        .navbar .nav-links a {
            color: #94a3b8;
            text-decoration: none;
            padding: 8px 16px;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 500;
        }
        .navbar .nav-links a:hover, .navbar .nav-links a.active { background-color: #2563eb; color: #ffffff; }

        .container { max-width: 1100px; margin: 30px auto; padding: 0 20px; }
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
    </style>
</head>
<body>
    <nav class="navbar">
        <a href="/admin/dashboard" class="brand">⚙️ VTU Admin Control</a>
        <ul class="nav-links">
            <li><a href="/admin/dashboard" class="{{ 'active' if active_page == 'dashboard' else '' }}">📊 Dashboard</a></li>
            <li><a href="/admin/users" class="{{ 'active' if active_page == 'users' else '' }}">👥 Track Users</a></li>
            <li><a href="/admin/transactions" class="{{ 'active' if active_page == 'transactions' else '' }}">💳 Transactions</a></li>
        </ul>
    </nav>
    <div class="container">
        {{ body_content | safe }}
    </div>
</body>
</html>
"""


@app.route("/admin/dashboard")
def admin_dashboard():
    total_users = User.query.count()
    total_transactions = Transaction.query.count()

    successful_txs = Transaction.query.filter_by(status="SUCCESS").all()
    total_volume = sum([tx.amount for tx in successful_txs]) if successful_txs else Decimal("0.00")

    recent_transactions = Transaction.query.order_by(Transaction.id.desc()).limit(10).all()

    tx_rows = ""
    for tx in recent_transactions:
        status_cls = "badge-success" if tx.status == "SUCCESS" else "badge-failed"
        tx_rows += f"""
        <tr>
            <td><code>{tx.reference}</code></td>
            <td>{tx.type}</td>
            <td>₦{tx.amount:,.2f}</td>
            <td>{tx.recipient}</td>
            <td class="{status_cls}">{tx.status}</td>
        </tr>
        """

    content = f"""
    <div class="card-grid">
        <div class="card"><h3>Total Registered Users</h3><p>{total_users}</p></div>
        <div class="card"><h3>Total Transactions</h3><p>{total_transactions}</p></div>
        <div class="card"><h3>Total Volume Processed</h3><p>₦{total_volume:,.2f}</p></div>
    </div>
    <h3 style="margin-bottom: 15px;">Recent Activity Stream</h3>
    <table>
        <thead>
            <tr><th>Reference</th><th>Type</th><th>Amount</th><th>Recipient</th><th>Status</th></tr>
        </thead>
        <tbody>
            {tx_rows if tx_rows else '<tr><td colspan="5" style="text-align:center;">No transactions logged yet</td></tr>'}
        </tbody>
    </table>
    """

    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=content, active_page="dashboard")


@app.route("/admin/users", methods=["GET"])
def admin_users():
    search_query = request.args.get("q", "").strip()
    if search_query:
        users = User.query.filter(User.phone.contains(search_query)).all()
    else:
        users = User.query.order_by(User.id.desc()).all()

    user_rows = ""
    for u in users:
        user_rows += f"""
        <tr>
            <td>#{u.id}</td>
            <td><b>{u.phone}</b></td>
            <td>₦{u.wallet_balance:,.2f}</td>
            <td><code>{u.current_state}</code></td>
            <td>
                <form method="POST" action="/admin/user/{u.id}/fund" style="display:flex; gap:6px;">
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
            <input type="text" name="q" placeholder="Search phone number..." value="{search_query}">
            <button type="submit">Search</button>
        </form>
    </div>
    <table>
        <thead>
            <tr><th>User ID</th><th>Phone Number</th><th>Wallet Balance</th><th>Bot State</th><th>Manual Wallet Top-up</th></tr>
        </thead>
        <tbody>
            {user_rows if user_rows else '<tr><td colspan="5" style="text-align:center;">No users found</td></tr>'}
        </tbody>
    </table>
    """

    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=content, active_page="users")


@app.route("/admin/user/<int:user_id>/fund", methods=["POST"])
def admin_fund_wallet(user_id):
    user = User.query.get_or_404(user_id)
    amount = Decimal(request.form.get("amount", "0"))
    action_type = request.form.get("action_type")

    if amount > 0:
        if action_type == "CREDIT":
            user.wallet_balance += amount
            desc = f"Admin Deposit (+₦{amount:,.2f})"
        elif action_type == "DEBIT" and user.wallet_balance >= amount:
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
    transactions = Transaction.query.order_by(Transaction.id.desc()).all()

    tx_rows = ""
    for tx in transactions:
        status_cls = "badge-success" if tx.status == "SUCCESS" else "badge-failed"
        tx_rows += f"""
        <tr>
            <td><code>{tx.reference}</code></td>
            <td>#{tx.user_id}</td>
            <td>{tx.type}</td>
            <td>₦{tx.amount:,.2f}</td>
            <td>{tx.recipient}</td>
            <td class="{status_cls}">{tx.status}</td>
            <td><small>{tx.description or ''}</small></td>
        </tr>
        """

    content = f"""
    <h2 style="margin-bottom:20px;">💳 System Transaction Ledger</h2>
    <table>
        <thead>
            <tr><th>Reference</th><th>User ID</th><th>Type</th><th>Amount</th><th>Recipient</th><th>Status</th><th>Description</th></tr>
        </thead>
        <tbody>
            {tx_rows if tx_rows else '<tr><td colspan="7" style="text-align:center;">No transactions logged</td></tr>'}
        </tbody>
    </table>
    """

    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=content, active_page="transactions")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port)