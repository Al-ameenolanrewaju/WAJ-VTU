import logging
import os
import uuid
import requests
from urllib.parse import urlencode
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("vtu_provider")

# Environment Credentials (Optional - fallback to mock if empty)
CLUBKONNECT_USERID = os.getenv("CLUBKONNECT_USERID", "").strip()
CLUBKONNECT_APIKEY = os.getenv("CLUBKONNECT_APIKEY", "").strip()
CLUBKONNECT_BASE_URL = os.getenv(
    "CLUBKONNECT_BASE_URL", "https://www.nellobytesystems.com"
)
CANONICAL_CLUBKONNECT_BASE_URL = "https://www.nellobytesystems.com"
MOCK_MODE = os.getenv("MOCK_MODE", "False").lower() == "true"

NETWORK_CODES = {"MTN": "01", "GLO": "02", "9MOBILE": "03", "AIRTEL": "04"}
NETWORK_NAMES = {"01": "MTN", "02": "Glo", "03": "m_9mobile", "04": "Airtel"}
DISCO_CODES = {"IKEDC": "01", "EKEDC": "02", "AEDC": "03", "IBEDC": "04"}
METER_TYPE_CODES = {"PREPAID": "01", "POSTPAID": "02"}


def generate_ref(prefix: str) -> str:
    """Generates a unique reference identifier."""
    return f"{prefix}_{uuid.uuid4().hex[:10].upper()}"


def _provider_request(endpoint: str, params: dict):
    """Make an authenticated ClubKonnect request and normalize its response."""
    request_params = {
        "UserID": CLUBKONNECT_USERID,
        "APIKey": CLUBKONNECT_APIKEY,
        **params,
    }
    path = f"/{endpoint.lstrip('/')}?{urlencode(request_params)}"
    base_urls = [CLUBKONNECT_BASE_URL.rstrip("/")]
    if base_urls[0].lower() != CANONICAL_CLUBKONNECT_BASE_URL.lower():
        base_urls.append(CANONICAL_CLUBKONNECT_BASE_URL)

    url = f"{base_urls[0]}{path}"
    response = requests.get(url, timeout=15)
    if response.status_code == 404 and len(base_urls) > 1:
        logger.warning("ClubKonnect URL returned 404; retrying canonical endpoint")
        url = f"{base_urls[1]}{path}"
        response = requests.get(url, timeout=15)
    if not response.ok:
        logger.error(
            "ClubKonnect request failed: endpoint=%s status=%s base_url=%s response=%s",
            endpoint,
            response.status_code,
            url.split("?")[0],
            response.text[:200],
        )
    response.raise_for_status()
    return response.json()


def _status_value(response: dict) -> str:
    return str(response.get("status", response.get("statuscode", ""))).upper()


def _is_success(response: dict) -> bool:
    return _status_value(response) in {
        "00", "200", "SUCCESS", "ORDER_RECEIVED", "ORDER_COMPLETED"
    }


def _failure(reference: str, message: str):
    return {"status": "FAILED", "reference": reference, "reason": message}


# --- DATA SERVICES ---


