import os
import json
import logging
from decimal import Decimal, InvalidOperation
import requests
from flask import Flask, request, jsonify, render_template, render_template_string
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy import func, inspect, or_, text

# Import provider functions from your clubkonnect/provider module
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

# Setup detailed logging for debugging incoming webhooks and provider responses
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("vtu_bot")

app = Flask(__name__)

# --- CONFIGURATION ---
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL", "sqlite:///vtu_bot.db")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "vtu-default-secret-key-change-in-production")
NODE_BRIDGE_URL = os.getenv("NODE_BRIDGE_URL", "https://waj-vtu-bridge.onrender.com").rstrip("/")
if NODE_BRIDGE_URL.endswith("/api/sendText"):
    NODE_BRIDGE_URL = NODE_BRIDGE_URL[:-len("/api/sendText")]
BRIDGE_API_TOKEN = os.getenv("BRIDGE_API_TOKEN", "")

db = SQLAlchemy(app)


# --- DATABASE MODELS ---
class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    whatsapp_id = db.Column(db.String(50), nullable=True)
    phone = db.Column(db.String(20), nullable=True)
    state_data = db.Column(db.Text, nullable=True)
    phone_number = db.Column(db.String(30), unique=True, nullable=False, index=True)
    wallet_balance = db.Column(db.Numeric(12, 2), default=Decimal("1000.00"), nullable=False)
    current_state = db.Column(db.String(50), default="IDLE", nullable=False)
    session_data = db.Column(db.Text, default="{}", nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "phone_number": self.phone_number,
            "wallet_balance": float(self.wallet_balance),
            "current_state": self.current_state,
            "session_data": self.get_session_data()
        }

    def get_session_data(self):
        try:
            return json.loads(self.session_data or "{}")
        except Exception as e:
            logger.error(f"Failed to parse session_data JSON for user {self.phone_number}: {e}")
            return {}

    def set_session(self, state, data):
        self.current_state = state
        self.session_data = json.dumps(data)


class Transaction(db.Model):
    __tablename__ = 'transactions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    reference = db.Column(db.String(80), unique=True, nullable=False, index=True)
    amount = db.Column(db.Numeric(12, 2), nullable=False)
    type = db.Column(db.String(30), nullable=False)  # DATA, AIRTIME, CABLE, ELECTRICITY, BETTING, EDUCATION
    recipient = db.Column(db.String(100), nullable=False)
    status = db.Column(db.String(30), nullable=False, default="PENDING")  # SUCCESS, FAILED, PENDING
    description = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    user = db.relationship('User', backref=db.backref('transactions', lazy=True))


with app.app_context():
    db.create_all()

    user_columns = {column["name"] for column in inspect(db.engine).get_columns("users")}
    legacy_columns = {
        "whatsapp_id": "VARCHAR(50)",
        "phone": "VARCHAR(20)",
        "state_data": "TEXT",
        "phone_number": "VARCHAR(30)",
        "session_data": "TEXT",
        "updated_at": "TIMESTAMP",
    }
    with db.engine.begin() as connection:
        for column_name, column_type in legacy_columns.items():
            if column_name not in user_columns:
                connection.execute(text(
                    f"ALTER TABLE users ADD COLUMN {column_name} {column_type}"
                ))

        connection.execute(text(
            "UPDATE users SET phone_number = COALESCE(phone, whatsapp_id) "
            "WHERE phone_number IS NULL"
        ))
        connection.execute(text(
            "UPDATE users SET session_data = COALESCE(state_data, '{}') "
            "WHERE session_data IS NULL"
        ))

# --- ALL SYSTEM STATES ---
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
    "AWAITING_CABLE_IUC": "AWAITING_CABLE_IUC",
    "AWAITING_CABLE_PACKAGE": "AWAITING_CABLE_PACKAGE",
    "CONFIRM_CABLE_PURCHASE": "CONFIRM_CABLE_PURCHASE",
    # Electricity Flow
    "AWAITING_ELEC_DISCO": "AWAITING_ELEC_DISCO",
    "AWAITING_ELEC_METER_TYPE": "AWAITING_ELEC_METER_TYPE",
    "AWAITING_ELEC_METER_NO": "AWAITING_ELEC_METER_NO",
    "AWAITING_ELEC_AMOUNT": "AWAITING_ELEC_AMOUNT",
    "CONFIRM_ELEC_PURCHASE": "CONFIRM_ELEC_PURCHASE",
    # Betting Flow
    "AWAITING_BET_PLATFORM": "AWAITING_BET_PLATFORM",
    "AWAITING_BET_USERID": "AWAITING_BET_USERID",
    "AWAITING_BET_AMOUNT": "AWAITING_BET_AMOUNT",
    "CONFIRM_BET_PURCHASE": "CONFIRM_BET_PURCHASE",
    # Education Flow
    "AWAITING_EDU_EXAM": "AWAITING_EDU_EXAM",
    "AWAITING_EDU_QUANTITY": "AWAITING_EDU_QUANTITY",
    "CONFIRM_EDU_PURCHASE": "CONFIRM_EDU_PURCHASE",
}


