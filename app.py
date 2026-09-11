import os
import json
import uuid
from decimal import Decimal
from flask import Flask, request, jsonify, render_template_string, redirect, url_for
from flask_sqlalchemy import SQLAlchemy

# Import provider functions from your clubkonnect/provider module
from provider import (
    fetch_data_variations,
    process_data_purchase,
    process_airtime_purchase,
    process_cable_tv,
    process_electricity_payment,
    process_betting_topup,
    process_education_pin
)

app = Flask(__name__)

# --- CONFIGURATION ---
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv("DATABASE_URL", "sqlite:///vtu_bot.db")
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)


# --- MODELS ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    phone_number = db.Column(db.String(20), unique=True, nullable=False)
    wallet_balance = db.Column(db.Numeric(10, 2), default=1000.00)  # Default demo balance
    current_state = db.Column(db.String(50), default="IDLE")
    session_data = db.Column(db.Text, default="{}")


class Transaction(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    reference = db.Column(db.String(50), unique=True, nullable=False)
    amount = db.Column(db.Numeric(10, 2), nullable=False)
    type = db.Column(db.String(20), nullable=False)
    recipient = db.Column(db.String(50), nullable=False)
    status = db.Column(db.String(20), nullable=False)
    description = db.Column(db.String(200))


with app.app_context():
    db.create_all()

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
}


# --- HELPER UTILITIES ---
def get_or_create_user(phone_number):
    user = User.query.filter_by(phone_number=phone_number).first()
    if not user:
        user = User(phone_number=phone_number, wallet_balance=Decimal("1000.00"))
        db.session.add(user)
        db.session.commit()
    return user


def set_user_session(user, state, data):
    user.current_state = state
    user.session_data = json.dumps(data)
    db.session.commit()


def get_user_session_data(user):
    try:
        return json.loads(user.session_data or "{}")
    except Exception:
        return {}


def send_whatsapp_message(chat_id, text):
    """
    Placeholder/Wrapper function for Meta Cloud API or your WhatsApp provider SDK.
    Replace print statement with your HTTP POST request to your Meta Cloud API endpoint.
    """
    print(f"\n[OUTGOING WHATSAPP TO {chat_id}]:\n{text}\n")


