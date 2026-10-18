"""
Customer-facing website routes.

Deliberately thin: every purchase/balance/funding action calls
chat_agent.execute_tool(), the same function the WhatsApp bot uses, so
pricing, wallet debits, provider calls and refunds behave identically on
both surfaces and can't drift apart.
"""
import secrets
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from flask import Blueprint, render_template, request, redirect, url_for, flash, session, send_file, jsonify

from models import db, Transaction, SavedService, User, SupportTicket
from auth import login_required, current_user
from chat_agent import execute_tool
from flask import current_app as app  # Use current_app to avoid circular imports
from provider import verify_smartcard, verify_meter, fetch_cable_plans, fetch_data_variations, fetch_education_packages
from wallet_service import generate_payment_link

web_bp = Blueprint("web", __name__)

SAVED_SERVICE_TYPES = {"airtime", "electricity", "cable"}


def saved_services_for(user):
    return (
        SavedService.query.filter_by(user_id=user.id)
        .order_by(SavedService.service_type, SavedService.created_at.desc())
        .all()
    )

def persist_saved_service(user, service_type, identifier, provider=None, label="", meter_type=""):
    identifier = (identifier or "").strip()
    provider = (provider or "").strip().upper() or None
    if not identifier:
        return False
    duplicate = SavedService.query.filter_by(
        user_id=user.id, service_type=service_type, provider=provider, identifier=identifier
    ).first()
    if duplicate:
        return False
    db.session.add(SavedService(
        user_id=user.id,
        service_type=service_type,
        label=(label or f"{provider or service_type.title()} - {identifier}").strip()[:100],
        provider=provider,
        identifier=identifier,
        service_metadata={"meter_type": (meter_type or "").strip().upper()},
    ))
    db.session.commit()
    return True


def redirect_after_purchase(user, result, service, payload):
    """Store one successful purchase for the dashboard receipt modal."""
    if result.get("status") == "success":
        transaction = Transaction.query.filter_by(
            user_id=user.id, reference=result.get("reference")
        ).first()
        session["purchase_receipt"] = {
            "service": service,
            "reference": result.get("reference") or "Pending reference",
            "amount": str(transaction.amount) if transaction else str(payload.get("amount", "0")),
            "recipient": transaction.recipient if transaction else (
                payload.get("phone") or payload.get("meter_number") or payload.get("smartcard") or payload.get("account_id") or ""
            ),
            "description": transaction.description if transaction else "Purchase completed successfully",
            "token": result.get("token"),
            "pins": result.get("pins") or [],
        }
    return redirect(url_for("web.dashboard"))



@web_bp.route("/saved-services", methods=["POST"])
@login_required
def save_service():
    user = current_user()
    service_type = (request.form.get("service_type") or "").strip().lower()
    identifier = (request.form.get("identifier") or "").strip()
    provider = (request.form.get("provider") or "").strip().upper() or None
    label = (request.form.get("label") or "").strip()[:100]
    if service_type not in SAVED_SERVICE_TYPES or not identifier:
        flash("Enter a valid service to save.", "error")
        return redirect(request.referrer or url_for("web.dashboard"))

    if not label:
        label = f"{provider or service_type.title()} - {identifier}"
    if persist_saved_service(user, service_type, identifier, provider, label, request.form.get("meter_type", "")):
        flash("Saved for next time.", "success")
    else:
        flash("That service is already saved.", "message")
    return redirect(request.referrer or url_for("web.dashboard"))


@web_bp.route("/saved-services/<int:saved_service_id>/delete", methods=["POST"])
@login_required
def delete_saved_service(saved_service_id):
    saved_service = SavedService.query.filter_by(
        id=saved_service_id, user_id=current_user().id
    ).first_or_404()
    db.session.delete(saved_service)
    db.session.commit()
    flash("Saved service removed.", "success")
    return redirect(request.referrer or url_for("web.dashboard"))


