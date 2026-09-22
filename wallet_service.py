import os
import uuid
import logging
import requests
from decimal import Decimal, ROUND_HALF_UP
from dotenv import load_dotenv
from flask import has_app_context

from models import PaymentFeeTier

load_dotenv()

logger = logging.getLogger("wallet_service")

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "sk_test_xxx")
PAYSTACK_INITIALIZE_URL = "https://api.paystack.co/transaction/initialize"
PAYSTACK_CALLBACK_URL = os.getenv("PAYSTACK_CALLBACK_URL") or os.getenv("APP_BASE_URL", "http://localhost:5000").rstrip("/") + "/payments/paystack/callback"
PAYSTACK_BASE_URL = "https://api.paystack.co"
# Providers Paystack can issue Dedicated NUBANs through. Wema and Titan are the
# two commonly available on a standard Nigerian business account; which ones
# are actually enabled depends on your Paystack settlement bank and KYC tier.
DVA_PREFERRED_BANK = os.getenv("PAYSTACK_DVA_PREFERRED_BANK", "wema-bank")
DEFAULT_PAYMENT_TIERS = [
    {"label": "BELOW_1000", "min_amount": Decimal("0.00"), "max_amount": Decimal("999.99"), "fee_percentage": Decimal("2.50")},
    {"label": "1000_TO_20000", "min_amount": Decimal("1000.00"), "max_amount": Decimal("19999.99"), "fee_percentage": Decimal("1.50")},
    {"label": "ABOVE_20000", "min_amount": Decimal("20000.00"), "max_amount": None, "fee_percentage": Decimal("1.00")},
]


def get_payment_fee_tiers():
    if not has_app_context():
        return [PaymentFeeTier(**tier) for tier in DEFAULT_PAYMENT_TIERS]

    db_tiers = PaymentFeeTier.query.order_by(PaymentFeeTier.min_amount.asc()).all()
    if db_tiers:
        return db_tiers
    return [PaymentFeeTier(**tier) for tier in DEFAULT_PAYMENT_TIERS]


def get_payment_fee_percentage(net_amount):
    amount = Decimal(str(net_amount))
    for tier in get_payment_fee_tiers():
        min_amount = Decimal(str(tier.min_amount))
        max_amount = Decimal(str(tier.max_amount)) if tier.max_amount is not None else None
        if amount >= min_amount and (max_amount is None or amount <= max_amount):
            return Decimal(str(tier.fee_percentage))
    return Decimal("1.00")


def calculate_paystack_gross(net_amount):
    """Calculate the total amount a customer must pay so the wallet is credited with the desired net amount after the configured Paystack fee."""
    net = Decimal(str(net_amount)).quantize(Decimal('0.01'))
    fee_rate = get_payment_fee_percentage(net) / Decimal('100')
    gross = net / (Decimal('1.00') - fee_rate)
    return gross.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)


def _paystack_headers():
    return {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json",
    }


def create_paystack_customer(email, phone, name=""):
    """
    Creates (or, if one already exists for this email, fetches) a Paystack
    customer record. A customer_code is required before a Dedicated Virtual
    Account can be issued.
    """
    parts = (name or "User").strip().split(" ", 1)
    first_name = parts[0] or "User"
    last_name = parts[1] if len(parts) > 1 else "Customer"

    try:
        response = requests.post(
            f"{PAYSTACK_BASE_URL}/customer",
            json={
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "phone": phone,
            },
            headers=_paystack_headers(),
            timeout=15,
        )
        data = response.json()
        if response.status_code == 200 and data.get("status"):
            return {"status": "SUCCESS", "customer_code": data["data"]["customer_code"]}

        # Paystack returns 400 with "Customer already exists" if this email
        # was already registered directly on their dashboard/API — fetch it
        # instead of failing.
        message = data.get("message", "")
        if "already exists" in message.lower():
            fetch = requests.get(
                f"{PAYSTACK_BASE_URL}/customer/{email}",
                headers=_paystack_headers(),
                timeout=15,
            )
            fetch_data = fetch.json()
            if fetch.status_code == 200 and fetch_data.get("status"):
                return {"status": "SUCCESS", "customer_code": fetch_data["data"]["customer_code"]}

        return {"status": "FAILED", "reason": message or "Could not create Paystack customer"}
    except requests.RequestException as e:
        logger.error(f"Paystack customer creation error: {e}")
        return {"status": "FAILED", "reason": f"Network error: {e}"}


