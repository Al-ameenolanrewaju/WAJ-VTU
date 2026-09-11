import os
import uuid
import logging
import requests
from decimal import Decimal, ROUND_HALF_UP

logger = logging.getLogger("wallet_service")

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "sk_test_xxx")
PAYSTACK_INITIALIZE_URL = "https://api.paystack.co/transaction/initialize"


def calculate_paystack_gross(net_amount):
    """
    Calculates the gross amount to charge via Paystack so that the user's wallet
    is credited with the exact intended net_amount after Paystack fees.

    Paystack Local Pricing Structure (Nigeria):
    - 1.5% fee on amounts under ₦2,500.
    - 1.5% + ₦100 flat fee on amounts ₦2,500 and above (capped at ₦2,000 total fee).
    """
    net = Decimal(str(net_amount))

    # Paystack flat ₦100 fee applies when Gross >= ₦2,500.
    # Gross without flat fee: net / 0.985. If this is < 2500, flat fee is waived.
    gross_no_flat = net / Decimal('0.985')

    if gross_no_flat < Decimal('2500.00'):
        gross = gross_no_flat
    else:
        gross = (net + Decimal('100.00')) / Decimal('0.985')

    # Calculate actual fee and apply the ₦2,000 cap
    fee = gross - net
    if fee > Decimal('2000.00'):
        gross = net + Decimal('2000.00')

    # Quantize to 2 decimal places using standard banking rounding
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