_FEATURED_DATA_DEALS = [
    {
        "network": "MTN",
        "name": "MTN 1GB Data Share",
        "size": "1GB",
        "validity": "7 Days",
        "price": Decimal("410.00"),
        "badge": "Popular",
        "network_color": "#FFC400",
        "text_color": "#090909",
        "url_network": "MTN",
    },
    {
        "network": "AIRTEL",
        "name": "AIRTEL 150MB Awoof",
        "size": "150MB",
        "validity": "1 Day",
        "price": Decimal("95.00"),
        "badge": "Daily Pick",
        "network_color": "#EF4444",
        "text_color": "#FFFFFF",
        "url_network": "AIRTEL",
    },
    {
        "network": "GLO",
        "name": "GLO 1GB Corporate Gifting",
        "size": "1GB",
        "validity": "30 Days",
        "price": Decimal("452.00"),
        "badge": "Best Value",
        "network_color": "#22C55E",
        "text_color": "#FFFFFF",
        "url_network": "GLO",
    },
    {
        "network": "9MOBILE",
        "name": "9MOBILE 1GB Corporate Gifting",
        "size": "1GB",
        "validity": "30 Days",
        "price": Decimal("294.00"),
        "badge": "Budget Rate",
        "network_color": "#059669",
        "text_color": "#FFFFFF",
        "url_network": "9MOBILE",
    },
]


@web_bp.route("/")
def home():
    from services.marketing_settings import get_setting_decimal, get_setting_bool
    from models import MarketingContent

    referral_reward = get_setting_decimal("REFERRAL_REWARD_AMOUNT", Decimal("100.00"))
    referral_min_tx = get_setting_decimal("REFERRAL_MIN_TRANSACTION", Decimal("500.00"))
    referral_enabled = get_setting_bool("REFERRAL_ENABLED", True)

    active_deal = MarketingContent.query.filter_by(content_type="DAILY_DEAL").order_by(MarketingContent.target_date.desc()).first()

    return render_template(
        "web/home.html",
        user=current_user(),
        referral_reward=referral_reward,
        referral_min_tx=referral_min_tx,
        referral_enabled=referral_enabled,
        active_deal=active_deal,
        featured_deals=_FEATURED_DATA_DEALS,
        support_phone="08102314725",
        support_whatsapp_url="https://wa.me/2348102314725?text=Hello%20WAJ%20VTU%20Support",
    )

SERVICES = [
    {"key": "data", "label": "Data", "icon": "📶", "url": "web.buy_data"},
    {"key": "airtime", "label": "Airtime", "icon": "📱", "url": "web.airtime_page"},
    {"key": "cable", "label": "Cable TV", "icon": "📺", "url": "web.cable_page"},
    {"key": "electricity", "label": "Electricity", "icon": "⚡", "url": "web.electricity_page"},
    {"key": "education", "label": "Exam PINs", "icon": "🎓", "url": "web.education_page"},
]


@web_bp.route("/transactions")
@login_required
def transactions_page():
    user = current_user()
    all_tx = (
        Transaction.query.filter_by(user_id=user.id)
        .order_by(Transaction.created_at.desc())
        .limit(100)
        .all()
    )
    return render_template("web/transactions.html", user=user, transactions=all_tx)


@web_bp.route("/support", methods=["GET", "POST"])
def support_page():
    user = current_user()
    searched_ref = (request.args.get("ref") or request.form.get("ref") or "").strip()
    found_tx = None
    if searched_ref:
        if user:
            found_tx = Transaction.query.filter_by(reference=searched_ref, user_id=user.id).first()
        if not found_tx:
            found_tx = Transaction.query.filter_by(reference=searched_ref).first()

    return render_template(
        "web/support.html",
        user=user,
        searched_ref=searched_ref,
        found_tx=found_tx,
        support_phone="08102314725",
        support_whatsapp_url="https://wa.me/2348102314725?text=Hello%20WAJ%20VTU%20Support",
        admin_email="oadedamola07@gmail.com",
    )