def categorize_data_plans(plans):
    """
    Categorizes raw data variations into validity & promo tiers:
    - DAILY (1 Day / 24hrs / Daily)
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


# --- MAIN WEBHOOK ENDPOINT ---
@app.route("/webhook", methods=["POST"])
def whatsapp_webhook():
    req_data = request.get_json() or {}

    # Extract phone number and incoming message text
    chat_id = req_data.get("from") or req_data.get("phone") or "2348000000000"
    text = (req_data.get("text") or req_data.get("message") or "").strip()

    user = get_or_create_user(chat_id)
    current_state = user.current_state or STATES["IDLE"]
    session_data = get_user_session_data(user)

    # Global Cancel / Menu Command
    if text.upper() in ["0", "MENU", "CANCEL"]:
        set_user_session(user, STATES["IDLE"], {})
        main_menu = (
            "📌 *MAIN SERVICES MENU*\n"
            "────────────────────\n"
            "1. 📶 Buy Data Bundle\n"
            "2. 📱 Buy Airtime\n"
            "3. 📺 Cable TV Subscription\n"
            "4. 💡 Pay Electricity Bill\n"
            "5. ⚽ Betting Wallet Topup\n"
            "6. 🎓 Education PINs (WAEC/JAMB)\n"
            "7. 💳 Check Wallet Balance\n\n"
            "💳 *Balance:* ₦{:,.2f}\n"
            "_Reply with a service number (1-7)_".format(user.wallet_balance)
        )
        send_whatsapp_message(chat_id, main_menu)
        return jsonify({"status": "ok"}), 200

    # --- IDLE STATE (MAIN MENU) ---
    if current_state == STATES["IDLE"]:
        if text == "1":
            set_user_session(user, STATES["AWAITING_DATA_NETWORK"], {})
            network_menu = (
                "📶 *Select Mobile Network*\n"
                "────────────────────\n"
                "1. MTN\n"
                "2. AIRTEL\n"
                "3. GLO\n"
                "4. 9MOBILE\n\n"
                "_Reply with 1, 2, 3, or 4_"
            )
            send_whatsapp_message(chat_id, network_menu)

        elif text == "2":
            set_user_session(user, STATES["AWAITING_AIRTIME_NETWORK"], {})
            airtime_menu = (
                "📱 *Select Airtime Network*\n"
                "────────────────────\n"
                "1. MTN\n"
                "2. AIRTEL\n"
                "3. GLO\n"
                "4. 9MOBILE\n\n"
                "_Reply with 1, 2, 3, or 4_"
            )
            send_whatsapp_message(chat_id, airtime_menu)

        elif text == "3":
            send_whatsapp_message(chat_id, "📺 *Cable TV Subscription*\nFeature coming soon! Type *MENU* to return.")

        elif text == "4":
            send_whatsapp_message(chat_id, "💡 *Pay Electricity Bill*\nFeature coming soon! Type *MENU* to return.")

        elif text == "5":
            send_whatsapp_message(chat_id, "⚽ *Betting Wallet Topup*\nFeature coming soon! Type *MENU* to return.")

        elif text == "6":
            send_whatsapp_message(chat_id,
                                  "🎓 *Education PINs (WAEC/JAMB)*\nFeature coming soon! Type *MENU* to return.")

        elif text == "7":
            send_whatsapp_message(
                chat_id,
                f"💳 *Wallet Balance:* ₦{user.wallet_balance:,.2f}\n\nType *MENU* to view options."
            )

        else:
            send_whatsapp_message(chat_id,
                                  "❌ Invalid option selected. Reply with a service number (1-7) or type *MENU*.")

    # --- DATA BUNDLE FLOW ---
    elif current_state == STATES["AWAITING_DATA_NETWORK"]:
        networks = {"1": "MTN", "2": "AIRTEL", "3": "GLO", "4": "9MOBILE"}
        if text not in networks:
            send_whatsapp_message(chat_id, "❌ Invalid choice. Reply 1 for MTN, 2 for AIRTEL, 3 for GLO, 4 for 9MOBILE:")
        else:
            network_name = networks[text]
            session_data["network"] = network_name
            send_whatsapp_message(chat_id, f"⏳ Fetching available {network_name} data plans...")

            variations = fetch_data_variations(network_name)
            if not variations:
                send_whatsapp_message(chat_id, "❌ Unable to load plans right now. Type *MENU* to return.")
                set_user_session(user, STATES["IDLE"], {})
                return jsonify({"status": "error"}), 200

            session_data["categorized_plans"] = categorize_data_plans(variations)

            set_user_session(user, STATES["AWAITING_DATA_CATEGORY"], session_data)
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
            send_whatsapp_message(chat_id, "❌ Invalid option. Please reply with a category number from 1 to 6:")
        else:
            selected_cat = cat_map[text]
            network_name = session_data["network"]
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
            for idx, plan in enumerate(filtered_plans[:8], start=1):
                name = plan.get("name")
                cost = float(plan.get("variation_amount")) + 50.00
                code = plan.get("variation_code")
                plans_map[str(idx)] = {"code": code, "amount": str(cost), "name": name}
                plan_menu += f"{idx}. {name} - ₦{cost:,.2f}\n"

            plan_menu += "\n_Reply with the plan number (e.g., 1)_"
            session_data["plans_map"] = plans_map
            set_user_session(user, STATES["AWAITING_DATA_PLAN"], session_data)
            send_whatsapp_message(chat_id, plan_menu)

    elif current_state == STATES["AWAITING_DATA_PLAN"]:
        plans_map = session_data.get("plans_map", {})
        if text not in plans_map:
            send_whatsapp_message(chat_id, "❌ Invalid option. Reply with a valid number from the list:")
        else:
            session_data["selected_plan"] = plans_map[text]
            set_user_session(user, STATES["AWAITING_DATA_NUMBER"], session_data)
            send_whatsapp_message(
                chat_id,
                f"📞 Enter the 11-digit phone number to receive *{plans_map[text]['name']}*:"
            )

    elif current_state == STATES["AWAITING_DATA_NUMBER"]:
        if len(text) != 11 or not text.isdigit():
            send_whatsapp_message(chat_id, "❌ Invalid phone number. Enter a valid 11-digit phone number:")
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

            send_whatsapp_message(chat_id, f"⏳ Processing {plan['name']} for {recipient_phone}...")
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
                    f"✅ *Data Purchase Successful!*\n"
                    f"────────────────────\n"
                    f"• *Ref:* `{result['reference']}`\n"
                    f"• *New Balance:* ₦{user.wallet_balance:,.2f}"
                )
            else:
                user.wallet_balance += cost_decimal
                db.session.commit()
                send_whatsapp_message(chat_id, f"❌ Transaction failed: {result.get('reason')}. Wallet refunded.")

            set_user_session(user, STATES["IDLE"], {})

    # --- AIRTIME FLOW ---
    elif current_state == STATES["AWAITING_AIRTIME_NETWORK"]:
        networks = {"1": "MTN", "2": "AIRTEL", "3": "GLO", "4": "9MOBILE"}
        if text not in networks:
            send_whatsapp_message(chat_id, "❌ Reply with 1, 2, 3, or 4:")
        else:
            session_data["network"] = networks[text]
            set_user_session(user, STATES["AWAITING_AIRTIME_AMOUNT"], session_data)
            send_whatsapp_message(chat_id, f"💵 Enter Airtime amount for *{networks[text]}* (e.g. 500):")

    elif current_state == STATES["AWAITING_AIRTIME_AMOUNT"]:
        if not text.isdigit() or int(text) < 50:
            send_whatsapp_message(chat_id, "❌ Enter a valid amount (minimum ₦50):")
        else:
            session_data["amount"] = text
            set_user_session(user, STATES["AWAITING_AIRTIME_NUMBER"], session_data)
            send_whatsapp_message(chat_id, f"📞 Enter recipient 11-digit phone number for ₦{text} Airtime:")

    elif current_state == STATES["AWAITING_AIRTIME_NUMBER"]:
        if len(text) != 11 or not text.isdigit():
            send_whatsapp_message(chat_id, "❌ Enter a valid 11-digit phone number:")
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

            send_whatsapp_message(chat_id, f"⏳ Processing ₦{amount_decimal} {network} airtime...")
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
                    f"✅ *Airtime Purchase Successful!*\n"
                    f"────────────────────\n"
                    f"• *Ref:* `{result['reference']}`\n"
                    f"• *New Balance:* ₦{user.wallet_balance:,.2f}"
                )
            else:
                user.wallet_balance += amount_decimal
                db.session.commit()
                send_whatsapp_message(chat_id, f"❌ Purchase failed: {result.get('reason')}. Wallet refunded.")

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
        users = User.query.filter(User.phone_number.contains(search_query)).all()
    else:
        users = User.query.order_by(User.id.desc()).all()

    user_rows = ""
    for u in users:
        user_rows += f"""
        <tr>
            <td>#{u.id}</td>
            <td><b>{u.phone_number}</b></td>
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
            recipient=user.phone_number,
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
    app.run(host="0.0.0.0", port=5000, debug=True)