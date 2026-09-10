import os
import hmac
import hashlib
import requests
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from decimal import Decimal
from flask_admin import Admin
from flask_admin.contrib.sqla import ModelView

from models import db, User, Transaction
from vtu_service import (
    process_airtime_purchase,
    fetch_data_variations,
    process_data_purchase,
    process_cable_tv,
    process_electricity_payment,
    process_betting_topup,
    process_education_pin
)
from wallet_service import generate_payment_link, calculate_paystack_gross

load_dotenv(override=True)

app = Flask(__name__)

secret_key = os.getenv("SECRET_KEY")
if not secret_key and os.getenv("FLASK_ENV") == "production":
    raise RuntimeError("SECRET_KEY must be configured in production")
app.config['SECRET_KEY'] = secret_key or "dev-only-secret-key"
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db.init_app(app)

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "sk_test_xxx")
NODE_BRIDGE_URL = os.getenv("NODE_BRIDGE_URL", "http://localhost:3000/api/sendText")
BRIDGE_API_TOKEN = os.getenv("BRIDGE_API_TOKEN")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")

# State Management Machine
STATES = {
    "IDLE": "IDLE",
    # Airtime
    "AWAITING_AIRTIME_NETWORK": "AWAITING_AIRTIME_NETWORK",
    "AWAITING_AIRTIME_NUMBER": "AWAITING_AIRTIME_NUMBER",
    "AWAITING_AIRTIME_AMOUNT": "AWAITING_AIRTIME_AMOUNT",
    # Data
    "AWAITING_DATA_NETWORK": "AWAITING_DATA_NETWORK",
    "AWAITING_DATA_PLAN": "AWAITING_DATA_PLAN",
    "AWAITING_DATA_NUMBER": "AWAITING_DATA_NUMBER",
    # Cable TV
    "AWAITING_CABLE_PROVIDER": "AWAITING_CABLE_PROVIDER",
    "AWAITING_CABLE_PACKAGE": "AWAITING_CABLE_PACKAGE",
    "AWAITING_CABLE_SMARTCARD": "AWAITING_CABLE_SMARTCARD",
    # Electricity
    "AWAITING_ELEC_DISCO": "AWAITING_ELEC_DISCO",
    "AWAITING_ELEC_TYPE": "AWAITING_ELEC_TYPE",
    "AWAITING_ELEC_METER": "AWAITING_ELEC_METER",
    "AWAITING_ELEC_AMOUNT": "AWAITING_ELEC_AMOUNT",
    # Betting
    "AWAITING_BET_PLATFORM": "AWAITING_BET_PLATFORM",
    "AWAITING_BET_USERID": "AWAITING_BET_USERID",
    "AWAITING_BET_AMOUNT": "AWAITING_BET_AMOUNT",
    # Education
    "AWAITING_EDU_EXAM": "AWAITING_EDU_EXAM",
    "AWAITING_EDU_QTY": "AWAITING_EDU_QTY",
    # Fund
    "AWAITING_FUND_AMOUNT": "AWAITING_FUND_AMOUNT",
}


# --- FLASK ADMIN CONTROL CENTER ---
class UserAdminView(ModelView):
    column_searchable_list = ['phone', 'whatsapp_id', 'name']
    column_filters = ['created_at']
    column_list = ['id', 'phone', 'name', 'wallet_balance', 'current_state', 'created_at']
    column_formatters = {
        'wallet_balance': lambda v, c, m, p: f"₦{m.wallet_balance:,.2f}"
    }


class TransactionAdminView(ModelView):
    column_searchable_list = ['reference', 'recipient', 'description']
    column_filters = ['type', 'status', 'created_at']
    column_list = ['id', 'user_id', 'reference', 'type', 'amount', 'recipient', 'status', 'created_at']
    column_formatters = {
        'amount': lambda v, c, m, p: f"₦{m.amount:,.2f}"
    }


# Initialize Admin
admin = Admin(app, name='WAJ VTU Control Center')
admin.base_template = 'admin/master.html'

admin.add_view(UserAdminView(User, db))
admin.add_view(TransactionAdminView(Transaction, db))


@app.before_request
def protect_admin():
    if not request.path.startswith('/admin'):
        return None

    credentials = request.authorization
    if (
        not ADMIN_USERNAME
        or not ADMIN_PASSWORD
        or not credentials
        or not hmac.compare_digest(credentials.username, ADMIN_USERNAME)
        or not hmac.compare_digest(credentials.password, ADMIN_PASSWORD)
    ):
        response = jsonify({"error": "Authentication required"})
        response.headers['WWW-Authenticate'] = 'Basic realm="WAJ VTU Admin"'
        return response, 401

    return None