def fetch_data_variations(network: str):
    """Fetches available data plans for a given network."""
    network_code = NETWORK_CODES.get(network.upper())

    if MOCK_MODE:
        return [
            {"name": f"{network} 1GB Daily (24 Hours)", "variation_code": "1gb_daily", "variation_amount": 300},
            {"name": f"{network} 2GB 2-Day Plan", "variation_code": "2gb_2day", "variation_amount": 600},
            {"name": f"{network} 3GB Weekly Plan (7 Days)", "variation_code": "3gb_weekly", "variation_amount": 1000},
            {"name": f"{network} 2GB Monthly", "variation_code": "2gb_monthly", "variation_amount": 650},
            {"name": f"{network} Night Awoof Promo", "variation_code": "2gb_awoof", "variation_amount": 200},
        ]

    if not network_code:
        logger.error("Cannot fetch data plans: unsupported network %s", network)
        return []

    if not CLUBKONNECT_USERID or not CLUBKONNECT_APIKEY:
        logger.error("Cannot fetch data plans: ClubKonnect credentials are missing")
        return []

    try:
        data = _provider_request("APIDatabundlePlansV2.asp", {})
        mobile_networks = data.get("MOBILE_NETWORK", {})
        if not isinstance(mobile_networks, dict):
            logger.error(
                "ClubKonnect plans response has unexpected MOBILE_NETWORK type: %s",
                type(mobile_networks).__name__,
            )
            return []
        network_name = NETWORK_NAMES[network_code]
        raw_plans = mobile_networks.get(network_name, [])
        if not raw_plans:
            logger.error(
                "ClubKonnect returned no plans for %s; available network keys=%s top-level keys=%s status=%s message=%s",
                network_name,
                list(mobile_networks.keys()),
                list(data.keys()),
                data.get("status", data.get("statuscode", "")),
                data.get("msg", data.get("message", "")),
            )
        if raw_plans and isinstance(raw_plans[0], dict) and "PRODUCT" in raw_plans[0]:
            raw_plans = raw_plans[0]["PRODUCT"]

        plans = []
        for item in raw_plans:
            if not isinstance(item, dict):
                continue
            code = item.get("PRODUCT_ID") or item.get("PRODUCT_CODE")
            name = item.get("PRODUCT_NAME")
            if not code or not name:
                continue
            plans.append(
                {
                    "name": name,
                    "variation_code": code,
                    "variation_amount": float(item.get("PRODUCT_AMOUNT", 0)),
                }
            )
        logger.info("Fetched %d %s data plans from ClubKonnect", len(plans), network)
        return plans
    except Exception:
        logger.exception("Failed to fetch live data plans for %s", network)
        return []


def process_data_purchase(
    phone: str, network: str, plan_code: str, amount: float
):
    """Processes a data top-up request."""
    logger.info(f"Processing Data: {network} {plan_code} to {phone}")
    network_code = NETWORK_CODES.get(network.upper())
    ref = generate_ref("REF_DATA")

    if MOCK_MODE:
        return {"status": "SUCCESS", "reference": ref, "reason": "", "data": {"status": "MOCK_SUCCESS"}}

    if not CLUBKONNECT_USERID or not CLUBKONNECT_APIKEY or not network_code:
        return _failure(ref, "ClubKonnect credentials or network configuration is missing")

    if network_code:
        try:
            res_data = _provider_request("APIDatabundleV1.asp", {"MobileNetwork": network_code, "DataPlan": plan_code, "MobileNumber": phone, "RequestID": ref})
            if _is_success(res_data):
                return {"status": "SUCCESS", "reference": ref, "reason": "", "data": res_data}
            return _failure(ref, res_data.get("msg", res_data.get("message", "API transaction declined")))
        except Exception as e:
            logger.error(f"Live Data Purchase Error: {e}")
            return _failure(ref, str(e))

    return _failure(ref, "Invalid mobile network")


# --- AIRTIME SERVICES ---


def process_airtime_purchase(phone: str, network: str, amount: float):
    """Processes an airtime top-up request."""
    logger.info(f"Processing Airtime: ₦{amount} {network} to {phone}")
    network_code = NETWORK_CODES.get(network.upper())
    ref = generate_ref("REF_AIRTIME")

    if MOCK_MODE:
        return {"status": "SUCCESS", "reference": ref, "reason": "", "data": {"status": "MOCK_SUCCESS"}}

    if not CLUBKONNECT_USERID or not CLUBKONNECT_APIKEY or not network_code:
        return _failure(ref, "ClubKonnect credentials or network configuration is missing")

    if network_code:
        try:
            res_data = _provider_request("APIAirtimeV1.asp", {"MobileNetwork": network_code, "Amount": amount, "MobileNumber": phone, "RequestID": ref})
            if _is_success(res_data):
                return {"status": "SUCCESS", "reference": ref, "reason": "", "data": res_data}
            return _failure(ref, res_data.get("msg", res_data.get("message", "Airtime order failed")))
        except Exception as e:
            logger.error(f"Live Airtime Purchase Error: {e}")
            return _failure(ref, str(e))

    return _failure(ref, "Invalid mobile network")


