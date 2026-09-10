import os
import uuid
import requests
from decimal import Decimal

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

    if net < Decimal('2500.00'):
        # Net = Gross * (1 - 0.015) => Gross = Net / 0.985
        gross = net / Decimal('0.985')
    else:
        # Net = (Gross * 0.985) - 100 => Gross = (Net + 100) / 0.985
        gross = (net + Decimal('100.00')) / Decimal('0.985')

    # Calculate actual fee capped at ₦2,000
    fee = gross - net
    if fee > Decimal('2000.00'):
        gross = net + Decimal('2000.00')

    return round(gross, 2)


def generate_payment_link(email, amount_naira, phone, pass_fee_to_user=True):
    """
    Generates a Paystack checkout URL for funding the user's wallet.
    Calculates gateway charges and converts NGN to Kobo for Paystack API compliance.
    """
    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json"
    }

    net_target = Decimal(str(amount_naira))

    # Determine gross charge based on fee policy
    if pass_fee_to_user:
        gross_naira = calculate_paystack_gross(net_target)
    else:
        gross_naira = net_target

    fee_amount = gross_naira - net_target
    amount_kobo = int(float(gross_naira) * 100)
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
        res_data = response.json()

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
        return {
            "status": "FAILED",
            "reason": f"Network error connecting to payment gateway: {str(e)}"
        }