@web_bp.route("/support/form", methods=["GET", "POST"])
def support_form_popup():
    user = current_user()
    searched_ref = (request.args.get("ref") or request.form.get("ref") or "").strip()
    phone = (request.args.get("phone") or request.form.get("phone") or (user.phone if user else "")).strip()
    service_hint = (request.args.get("service") or request.form.get("service") or "").strip().upper()

    if not user and phone:
        clean_phone = phone.replace("+", "").strip()
        user = User.query.filter((User.phone == clean_phone) | (User.whatsapp_id == clean_phone)).first()
        if not user and len(clean_phone) == 11 and clean_phone.startswith("0"):
            intl_phone = "234" + clean_phone[1:]
            user = User.query.filter((User.phone == intl_phone) | (User.whatsapp_id == intl_phone)).first()

    found_tx = None
    if searched_ref:
        found_tx = Transaction.query.filter_by(reference=searched_ref).first()
        if found_tx and not service_hint:
            service_hint = (found_tx.type or "").upper()

    if request.method == "POST":
        import os
        from datetime import datetime
        from app import send_whatsapp_message

        data = request.get_json(silent=True) or request.form
        ref = (data.get("ref") or data.get("reference") or "UNKNOWN").strip()
        service_type = (data.get("service_type") or data.get("service") or "VTU Service").strip().upper()
        recipient_target = (data.get("recipient") or data.get("target") or "N/A").strip()
        category = (data.get("category") or data.get("reason") or "Dispute").strip()
        description = (data.get("description") or data.get("issue") or "").strip()
        contact_phone = (data.get("phone") or (user.phone if user else phone) or "").strip()

        if not contact_phone:
            if request.is_json:
                return jsonify({"status": "error", "message": "Phone number is required."}), 400
            flash("Phone number is required to process dispute.", "error")
            return redirect(url_for("web.support_form_popup", ref=ref, service=service_type))

        clean_contact = contact_phone.replace("+", "").strip()
        user_record = User.query.filter((User.phone == clean_contact) | (User.whatsapp_id == clean_contact)).first()
        if not user_record and len(clean_contact) == 11 and clean_contact.startswith("0"):
            intl = "234" + clean_contact[1:]
            user_record = User.query.filter((User.phone == intl) | (User.whatsapp_id == intl)).first()

        ticket_code = f"TKT-{service_type[:4].upper()}-{int(datetime.now(timezone.utc).timestamp()) % 10000:04d}-{secrets.token_hex(2).upper()}"

        new_ticket = SupportTicket(
            ticket_code=ticket_code,
            user_id=user_record.id if user_record else None,
            customer_phone=contact_phone,
            reference=ref,
            service_type=service_type,
            recipient=recipient_target,
            category=category,
            description=description,
            status="OPEN"
        )
        db.session.add(new_ticket)

        if user_record:
            user_record.is_escalated = True
        db.session.commit()

        # Alert Admin on WhatsApp
        admin_phone = os.getenv("ADMIN_PHONE")
        if admin_phone:
            admin_msg = (
                f"🎫 *New WhatsApp In-App Support Form Ticket*\n"
                f"• Ticket: {ticket_code}\n"
                f"• Customer: {contact_phone} ({user_record.name if user_record else 'Customer'})\n"
                f"• Ref: {ref}\n"
                f"• Service: {service_type}\n"
                f"• Recipient: {recipient_target}\n"
                f"• Category: {category}\n"
                f"• Description: {description or 'N/A'}\n\n"
                f"To reply, type:\n`/reply {contact_phone} Your message`\n\n"
                f"To resolve/resume AI, type:\n`/resolve {contact_phone}`"
            )
            send_whatsapp_message(admin_phone, admin_msg)

        # Notify Customer on WhatsApp
        user_msg = (
            f"🎫 *WAJ VTU Support Form Received!*\n"
            f"Ticket: *{ticket_code}*\n"
            f"Ref: *{ref}* ({service_type})\n\n"
            f"We have received your dispute details. Our human support supervisor has been alerted and will reply to you directly in this chat shortly.\n\n"
            f"🛡️ *Security Reminder:* WAJ VTU staff will NEVER ask for your Transaction PIN, password, or bank OTP."
        )
        send_whatsapp_message(contact_phone, user_msg)

        if request.is_json:
            return jsonify({
                "status": "success",
                "ticket_code": ticket_code,
                "reference": ref,
                "service": service_type,
                "message": "Support ticket submitted successfully."
            }), 200

        return render_template(
            "web/support_form.html",
            submitted=True,
            ticket_code=ticket_code,
            reference=ref,
            service=service_type,
            contact_phone=contact_phone,
            support_phone="08102314725",
            admin_email="oadedamola07@gmail.com",
        )

    return render_template(
        "web/support_form.html",
        submitted=False,
        user=user,
        phone=phone,
        searched_ref=searched_ref,
        service_hint=service_hint,
        found_tx=found_tx,
        support_phone="08102314725",
        support_whatsapp_url="https://wa.me/2348102314725",
        admin_email="oadedamola07@gmail.com",
    )