# --- HELPER UTILITIES ---
def get_or_create_user(phone_number):
    """Retrieves an existing user or creates a new account with a starting balance."""
    clean_phone = phone_number.replace("whatsapp:", "").strip()
    try:
        user = User.query.filter(or_(
            User.phone_number == clean_phone,
            User.whatsapp_id == clean_phone,
            User.phone == clean_phone
        )).first()
        if not user:
            logger.info(f"Creating new user account for: {clean_phone}")
            user = User(
                phone_number=clean_phone,
                whatsapp_id=clean_phone,
                phone=clean_phone,
                state_data="{}",
                wallet_balance=Decimal("1000.00"),
                current_state=STATES["IDLE"],
                session_data="{}"
            )
            db.session.add(user)
            db.session.commit()
        else:
            if user.phone_number != clean_phone or user.whatsapp_id != clean_phone or user.phone != clean_phone:
                user.phone_number = clean_phone
                user.whatsapp_id = clean_phone
                user.phone = clean_phone
                db.session.commit()
        return user
    except IntegrityError:
        db.session.rollback()
        user = User.query.filter(or_(
            User.phone_number == clean_phone,
            User.whatsapp_id == clean_phone,
            User.phone == clean_phone
        )).first()
        if user:
            return user
        raise
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Database error during get_or_create_user: {e}")
        raise e


def reset_user_to_idle(user):
    """Resets user state to IDLE and wipes active session cache."""
    try:
        user.set_session(STATES["IDLE"], {})
        db.session.commit()
    except SQLAlchemyError as e:
        db.session.rollback()
        logger.error(f"Failed to reset user {user.phone_number} to IDLE: {e}")


def send_whatsapp_message(chat_id, text):
    """Send a response through the WhatsApp bridge."""
    logger.info(f"OUTGOING MESSAGE TO [{chat_id}]:\n{text}")
    headers = {"Content-Type": "application/json"}
    if BRIDGE_API_TOKEN:
        headers["Authorization"] = f"Bearer {BRIDGE_API_TOKEN}"

    try:
        response = requests.post(
            f"{NODE_BRIDGE_URL}/api/sendText",
            json={"chatId": chat_id, "text": text},
            headers=headers,
            timeout=15
        )
        response.raise_for_status()
        logger.info(f"WhatsApp message delivered to [{chat_id}] via bridge")
        return True
    except requests.RequestException as error:
        logger.error(f"Failed to send WhatsApp message to [{chat_id}]: {error}")
        return False


def format_currency(amount):
    """Safely formats Decimals or floats into standard ₦ currency format."""
    try:
        val = Decimal(str(amount))
        return f"₦{val:,.2f}"
    except (InvalidOperation, ValueError):
        return f"₦{amount}"


def categorize_data_plans(plans):
    """
    Categorizes raw data variations into duration & promo tiers:
    - DAILY (1 Day / 24hrs)
    - TWO_DAYS (2 Days / 48hrs)
    - WEEKLY (7 Days / 14 Days)
    - MONTHLY (30 Days / SME / Corporate)
    - AWOOF (Promo / Night / Social / Streaming)
    - OTHERS (Fallback)
    """
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
        elif any(k in name for k in ["2 DAYS", "2DAY", "48HRS", "48 HRS"]):
            categorized["TWO_DAYS"].append(plan)
        elif any(k in name for k in ["1 DAY", "1DAY", "DAILY", "24HRS", "24 HRS"]):
            categorized["DAILY"].append(plan)
        elif any(k in name for k in ["7 DAYS", "7DAYS", "WEEKLY", "14 DAYS", "14DAYS"]):
            categorized["WEEKLY"].append(plan)
        elif any(k in name for k in ["30 DAYS", "30DAYS", "MONTHLY", "SME", "CORPORATE", "CG"]):
            categorized["MONTHLY"].append(plan)
        else:
            categorized["OTHERS"].append(plan)

    return categorized


def build_main_menu_text(user_balance):
    """Constructs standard full service main menu."""
    return (
        "📌 *MAIN SERVICES MENU*\n"
        "────────────────────\n"
        "1. 📶 Buy Data Bundle\n"
        "2. 📱 Buy Airtime\n"
        "3. 📺 Cable TV Subscription\n"
        "4. 💡 Pay Electricity Bill\n"
        "5. ⚽ Betting Wallet Topup\n"
        "6. 🎓 Education PINs (WAEC/JAMB)\n"
        "7. 💳 Check Wallet Balance\n\n"
        f"💳 Balance: *{format_currency(user_balance)}*\n"
        "_Reply with a service number (1-7)_"
    )


# --- HEALTH CHECK & WEB ROUTES ---
@app.route('/', methods=['GET', 'HEAD'])
@app.route('/health', methods=['GET', 'HEAD'])
def health_check():
    return {"status": "healthy", "service": "waj-vtu"}, 200


@app.route("/admin/users", methods=["GET"])
def list_users():
    try:
        users = User.query.all()
        return jsonify([u.to_dict() for u in users]), 200
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/admin", methods=["GET"])
def admin_dashboard():
    """Shows successful service sales and revenue totals for the admin."""
    revenue = db.session.query(func.coalesce(func.sum(Transaction.amount), 0)).filter(
        Transaction.status == "SUCCESS"
    ).scalar()
    transaction_count = Transaction.query.filter_by(status="SUCCESS").count()
    user_count = db.session.query(func.count(User.id)).scalar()

    revenue_by_type = db.session.query(
        Transaction.type,
        func.sum(Transaction.amount).label("total"),
        func.count(Transaction.id).label("count")
    ).filter(
        Transaction.status == "SUCCESS"
    ).group_by(Transaction.type).order_by(func.sum(Transaction.amount).desc()).all()

    return render_template(
        "admin/master.html",
        revenue=Decimal(str(revenue or 0)),
        transaction_count=transaction_count,
        user_count=user_count,
        revenue_by_type=revenue_by_type,
        format_currency=format_currency,
    )