with app.app_context():
    db.create_all()



def valid_bridge_request():
    authorization = request.headers.get('Authorization', '')
    expected = f"Bearer {BRIDGE_API_TOKEN}" if BRIDGE_API_TOKEN else ''
    return bool(expected) and hmac.compare_digest(authorization, expected)


def send_whatsapp_message(chat_id, text):
    """Utility function to send WhatsApp messages through the Node.js Baileys Bridge."""
    try:
        headers = {"Authorization": f"Bearer {BRIDGE_API_TOKEN}"} if BRIDGE_API_TOKEN else {}
        requests.post(
            NODE_BRIDGE_URL,
            json={"chatId": chat_id, "text": text},
            headers=headers,
            timeout=10
        )
    except Exception as e:
        print(f"Error sending message to Node bridge: {e}")


def get_or_create_user(whatsapp_id):
    """Fetches existing user from DB or registers a new user by WhatsApp ID."""
    user = User.query.filter_by(whatsapp_id=whatsapp_id).first()
    if not user:
        clean_phone = whatsapp_id.split('@')[0]
        user = User(
            whatsapp_id=whatsapp_id,
            phone=clean_phone,
            name=f"User_{clean_phone[-4:]}",
            wallet_balance=Decimal('0.00'),
            current_state=STATES["IDLE"],
            state_data={}
        )
        db.session.add(user)
        db.session.commit()
    return user


def set_user_session(user, state, data=None):
    """Persists user current state and conversation parameters in the database."""
    user.current_state = state
    user.state_data = data if data is not None else {}
    db.session.commit()


def send_main_menu(user):
    """Renders the official WAJ VTU main menu layout."""
    menu = (
        " *WAJ VTU SERVICES*\n"
        "────────────────────\n"
        f"Hello *{user.name}*, welcome!\n\n"
        f"💳 *Wallet Balance:* ₦{user.wallet_balance:,.2f}\n\n"
        "*Main Menu*\n"
        "1. Buy Airtime\n"
        "2. Buy Data Bundle\n"
        "3. Cable TV Subscription\n"
        "4. Electricity Bill Payment\n"
        "5. Betting Wallet Topup\n"
        "6. Education Pins (WAEC / JAMB)\n"
        "7. Fund Wallet\n"
        "8. Account & History\n\n"
        "_Reply with a number to proceed._"
    )
    send_whatsapp_message(user.whatsapp_id, menu)

@app.route('/health', methods=['GET'])
def health_check_endpoint():  # <-- Changed function name here
    return {"status": "ok"}, 200


