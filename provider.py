import logging

logger = logging.getLogger("vtu_provider")

def fetch_data_variations(network):
    """Returns dummy/mock variations if live API is not configured yet."""
    return [
        {"name": f"{network} 1GB Monthly", "variation_code": "1gb", "variation_amount": 300},
        {"name": f"{network} 2GB Monthly", "variation_code": "2gb", "variation_amount": 600},
        {"name": f"{network} 5GB Monthly", "variation_code": "5gb", "variation_amount": 1500},
    ]

def process_data_purchase(phone, network, plan_code, amount):
    return {"status": "SUCCESS", "reference": f"REF_DATA_{phone}", "reason": ""}

def process_airtime_purchase(phone, network, amount):
    return {"status": "SUCCESS", "reference": f"REF_AIRTIME_{phone}", "reason": ""}

def fetch_cable_plans(provider):
    return [
        {"name": f"{provider} Basic", "code": "basic", "amount": 2500},
        {"name": f"{provider} Premium", "code": "premium", "amount": 5000},
    ]

def verify_smartcard(provider, iuc):
    return {"valid": True, "customer_name": "Test Customer", "message": "Success"}

def process_cable_tv(provider, iuc, plan_code, amount):
    return {"status": "SUCCESS", "reference": f"REF_CABLE_{iuc}", "reason": ""}

def verify_meter(disco, meter_no, meter_type):
    return {"valid": True, "customer_name": "Test Meter User", "message": "Success"}

def process_electricity_payment(disco, meter_no, meter_type, amount):
    return {"status": "SUCCESS", "reference": f"REF_ELEC_{meter_no}", "token": "1234-5678-9012-3456", "reason": ""}

def verify_betting_account(platform, user_id):
    return {"valid": True, "account_name": "Verified Bettor", "message": "Success"}

def process_betting_topup(platform, user_id, amount):
    return {"status": "SUCCESS", "reference": f"REF_BET_{user_id}", "reason": ""}

def fetch_education_packages():
    return [
        {"name": "WAEC Result Checker", "code": "waec", "amount": 3800},
        {"name": "NECO Result Checker", "code": "neco", "amount": 1200},
    ]

def process_education_pin(exam, quantity):
    pins = [f"PIN-{exam}-{i+1000}" for i in range(quantity)]
    return {"status": "SUCCESS", "reference": f"REF_EDU_{exam}", "pins": pins, "reason": ""}