# --- CABLE TV SERVICES ---


def fetch_cable_plans(provider: str):
    """Returns the supported TV packages used to build the purchase menu."""
    return [
        {"name": f"{provider} Basic", "code": "basic", "amount": 2500},
        {"name": f"{provider} Premium", "code": "premium", "amount": 5000},
    ]


def verify_smartcard(provider: str, iuc: str):
    """Verifies cable TV decoder / IUC number."""
    if MOCK_MODE:
        return {"valid": True, "customer_name": "Test Customer", "message": "Success"}
    if not CLUBKONNECT_USERID or not CLUBKONNECT_APIKEY:
        return {"valid": False, "message": "ClubKonnect credentials are missing"}
    try:
        response = _provider_request(
            "APIVerifyCableTVV1.asp",
            {"CableTV": provider.upper(), "SmartCardNo": iuc},
        )
        return {
            "valid": _is_success(response) or bool(response.get("customer_name")) and not response.get("customer_name", "").startswith("INVALID_"),
            "customer_name": response.get("customer_name", response.get("name", "")),
            "message": response.get("msg", response.get("message", "Cable account verification failed")),
            "data": response,
        }
    except Exception as e:
        logger.error(f"Cable verification failed: {e}")
        return {"valid": False, "message": str(e)}


def process_cable_tv(
    provider: str, iuc: str, plan_code: str, amount: float, phone: str = ""
):
    """Executes TV package subscription."""
    ref = generate_ref("REF_CABLE")
    if MOCK_MODE:
        return {"status": "SUCCESS", "reference": ref, "reason": "", "data": {"status": "MOCK_SUCCESS"}}
    if not CLUBKONNECT_USERID or not CLUBKONNECT_APIKEY:
        return _failure(ref, "ClubKonnect credentials are missing")
    try:
        response = _provider_request(
            "APICableTVV1.asp",
            {"CableTV": provider.lower(), "Package": plan_code, "SmartCardNo": iuc, "PhoneNo": phone, "RequestID": ref},
        )
        if _is_success(response):
            return {"status": "SUCCESS", "reference": ref, "reason": "", "data": response}
        return _failure(ref, response.get("msg", response.get("message", "Cable subscription failed")))
    except Exception as e:
        return _failure(ref, str(e))


# --- ELECTRICITY SERVICES ---


def verify_meter(disco: str, meter_no: str, meter_type: str):
    """Verifies electricity meter account details."""
    if MOCK_MODE:
        return {"valid": True, "customer_name": "Test Meter User", "message": "Success"}
    if not CLUBKONNECT_USERID or not CLUBKONNECT_APIKEY:
        return {"valid": False, "message": "ClubKonnect credentials are missing"}
    try:
        response = _provider_request(
            "APIVerifyElectricityV1.asp",
            {"ElectricCompany": DISCO_CODES.get(disco.upper(), disco.upper()), "MeterNo": meter_no, "MeterType": METER_TYPE_CODES.get(meter_type.upper(), meter_type)},
        )
        return {
            "valid": bool(response.get("customer_name")) and response.get("customer_name", "").upper() not in {"N/A", "NA", "INVALID_METERNO"},
            "customer_name": response.get("customer_name", response.get("name", "")),
            "message": response.get("msg", response.get("message", "Meter verification failed")),
            "data": response,
        }
    except Exception as e:
        logger.error(f"Meter verification failed: {e}")
        return {"valid": False, "message": str(e)}


def process_electricity_payment(
    disco: str, meter_no: str, meter_type: str, amount: float, phone: str = ""
):
    """Executes electricity bill payment and returns generated token."""
    ref = generate_ref("REF_ELEC")
    if MOCK_MODE:
        return {"status": "SUCCESS", "reference": ref, "token": "MOCK-1234-5678-9012", "reason": "", "data": {"status": "MOCK_SUCCESS"}}
    if not CLUBKONNECT_USERID or not CLUBKONNECT_APIKEY:
        return _failure(ref, "ClubKonnect credentials are missing")
    try:
        response = _provider_request(
            "APIElectricityV1.asp",
            {"ElectricCompany": DISCO_CODES.get(disco.upper(), disco.upper()), "MeterType": METER_TYPE_CODES.get(meter_type.upper(), meter_type), "MeterNo": meter_no, "Amount": int(amount), "PhoneNo": phone, "RequestID": ref},
        )
        if _is_success(response):
            return {"status": "SUCCESS", "reference": ref, "token": response.get("token", response.get("metertoken", "N/A")), "reason": "", "data": response}
        return _failure(ref, response.get("msg", response.get("message", "Electricity payment failed")))
    except Exception as e:
        return _failure(ref, str(e))


