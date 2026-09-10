# wallet_service.py
import os
import requests
from dotenv import load_dotenv

load_dotenv()

PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "")


def create_dedicated_virtual_account(phone_number, email):
    """
    1. Creates a Paystack Customer using the user's phone/email.
    2. Assigns a Dedicated NUBAN Virtual Account (Wema Bank, Sterling, or GTBank).
    """
    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json"
    }

    # Step 1: Create or Fetch Paystack Customer
    customer_url = "https://api.paystack.co/customer"
    customer_data = {
        "email": email,
        "first_name": "VTU User",
        "last_name": f"_{phone_number}",
        "phone": phone_number
    }

    try:
        cust_res = requests.post(customer_url, json=customer_data, headers=headers, timeout=15).json()

        if not cust_res.get("status"):
            # If customer already exists, fetch existing customer
            cust_res = requests.get(f"{customer_url}/{email}", headers=headers, timeout=15).json()

        customer_code = cust_res.get("data", {}).get("customer_code")

        if not customer_code:
            return {"status": "FAILED", "reason": "Could not create Paystack customer"}

        # Step 2: Assign Dedicated Virtual Account (DVA)
        dva_url = "https://api.paystack.co/dedicated_account"
        dva_data = {
            "customer": customer_code,
            "preferred_bank": "wema-bank"  # Options: wema-bank, sterling-bank
        }

        dva_res = requests.post(dva_url, json=dva_data, headers=headers, timeout=15).json()

        if dva_res.get("status"):
            acc_data = dva_res.get("data", {})
            return {
                "status": "SUCCESS",
                "bank_name": acc_data.get("bank", {}).get("name", "Wema Bank"),
                "account_number": acc_data.get("account_number"),
                "account_name": acc_data.get("account_name"),
                "customer_code": customer_code
            }
        else:
            return {"status": "FAILED", "reason": dva_res.get("message", "DVA assignment failed")}

    except Exception as e:
        print(f"[WALLET ERROR]: {e}")
        return {"status": "FAILED", "reason": "Network error calling Paystack"}