# --- MAIN WEBHOOK ENDPOINT ---
@app.route("/webhook", methods=["POST", "GET"])
def whatsapp_webhook():
    # Handle Meta Cloud API Webhook Verification Request
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        verify_secret = os.getenv("WHATSAPP_VERIFY_TOKEN", "vtu_bot_verify_token")

        if mode == "subscribe" and token == verify_secret:
            logger.info("Webhook verification challenge passed successfully.")
            return challenge, 200
        return jsonify({"error": "Forbidden"}), 403

    req_data = request.get_json() or {}
    logger.info(f"INCOMING WEBHOOK PAYLOAD: {json.dumps(req_data)}")

    # Extract user phone number and input text from different gateway standards
    chat_id = "2348000000000"
    text = ""

    # Meta Cloud API extraction structure
    if "entry" in req_data and len(req_data["entry"]) > 0:
        changes = req_data["entry"][0].get("changes", [])
        if changes and "value" in changes[0]:
            value = changes[0]["value"]
            messages = value.get("messages", [])
            if messages:
                chat_id = messages[0].get("from", chat_id)
                text = messages[0].get("text", {}).get("body", "").strip()
    else:
        # Fallback for generic webhook structures (e.g. Twilio, UltraMsg, custom gateway)
        chat_id = req_data.get("from") or req_data.get("phone") or req_data.get("sender") or chat_id
        text = (req_data.get("text") or req_data.get("message") or req_data.get("body") or "").strip()

    if not text:
        return jsonify({"status": "ignored_no_text"}), 200

    try:
        user = get_or_create_user(chat_id)
    except Exception as e:
        logger.critical(f"Failed to access database for user {chat_id}: {e}")
        return jsonify({"status": "database_error"}), 500

    current_state = user.current_state or STATES["IDLE"]
    session_data = user.get_session_data()

    # --- GLOBAL CANCEL / MENU COMMAND HANDLER ---
    if text.upper() in ["0", "MENU", "CANCEL", "RESET", "RESTART"]:
        reset_user_to_idle(user)
        send_whatsapp_message(chat_id, build_main_menu_text(user.wallet_balance))
        return jsonify({"status": "reset_to_menu"}), 200

    # ==========================================
    # --- IDLE STATE (MAIN MENU DISPATCH) ---
    # ==========================================
    if current_state == STATES["IDLE"]:
        if text == "1":
            user.set_session(STATES["AWAITING_DATA_NETWORK"], {})
            db.session.commit()
            send_whatsapp_message(
                chat_id,
                "📶 *Select Mobile Network*\n"
                "────────────────────\n"
                "1. MTN\n"
                "2. AIRTEL\n"
                "3. GLO\n"
                "4. 9MOBILE\n\n"
                "_Reply with 1, 2, 3, or 4_"
            )
        elif text == "2":
            user.set_session(STATES["AWAITING_AIRTIME_NETWORK"], {})
            db.session.commit()
            send_whatsapp_message(
                chat_id,
                "📱 *Select Airtime Network*\n"
                "────────────────────\n"
                "1. MTN\n"
                "2. AIRTEL\n"
                "3. GLO\n"
                "4. 9MOBILE\n\n"
                "_Reply with 1, 2, 3, or 4_"
            )
        elif text == "3":
            user.set_session(STATES["AWAITING_CABLE_PROVIDER"], {})
            db.session.commit()
            send_whatsapp_message(
                chat_id,
                "📺 *Select Cable TV Provider*\n"
                "────────────────────\n"
                "1. DSTV\n"
                "2. GOTV\n"
                "3. STARTIMES\n\n"
                "_Reply with 1, 2, or 3_"
            )
        elif text == "4":
            user.set_session(STATES["AWAITING_ELEC_DISCO"], {})
            db.session.commit()
            send_whatsapp_message(
                chat_id,
                "💡 *Select Electricity Provider*\n"
                "────────────────────\n"
                "1. IKEDC (Ikeja Electric)\n"
                "2. EKEDC (Eko Electric)\n"
                "3. AEDC (Abuja Electric)\n"
                "4. IBEDC (Ibadan Electric)\n"
                "5. KEDCO (Kano Electric)\n"
                "6. PHED (Port Harcourt Electric)\n\n"
                "_Reply with 1, 2, 3, 4, 5, or 6_"
            )
        elif text == "5":
            user.set_session(STATES["AWAITING_BET_PLATFORM"], {})
            db.session.commit()
            send_whatsapp_message(
                chat_id,
                "⚽ *Select Betting Platform*\n"
                "────────────────────\n"
                "1. SportyBet\n"
                "2. Bet9ja\n"
                "3. 1xBet\n"
                "4. BangBet\n"
                "5. MerryBet\n\n"
                "_Reply with 1, 2, 3, 4, or 5_"
            )
        elif text == "6":
            user.set_session(STATES["AWAITING_EDU_EXAM"], {})
            db.session.commit()
            send_whatsapp_message(
                chat_id,
                "🎓 *Select Education Exam Pin*\n"
                "────────────────────\n"
                "1. WAEC Result Checker\n"
                "2. NECO Result Checker\n"
                "3. JAMB UTME Profile/Registration Pin\n\n"
                "_Reply with 1, 2, or 3_"
            )
        elif text == "7":
            send_whatsapp_message(
                chat_id,
                f"💳 *Your Current Wallet Balance:* {format_currency(user.wallet_balance)}\n\n"
                f"Type *MENU* to view options."
            )
        else:
            send_whatsapp_message(
                chat_id,
                "❌ Invalid option selected.\n\n" + build_main_menu_text(user.wallet_balance)
            )

    # ==========================================
    # --- 1. DATA BUNDLE FLOW ---
    # ==========================================
    elif current_state == STATES["AWAITING_DATA_NETWORK"]:
        networks = {"1": "MTN", "2": "AIRTEL", "3": "GLO", "4": "9MOBILE"}
        if text not in networks:
            send_whatsapp_message(chat_id,
                                  "❌ Invalid network option. Reply 1 for MTN, 2 for AIRTEL, 3 for GLO, 4 for 9MOBILE:")
        else:
            network_name = networks[text]
            session_data["network"] = network_name
            send_whatsapp_message(chat_id, f"⏳ Fetching available {network_name} data plans...")

            variations = fetch_data_variations(network_name)
            if not variations:
                send_whatsapp_message(chat_id,
                                      f"❌ Unable to load {network_name} plans at the moment. Type *MENU* to try again.")
                reset_user_to_idle(user)
                return jsonify({"status": "provider_error"}), 200

            session_data["categorized_plans"] = categorize_data_plans(variations)
            user.set_session(STATES["AWAITING_DATA_CATEGORY"], session_data)
            db.session.commit()

            category_menu = (
                f"📶 *Select {network_name} Data Category*\n"
                "────────────────────\n"
                "1. ⚡ Daily (1 Day / 24 Hours)\n"
                "2. ⚡ 2-Day Plans\n"
                "3. 📅 Weekly Plans (7 - 14 Days)\n"
                "4. 🗓️ Monthly / SME / Corporate Plans\n"
                "5. 🎉 Awoof & Special Promo Offers\n"
                "6. 📦 View All Available Plans\n\n"
                "_Reply with a category number (1-6) or type 0 to return to Menu._"
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
            send_whatsapp_message(chat_id, "❌ Invalid category selection. Please reply with a number from 1 to 6:")
        else:
            selected_cat = cat_map[text]
            network_name = session_data.get("network", "Mobile")
            categorized_plans = session_data.get("categorized_plans", {})

            if selected_cat == "ALL":
                filtered_plans = [p for cat in categorized_plans.values() for p in cat]
            else:
                filtered_plans = categorized_plans.get(selected_cat, [])

            if not filtered_plans:
                send_whatsapp_message(chat_id,
                                      f"ℹ️ No specific plans found under that category. Displaying all available {network_name} plans:")
                filtered_plans = [p for cat in categorized_plans.values() for p in cat]

            plan_menu = f"📊 *Select {network_name} Data Plan*\n────────────────────\n"
            plans_map = {}
            for idx, plan in enumerate(filtered_plans[:10], start=1):
                name = plan.get("name")
                cost = float(plan.get("variation_amount", 0)) + 50.00  # Added margin markup
                code = plan.get("variation_code")
                plans_map[str(idx)] = {"code": code, "amount": str(cost), "name": name}
                plan_menu += f"{idx}. {name} - {format_currency(cost)}\n"

            plan_menu += "\n_Reply with the plan number (e.g., 1)_"
            session_data["plans_map"] = plans_map
            user.set_session(STATES["AWAITING_DATA_PLAN"], session_data)
            db.session.commit()
            send_whatsapp_message(chat_id, plan_menu)

    elif current_state == STATES["AWAITING_DATA_PLAN"]:
        plans_map = session_data.get("plans_map", {})
        if text not in plans_map:
            send_whatsapp_message(chat_id,
                                  "❌ Invalid selection. Please reply with a valid number from the listed plans:")
        else:
            session_data["selected_plan"] = plans_map[text]
            user.set_session(STATES["AWAITING_DATA_NUMBER"], session_data)
            db.session.commit()
            send_whatsapp_message(
                chat_id,
                f"📞 Enter the 11-digit phone number to receive *{plans_map[text]['name']}*:"
            )

    elif current_state == STATES["AWAITING_DATA_NUMBER"]:
        clean_recipient = text.replace(" ", "").replace("-", "")
        if len(clean_recipient) != 11 or not clean_recipient.isdigit():
            send_whatsapp_message(chat_id, "❌ Invalid phone number. Please enter a valid 11-digit mobile number:")
        else:
            recipient_phone = clean_recipient
            plan = session_data.get("selected_plan", {})
            network = session_data.get("network", "DATA")
            cost_decimal = Decimal(str(plan.get("amount", "0")))

            if user.wallet_balance < cost_decimal:
                send_whatsapp_message(
                    chat_id,
                    f"❌ *Insufficient Balance!*\n"
                    f"Plan Cost: {format_currency(cost_decimal)}\n"
                    f"Wallet Balance: {format_currency(user.wallet_balance)}\n\n"
                    f"Type *MENU* to cancel or choose a different plan."
                )
                reset_user_to_idle(user)
                return jsonify({"status": "insufficient_balance"}), 200

            # Deduct balance prior to calling API
            user.wallet_balance -= cost_decimal
            db.session.commit()

            send_whatsapp_message(chat_id, f"⏳ Processing *{plan['name']}* for `{recipient_phone}`...")
            result = process_data_purchase(recipient_phone, network, plan["code"], float(cost_decimal))

            if result.get("status") == "SUCCESS":
                tx = Transaction(
                    user_id=user.id,
                    reference=result.get("reference", f"DATA_{user.id}_{recipient_phone}"),
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
                    f"✅ *Data Purchase Successful!*\n"
                    f"────────────────────\n"
                    f"• Plan: *{plan['name']}*\n"
                    f"• Recipient: `{recipient_phone}`\n"
                    f"• Ref: `{result.get('reference')}`\n"
                    f"• New Balance: *{format_currency(user.wallet_balance)}*"
                )
            else:
                # Revert wallet balance on provider failure
                user.wallet_balance += cost_decimal
                db.session.commit()
                send_whatsapp_message(
                    chat_id,
                    f"❌ *Transaction Failed:* {result.get('reason', 'Provider error')}\n"
                    f"Your wallet balance of {format_currency(cost_decimal)} has been fully refunded."
                )

            reset_user_to_idle(user)

    # ==========================================
    # --- 2. AIRTIME FLOW ---
    # ==========================================
    elif current_state == STATES["AWAITING_AIRTIME_NETWORK"]:
        networks = {"1": "MTN", "2": "AIRTEL", "3": "GLO", "4": "9MOBILE"}
        if text not in networks:
            send_whatsapp_message(chat_id, "❌ Invalid network choice. Reply with 1, 2, 3, or 4:")
        else:
            session_data["network"] = networks[text]
            user.set_session(STATES["AWAITING_AIRTIME_AMOUNT"], session_data)
            db.session.commit()
            send_whatsapp_message(chat_id, f"💵 Enter Airtime amount for *{networks[text]}* (Minimum ₦50):")

    elif current_state == STATES["AWAITING_AIRTIME_AMOUNT"]:
        if not text.isdigit() or int(text) < 50:
            send_whatsapp_message(chat_id, "❌ Minimum airtime amount is ₦50. Please enter a valid numerical amount:")
        else:
            session_data["amount"] = text
            user.set_session(STATES["AWAITING_AIRTIME_NUMBER"], session_data)
            db.session.commit()
            send_whatsapp_message(chat_id,
                                  f"📞 Enter recipient 11-digit phone number for {format_currency(text)} Airtime:")

    elif current_state == STATES["AWAITING_AIRTIME_NUMBER"]:
        clean_recipient = text.replace(" ", "").replace("-", "")
        if len(clean_recipient) != 11 or not clean_recipient.isdigit():
            send_whatsapp_message(chat_id, "❌ Invalid phone number. Please enter a valid 11-digit mobile number:")
        else:
            recipient_phone = clean_recipient
            amount_decimal = Decimal(session_data.get("amount", "0"))
            network = session_data.get("network", "AIRTIME")

            if user.wallet_balance < amount_decimal:
                send_whatsapp_message(
                    chat_id,
                    f"❌ *Insufficient Balance!*\nRequired: {format_currency(amount_decimal)} | Balance: {format_currency(user.wallet_balance)}"
                )
                reset_user_to_idle(user)
                return jsonify({"status": "insufficient_balance"}), 200

            user.wallet_balance -= amount_decimal
            db.session.commit()

            send_whatsapp_message(chat_id,
                                  f"⏳ Processing {format_currency(amount_decimal)} {network} airtime to `{recipient_phone}`...")
            result = process_airtime_purchase(recipient_phone, network, float(amount_decimal))

            if result.get("status") == "SUCCESS":
                tx = Transaction(
                    user_id=user.id,
                    reference=result.get("reference", f"AIRTIME_{user.id}_{recipient_phone}"),
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
                    f"✅ *Airtime Purchase Successful!*\n"
                    f"────────────────────\n"
                    f"• Amount: *{format_currency(amount_decimal)}*\n"
                    f"• Recipient: `{recipient_phone}`\n"
                    f"• Ref: `{result.get('reference')}`\n"
                    f"• New Balance: *{format_currency(user.wallet_balance)}*"
                )
            else:
                user.wallet_balance += amount_decimal
                db.session.commit()
                send_whatsapp_message(
                    chat_id,
                    f"❌ *Purchase Failed:* {result.get('reason', 'Provider error')}\nYour wallet has been refunded."
                )

            reset_user_to_idle(user)

    # ==========================================
    # --- 3. CABLE TV FLOW ---
    # ==========================================
    elif current_state == STATES["AWAITING_CABLE_PROVIDER"]:
        providers = {"1": "DSTV", "2": "GOTV", "3": "STARTIMES"}
        if text not in providers:
            send_whatsapp_message(chat_id, "❌ Reply 1 for DSTV, 2 for GOTV, or 3 for STARTIMES:")
        else:
            session_data["provider"] = providers[text]
            user.set_session(STATES["AWAITING_CABLE_IUC"], session_data)
            db.session.commit()
            send_whatsapp_message(chat_id, f"💳 Enter your 10 or 11 digit {providers[text]} IUC / Smartcard Number:")

    elif current_state == STATES["AWAITING_CABLE_IUC"]:
        clean_iuc = text.replace(" ", "").strip()
        if not clean_iuc.isdigit() or len(clean_iuc) < 10:
            send_whatsapp_message(chat_id, "❌ Invalid Smartcard/IUC length. Enter a valid 10 or 11 digit number:")
        else:
            provider = session_data.get("provider", "CABLE")
            send_whatsapp_message(chat_id, f"⏳ Verifying IUC `{clean_iuc}` with {provider}...")

            ver = verify_smartcard(provider, clean_iuc)
            if not ver.get("valid"):
                send_whatsapp_message(chat_id,
                                      f"❌ IUC Verification Failed: {ver.get('message', 'Invalid card number')}\nPlease check and re-enter IUC:")
                return jsonify({"status": "verification_failed"}), 200

            session_data["iuc"] = clean_iuc
            session_data["customer_name"] = ver.get("customer_name", "Customer Account")
            plans = fetch_cable_plans(provider)

            if not plans:
                send_whatsapp_message(chat_id,
                                      f"❌ Unable to fetch {provider} subscription packages right now. Type *MENU* to restart.")
                reset_user_to_idle(user)
                return jsonify({"status": "provider_error"}), 200

            plan_menu = (
                f"📺 *Select {provider} Package*\n"
                f"👤 Name: *{session_data['customer_name']}*\n"
                f"────────────────────\n"
            )
            plans_map = {}
            for idx, plan in enumerate(plans[:10], start=1):
                plans_map[str(idx)] = plan
                plan_menu += f"{idx}. {plan['name']} - {format_currency(plan['amount'])}\n"

            plan_menu += "\n_Reply with package number (e.g. 1)_"
            session_data["plans_map"] = plans_map
            user.set_session(STATES["AWAITING_CABLE_PACKAGE"], session_data)
            db.session.commit()
            send_whatsapp_message(chat_id, plan_menu)

    elif current_state == STATES["AWAITING_CABLE_PACKAGE"]:
        plans_map = session_data.get("plans_map", {})
        if text not in plans_map:
            send_whatsapp_message(chat_id, "❌ Reply with a valid package number from the listed options:")
        else:
            pkg = plans_map[text]
            session_data["selected_package"] = pkg
            user.set_session(STATES["CONFIRM_CABLE_PURCHASE"], session_data)
            db.session.commit()

            confirmation_msg = (
                f"🧾 *Confirm Cable TV Subscription*\n"
                f"────────────────────\n"
                f"• Provider: *{session_data['provider']}*\n"
                f"• Package: *{pkg['name']}*\n"
                f"• IUC Number: `{session_data['iuc']}`\n"
                f"• Account Name: *{session_data['customer_name']}*\n"
                f"• Total Price: *{format_currency(pkg['amount'])}*\n\n"
                f"Reply *1* to Confirm Payment or *0* to Cancel."
            )
            send_whatsapp_message(chat_id, confirmation_msg)

    elif current_state == STATES["CONFIRM_CABLE_PURCHASE"]:
        if text == "1":
            pkg = session_data.get("selected_package", {})
            cost = Decimal(str(pkg.get("amount", "0")))

            if user.wallet_balance < cost:
                send_whatsapp_message(chat_id,
                                      f"❌ Insufficient balance! Required: {format_currency(cost)} | Balance: {format_currency(user.wallet_balance)}")
                reset_user_to_idle(user)
                return jsonify({"status": "insufficient_balance"}), 200

            user.wallet_balance -= cost
            db.session.commit()

            send_whatsapp_message(chat_id, "⏳ Submitting Cable TV subscription request...")
            result = process_cable_tv(session_data["provider"], session_data["iuc"], pkg["code"], float(cost))

            if result.get("status") == "SUCCESS":
                tx = Transaction(
                    user_id=user.id,
                    reference=result.get("reference", f"CABLE_{user.id}_{session_data['iuc']}"),
                    amount=cost,
                    type='CABLE',
                    recipient=session_data['iuc'],
                    status='SUCCESS',
                    description=f"{session_data['provider']} {pkg['name']}"
                )
                db.session.add(tx)
                db.session.commit()
                send_whatsapp_message(
                    chat_id,
                    f"✅ *Cable TV Recharged Successfully!*\n"
                    f"• Ref: `{result.get('reference')}`\n"
                    f"• New Balance: *{format_currency(user.wallet_balance)}*"
                )
            else:
                user.wallet_balance += cost
                db.session.commit()
                send_whatsapp_message(chat_id,
                                      f"❌ Recharging failed: {result.get('reason', 'Provider error')}. Wallet refunded.")
        else:
            send_whatsapp_message(chat_id, "🚫 Cable TV subscription order cancelled.")

        reset_user_to_idle(user)

    # ==========================================
    # --- 4. ELECTRICITY BILL FLOW ---
    # ==========================================
    elif current_state == STATES["AWAITING_ELEC_DISCO"]:
        discos = {
            "1": "IKEDC",
            "2": "EKEDC",
            "3": "AEDC",
            "4": "IBEDC",
            "5": "KEDCO",
            "6": "PHED"
        }
        if text not in discos:
            send_whatsapp_message(chat_id, "❌ Invalid selection. Reply with a number from 1 to 6:")
        else:
            session_data["disco"] = discos[text]
            user.set_session(STATES["AWAITING_ELEC_METER_TYPE"], session_data)
            db.session.commit()
            send_whatsapp_message(chat_id, f"💡 Select Meter Type for *{discos[text]}*:\n1. Prepaid\n2. Postpaid")

    elif current_state == STATES["AWAITING_ELEC_METER_TYPE"]:
        if text not in ["1", "2"]:
            send_whatsapp_message(chat_id, "❌ Reply 1 for Prepaid or 2 for Postpaid:")
        else:
            session_data["meter_type"] = "PREPAID" if text == "1" else "POSTPAID"
            user.set_session(STATES["AWAITING_ELEC_METER_NO"], session_data)
            db.session.commit()
            send_whatsapp_message(chat_id,
                                  f"🔢 Enter Meter Number ({session_data['disco']} {session_data['meter_type']}):")

    elif current_state == STATES["AWAITING_ELEC_METER_NO"]:
        clean_meter = text.replace(" ", "").strip()
        if not clean_meter.isdigit() or len(clean_meter) < 6:
            send_whatsapp_message(chat_id, "❌ Enter a valid Meter Number:")
        else:
            disco = session_data.get("disco", "ELEC")
            mtype = session_data.get("meter_type", "PREPAID")
            send_whatsapp_message(chat_id, f"⏳ Verifying meter `{clean_meter}` with {disco}...")

            ver = verify_meter(disco, clean_meter, mtype)
            if not ver.get("valid"):
                send_whatsapp_message(chat_id,
                                      f"❌ Meter Verification Failed: {ver.get('message', 'Invalid meter number')}\nPlease check and re-enter Meter Number:")
                return jsonify({"status": "verification_failed"}), 200

            session_data["meter_no"] = clean_meter
            session_data["customer_name"] = ver.get("customer_name", "Registered Meter Account")
            user.set_session(STATES["AWAITING_ELEC_AMOUNT"], session_data)
            db.session.commit()

            send_whatsapp_message(
                chat_id,
                f"👤 Meter Account: *{session_data['customer_name']}*\n"
                f"💵 Enter Amount to purchase (Minimum ₦500):"
            )

    elif current_state == STATES["AWAITING_ELEC_AMOUNT"]:
        if not text.isdigit() or int(text) < 500:
            send_whatsapp_message(chat_id, "❌ Minimum electricity purchase is ₦500. Enter a valid numerical amount:")
        else:
            session_data["amount"] = text
            user.set_session(STATES["CONFIRM_ELEC_PURCHASE"], session_data)
            db.session.commit()

            confirmation_msg = (
                f"🧾 *Confirm Electricity Purchase*\n"
                f"────────────────────\n"
                f"• Disco: *{session_data['disco']}*\n"
                f"• Meter Type: *{session_data['meter_type']}*\n"
                f"• Meter No: `{session_data['meter_no']}`\n"
                f"• Account: *{session_data['customer_name']}*\n"
                f"• Amount: *{format_currency(text)}*\n\n"
                f"Reply *1* to Confirm Payment or *0* to Cancel."
            )
            send_whatsapp_message(chat_id, confirmation_msg)

    elif current_state == STATES["CONFIRM_ELEC_PURCHASE"]:
        if text == "1":
            amount = Decimal(session_data.get("amount", "0"))
            if user.wallet_balance < amount:
                send_whatsapp_message(chat_id,
                                      f"❌ Insufficient wallet balance! Cost: {format_currency(amount)} | Balance: {format_currency(user.wallet_balance)}")
                reset_user_to_idle(user)
                return jsonify({"status": "insufficient_balance"}), 200

            user.wallet_balance -= amount
            db.session.commit()

            send_whatsapp_message(chat_id, "⏳ Generating Electricity Token...")
            result = process_electricity_payment(session_data["disco"], session_data["meter_no"],
                                                 session_data["meter_type"], float(amount))

            if result.get("status") == "SUCCESS":
                token_text = f"\n• *TOKEN:* `{result.get('token')}`" if result.get('token') else ""
                tx = Transaction(
                    user_id=user.id,
                    reference=result.get("reference", f"ELEC_{user.id}_{session_data['meter_no']}"),
                    amount=amount,
                    type='ELECTRICITY',
                    recipient=session_data['meter_no'],
                    status='SUCCESS',
                    description=f"{session_data['disco']} {session_data['meter_no']}"
                )
                db.session.add(tx)
                db.session.commit()
                send_whatsapp_message(
                    chat_id,
                    f"✅ *Electricity Payment Successful!*{token_text}\n"
                    f"• Ref: `{result.get('reference')}`\n"
                    f"• New Balance: *{format_currency(user.wallet_balance)}*"
                )
            else:
                user.wallet_balance += amount
                db.session.commit()
                send_whatsapp_message(chat_id,
                                      f"❌ Transaction failed: {result.get('reason', 'Provider error')}. Wallet refunded.")
        else:
            send_whatsapp_message(chat_id, "🚫 Electricity bill payment cancelled.")

        reset_user_to_idle(user)

    # ==========================================
    # --- 5. BETTING TOP-UP FLOW ---
    # ==========================================
    elif current_state == STATES["AWAITING_BET_PLATFORM"]:
        platforms = {
            "1": "SportyBet",
            "2": "Bet9ja",
            "3": "1xBet",
            "4": "BangBet",
            "5": "MerryBet"
        }
        if text not in platforms:
            send_whatsapp_message(chat_id, "❌ Reply with a valid option from 1 to 5:")
        else:
            session_data["platform"] = platforms[text]
            user.set_session(STATES["AWAITING_BET_USERID"], session_data)
            db.session.commit()
            send_whatsapp_message(chat_id, f"⚽ Enter your *{platforms[text]}* User ID / Account ID:")

    elif current_state == STATES["AWAITING_BET_USERID"]:
        clean_user_id = text.replace(" ", "").strip()
        if not clean_user_id.isalnum():
            send_whatsapp_message(chat_id, "❌ Enter a valid alphanumeric Betting User ID:")
        else:
            platform = session_data.get("platform", "Betting")
            send_whatsapp_message(chat_id, f"⏳ Validating {platform} User ID `{clean_user_id}`...")

            ver = verify_betting_account(platform, clean_user_id)
            if not ver.get("valid"):
                send_whatsapp_message(chat_id,
                                      f"❌ Account Validation Failed: {ver.get('message', 'Invalid User ID')}\nPlease re-enter User ID:")
                return jsonify({"status": "verification_failed"}), 200

            session_data["bet_user_id"] = clean_user_id
            session_data["account_name"] = ver.get("account_name", "Verified Betting Account")
            user.set_session(STATES["AWAITING_BET_AMOUNT"], session_data)
            db.session.commit()

            send_whatsapp_message(
                chat_id,
                f"👤 Account Name: *{session_data['account_name']}*\n"
                f"💵 Enter Top-up amount (Minimum ₦100):"
            )

    elif current_state == STATES["AWAITING_BET_AMOUNT"]:
        if not text.isdigit() or int(text) < 100:
            send_whatsapp_message(chat_id, "❌ Minimum betting top-up is ₦100. Enter amount:")
        else:
            session_data["amount"] = text
            user.set_session(STATES["CONFIRM_BET_PURCHASE"], session_data)
            db.session.commit()

            msg = (
                f"🧾 *Confirm Betting Top-Up*\n"
                f"────────────────────\n"
                f"• Platform: *{session_data['platform']}*\n"
                f"• User ID: `{session_data['bet_user_id']}`\n"
                f"• Account Name: *{session_data['account_name']}*\n"
                f"• Amount: *{format_currency(text)}*\n\n"
                f"Reply *1* to Confirm Payment or *0* to Cancel."
            )
            send_whatsapp_message(chat_id, msg)

    elif current_state == STATES["CONFIRM_BET_PURCHASE"]:
        if text == "1":
            amount = Decimal(session_data.get("amount", "0"))
            if user.wallet_balance < amount:
                send_whatsapp_message(chat_id,
                                      f"❌ Insufficient wallet balance! Required: {format_currency(amount)} | Balance: {format_currency(user.wallet_balance)}")
                reset_user_to_idle(user)
                return jsonify({"status": "insufficient_balance"}), 200

            user.wallet_balance -= amount
            db.session.commit()

            send_whatsapp_message(chat_id, f"⏳ Processing {session_data['platform']} wallet top-up...")
            result = process_betting_topup(session_data["platform"], session_data["bet_user_id"], float(amount))

            if result.get("status") == "SUCCESS":
                tx = Transaction(
                    user_id=user.id,
                    reference=result.get("reference", f"BET_{user.id}_{session_data['bet_user_id']}"),
                    amount=amount,
                    type='BETTING',
                    recipient=session_data['bet_user_id'],
                    status='SUCCESS',
                    description=f"{session_data['platform']} Wallet Topup"
                )
                db.session.add(tx)
                db.session.commit()
                send_whatsapp_message(
                    chat_id,
                    f"✅ *Betting Account Credited Successfully!*\n"
                    f"• Ref: `{result.get('reference')}`\n"
                    f"• New Balance: *{format_currency(user.wallet_balance)}*"
                )
            else:
                user.wallet_balance += amount
                db.session.commit()
                send_whatsapp_message(chat_id,
                                      f"❌ Top-up failed: {result.get('reason', 'Provider error')}. Wallet refunded.")
        else:
            send_whatsapp_message(chat_id, "🚫 Betting wallet top-up cancelled.")

        reset_user_to_idle(user)

    # ==========================================
    # --- 6. EDUCATION PIN FLOW ---
    # ==========================================
    elif current_state == STATES["AWAITING_EDU_EXAM"]:
        exams = {
            "1": {"name": "WAEC", "price": 3800},
            "2": {"name": "NECO", "price": 1200},
            "3": {"name": "JAMB", "price": 4700}
        }
        if text not in exams:
            send_whatsapp_message(chat_id, "❌ Reply 1 for WAEC, 2 for NECO, or 3 for JAMB:")
        else:
            session_data["exam"] = exams[text]["name"]
            session_data["unit_price"] = exams[text]["price"]
            user.set_session(STATES["AWAITING_EDU_QUANTITY"], session_data)
            db.session.commit()

            send_whatsapp_message(
                chat_id,
                f"🎓 *{session_data['exam']} Result Pin*\n"
                f"Price per PIN: {format_currency(session_data['unit_price'])}\n\n"
                f"Enter quantity of PINs needed (1 - 5):"
            )

    elif current_state == STATES["AWAITING_EDU_QUANTITY"]:
        if not text.isdigit() or not (1 <= int(text) <= 5):
            send_whatsapp_message(chat_id, "❌ Please enter a valid quantity between 1 and 5:")
        else:
            qty = int(text)
            unit_price = session_data.get("unit_price", 0)
            total_price = Decimal(str(unit_price * qty))

            session_data["quantity"] = qty
            session_data["total_price"] = str(total_price)
            user.set_session(STATES["CONFIRM_EDU_PURCHASE"], session_data)
            db.session.commit()

            msg = (
                f"🧾 *Confirm Education PIN Purchase*\n"
                f"────────────────────\n"
                f"• Exam: *{session_data['exam']}*\n"
                f"• Quantity: *{qty}*\n"
                f"• Total Cost: *{format_currency(total_price)}*\n\n"
                f"Reply *1* to Confirm Payment or *0* to Cancel."
            )
            send_whatsapp_message(chat_id, msg)

    elif current_state == STATES["CONFIRM_EDU_PURCHASE"]:
        if text == "1":
            cost = Decimal(session_data.get("total_price", "0"))
            if user.wallet_balance < cost:
                send_whatsapp_message(chat_id,
                                      f"❌ Insufficient balance! Required: {format_currency(cost)} | Balance: {format_currency(user.wallet_balance)}")
                reset_user_to_idle(user)
                return jsonify({"status": "insufficient_balance"}), 200

            user.wallet_balance -= cost
            db.session.commit()

            send_whatsapp_message(chat_id, f"⏳ Generating {session_data['exam']} PIN(s)...")
            result = process_education_pin(session_data["exam"], session_data["quantity"])

            if result.get("status") == "SUCCESS":
                pins = result.get("pins", [])
                pin_text = "\n".join(
                    [f"• Pin {i + 1}: `{p}`" for i, p in enumerate(pins)]) if pins else f"• Pin: `{result.get('pin')}`"
                tx = Transaction(
                    user_id=user.id,
                    reference=result.get("reference", f"EDU_{user.id}_{session_data['exam']}"),
                    amount=cost,
                    type='EDUCATION',
                    recipient=chat_id,
                    status='SUCCESS',
                    description=f"{session_data['exam']} x{session_data['quantity']}"
                )
                db.session.add(tx)
                db.session.commit()

                send_whatsapp_message(
                    chat_id,
                    f"✅ *Education PIN(s) Generated Successfully!*\n"
                    f"────────────────────\n"
                    f"{pin_text}\n\n"
                    f"• Ref: `{result.get('reference')}`\n"
                    f"• New Balance: *{format_currency(user.wallet_balance)}*"
                )
            else:
                user.wallet_balance += cost
                db.session.commit()
                send_whatsapp_message(chat_id,
                                      f"❌ Generation failed: {result.get('reason', 'Provider error')}. Wallet refunded.")
        else:
            send_whatsapp_message(chat_id, "🚫 Education PIN purchase cancelled.")

        reset_user_to_idle(user)

    return jsonify({"status": "success"}), 200


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    debug_mode = os.getenv("FLASK_ENV") == "development"
    app.run(host="0.0.0.0", port=port, debug=debug_mode)