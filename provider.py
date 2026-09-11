import uuid
import logging

logger = logging.getLogger("vtu_provider")

def generate_ref(prefix):
    return f"{prefix}_{uuid.uuid4().hex[:10].upper()}"

def fetch_data_variations(network):
    """Returns mock data variations across different validity tiers for testing."""
    return [
        {"name": f"{network} 1GB Daily (24 Hours)", "variation_code": "1gb_daily", "variation_amount": 300},
        {"name": f"{network} 2.5GB 2-Day Plan", "variation_code": "2.5gb_2day", "variation_amount": 600},
        {"name": f"{network} 3GB Weekly Plan (7 Days)", "variation_code": "3gb_weekly", "variation_amount": 1000},
        {"name": f"{network} 1GB Monthly SME", "variation_code": "1gb_monthly", "variation_amount": 350},
        {"name": f"{network} 2GB Monthly Corporate", "variation_code": "2gb_monthly", "variation_amount": 650},
        {"name": f"{network} 5GB Monthly", "variation_code": "5gb_monthly", "variation_amount": 1550},
        {"name": f"{network} 2GB Night & Awoof Promo", "variation_code": "2gb_awoof", "variation_amount": 200},
    ]

def process_data_purchase(phone, network, plan_code, amount):
    logger.info(f"Processing Data: {network} {plan_code} to {phone}")
    return {"status": "SUCCESS", "reference": generate_ref("REF_DATA"), "reason": ""}

def process_airtime_purchase(phone, network, amount):
    logger.info(f"Processing Airtime: ₦{amount} {network} to {phone}")
    return {"status": "SUCCESS", "reference": generate_ref("REF_AIRTIME"), "reason": ""}

def fetch_cable_plans(provider):
    return [
        {"name": f"{provider} Basic", "code": "basic", "amount": 2500},
        {"name": f"{provider} Premium", "code": "premium", "amount": 5000},
    ]

def verify_smartcard(provider, iuc):
    return {"valid": True, "customer_name": "Test Customer", "message": "Success"}

def process_cable_tv(provider, iuc, plan_code, amount):
    return {"status": "SUCCESS", "reference": generate_ref("REF_CABLE"), "reason": ""}

def verify_meter(disco, meter_no, meter_type):
    return {"valid": True, "customer_name": "Test Meter User", "message": "Success"}

def process_electricity_payment(disco, meter_no, meter_type, amount):
    return {
        "status": "SUCCESS",
        "reference": generate_ref("REF_ELEC"),
        "token": "1234-5678-9012-3456",
        "reason": ""
    }

def verify_betting_account(platform, user_id):
    return {"valid": True, "account_name": "Verified Bettor", "message": "Success"}

def process_betting_topup(platform, user_id, amount):
    return {"status": "SUCCESS", "reference": generate_ref("REF_BET"), "reason": ""}

def fetch_education_packages():
    return [
        {"name": "WAEC Result Checker", "code": "waec", "amount": 3800},
        {"name": "NECO Result Checker", "code": "neco", "amount": 1200},
    ]

def process_education_pin(exam, quantity=1):
    pins = [f"PIN-{exam.upper()}-{uuid.uuid4().hex[:8].upper()}" for _ in range(quantity)]
    return {"status": "SUCCESS", "reference": generate_ref("REF_EDU"), "pins": pins, "reason": ""}