# --- WHATSAPP BOT WEBHOOK ---
@app.route("/whatsapp/webhook", methods=["POST"])
@app.route("/bot/webhook", methods=["POST"])
def whatsapp_webhook():
    if not valid_bridge_request():
        return jsonify({"error": "Unauthorized"}), 401

    payload = request.json or {}

    msg = payload.get("payload", payload)
    chat_id = msg.get("from") or msg.get("chatId")
    text = msg.get("body", "").strip()

    if msg.get("fromMe") or not text or not chat_id:
        return jsonify({"status": "ignored"}), 200

    user = get_or_create_user(chat_id)
    current_state = user.current_state or STATES["IDLE"]
    session_data = user.state_data or {}

    # Global Reset & Greeting Commands
    if text.lower() in ["menu", "reset", "cancel", "0", "hi", "hello"]:
        set_user_session(user, STATES["IDLE"], {})
        send_main_menu(user)
        return jsonify({"status": "success"}), 200

    # --- IDLE STATE ---
    if current_state == STATES["IDLE"]:
        if text == "1":
            set_user_session(user, STATES["AWAITING_AIRTIME_NETWORK"], {})
            send_whatsapp_message(
                chat_id,
                "📱 *Select Network Provider*\n"
                "────────────────────\n"
                "1. MTN\n"
                "2. Airtel\n"
                "3. Glo\n"
                "4. 9mobile\n\n"
                "_Reply with a number (1-4) or type *0* for Main Menu._"
            )
        elif text == "2":
            set_user_session(user, STATES["AWAITING_DATA_NETWORK"], {})
            send_whatsapp_message(
                chat_id,
                "📶 *Select Network Provider*\n"
                "────────────────────\n"
                "1. MTN\n"
                "2. Airtel\n"
                "3. Glo\n"
                "4. 9mobile\n\n"
                "_Reply with a number (1-4) or type *0* for Main Menu._"
            )
        elif text == "3":
            set_user_session(user, STATES["AWAITING_CABLE_PROVIDER"], {})
            send_whatsapp_message(
                chat_id,
                "📺 *Select Cable Provider*\n"
                "────────────────────\n"
                "1. DSTV\n"
                "2. GOTV\n"
                "3. Startimes\n\n"
                "_Reply with a number (1-3) or type *0* for Main Menu._"
            )
        elif text == "4":
            set_user_session(user, STATES["AWAITING_ELEC_DISCO"], {})
            send_whatsapp_message(
                chat_id,
                "💡 *Select Electricity Provider*\n"
                "────────────────────\n"
                "1. Ikeja Electric (IKEDC)\n"
                "2. Eko Electric (EKEDC)\n"
                "3. Abuja Electric (AEDC)\n"
                "4. Ibadan Electric (IBEDC)\n"
                "5. Enugu Electric (EEDC)\n"
                "6. Port Harcourt Electric (PHEDC)\n\n"
                "_Reply with a number (1-6) or type *0* for Main Menu._"
            )
        elif text == "5":
            set_user_session(user, STATES["AWAITING_BET_PLATFORM"], {})
            send_whatsapp_message(
                chat_id,
                "⚽ *Select Betting Platform*\n"
                "────────────────────\n"
                "1. SportyBet\n"
                "2. Bet9ja\n"
                "3. 1xBet\n"
                "4. BangBet\n\n"
                "_Reply with a number (1-4) or type *0* for Main Menu._"
            )
        elif text == "6":
            set_user_session(user, STATES["AWAITING_EDU_EXAM"], {})
            send_whatsapp_message(
                chat_id,
                "🎓 *Select Exam Body*\n"
                "────────────────────\n"
                "1. WAEC Result Checker\n"
                "2. JAMB Registration Pin\n\n"
                "_Reply with a number (1-2) or type *0* for Main Menu._"
            )
        elif text in ["7", "wallet", "balance", "fund"]:
            set_user_session(user, STATES["AWAITING_FUND_AMOUNT"], {})
            send_whatsapp_message(
                chat_id,
                "💳 *Fund Wallet*\n"
                "────────────────────\n"
                f"• *Current Balance:* ₦{user.wallet_balance:,.2f}\n"
                f"• *Minimum Deposit:* ₦1,000.00\n\n"
                "Enter the amount in NGN you want to deposit (e.g., 1000):"
            )
        elif text == "8":
            txs = Transaction.query.filter_by(user_id=user.id).order_by(Transaction.created_at.desc()).limit(5).all()
            history_text = (
                "👤 *Account Summary*\n"
                "────────────────────\n"
                f"• *Phone:* {user.phone}\n"
                f"• *Balance:* ₦{user.wallet_balance:,.2f}\n\n"
                "📜 *Recent Transactions*\n"
            )
            if txs:
                for tx in txs:
                    history_text += f"• [{tx.type}] ₦{tx.amount:,.2f} - {tx.status} ({tx.created_at.strftime('%Y-%m-%d %H:%M')})\n"
            else:
                history_text += "No transactions found.\n"
            history_text += "\n_Type *MENU* for main options._"
            send_whatsapp_message(chat_id, history_text)
        else:
            send_main_menu(user)

    # --- FUND WALLET FLOW ---
    elif current_state == STATES["AWAITING_FUND_AMOUNT"]:
        if not text.isdigit() or int(text) < 1000:
            send_whatsapp_message(
                chat_id,
                "❌ *Minimum deposit is ₦1,000.*\n\nPlease enter an amount of ₦1,000 or more:"
            )
        else:
            wallet_credit_amount = float(text)
            user_email = f"{user.phone}@wajvtu.com"

            send_whatsapp_message(chat_id, "⏳ Generating secure payment link...")

            link_res = generate_payment_link(user_email, wallet_credit_amount, user.phone, pass_fee_to_user=True)

            if link_res.get("status") == "SUCCESS":
                pay_url = link_res["payment_url"]
                gross_payable = link_res["gross_amount"]
                gateway_fee = link_res["fee_amount"]

                msg = (
                    "💳 *WAJ VTU Wallet Topup*\n"
                    "────────────────────\n"
                    f"• *Wallet Credit:* ₦{wallet_credit_amount:,.2f}\n"
                    f"• *Gateway Fee:* ₦{gateway_fee:,.2f}\n"
                    f"• *Total Payable:* ₦{gross_payable:,.2f}\n\n"
                    f"Tap link to pay via Transfer, Card, or USSD:\n🔗 {pay_url}\n\n"
                    "_Your wallet updates automatically upon payment!_"
                )
            else:
                msg = f"❌ Payment setup failed: {link_res.get('reason')}. Reply MENU to retry."

            send_whatsapp_message(chat_id, msg)
            set_user_session(user, STATES["IDLE"], {})

    # --- AIRTIME FLOW ---
    elif current_state == STATES["AWAITING_AIRTIME_NETWORK"]:
        networks = {"1": "MTN", "2": "AIRTEL", "3": "GLO", "4": "9MOBILE"}
        if text not in networks:
            send_whatsapp_message(chat_id, "❌ Invalid choice. Reply 1, 2, 3, or 4:")
        else:
            session_data["network"] = networks[text]
            set_user_session(user, STATES["AWAITING_AIRTIME_NUMBER"], session_data)
            send_whatsapp_message(chat_id, "📞 Enter recipient phone number (e.g., 08012345678):")

    elif current_state == STATES["AWAITING_AIRTIME_NUMBER"]:
        if len(text) != 11 or not text.isdigit():
            send_whatsapp_message(chat_id, "❌ Invalid phone number. Enter an 11-digit number:")
        else:
            session_data["phone"] = text
            set_user_session(user, STATES["AWAITING_AIRTIME_AMOUNT"], session_data)
            send_whatsapp_message(chat_id, f"💵 Enter amount in NGN (Balance: ₦{user.wallet_balance:,.2f}):")

    elif current_state == STATES["AWAITING_AIRTIME_AMOUNT"]:
        if not text.isdigit() or int(text) < 50:
            send_whatsapp_message(chat_id, "❌ Minimum purchase is ₦50. Enter a valid amount:")
        else:
            amount_decimal = Decimal(str(text))
            if user.wallet_balance < amount_decimal:
                send_whatsapp_message(
                    chat_id,
                    f"❌ Insufficient Balance!\n"
                    f"Required: ₦{amount_decimal:,.2f} | Wallet: ₦{user.wallet_balance:,.2f}\n"
                    f"Type *MENU* to return."
                )
                set_user_session(user, STATES["IDLE"], {})
                return jsonify({"status": "insufficient_funds"}), 200

            network = session_data["network"]
            recipient_phone = session_data["phone"]

            user.wallet_balance -= amount_decimal
            db.session.commit()

            send_whatsapp_message(chat_id,
                                  f"⏳ Processing ₦{amount_decimal:,.2f} {network} Airtime to {recipient_phone}...")
            result = process_airtime_purchase(recipient_phone, network, float(text))

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
                    "✅ *Airtime Purchase Successful!*\n"
                    "────────────────────\n"
                    f"• *Ref:* `{result['reference']}`\n"
                    f"• *New Balance:* ₦{user.wallet_balance:,.2f}"
                )
            else:
                user.wallet_balance += amount_decimal
                db.session.commit()
                send_whatsapp_message(chat_id, f"❌ Failed: {result.get('reason')}. Balance refunded.")

            set_user_session(user, STATES["IDLE"], {})

    # --- DATA BUNDLE FLOW ---
    elif current_state == STATES["AWAITING_DATA_NETWORK"]:
        networks = {"1": "MTN", "2": "AIRTEL", "3": "GLO", "4": "9MOBILE"}
        if text not in networks:
            send_whatsapp_message(chat_id, "❌ Reply with 1, 2, 3, or 4:")
        else:
            network_name = networks[text]
            session_data["network"] = network_name
            send_whatsapp_message(chat_id, f"⏳ Fetching WAJ VTU {network_name} data plans...")

            variations = fetch_data_variations(network_name)
            if not variations:
                send_whatsapp_message(chat_id, "❌ Could not load plans. Type MENU to restart.")
                set_user_session(user, STATES["IDLE"], {})
                return jsonify({"status": "error"}), 200

            plan_menu = f"📊 *Select {network_name} Data Plan*\n────────────────────\n"
            plans_map = {}
            for idx, plan in enumerate(variations[:8], start=1):
                name = plan.get("name")
                # Apply ₦50 retail markup to data bundles
                cost = float(plan.get("variation_amount")) + 50.00
                code = plan.get("variation_code")
                plans_map[str(idx)] = {"code": code, "amount": str(cost), "name": name}
                plan_menu += f"{idx}. {name} - ₦{cost:,.2f}\n"

            plan_menu += "\n_Reply with plan number (e.g., 1)_"
            session_data["plans_map"] = plans_map
            set_user_session(user, STATES["AWAITING_DATA_PLAN"], session_data)
            send_whatsapp_message(chat_id, plan_menu)

    elif current_state == STATES["AWAITING_DATA_PLAN"]:
        plans_map = session_data.get("plans_map", {})
        if text not in plans_map:
            send_whatsapp_message(chat_id, "❌ Invalid selection. Choose a plan number from above:")
        else:
            session_data["selected_plan"] = plans_map[text]
            set_user_session(user, STATES["AWAITING_DATA_NUMBER"], session_data)
            send_whatsapp_message(chat_id, f"📞 Enter recipient phone number for *{plans_map[text]['name']}*:")

    elif current_state == STATES["AWAITING_DATA_NUMBER"]:
        if len(text) != 11 or not text.isdigit():
            send_whatsapp_message(chat_id, "❌ Enter a valid 11-digit phone number:")
        else:
            recipient_phone = text
            plan = session_data["selected_plan"]
            network = session_data["network"]
            cost_decimal = Decimal(str(plan["amount"]))

            if user.wallet_balance < cost_decimal:
                send_whatsapp_message(
                    chat_id,
                    f"❌ Insufficient wallet balance!\n"
                    f"Plan Cost: ₦{cost_decimal:,.2f} | Balance: ₦{user.wallet_balance:,.2f}\n"
                    f"Type *MENU* to cancel."
                )
                set_user_session(user, STATES["IDLE"], {})
                return jsonify({"status": "insufficient_balance"}), 200

            user.wallet_balance -= cost_decimal
            db.session.commit()

            send_whatsapp_message(chat_id, f"⏳ Processing {plan['name']} to {recipient_phone}...")
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
                    "✅ *Data Purchase Successful!*\n"
                    "────────────────────\n"
                    f"• *Ref:* `{result['reference']}`\n"
                    f"• *New Balance:* ₦{user.wallet_balance:,.2f}"
                )
            else:
                user.wallet_balance += cost_decimal
                db.session.commit()
                send_whatsapp_message(chat_id, f"❌ Transaction failed: {result.get('reason')}. Wallet refunded.")

            set_user_session(user, STATES["IDLE"], {})

    # --- CABLE TV FLOW ---
    elif current_state == STATES["AWAITING_CABLE_PROVIDER"]:
        providers = {"1": ("01", "DSTV"), "2": ("02", "GOTV"), "3": ("03", "STARTIMES")}
        if text not in providers:
            send_whatsapp_message(chat_id, "❌ Select provider: 1. DSTV, 2. GOTV, 3. Startimes:")
        else:
            p_code, p_name = providers[text]
            session_data["cable_code"] = p_code
            session_data["cable_name"] = p_name
            set_user_session(user, STATES["AWAITING_CABLE_PACKAGE"], session_data)

            packages = {
                "DSTV": "1. DSTV Padi (₦2,600)\n2. DSTV Yanga (₦4,300)\n3. DSTV Compact (₦12,600)",
                "GOTV": "1. GOTV Smallie (₦1,400)\n2. GOTV Jinja (₦2,800)\n3. GOTV Jolli (₦4,050)",
                "STARTIMES": "1. Nova Monthly (₦1,600)\n2. Basic Monthly (₦2,700)\n3. Classic Monthly (₦3,900)"
            }
            send_whatsapp_message(
                chat_id,
                f"📺 *Select {p_name} Package*\n"
                "────────────────────\n"
                f"{packages.get(p_name)}\n\n"
                "_Includes ₦100 service charge. Reply 1, 2, or 3._"
            )

    elif current_state == STATES["AWAITING_CABLE_PACKAGE"]:
        # Packages include ₦100 convenience fee
        package_map = {
            "1": {"code": "101", "amount": "2600", "name": "Basic/Padi"},
            "2": {"code": "102", "amount": "4300", "name": "Mid Package"},
            "3": {"code": "103", "amount": "12600", "name": "Premium Package"}
        }
        if text not in package_map:
            send_whatsapp_message(chat_id, "❌ Reply with 1, 2, or 3:")
        else:
            session_data["package"] = package_map[text]
            set_user_session(user, STATES["AWAITING_CABLE_SMARTCARD"], session_data)
            send_whatsapp_message(chat_id, f"💳 Enter your {session_data['cable_name']} Smartcard / IUC Number:")

    elif current_state == STATES["AWAITING_CABLE_SMARTCARD"]:
        if not text.isdigit() or len(text) < 8:
            send_whatsapp_message(chat_id, "❌ Invalid Smartcard/IUC Number. Re-enter digits:")
        else:
            iuc_no = text
            pkg = session_data["package"]
            c_code = session_data["cable_code"]
            cost = Decimal(pkg["amount"])

            if user.wallet_balance < cost:
                send_whatsapp_message(
                    chat_id,
                    f"❌ Insufficient Balance! Needed: ₦{cost:,.2f}, Wallet: ₦{user.wallet_balance:,.2f}"
                )
                set_user_session(user, STATES["IDLE"], {})
                return jsonify({"status": "insufficient_funds"}), 200

            user.wallet_balance -= cost
            db.session.commit()

            send_whatsapp_message(chat_id,
                                  f"⏳ Activating {session_data['cable_name']} ({pkg['name']}) on IUC: {iuc_no}...")
            res = process_cable_tv(user.phone, c_code, pkg["code"], iuc_no)

            if res.get("status") == "SUCCESS":
                tx = Transaction(
                    user_id=user.id,
                    reference=res['reference'],
                    amount=cost,
                    type='CABLE',
                    recipient=iuc_no,
                    status='SUCCESS',
                    description=f"{session_data['cable_name']} Subscription to {iuc_no}"
                )
                db.session.add(tx)
                db.session.commit()
                send_whatsapp_message(
                    chat_id,
                    "✅ *Subscription Activated!*\n"
                    "────────────────────\n"
                    f"• *Ref:* `{res['reference']}`\n"
                    f"• *Balance:* ₦{user.wallet_balance:,.2f}"
                )
            else:
                user.wallet_balance += cost
                db.session.commit()
                send_whatsapp_message(chat_id, f"❌ Cable subscription failed: {res.get('reason')}. Wallet refunded.")

            set_user_session(user, STATES["IDLE"], {})

    # --- ELECTRICITY FLOW ---
    elif current_state == STATES["AWAITING_ELEC_DISCO"]:
        discos = {"1": "01", "2": "02", "3": "03", "4": "04", "5": "05", "6": "06"}
        if text not in discos:
            send_whatsapp_message(chat_id, "❌ Select a valid disco number (1-6):")
        else:
            session_data["disco_code"] = discos[text]
            set_user_session(user, STATES["AWAITING_ELEC_TYPE"], session_data)
            send_whatsapp_message(chat_id, "🔌 Select Meter Type:\n1. Prepaid\n2. Postpaid")

    elif current_state == STATES["AWAITING_ELEC_TYPE"]:
        types = {"1": "01", "2": "02"}
        if text not in types:
            send_whatsapp_message(chat_id, "❌ Reply 1 for Prepaid or 2 for Postpaid:")
        else:
            session_data["meter_type"] = types[text]
            set_user_session(user, STATES["AWAITING_ELEC_METER"], session_data)
            send_whatsapp_message(chat_id, "🔢 Enter your Meter Number:")

    elif current_state == STATES["AWAITING_ELEC_METER"]:
        if not text.isdigit() or len(text) < 6:
            send_whatsapp_message(chat_id, "❌ Invalid meter number. Enter valid digits:")
        else:
            session_data["meter_no"] = text
            set_user_session(user, STATES["AWAITING_ELEC_AMOUNT"], session_data)
            send_whatsapp_message(chat_id, f"💵 Enter amount in NGN (Balance: ₦{user.wallet_balance:,.2f}):")

    elif current_state == STATES["AWAITING_ELEC_AMOUNT"]:
        if not text.isdigit() or int(text) < 500:
            send_whatsapp_message(chat_id, "❌ Minimum electricity payment is ₦500. Enter amount:")
        else:
            bill_amount = Decimal(str(text))
            fee = Decimal('100.00')
            total_charge = bill_amount + fee

            if user.wallet_balance < total_charge:
                send_whatsapp_message(
                    chat_id,
                    f"❌ Insufficient funds!\n"
                    f"Amount + ₦100 Fee: ₦{total_charge:,.2f} | Wallet: ₦{user.wallet_balance:,.2f}"
                )
                set_user_session(user, STATES["IDLE"], {})
                return jsonify({"status": "insufficient_funds"}), 200

            user.wallet_balance -= total_charge
            db.session.commit()

            send_whatsapp_message(chat_id,
                                  f"⏳ Processing ₦{bill_amount:,.2f} electricity topup to meter {session_data['meter_no']}...")
            res = process_electricity_payment(
                user.phone, session_data["disco_code"], session_data["meter_type"], session_data["meter_no"],
                float(bill_amount)
            )

            if res.get("status") == "SUCCESS":
                tx = Transaction(
                    user_id=user.id,
                    reference=res['reference'],
                    amount=total_charge,
                    type='ELECTRICITY',
                    recipient=session_data["meter_no"],
                    status='SUCCESS',
                    description=f"Electricity Payment to {session_data['meter_no']}"
                )
                db.session.add(tx)
                db.session.commit()

                token_msg = f"\n🔑 *Token:* `{res.get('token')}`" if res.get("token") and res.get(
                    "token") != "N/A" else ""
                send_whatsapp_message(
                    chat_id,
                    "✅ *Payment Successful!*"
                    f"{token_msg}\n"
                    "────────────────────\n"
                    f"• *Ref:* `{res['reference']}`\n"
                    f"• *Balance:* ₦{user.wallet_balance:,.2f}"
                )
            else:
                user.wallet_balance += total_charge
                db.session.commit()
                send_whatsapp_message(chat_id, f"❌ Electricity payment failed: {res.get('reason')}. Wallet refunded.")

            set_user_session(user, STATES["IDLE"], {})

    # --- BETTING FLOW ---
    elif current_state == STATES["AWAITING_BET_PLATFORM"]:
        platforms = {"1": "01", "2": "02", "3": "03", "4": "04"}
        if text not in platforms:
            send_whatsapp_message(chat_id, "❌ Reply 1 for SportyBet, 2 for Bet9ja, 3 for 1xBet, or 4 for BangBet:")
        else:
            session_data["bet_platform"] = platforms[text]
            set_user_session(user, STATES["AWAITING_BET_USERID"], session_data)
            send_whatsapp_message(chat_id, "🆔 Enter your Betting Customer/User ID:")

    elif current_state == STATES["AWAITING_BET_USERID"]:
        if not text.isalnum():
            send_whatsapp_message(chat_id, "❌ Invalid User ID format. Re-enter:")
        else:
            session_data["bet_user_id"] = text
            set_user_session(user, STATES["AWAITING_BET_AMOUNT"], session_data)
            send_whatsapp_message(chat_id, f"💵 Enter topup amount in NGN (Balance: ₦{user.wallet_balance:,.2f}):")

    elif current_state == STATES["AWAITING_BET_AMOUNT"]:
        if not text.isdigit() or int(text) < 100:
            send_whatsapp_message(chat_id, "❌ Minimum betting topup is ₦100. Enter amount:")
        else:
            amount_decimal = Decimal(str(text))
            if user.wallet_balance < amount_decimal:
                send_whatsapp_message(chat_id, f"❌ Insufficient funds! Needed: ₦{amount_decimal:,.2f}")
                set_user_session(user, STATES["IDLE"], {})
                return jsonify({"status": "insufficient_funds"}), 200

            user.wallet_balance -= amount_decimal
            db.session.commit()

            send_whatsapp_message(chat_id,
                                  f"⏳ Funding betting ID `{session_data['bet_user_id']}` with ₦{amount_decimal:,.2f}...")
            res = process_betting_topup(user.phone, session_data["bet_platform"], session_data["bet_user_id"],
                                        float(text))

            if res.get("status") == "SUCCESS":
                tx = Transaction(
                    user_id=user.id,
                    reference=res['reference'],
                    amount=amount_decimal,
                    type='BETTING',
                    recipient=session_data["bet_user_id"],
                    status='SUCCESS',
                    description=f"Betting Topup to ID {session_data['bet_user_id']}"
                )
                db.session.add(tx)
                db.session.commit()
                send_whatsapp_message(
                    chat_id,
                    "✅ *Betting Wallet Funded!*\n"
                    "────────────────────\n"
                    f"• *Ref:* `{res['reference']}`\n"
                    f"• *Balance:* ₦{user.wallet_balance:,.2f}"
                )
            else:
                user.wallet_balance += amount_decimal
                db.session.commit()
                send_whatsapp_message(chat_id, f"❌ Topup failed: {res.get('reason')}. Wallet refunded.")

            set_user_session(user, STATES["IDLE"], {})

    # --- EDUCATION FLOW ---
    elif current_state == STATES["AWAITING_EDU_EXAM"]:
        exams = {"1": "WAEC", "2": "JAMB"}
        if text not in exams:
            send_whatsapp_message(chat_id, "❌ Select 1 for WAEC or 2 for JAMB:")
        else:
            session_data["exam_type"] = exams[text]
            set_user_session(user, STATES["AWAITING_EDU_QTY"], session_data)
            price = "3,800" if exams[text] == "WAEC" else "4,700"
            send_whatsapp_message(chat_id, f"🎫 *{exams[text]} Pin Price:* ₦{price}\nEnter quantity (e.g., 1):")

    elif current_state == STATES["AWAITING_EDU_QTY"]:
        if not text.isdigit() or int(text) < 1:
            send_whatsapp_message(chat_id, "❌ Enter valid quantity (1, 2, etc.):")
        else:
            qty = int(text)
            exam = session_data["exam_type"]
            unit_price = Decimal('3800.00') if exam == "WAEC" else Decimal('4700.00')
            total_cost = unit_price * qty

            if user.wallet_balance < total_cost:
                send_whatsapp_message(
                    chat_id,
                    f"❌ Insufficient funds! Total Cost: ₦{total_cost:,.2f}, Balance: ₦{user.wallet_balance:,.2f}"
                )
                set_user_session(user, STATES["IDLE"], {})
                return jsonify({"status": "insufficient_funds"}), 200

            user.wallet_balance -= total_cost
            db.session.commit()

            send_whatsapp_message(chat_id, f"⏳ Generating {qty} {exam} Pin(s)...")
            res = process_education_pin(user.phone, exam, qty)

            if res.get("status") == "SUCCESS":
                tx = Transaction(
                    user_id=user.id,
                    reference=res['reference'],
                    amount=total_cost,
                    type='EDUCATION',
                    recipient=user.phone,
                    status='SUCCESS',
                    description=f"{qty}x {exam} Pin Purchase"
                )
                db.session.add(tx)
                db.session.commit()
                send_whatsapp_message(
                    chat_id,
                    "✅ *Pin Purchased Successfully!*\n"
                    "────────────────────\n"
                    f"• *Exam:* {exam}\n"
                    f"• *Pin/Token:* `{res.get('pin')}`\n"
                    f"• *Ref:* `{res['reference']}`\n"
                    f"• *Balance:* ₦{user.wallet_balance:,.2f}"
                )
            else:
                user.wallet_balance += total_cost
                db.session.commit()
                send_whatsapp_message(chat_id, f"❌ Purchase failed: {res.get('reason')}. Wallet refunded.")

            set_user_session(user, STATES["IDLE"], {})

    return jsonify({"status": "success"}), 200