@web_bp.route("/dashboard")
@login_required
def dashboard():
    from app import generate_whatsapp_link_token

    user = current_user()
    recent = (
        Transaction.query.filter_by(user_id=user.id)
        .order_by(Transaction.created_at.desc())
        .limit(10)
        .all()
    )
    is_admin = bool(
        user
        and user.email
        and app.config.get("ADMIN_EMAIL")
        and user.email.strip().lower() == app.config["ADMIN_EMAIL"].strip().lower()
    )
    whatsapp_link_token = generate_whatsapp_link_token(user) if user else ""
    whatsapp_link_url = ""
    if whatsapp_link_token:
        from urllib.parse import quote
        whatsapp_link_url = f"https://wa.me/2348102314725?text={quote('LINK ' + whatsapp_link_token)}"
    from services.referral_service import get_user_referral_summary
    referral_summary = get_user_referral_summary(user)
    return render_template(
        "web/dashboard.html",
        user=user,
        transactions=recent,
        saved_services=saved_services_for(user),
        services=SERVICES,
        is_admin=is_admin,
        whatsapp_linked=bool(user.whatsapp_id and not str(user.whatsapp_id).startswith("web_")),
        whatsapp_link_url=whatsapp_link_url,
        purchase_receipt=session.pop("purchase_receipt", None),
        referral=referral_summary,
    )


@web_bp.route("/referrals")
@login_required
def referrals_page():
    from services.referral_service import get_user_referral_summary
    user = current_user()
    referral_summary = get_user_referral_summary(user)
    return render_template("web/referrals.html", user=user, referral=referral_summary)


@web_bp.route("/receipts/<reference>.png")
@login_required
def receipt_image(reference):
    user = current_user()
    transaction = Transaction.query.filter_by(
        user_id=user.id, reference=reference, status="SUCCESS"
    ).first_or_404()
    from app import generate_receipt_png
    image_buffer = generate_receipt_png(transaction)
    return send_file(
        image_buffer,
        mimetype="image/png",
        download_name=f"waj-vtu-{reference}.png",
        max_age=0,
    )


# --------------------------------------------------------------------------
# DATA
# --------------------------------------------------------------------------

@web_bp.route("/buy/data", methods=["GET", "POST"])
@login_required
def buy_data():
    user = current_user()
    from app import get_markup

    if request.method == "GET":
        network = request.args.get("network", "MTN")
        raw_plans = fetch_data_variations(network)
        plans = []
        for plan in raw_plans:
            base_amount = Decimal(str(plan.get("variation_amount", "0")))
            plans.append({
                "name": plan.get("name", "Data plan"),
                "code": plan.get("variation_code"),
                "amount": base_amount + get_markup(f"DATA_{network}", base_amount),
            })
        return render_template("web/buy_data.html", user=user, network=network, plans=plans)

    network = request.form.get("network", "").upper()
    plan_code = request.form.get("plan_code", "")
    bypass = (request.form.get("bypass") or "").strip() or None
    ported_number = (request.form.get("ported_number") or "").strip() or None
    selected_plan = next(
        (plan for plan in fetch_data_variations(network)
         if str(plan.get("variation_code")) == plan_code),
        None,
    )
    if not selected_plan:
        flash("Select a valid data plan.", "error")
        return redirect(url_for("web.buy_data", network=network))

    base_amount = Decimal(str(selected_plan.get("variation_amount", "0")))
    payload = {
        "network": network,
        "plan_code": plan_code,
        "amount": base_amount + get_markup(f"DATA_{network}", base_amount),
        "phone": request.form.get("phone") or user.phone,
        "bypass": bypass,
        "ported_number": ported_number,
        "confirm": True,
    }
    result = execute_tool(app, db, user, user.phone, "buy_data", payload)
    flash(result.get("message", "Request processed."), "success" if result.get("status") == "success" else "error")
    return redirect_after_purchase(user, result, "Data", payload)


# --------------------------------------------------------------------------
# AIRTIME
# --------------------------------------------------------------------------

