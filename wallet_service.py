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