# --- PAYSTACK WEBHOOK (AUTOMATED WALLET CREDIT) ---
@app.route("/paystack/webhook", methods=["POST"])
def paystack_webhook():
    paystack_signature = request.headers.get("x-paystack-signature")
    raw_body = request.get_data()

    if not paystack_signature:
        return jsonify({"status": "forbidden", "message": "Missing signature"}), 400

    computed_signature = hmac.new(
        PAYSTACK_SECRET_KEY.encode('utf-8'),
        raw_body,
        hashlib.sha512
    ).hexdigest()

    if not hmac.compare_digest(paystack_signature, computed_signature):
        print("❌ Security Alert: Invalid Paystack signature received.")
        return jsonify({"status": "forbidden"}), 400

    payload = request.json or {}
    event = payload.get("event")

    if event == "charge.success":
        data = payload.get("data", {})
        reference = data.get("reference")
        metadata = data.get("metadata", {})
        customer = data.get("customer", {})

        # Extract target net credit amount from metadata (if available)
        net_credit_str = metadata.get("net_credit_amount")
        if net_credit_str:
            target_wallet_credit = Decimal(str(net_credit_str))
        else:
            # Fallback to gross amount received divided by 100 if metadata is missing
            raw_kobo = data.get("amount", 0)
            gross_received = Decimal(str(raw_kobo)) / Decimal('100.0')
            if gross_received < Decimal('2500.00'):
                target_wallet_credit = round(gross_received * Decimal('0.985'), 2)
            else:
                target_wallet_credit = round((gross_received * Decimal('0.985')) - Decimal('100.00'), 2)

        phone_number = metadata.get("phone_number")
        if not phone_number:
            customer_email = customer.get("email", "")
            phone_number = customer_email.split('@')[0] if customer_email else customer.get("phone", "")

        existing_tx = Transaction.query.filter_by(reference=reference).first()
        if existing_tx:
            return jsonify({"status": "already_processed"}), 200

        user = User.query.filter(
            (User.phone == phone_number) |
            (User.whatsapp_id.like(f"%{phone_number}%"))
        ).first()

        if user:
            user.wallet_balance += target_wallet_credit
            tx = Transaction(
                user_id=user.id,
                reference=reference,
                amount=target_wallet_credit,
                type='DEPOSIT',
                recipient=user.phone,
                status='SUCCESS',
                description=f"Paystack Deposit ({data.get('channel', 'online')})"
            )
            db.session.add(tx)
            db.session.commit()

            print(f"✅ Successfully credited ₦{target_wallet_credit:,.2f} to user {user.phone}")

            send_whatsapp_message(
                user.whatsapp_id,
                "🎉 *WAJ VTU - Wallet Credit Alert!*\n"
                "────────────────────\n"
                f"• *Amount Credited:* ₦{target_wallet_credit:,.2f}\n"
                f"• *Ref:* `{reference}`\n"
                f"• *New Balance:* ₦{user.wallet_balance:,.2f}\n\n"
                "_Type *MENU* to view options._"
            )

    return jsonify({"status": "success"}), 200


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True, host='0.0.0.0', port=5000)