@web_bp.route("/buy/airtime-page")
@login_required
def airtime_page():
    user = current_user()
    return render_template(
        "web/buy_airtime.html",
        user=user,
        saved_services=SavedService.query.filter_by(user_id=user.id, service_type="airtime").all(),
    )


@web_bp.route("/buy/airtime", methods=["POST"])
@login_required
def buy_airtime():
    user = current_user()
    payload = {
        "network": request.form.get("network"),
        "amount": request.form.get("amount"),
        "phone": request.form.get("phone") or user.phone,
        "confirm": True,
    }
    result = execute_tool(app, db, user, user.phone, "buy_airtime", payload)
    if request.form.get("save_service") and persist_saved_service(user, "airtime", payload["phone"], payload["network"], request.form.get("save_label")):
        flash("Phone number saved for next time.", "success")
    flash(result.get("message", "Request processed."), "success" if result.get("status") == "success" else "error")
    return redirect_after_purchase(user, result, "Airtime", payload)


# --------------------------------------------------------------------------
# CABLE TV
# --------------------------------------------------------------------------

@web_bp.route("/buy/cable", methods=["GET", "POST"])
@login_required
def cable_page():
    user = current_user()
    from app import get_markup

    if request.method == "GET":
        provider = request.args.get("provider", "DSTV")
        cable_plans = []
        for plan in fetch_cable_plans(provider):
            base_amount = Decimal(str(plan.get("amount", "0")))
            cable_plans.append({
                "name": plan.get("name", "TV plan"),
                "code": plan.get("code"),
                "amount": base_amount + get_markup("CABLE", base_amount),
                "api_cost": base_amount,
                "api_discount_amount": Decimal(str(plan.get("discount_amount", "0"))),
            })
        return render_template(
            "web/buy_cable.html",
            user=user,
            provider=provider,
            plans=cable_plans,
            saved_services=SavedService.query.filter_by(user_id=user.id, service_type="cable").all(),
        )

    provider = request.form.get("provider", "").upper()
    plan_code = request.form.get("plan_code", "")
    selected_plan = next(
        (plan for plan in fetch_cable_plans(provider) if str(plan.get("code")) == plan_code),
        None,
    )
    if not selected_plan:
        flash("Select a valid TV plan.", "error")
        return redirect(url_for("web.cable_page", provider=provider))
    base_amount = Decimal(str(selected_plan.get("amount", "0")))
    payload = {
        "provider": provider,
        "smartcard": request.form.get("smartcard"),
        "plan_code": plan_code,
        "amount": base_amount + get_markup("CABLE", base_amount),
        "api_cost": base_amount,
        "api_discount_amount": Decimal(str(selected_plan.get("discount_amount", "0"))),
        "confirm": True,
    }
    result = execute_tool(app, db, user, user.phone, "buy_cable", payload)
    if request.form.get("save_service") and persist_saved_service(user, "cable", payload["smartcard"], payload["provider"], request.form.get("save_label")):
        flash("TV account saved for next time.", "success")
    flash(result.get("message", "Request processed."), "success" if result.get("status") == "success" else "error")
    return redirect_after_purchase(user, result, "Cable TV", payload)


@web_bp.route("/verify/smartcard")
@login_required
def verify_smartcard_ajax():
    """Lightweight endpoint the cable page can call before submit to show the customer's name."""
    provider = request.args.get("provider", "")
    smartcard = request.args.get("smartcard", "")
    result = verify_smartcard(provider, smartcard)
    return {"valid": bool(result.get("valid")), "name": result.get("customer_name") or result.get("message", "")}


# --------------------------------------------------------------------------
# ELECTRICITY
# --------------------------------------------------------------------------

@web_bp.route("/buy/electricity", methods=["GET", "POST"])
@login_required
def electricity_page():
    user = current_user()

    if request.method == "GET":
        return render_template(
            "web/buy_electricity.html",
            user=user,
            saved_services=SavedService.query.filter_by(user_id=user.id, service_type="electricity").all(),
        )

    payload = {
        "disco": request.form.get("disco"),
        "meter_number": request.form.get("meter_number"),
        "meter_type": request.form.get("meter_type"),
        "amount": request.form.get("amount"),
        "confirm": True,
    }
    result = execute_tool(app, db, user, user.phone, "pay_electricity", payload)
    if request.form.get("save_service") and persist_saved_service(user, "electricity", payload["meter_number"], payload["disco"], request.form.get("save_label"), payload["meter_type"]):
        flash("Meter saved for next time.", "success")
    if result.get("status") == "success" and result.get("token"):
        flash(f"Payment successful. Token: {result['token']}", "success")
    else:
        flash(result.get("message", "Request processed."), "success" if result.get("status") == "success" else "error")
    return redirect_after_purchase(user, result, "Electricity", payload)