def create_dedicated_virtual_account(customer_code, phone, preferred_bank=None):
    """
    Issues a permanent NUBAN account number tied to a Paystack customer.
    Requires Dedicated NUBAN to be enabled on the Paystack account (Nigeria
    only, and gated behind Paystack's own KYC/compliance approval) — this
    call will fail with a clear Paystack error message if it isn't.
    """
    try:
        response = requests.post(
            f"{PAYSTACK_BASE_URL}/dedicated_account",
            json={
                "customer": customer_code,
                "preferred_bank": preferred_bank or DVA_PREFERRED_BANK,
                "phone": phone,
            },
            headers=_paystack_headers(),
            timeout=20,
        )
        data = response.json()
        if response.status_code == 200 and data.get("status"):
            account = data["data"]
            return {
                "status": "SUCCESS",
                "account_number": account.get("account_number"),
                "account_name": account.get("account_name"),
                "bank_name": (account.get("bank") or {}).get("name"),
            }
        return {"status": "FAILED", "reason": data.get("message", "Could not create dedicated account")}
    except requests.RequestException as e:
        logger.error(f"Paystack DVA creation error: {e}")
        return {"status": "FAILED", "reason": f"Network error: {e}"}


def get_or_create_dva(user, db):
    """
    Returns the user's permanent funding account, creating the Paystack
    customer + Dedicated Virtual Account on first use and caching both on
    the User row so this only ever hits Paystack once per user.
    """
    if user.dva_account_number and user.dva_bank_name:
        return {
            "status": "SUCCESS",
            "account_number": user.dva_account_number,
            "bank_name": user.dva_bank_name,
        }

    if not user.email:
        return {"status": "FAILED", "reason": "Add an email to your account before generating a funding account."}

    if not user.paystack_customer_code:
        customer = create_paystack_customer(user.email, user.phone, user.name)
        if customer["status"] != "SUCCESS":
            return customer
        user.paystack_customer_code = customer["customer_code"]
        db.session.commit()

    dva = create_dedicated_virtual_account(user.paystack_customer_code, user.phone)
    if dva["status"] != "SUCCESS":
        return dva

    user.dva_account_number = dva["account_number"]
    user.dva_bank_name = dva["bank_name"]
    db.session.commit()
    return dva


def generate_payment_link(email, amount_naira, phone, pass_fee_to_user=True):
    """
    Generates a Paystack checkout URL for funding the user's wallet.
    Calculates gateway charges and converts NGN to Kobo for Paystack API compliance.
    """
    if not PAYSTACK_SECRET_KEY or PAYSTACK_SECRET_KEY == "sk_test_xxx":
        logger.warning("Paystack secret key is missing or set to default test placeholder.")

    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json"
    }

    net_target = Decimal(str(amount_naira)).quantize(Decimal('0.01'))

    # Determine gross charge based on fee policy
    if pass_fee_to_user:
        gross_naira = calculate_paystack_gross(net_target)
    else:
        gross_naira = net_target

    fee_amount = gross_naira - net_target

    # Precise conversion to Kobo without float rounding issues
    amount_kobo = int((gross_naira * Decimal('100')).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    reference = f"DEP_{uuid.uuid4().hex[:12].upper()}"

    payload = {
        "email": email,
        "amount": amount_kobo,
        "reference": reference,
        "callback_url": PAYSTACK_CALLBACK_URL,
        "metadata": {
            "phone_number": phone,
            "net_credit_amount": str(net_target),
            "fee_amount": str(fee_amount),
            "custom_fields": [
                {
                    "display_name": "Phone Number",
                    "variable_name": "phone_number",
                    "value": phone
                },
                {
                    "display_name": "Net Wallet Credit",
                    "variable_name": "net_credit_amount",
                    "value": f"NGN {net_target:,.2f}"
                }
            ]
        }
    }

    try:
        response = requests.post(PAYSTACK_INITIALIZE_URL, json=payload, headers=headers, timeout=15)

        try:
            res_data = response.json()
        except ValueError:
            return {
                "status": "FAILED",
                "reason": f"Invalid gateway response format (HTTP {response.status_code})"
            }

        if response.status_code == 200 and res_data.get("status"):
            return {
                "status": "SUCCESS",
                "payment_url": res_data["data"]["authorization_url"],
                "reference": reference,
                "gross_amount": float(gross_naira),
                "net_amount": float(net_target),
                "fee_amount": float(fee_amount)
            }

        return {
            "status": "FAILED",
            "reason": res_data.get("message", "Unable to initialize Paystack payment.")
        }
    except requests.RequestException as e:
        logger.error(f"Payment Link Generation Error: {e}")
        return {
            "status": "FAILED",
            "reason": f"Network error connecting to payment gateway: {str(e)}"
        }