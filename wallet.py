import os
import requests
import logging
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("wallet_service")
PAYSTACK_SECRET_KEY = os.getenv("PAYSTACK_SECRET_KEY", "")


def create_dedicated_virtual_account(phone_number, email, first_name="VTU", last_name="User"):
    """
    1. Creates a Paystack Customer using the user's details.
    2. Assigns a Dedicated NUBAN Virtual Account.
    """
    if not PAYSTACK_SECRET_KEY:
        logger.error("PAYSTACK_SECRET_KEY is missing in environment variables.")
        return {"status": "FAILED", "reason": "Paystack API key missing"}

    headers = {
        "Authorization": f"Bearer {PAYSTACK_SECRET_KEY}",
        "Content-Type": "application/json"
    }

    customer_url = "https://api.paystack.co/customer"
    customer_data = {
        "email": email,
        "first_name": first_name,
        "last_name": last_name,
        "phone": phone_number
    }

    try:
        # Step 1: Create or Fetch Paystack Customer
        response = requests.post(customer_url, json=customer_data, headers=headers, timeout=15)

        try:
            cust_res = response.json()
        except ValueError:
            return {"status": "FAILED", "reason": f"Invalid provider response: {response.text[:80]}"}

        # If customer exists already, fetch customer code by email
        if not cust_res.get("status"):
            fetch_res = requests.get(f"{customer_url}/{email}", headers=headers, timeout=15)
            try:
                cust_res = fetch_res.json()
            except ValueError:
                return {"status": "FAILED", "reason": "Failed to parse customer retrieval response"}

        customer_code = cust_res.get("data", {}).get("customer_code")

        if not customer_code:
            return {
                "status": "FAILED",
                "reason": cust_res.get("message", "Could not resolve Paystack customer code")
            }

        # Step 2: Assign Dedicated Virtual Account (DVA)
        dva_url = "https://api.paystack.co/dedicated_account"
        dva_data = {
            "customer": customer_code,
            "preferred_bank": "wema-bank"  # Fallback options supported by Paystack: wema-bank, sterling-bank
        }

        dva_response = requests.post(dva_url, json=dva_data, headers=headers, timeout=15)

        try:
            dva_res = dva_response.json()
        except ValueError:
            return {"status": "FAILED", "reason": f"Invalid response during DVA allocation: {dva_response.text[:80]}"}

        if dva_res.get("status"):
            acc_data = dva_res.get("data", {})
            return {
                "status": "SUCCESS",
                "bank_name": acc_data.get("bank", {}).get("name", "Wema Bank"),
                "account_number": acc_data.get("account_number"),
                "account_name": acc_data.get("account_name"),
                "customer_code": customer_code
            }

        return {"status": "FAILED", "reason": dva_res.get("message", "DVA assignment failed")}

    except Exception as e:
        logger.error(f"[WALLET ERROR]: {e}")
        return {"status": "FAILED", "reason": f"Network error calling Paystack: {str(e)}"}