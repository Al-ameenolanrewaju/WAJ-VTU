import logging
import os
import uuid
from datetime import datetime, timezone, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("vtpass_provider")
VTPASS_BASE_URL = os.getenv("VTPASS_BASE_URL", "https://vtpass.com/api").rstrip("/")
VTPASS_API_KEY = os.getenv("VTPASS_API_KEY", "").strip()
VTPASS_PUBLIC_KEY = os.getenv("VTPASS_PUBLIC_KEY", "").strip()
VTPASS_SECRET_KEY = os.getenv("VTPASS_SECRET_KEY", "").strip()
MOCK_MODE = os.getenv("MOCK_MODE", "False").lower() == "true"

NETWORK_SERVICE_IDS = {
    "MTN": "mtn-data",
    "AIRTEL": "airtel-data",
    "GLO": "glo-data",
    "9MOBILE": "etisalat-data",
}
NETWORK_AIRTIME_IDS = {
    "MTN": "mtn",
    "AIRTEL": "airtel",
    "GLO": "glo",
    "9MOBILE": "etisalat",
}
CABLE_SERVICE_IDS = {"DSTV": "dstv", "GOTV": "gotv", "STARTIMES": "startimes"}
DISCO_SERVICE_IDS = {
    "IKEDC": "ikeja-electric",
    "EKEDC": "eko-electric",
    "AEDC": "abuja-electric",
    "IBEDC": "ibadan-electric",
}


def generate_ref(prefix="VTU"):
    lagos_time = datetime.now(timezone.utc) + timedelta(hours=1)
    return f"{lagos_time:%Y%m%d%H%M}{prefix}{uuid.uuid4().hex[:12]}"


def _get_headers():
    return {"api-key": VTPASS_API_KEY, "public-key": VTPASS_PUBLIC_KEY}


def _post_headers():
    return {"api-key": VTPASS_API_KEY, "secret-key": VTPASS_SECRET_KEY}


def _credentials_ready(post=False):
    required = (VTPASS_API_KEY, VTPASS_SECRET_KEY) if post else (VTPASS_API_KEY, VTPASS_PUBLIC_KEY)
    if all(required):
        return True
    logger.error("VTPass credentials are missing; configure VTPASS_API_KEY, VTPASS_PUBLIC_KEY, and VTPASS_SECRET_KEY")
    return False