@web_bp.route("/verify/meter")
@login_required
def verify_meter_ajax():
    disco = request.args.get("disco", "")
    meter = request.args.get("meter_number", "")
    mtype = request.args.get("meter_type", "")
    result = verify_meter(disco, meter, mtype)
    return {"valid": bool(result.get("valid")), "name": result.get("customer_name") or result.get("message", "")}


# --------------------------------------------------------------------------
# BETTING
# --------------------------------------------------------------------------

@web_bp.route("/buy/betting", methods=["GET", "POST"])
@login_required
def betting_page():
    flash("Betting service is currently unavailable.", "info")
    return redirect(url_for("web.dashboard"))


@web_bp.route("/verify/betting")
@login_required
def verify_betting_ajax():
    platform = request.args.get("platform", "")
    account_id = request.args.get("account_id", "")
    result = execute_tool(app, db, current_user(), current_user().phone, "verify_betting", {"platform": platform, "account_id": account_id})
    return {"valid": result.get("status") == "success", "name": result.get("account_name") or result.get("message", "")}


# --------------------------------------------------------------------------
# EDUCATION (WAEC / JAMB PINS)
# --------------------------------------------------------------------------

@web_bp.route("/buy/education", methods=["GET", "POST"])
@login_required
def education_page():
    user = current_user()
    from app import get_markup

    if request.method == "GET":
        plans = []
        for package in fetch_education_packages():
            base_amount = Decimal(str(package.get("amount", "0")))
            plans.append({
                "name": package.get("name", "Exam PIN"),
                "code": package.get("code"),
                "amount": base_amount + get_markup("EDU", base_amount),
            })
        return render_template("web/buy_education.html", user=user, plans=plans)

    exam = request.form.get("exam", "")
    selected_package = next(
        (package for package in fetch_education_packages() if str(package.get("code")) == exam),
        None,
    )
    if not selected_package:
        flash("Select a valid exam package.", "error")
        return redirect(url_for("web.education_page"))
    try:
        quantity = max(int(request.form.get("quantity", 1)), 1)
    except (TypeError, ValueError):
        quantity = 1
    base_amount = Decimal(str(selected_package.get("amount", "0"))) * quantity
    payload = {
        "exam": exam,
        "quantity": quantity,
        "amount": base_amount + get_markup("EDU", base_amount),
        "confirm": True,
    }
    result = execute_tool(app, db, user, user.phone, "buy_education_pin", payload)
    if result.get("status") == "success" and result.get("pins"):
        flash(f"PIN(s): {', '.join(result['pins'])}", "success")
    else:
        flash(result.get("message", "Request processed."), "success" if result.get("status") == "success" else "error")
    return redirect_after_purchase(user, result, "Education PIN", payload)


# --------------------------------------------------------------------------
# WALLET FUNDING
# --------------------------------------------------------------------------

@web_bp.route("/wallet/fund", methods=["GET", "POST"])
@login_required
def fund_wallet():
    user = current_user()
    if not user.email:
        flash("Add an email to your account before funding your wallet.", "error")
        return redirect(url_for("web.dashboard"))

    if request.method == "POST":
        try:
            amount = Decimal(str(request.form.get("amount", "0"))).quantize(Decimal("0.01"))
        except (InvalidOperation, ValueError):
            amount = Decimal("0")
        if amount <= 0:
            flash("Enter a valid funding amount.", "error")
            return redirect(url_for("web.fund_wallet"))

        result = generate_payment_link(
            user.email,
            amount,
            user.phone,
            pass_fee_to_user=True,
            payment_source="website",
        )
        if result.get("status") != "SUCCESS":
            flash(result.get("reason", "Could not create the Paystack payment link."), "error")
            return redirect(url_for("web.fund_wallet"))

        return redirect(result["payment_url"], code=303)

    return render_template("web/fund_wallet.html", user=user)