# --- BETTING & EDUCATION SERVICES ---


def verify_betting_account(platform: str, user_id: str):
    """Verifies betting wallet user account ID."""
    if MOCK_MODE:
        return {"valid": True, "account_name": "Verified Bettor", "message": "Success"}
    if not CLUBKONNECT_USERID or not CLUBKONNECT_APIKEY:
        return {"valid": False, "message": "ClubKonnect credentials are missing"}
    try:
        response = _provider_request(
            "APIVerifyBettingV1.asp",
            {"BettingCompany": platform.upper(), "CustomerID": user_id},
        )
        return {
            "valid": bool(response.get("customer_name")) and not response.get("customer_name", "").lower().startswith("error"),
            "account_name": response.get("customer_name", response.get("name", "")),
            "message": response.get("msg", response.get("message", "Betting account verification failed")),
            "data": response,
        }
    except Exception as e:
        logger.error(f"Betting verification failed: {e}")
        return {"valid": False, "message": str(e)}


def process_betting_topup(platform: str, user_id: str, amount: float, phone: str = ""):
    """Top up a betting account wallet."""
    ref = generate_ref("REF_BET")
    if MOCK_MODE:
        return {"status": "SUCCESS", "reference": ref, "reason": "", "data": {"status": "MOCK_SUCCESS"}}
    if not CLUBKONNECT_USERID or not CLUBKONNECT_APIKEY:
        return _failure(ref, "ClubKonnect credentials are missing")
    try:
        response = _provider_request(
            "APIBettingV1.asp",
            {"BettingCompany": platform.upper(), "CustomerID": user_id, "Amount": int(amount), "PhoneNo": phone, "RequestID": ref},
        )
        if _is_success(response):
            return {"status": "SUCCESS", "reference": ref, "reason": "", "data": response}
        return _failure(ref, response.get("msg", response.get("message", "Betting top up failed")))
    except Exception as e:
        return _failure(ref, str(e))


def fetch_education_packages():
    """Returns educational scratch card packages (WAEC/NECO)."""
    return [
        {"name": "WAEC Result Checker", "code": "waecdirect", "amount": 3800},
        {"name": "NECO Result Checker", "code": "neco", "amount": 1200},
    ]


def process_education_pin(exam: str, quantity: int = 1, phone: str = ""):
    """Generates educational exam result checker PINs."""
    ref = generate_ref("REF_EDU")
    if MOCK_MODE:
        return {"status": "SUCCESS", "reference": ref, "pins": [f"PIN-{exam.upper()}-MOCK" for _ in range(quantity)], "reason": "", "data": {"status": "MOCK_SUCCESS"}}
    if not CLUBKONNECT_USERID or not CLUBKONNECT_APIKEY:
        return _failure(ref, "ClubKonnect credentials are missing")
    try:
        endpoint = "APIJAMBV1.asp" if exam.lower().startswith("jamb") else "APIWAECV1.asp"
        response = _provider_request(
            endpoint,
            {"ExamType": exam.lower(), "PhoneNo": phone, "RequestID": ref},
        )
        if _is_success(response):
            pin_values = response.get("pins", response.get("pin", response.get("serial_number", "")))
            pins = pin_values if isinstance(pin_values, list) else [pin_values]
            return {"status": "SUCCESS", "reference": ref, "pins": pins, "reason": "", "data": response}
        return _failure(ref, response.get("msg", response.get("message", "Education PIN purchase failed")))
    except Exception as e:
        return _failure(ref, str(e))