def _get(endpoint, params):
    response = requests.get(
        f"{VTPASS_BASE_URL}/{endpoint.lstrip('/')}",
        params=params,
        headers=_get_headers(),
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def _post(payload):
    response = requests.post(
        f"{VTPASS_BASE_URL}/pay",
        json=payload,
        headers=_post_headers(),
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


def _response_success(response):
    return str(response.get("code", response.get("response_description", ""))).upper() in {"000", "00", "200", "SUCCESS"}


def _failure(reference, message):
    return {"status": "FAILED", "reference": reference, "reason": message}


def _content_message(response, fallback):
    return response.get("response_description") or response.get("content", {}).get("errors") or fallback


def fetch_data_variations(network):
    network = network.upper()
    service_id = NETWORK_SERVICE_IDS.get(network)
    if MOCK_MODE:
        return [
            {"name": f"{network} 1GB Daily", "variation_code": "mock-1gb", "variation_amount": 300},
            {"name": f"{network} 2GB Monthly", "variation_code": "mock-2gb", "variation_amount": 650},
        ]
    if not service_id or not _credentials_ready(False):
        return []
    try:
        response = _get("service-variations", {"serviceID": service_id})
        variations = response.get("content", {}).get("variations", [])
        plans = []
        for item in variations:
            code = item.get("variation_code")
            name = item.get("name")
            if code and name:
                plans.append({
                    "name": name,
                    "variation_code": code,
                    "variation_amount": float(item.get("variation_amount", 0)),
                })
        logger.info("Fetched %d %s VTPass data plans", len(plans), network)
        return plans
    except Exception:
        logger.exception("Failed to fetch VTPass data plans for %s", network)
        return []


def process_data_purchase(phone, network, plan_code, amount=None):
    reference = generate_ref("DATA")
    service_id = NETWORK_SERVICE_IDS.get(network.upper())
    if MOCK_MODE:
        return {"status": "SUCCESS", "reference": reference, "data": {"status": "MOCK_SUCCESS"}}
    if not service_id or not _credentials_ready(True):
        return _failure(reference, "VTPass credentials or network configuration is missing")
    try:
        response = _post({
            "request_id": reference,
            "serviceID": service_id,
            "billersCode": phone,
            "variation_code": plan_code,
            "amount": str(amount or 0),
            "phone": phone,
        })
        if _response_success(response):
            return {"status": "SUCCESS", "reference": reference, "data": response}
        return _failure(reference, _content_message(response, "VTPass data purchase failed"))
    except Exception as exc:
        return _failure(reference, str(exc))


def process_airtime_purchase(phone, network, amount):
    reference = generate_ref("AIR")
    service_id = NETWORK_AIRTIME_IDS.get(network.upper())
    if MOCK_MODE:
        return {"status": "SUCCESS", "reference": reference, "data": {"status": "MOCK_SUCCESS"}}
    if not service_id or not _credentials_ready(True):
        return _failure(reference, "VTPass credentials or network configuration is missing")
    try:
        response = _post({"request_id": reference, "serviceID": service_id, "amount": str(amount), "phone": phone})
        if _response_success(response):
            return {"status": "SUCCESS", "reference": reference, "data": response}
        return _failure(reference, _content_message(response, "VTPass airtime purchase failed"))
    except Exception as exc:
        return _failure(reference, str(exc))


def fetch_cable_plans(provider):
    service_id = CABLE_SERVICE_IDS.get(provider.upper())
    if not service_id or not _credentials_ready(False):
        return []
    try:
        response = _get("service-variations", {"serviceID": service_id})
        return [
            {"name": item.get("name"), "code": item.get("variation_code"), "amount": float(item.get("variation_amount", 0))}
            for item in response.get("content", {}).get("variations", [])
            if item.get("variation_code") and item.get("name")
        ]
    except Exception:
        logger.exception("Failed to fetch VTPass cable plans for %s", provider)
        return []


def verify_smartcard(provider, iuc):
    return _verify_account(CABLE_SERVICE_IDS.get(provider.upper()), iuc, "smartcard")


def process_cable_tv(provider, iuc, plan_code, amount, phone=""):
    return _purchase({"serviceID": CABLE_SERVICE_IDS.get(provider.upper()), "billersCode": iuc, "variation_code": plan_code, "amount": str(amount), "phone": phone}, "CABLE")


def verify_meter(disco, meter_no, meter_type):
    return _verify_account(DISCO_SERVICE_IDS.get(disco.upper()), meter_no, meter_type.lower())


def process_electricity_payment(disco, meter_no, meter_type, amount, phone=""):
    return _purchase({"serviceID": DISCO_SERVICE_IDS.get(disco.upper()), "billersCode": meter_no, "variation_code": meter_type.lower(), "amount": str(amount), "phone": phone}, "ELEC", token=True)


def verify_betting_account(platform, user_id):
    return {"valid": False, "message": "VTPass betting account verification is not configured yet"}


def process_betting_topup(platform, user_id, amount, phone=""):
    return _failure(generate_ref("BET"), "VTPass betting integration is not configured yet")


def fetch_education_packages():
    return []


def process_education_pin(exam, quantity=1, phone=""):
    return _failure(generate_ref("EDU"), "VTPass education integration is not configured yet")


def _verify_account(service_id, billers_code, variation_code):
    if not service_id or not _credentials_ready(True):
        return {"valid": False, "message": "VTPass credentials or service configuration is missing"}
    try:
        response = requests.post(
            f"{VTPASS_BASE_URL}/merchant-verify",
            json={"serviceID": service_id, "billersCode": billers_code, "type": variation_code},
            headers=_post_headers(),
            timeout=20,
        ).json()
        content = response.get("content") or {}
        return {"valid": _response_success(response), "customer_name": content.get("Customer_Name", content.get("name", "")), "message": _content_message(response, "Account verification failed"), "data": response}
    except Exception as exc:
        return {"valid": False, "message": str(exc)}


def _purchase(payload, prefix, token=False):
    reference = generate_ref(prefix)
    if not payload.get("serviceID") or not _credentials_ready(True):
        return _failure(reference, "VTPass credentials or service configuration is missing")
    payload["request_id"] = reference
    try:
        response = _post(payload)
        if _response_success(response):
            content = response.get("content") or {}
            result = {"status": "SUCCESS", "reference": reference, "reason": "", "data": response}
            if token:
                result["token"] = content.get("Token", content.get("token", "N/A"))
            return result
        return _failure(reference, _content_message(response, "VTPass purchase failed"))
    except Exception as exc:
        return _failure(reference, str(exc))
