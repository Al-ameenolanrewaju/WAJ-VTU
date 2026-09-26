import logging
import os
import uuid
import hashlib
import re
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
SWIFTBILLS_API_KEY = os.getenv("SWIFTBILLS_API_KEY", "").strip()
SWIFTBILLS_BASE_URL = os.getenv("SWIFTBILLS_BASE_URL", "https://swiftbills.com.ng/api").rstrip("/")
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


def _swiftbills_request(endpoint: str, method: str = "GET", payload: dict | None = None):
    """Make an authenticated SwiftBills request."""
    headers = {
        "Authorization": f"Token {SWIFTBILLS_API_KEY}",
        "Content-Type": "application/json",
    }
    url = f"{SWIFTBILLS_BASE_URL}/{endpoint.lstrip('/')}"
    if method.upper() == "POST":
        response = requests.post(url, json=payload or {}, headers=headers, timeout=15)
    else:
        response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()
    return response.json()


def _swiftbills_network_id(network: str, service: str = "data"):
    networks = _swiftbills_request(f"get-networks?service={service}")
    if not isinstance(networks, list):
        return None
    for item in networks:
        if isinstance(item, dict) and str(item.get("network", "")).strip().upper() == network.upper():
            return item.get("id")
    return None


def _status_value(response: dict) -> str:
    return str(response.get("status", response.get("statuscode", ""))).upper()


def _is_success(response: dict) -> bool:
    return _status_value(response) in {
        "00", "200", "SUCCESS", "ORDER_RECEIVED", "ORDER_COMPLETED"
    }


def _failure(reference: str, message: str, provider: str | None = None):
    response = {"status": "FAILED", "reference": reference, "reason": message}
    if provider:
        response["provider"] = provider
    return response


def fetch_account_balance():
    """Fetch the current ClubKonnect wallet balance for the admin dashboard."""
    if MOCK_MODE:
        return {"status": "SUCCESS", "balance": 100000.00}
    if not CLUBKONNECT_USERID or not CLUBKONNECT_APIKEY:
        return {"status": "FAILED", "reason": "ClubKonnect credentials are missing"}
    try:
        response = _provider_request("APIWalletBalance.asp", {})
        raw_balance = (
            response.get("balance")
            or response.get("Balance")
            or response.get("BALANCE")
            or response.get("wallet_balance")
        )
        if raw_balance is None:
            return {"status": "FAILED", "reason": response.get("msg", response.get("message", "Balance was not returned"))}
        return {"status": "SUCCESS", "balance": float(str(raw_balance).replace(",", "")), "data": response}
    except (TypeError, ValueError):
        return {"status": "FAILED", "reason": "ClubKonnect returned an invalid balance"}
    except Exception as exc:
        logger.exception("Failed to fetch ClubKonnect balance")
        return {"status": "FAILED", "reason": str(exc)}


# --- DATA SERVICES ---


def _fetch_clubkonnect_data_variations(network: str):
    """Fetch available data plans from ClubKonnect."""
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

    credential_fingerprint = hashlib.sha256(
        f"{CLUBKONNECT_USERID}:{CLUBKONNECT_APIKEY}".encode("utf-8")
    ).hexdigest()[:8]
    logger.info(
        "Using ClubKonnect credentials: userid=%s key_length=%d fingerprint=%s",
        CLUBKONNECT_USERID,
        len(CLUBKONNECT_APIKEY),
        credential_fingerprint,
    )

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


def _data_plan_key(name: str, data_size: str = "", plan_type: str = "", days: str = ""):
    """Build a comparable key from the fields exposed by either provider."""
    text = f"{data_size} {name}".lower()
    size_match = re.search(r"\b\d+(?:\.\d+)?\s*(?:gb|mb)\b", text)
    size = re.sub(r"\s+", "", size_match.group(0)) if size_match else ""
    type_match = re.search(r"\b(sme|gifting|corporate|direct)\b", f"{plan_type} {name}".lower())
    normalized_type = type_match.group(1) if type_match else "unknown"
    day_match = re.search(r"\b\d+\s*(?:day|days|month|months)\b", f"{days} {name}".lower())
    duration = re.sub(r"\s+", "", day_match.group(0)) if day_match else "unknown"
    return size, normalized_type, duration


def _fetch_swiftbills_data_variations(network: str):
    """Fetch SwiftBills data plans and normalize them to the local plan shape."""
    if not SWIFTBILLS_API_KEY:
        return []
    try:
        network_id = _swiftbills_network_id(network, "data")
        if network_id is None:
            logger.error("SwiftBills did not return a network ID for %s", network)
            return []

        response = _swiftbills_request("data_plans")
        if not isinstance(response, list):
            logger.error("SwiftBills data plans response was not a list")
            return []

        plans = []
        for item in response:
            if not isinstance(item, dict):
                continue
            provider_network = item.get("network")
            if str(provider_network).strip().upper() != network.upper():
                continue
            plan_id = item.get("plan_id")
            raw_price = item.get("price")
            if plan_id in (None, "") or raw_price in (None, ""):
                continue
            try:
                price = float(raw_price)
            except (TypeError, ValueError):
                continue
            data_size = str(item.get("datasize", "")).strip()
            plan_type = str(item.get("type", "")).strip()
            days = str(item.get("day", "")).strip()
            name = " ".join(part for part in (network.upper(), data_size, plan_type, f"({days} days)") if part)
            plans.append({
                "name": name,
                "variation_code": f"swiftbills:{network_id}:{plan_id}",
                "variation_amount": price,
                "comparison_key": _data_plan_key(name, data_size, plan_type, days),
            })
        logger.info("Fetched %d %s data plans from SwiftBills", len(plans), network)
        return plans
    except Exception:
        logger.exception("Failed to fetch live SwiftBills data plans for %s", network)
        return []


def fetch_data_variations(network: str):
    """Fetch both providers' data plans and keep the lowest matching price."""
    if MOCK_MODE:
        return _fetch_clubkonnect_data_variations(network)

    provider_plans = []
    if CLUBKONNECT_USERID and CLUBKONNECT_APIKEY:
        provider_plans.extend(_fetch_clubkonnect_data_variations(network))
    provider_plans.extend(_fetch_swiftbills_data_variations(network))

    lowest_by_key = {}
    unmatched = []
    for plan in provider_plans:
        key = plan.pop("comparison_key", _data_plan_key(plan.get("name", "")))
        if not key[0]:
            unmatched.append(plan)
            continue
        current = lowest_by_key.get(key)
        if current is None or float(plan.get("variation_amount", 0)) < float(current.get("variation_amount", 0)):
            lowest_by_key[key] = plan

    return list(lowest_by_key.values()) + unmatched


def process_data_purchase(
    phone: str, network: str, plan_code: str, amount: float
):
    """Processes a data top-up request."""
    logger.info(f"Processing Data: {network} {plan_code} to {phone}")
    network_code = NETWORK_CODES.get(network.upper())
    ref = generate_ref("REF_DATA")

    if MOCK_MODE:
        return {"status": "SUCCESS", "reference": ref, "provider": "mock", "reason": "", "data": {"status": "MOCK_SUCCESS"}}

    if str(plan_code).startswith("swiftbills:"):
        if not SWIFTBILLS_API_KEY:
            return _failure(ref, "SwiftBills API credentials are missing", provider="swiftbills")
        try:
            _, swift_network, swift_plan_id = str(plan_code).split(":", 2)
            response = _swiftbills_request(
                "data",
                method="POST",
                payload={
                    "network": int(swift_network),
                    "phone": phone,
                    "data_plan": int(swift_plan_id),
                    "request-id": ref,
                },
            )
            if _is_success(response):
                return {"status": "SUCCESS", "reference": ref, "provider": "swiftbills", "provider_reference": response.get("reference", response.get("request_id", ref)), "reason": "", "data": response}
            return _failure(ref, response.get("message", response.get("response", "SwiftBills data purchase failed")), provider="swiftbills")
        except Exception as exc:
            logger.error("SwiftBills data purchase failed: %s", exc)
            return _failure(ref, str(exc), provider="swiftbills")

    if not CLUBKONNECT_USERID or not CLUBKONNECT_APIKEY or not network_code:
        return _failure(ref, "ClubKonnect credentials or network configuration is missing", provider="clubkonnect")

    if network_code:
        try:
            res_data = _provider_request("APIDatabundleV1.asp", {"MobileNetwork": network_code, "DataPlan": plan_code, "MobileNumber": phone, "RequestID": ref})
            if _is_success(res_data):
                return {"status": "SUCCESS", "reference": ref, "provider": "clubkonnect", "provider_reference": res_data.get("reference", res_data.get("requestid", ref)), "reason": "", "data": res_data}
            return _failure(ref, res_data.get("msg", res_data.get("message", "API transaction declined")), provider="clubkonnect")
        except Exception as e:
            logger.error(f"Live Data Purchase Error: {e}")
            return _failure(ref, str(e), provider="clubkonnect")

    return _failure(ref, "Invalid mobile network", provider="clubkonnect")


# --- AIRTIME SERVICES ---


def process_airtime_purchase(phone: str, network: str, amount: float):
    """Processes an airtime top-up request."""
    logger.info(f"Processing Airtime: ₦{amount} {network} to {phone}")
    network_code = NETWORK_CODES.get(network.upper())
    ref = generate_ref("REF_AIRTIME")

    if MOCK_MODE:
        return {"status": "SUCCESS", "reference": ref, "provider": "mock", "reason": "", "data": {"status": "MOCK_SUCCESS"}}

    if CLUBKONNECT_USERID and CLUBKONNECT_APIKEY and network_code:
        try:
            res_data = _provider_request("APIAirtimeV1.asp", {"MobileNetwork": network_code, "Amount": amount, "MobileNumber": phone, "RequestID": ref})
            if _is_success(res_data):
                return {"status": "SUCCESS", "reference": ref, "provider": "clubkonnect", "provider_reference": res_data.get("reference", res_data.get("requestid", ref)), "reason": "", "data": res_data}
            club_error = res_data.get("msg", res_data.get("message", "Airtime order failed"))
            if not SWIFTBILLS_API_KEY:
                return _failure(ref, club_error, provider="clubkonnect")
            logger.warning("ClubKonnect airtime failed, falling back to SwiftBills: %s", club_error)
        except Exception as e:
            logger.warning("ClubKonnect airtime failed, falling back to SwiftBills: %s", e)
            if not SWIFTBILLS_API_KEY:
                return _failure(ref, str(e), provider="clubkonnect")

    if SWIFTBILLS_API_KEY:
        try:
            network_id = _swiftbills_network_id(network, "airtime")
            if network_id is None:
                return _failure(ref, "SwiftBills network configuration is missing", provider="swiftbills")
            response = _swiftbills_request(
                "airtime",
                method="POST",
                payload={
                    "network": int(network_id),
                    "phone": phone,
                    "amount": str(amount),
                    "plan_type": "VTU",
                    "request-id": ref,
                },
            )
            if _is_success(response):
                return {"status": "SUCCESS", "reference": ref, "provider": "swiftbills", "provider_reference": response.get("reference", response.get("request_id", ref)), "reason": "", "data": response}
            return _failure(ref, response.get("message", "SwiftBills airtime purchase failed"), provider="swiftbills")
        except Exception as exc:
            logger.error("SwiftBills airtime purchase failed: %s", exc)
            return _failure(ref, str(exc), provider="swiftbills")

    if not CLUBKONNECT_USERID or not CLUBKONNECT_APIKEY or not network_code:
        return _failure(ref, "ClubKonnect credentials or network configuration is missing", provider="clubkonnect")

    return _failure(ref, "Invalid mobile network", provider="clubkonnect")


# --- CABLE TV SERVICES ---


def _fetch_clubkonnect_cable_plans(provider: str):
    """Fetch available cable TV packages from ClubKonnect."""
    mock_plans = {
        "DSTV": [
            {"name": "DStv Padi", "code": "dstv-padi", "amount": 4400},
            {"name": "DStv Yanga", "code": "dstv-yanga", "amount": 6000},
        ],
        "GOTV": [
            {"name": "GOtv Jinja", "code": "gotv-jinja", "amount": 3900},
            {"name": "GOtv Max", "code": "gotv-max", "amount": 8500},
        ],
        "STARTIMES": [
            {"name": "Nova (Dish)", "code": "nova", "amount": 2100},
            {"name": "Basic (Antenna)", "code": "basic", "amount": 4000},
        ],
    }
    provider_key = provider.upper()

    if MOCK_MODE:
        return mock_plans.get(provider_key, [])

    if not CLUBKONNECT_USERID or not CLUBKONNECT_APIKEY:
        logger.error("Cannot fetch cable plans: ClubKonnect credentials are missing")
        return []

    category_names = {
        "DSTV": "DStv",
        "GOTV": "GOtv",
        "STARTIMES": "Startimes",
        "SHOWMAX": "Showmax",
    }
    category_name = category_names.get(provider_key)
    if not category_name:
        logger.error("Cannot fetch cable plans: unsupported provider %s", provider)
        return []

    try:
        response = _provider_request("APICableTVPackagesV2.asp", {})
        tv_catalog = response.get("TV_ID", {})
        if not isinstance(tv_catalog, dict):
            logger.error("ClubKonnect cable plans response has unexpected TV_ID type: %s", type(tv_catalog).__name__)
            return []

        provider_entries = tv_catalog.get(category_name, [])
        if not isinstance(provider_entries, list):
            logger.error("ClubKonnect cable plans for %s have unexpected type: %s", provider, type(provider_entries).__name__)
            return []

        raw_plans = []
        for entry in provider_entries:
            if isinstance(entry, dict) and isinstance(entry.get("PRODUCT"), list):
                raw_plans.extend(entry["PRODUCT"])

        plans = []
        for item in raw_plans:
            if not isinstance(item, dict):
                continue
            code = item.get("PACKAGE_ID")
            name = item.get("PACKAGE_NAME")
            raw_amount = item.get("PACKAGE_AMOUNT")
            if not code or not name or raw_amount in (None, ""):
                continue
            try:
                amount = float(raw_amount)
            except (TypeError, ValueError):
                continue
            if amount < 0:
                continue
            raw_discount_amount = item.get("PRODUCT_DISCOUNT_AMOUNT")
            raw_discount = item.get("PRODUCT_DISCOUNT", 0)
            try:
                discount_amount = float(raw_discount_amount) if raw_discount_amount not in (None, "") else amount
                discount_percent = float(raw_discount or 0) * 100
            except (TypeError, ValueError):
                discount_amount = amount
                discount_percent = 0.0
            plans.append({
                "name": name,
                "code": code,
                "amount": discount_amount,
                "list_amount": amount,
                "discount_amount": max(amount - discount_amount, 0),
                "discount_percent": discount_percent,
            })

        if not plans:
            logger.error(
                "ClubKonnect returned no cable plans for %s; available categories=%s status=%s message=%s",
                provider,
                list(tv_catalog.keys()),
                response.get("status", response.get("statuscode", "")),
                response.get("msg", response.get("message", "")),
            )
        logger.info("Fetched %d %s cable plans from ClubKonnect", len(plans), provider)
        return plans
    except Exception:
        logger.exception("Failed to fetch cable plans for %s", provider)
        return []


def _fetch_swiftbills_cable_plans(provider: str):
    """Fetch and normalize cable plans from SwiftBills."""
    if not SWIFTBILLS_API_KEY:
        return []
    try:
        response = _swiftbills_request(f"get-cable-plan?cable={provider.upper()}")
        if not isinstance(response, list):
            return []
        plans = []
        for item in response:
            if not isinstance(item, dict) or item.get("id") in (None, ""):
                continue
            try:
                amount = float(item.get("price"))
            except (TypeError, ValueError):
                continue
            name = str(item.get("name", "Cable plan")).strip()
            cable_id = item.get("cable_id")
            plans.append({
                "name": name,
                "code": f"swiftbills:{cable_id}:{item['id']}",
                "amount": amount,
                "discount_amount": 0,
                "comparison_key": re.sub(r"[^a-z0-9]+", " ", name.lower()).strip(),
            })
        return plans
    except Exception:
        logger.exception("Failed to fetch SwiftBills cable plans for %s", provider)
        return []


def fetch_cable_plans(provider: str):
    """Fetch cable plans from both providers and retain the lowest matching price."""
    if MOCK_MODE:
        return _fetch_clubkonnect_cable_plans(provider)
    plans = []
    if CLUBKONNECT_USERID and CLUBKONNECT_APIKEY:
        plans.extend(_fetch_clubkonnect_cable_plans(provider))
    plans.extend(_fetch_swiftbills_cable_plans(provider))
    lowest_by_key = {}
    unmatched = []
    for plan in plans:
        key = plan.pop("comparison_key", re.sub(r"[^a-z0-9]+", " ", str(plan.get("name", "")).lower()).strip())
        if not key:
            unmatched.append(plan)
            continue
        current = lowest_by_key.get(key)
        if current is None or float(plan.get("amount", 0)) < float(current.get("amount", 0)):
            lowest_by_key[key] = plan
    return list(lowest_by_key.values()) + unmatched


def verify_smartcard(provider: str, iuc: str):
    """Verifies cable TV decoder / IUC number."""
    if MOCK_MODE:
        return {"valid": True, "customer_name": "Test Customer", "message": "Success"}
    if CLUBKONNECT_USERID and CLUBKONNECT_APIKEY:
        try:
            response = _provider_request(
                "APIVerifyCableTVV1.asp",
                {"CableTV": provider.upper(), "SmartCardNo": iuc},
            )
            if _is_success(response) or bool(response.get("customer_name")) and not response.get("customer_name", "").startswith("INVALID_"):
                return {
                    "valid": True,
                    "customer_name": response.get("customer_name", response.get("name", "")),
                    "message": response.get("msg", response.get("message", "Cable account verification failed")),
                    "data": response,
                }
            if not SWIFTBILLS_API_KEY:
                return {"valid": False, "message": response.get("msg", response.get("message", "Cable account verification failed"))}
            logger.warning("ClubKonnect cable verification failed, falling back to SwiftBills: %s", response)
        except Exception as e:
            logger.warning("ClubKonnect cable verification failed, falling back to SwiftBills: %s", e)
            if not SWIFTBILLS_API_KEY:
                return {"valid": False, "message": str(e)}
    if SWIFTBILLS_API_KEY:
        try:
            swift_plans = _fetch_swiftbills_cable_plans(provider)
            cable_id = next(
                (str(plan["code"]).split(":")[1] for plan in swift_plans if str(plan.get("code", "")).startswith("swiftbills:")),
                None,
            )
            if cable_id is None:
                return {"valid": False, "message": "SwiftBills cable configuration is missing"}
            response = _swiftbills_request(f"cable/cable-validation?iuc={iuc}&cablenumber={cable_id}")
            return {
                "valid": _is_success(response) or bool(response.get("name")),
                "customer_name": response.get("name", ""),
                "message": response.get("message", "Cable account verification failed"),
                "data": response,
            }
        except Exception as exc:
            logger.error("SwiftBills cable verification failed: %s", exc)
            return {"valid": False, "message": str(exc)}
    if not CLUBKONNECT_USERID or not CLUBKONNECT_APIKEY:
        return {"valid": False, "message": "ClubKonnect credentials are missing"}
    return {"valid": False, "message": "Cable account verification failed"}


def process_cable_tv(
    provider: str, iuc: str, plan_code: str, amount: float, phone: str = ""
):
    """Executes TV package subscription."""
    ref = generate_ref("REF_CABLE")
    if MOCK_MODE:
        return {"status": "SUCCESS", "reference": ref, "provider": "mock", "reason": "", "data": {"status": "MOCK_SUCCESS"}}
    if CLUBKONNECT_USERID and CLUBKONNECT_APIKEY:
        try:
            response = _provider_request(
                "APICableTVV1.asp",
                {"CableTV": provider.lower(), "Package": plan_code, "SmartCardNo": iuc, "PhoneNo": phone, "RequestID": ref},
            )
            if _is_success(response):
                return {"status": "SUCCESS", "reference": ref, "provider": "clubkonnect", "provider_reference": response.get("reference", response.get("requestid", ref)), "reason": "", "data": response}
            club_error = response.get("msg", response.get("message", "Cable subscription failed"))
            if not SWIFTBILLS_API_KEY or not str(plan_code).startswith("swiftbills:"):
                return _failure(ref, club_error, provider="clubkonnect")
            logger.warning("ClubKonnect cable payment failed, falling back to SwiftBills: %s", club_error)
        except Exception as e:
            logger.warning("ClubKonnect cable payment failed, falling back to SwiftBills: %s", e)
            if not SWIFTBILLS_API_KEY or not str(plan_code).startswith("swiftbills:"):
                return _failure(ref, str(e), provider="clubkonnect")
    if str(plan_code).startswith("swiftbills:") and SWIFTBILLS_API_KEY:
        try:
            _, cable_id, plan_id = str(plan_code).split(":", 2)
            response = _swiftbills_request(
                "cable",
                method="POST",
                payload={
                    "cable": int(cable_id),
                    "iuc": iuc,
                    "cable_plan": int(plan_id),
                    "request-id": ref,
                },
            )
            if _is_success(response):
                return {"status": "SUCCESS", "reference": ref, "provider": "swiftbills", "provider_reference": response.get("reference", response.get("request_id", ref)), "reason": "", "data": response}
            return _failure(ref, response.get("message", "SwiftBills cable purchase failed"), provider="swiftbills")
        except Exception as exc:
            logger.error("SwiftBills cable purchase failed: %s", exc)
            return _failure(ref, str(exc), provider="swiftbills")
    if not CLUBKONNECT_USERID or not CLUBKONNECT_APIKEY:
        return _failure(ref, "ClubKonnect credentials are missing", provider="clubkonnect")
    return _failure(ref, "Cable subscription failed", provider="clubkonnect")


# --- ELECTRICITY SERVICES ---


def _swiftbills_disco_id(disco: str):
    bills = _swiftbills_request("get-bill")
    if not isinstance(bills, list):
        return None
    for item in bills:
        if not isinstance(item, dict):
            continue
        if str(item.get("abb", "")).strip().upper() == disco.upper():
            return item.get("id")
    return None


def verify_meter(disco: str, meter_no: str, meter_type: str):
    """Verifies electricity meter account details."""
    if MOCK_MODE:
        return {"valid": True, "customer_name": "Test Meter User", "message": "Success"}
    if CLUBKONNECT_USERID and CLUBKONNECT_APIKEY:
        try:
            response = _provider_request(
                "APIVerifyElectricityV1.asp",
                {"ElectricCompany": DISCO_CODES.get(disco.upper(), disco.upper()), "MeterNo": meter_no, "MeterType": METER_TYPE_CODES.get(meter_type.upper(), meter_type)},
            )
            if bool(response.get("customer_name")) and response.get("customer_name", "").upper() not in {"N/A", "NA", "INVALID_METERNO"}:
                return {
                    "valid": True,
                    "customer_name": response.get("customer_name", response.get("name", "")),
                    "message": response.get("msg", response.get("message", "Meter verification failed")),
                    "data": response,
                }
            if not SWIFTBILLS_API_KEY:
                return {"valid": False, "message": response.get("msg", response.get("message", "Meter verification failed"))}
            logger.warning("ClubKonnect meter verification failed, falling back to SwiftBills: %s", response)
        except Exception as e:
            logger.warning("ClubKonnect meter verification failed, falling back to SwiftBills: %s", e)
            if not SWIFTBILLS_API_KEY:
                return {"valid": False, "message": str(e)}
    if SWIFTBILLS_API_KEY:
        try:
            disco_id = _swiftbills_disco_id(disco)
            if disco_id is None:
                return {"valid": False, "message": "SwiftBills electricity configuration is missing"}
            response = _swiftbills_request(
                f"bill/bill-validation?meter_number={meter_no}&meter_type={meter_type.lower()}&disconumber={disco_id}"
            )
            return {
                "valid": _is_success(response) or bool(response.get("name")),
                "customer_name": response.get("name", ""),
                "message": response.get("message", "Meter verification failed"),
                "data": response,
            }
        except Exception as exc:
            logger.error("SwiftBills meter verification failed: %s", exc)
            return {"valid": False, "message": str(exc)}
    if not CLUBKONNECT_USERID or not CLUBKONNECT_APIKEY:
        return {"valid": False, "message": "ClubKonnect credentials are missing"}
    return {"valid": False, "message": "Meter verification failed"}


def process_electricity_payment(
    disco: str, meter_no: str, meter_type: str, amount: float, phone: str = ""
):
    """Executes electricity bill payment and returns generated token."""
    ref = generate_ref("REF_ELEC")
    if MOCK_MODE:
        return {"status": "SUCCESS", "reference": ref, "provider": "mock", "token": "MOCK-1234-5678-9012", "reason": "", "data": {"status": "MOCK_SUCCESS"}}
    if CLUBKONNECT_USERID and CLUBKONNECT_APIKEY:
        try:
            response = _provider_request(
                "APIElectricityV1.asp",
                {"ElectricCompany": DISCO_CODES.get(disco.upper(), disco.upper()), "MeterType": METER_TYPE_CODES.get(meter_type.upper(), meter_type), "MeterNo": meter_no, "Amount": int(amount), "PhoneNo": phone, "RequestID": ref},
            )
            if _is_success(response):
                return {"status": "SUCCESS", "reference": ref, "provider": "clubkonnect", "provider_reference": response.get("reference", response.get("requestid", ref)), "token": response.get("token", response.get("metertoken", "N/A")), "reason": "", "data": response}
            club_error = response.get("msg", response.get("message", "Electricity payment failed"))
            if not SWIFTBILLS_API_KEY:
                return _failure(ref, club_error, provider="clubkonnect")
            logger.warning("ClubKonnect electricity payment failed, falling back to SwiftBills: %s", club_error)
        except Exception as e:
            logger.warning("ClubKonnect electricity payment failed, falling back to SwiftBills: %s", e)
            if not SWIFTBILLS_API_KEY:
                return _failure(ref, str(e), provider="clubkonnect")
    if SWIFTBILLS_API_KEY:
        try:
            disco_id = _swiftbills_disco_id(disco)
            if disco_id is None:
                return _failure(ref, "SwiftBills electricity configuration is missing", provider="swiftbills")
            response = _swiftbills_request(
                "bill",
                method="POST",
                payload={
                    "disco": int(disco_id),
                    "meter_type": meter_type.lower(),
                    "meter_number": meter_no,
                    "amount": str(amount),
                    "phone": phone,
                    "request-id": ref,
                },
            )
            if _is_success(response):
                return {
                    "status": "SUCCESS",
                    "reference": ref,
                    "provider": "swiftbills",
                    "provider_reference": response.get("reference", response.get("request_id", ref)),
                    "token": response.get("token", "N/A"),
                    "reason": "",
                    "data": response,
                }
            return _failure(ref, response.get("message", "SwiftBills electricity payment failed"), provider="swiftbills")
        except Exception as exc:
            logger.error("SwiftBills electricity payment failed: %s", exc)
            return _failure(ref, str(exc), provider="swiftbills")
    if not CLUBKONNECT_USERID or not CLUBKONNECT_APIKEY:
        return _failure(ref, "ClubKonnect credentials are missing", provider="clubkonnect")
    return _failure(ref, "Electricity payment failed", provider="clubkonnect")


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
        return {"status": "SUCCESS", "reference": ref, "provider": "mock", "reason": "", "data": {"status": "MOCK_SUCCESS"}}
    if not CLUBKONNECT_USERID or not CLUBKONNECT_APIKEY:
        return _failure(ref, "ClubKonnect credentials are missing", provider="clubkonnect")
    try:
        response = _provider_request(
            "APIBettingV1.asp",
            {"BettingCompany": platform.upper(), "CustomerID": user_id, "Amount": int(amount), "PhoneNo": phone, "RequestID": ref},
        )
        if _is_success(response):
            return {"status": "SUCCESS", "reference": ref, "provider": "clubkonnect", "provider_reference": response.get("reference", response.get("requestid", ref)), "reason": "", "data": response}
        return _failure(ref, response.get("msg", response.get("message", "Betting top up failed")), provider="clubkonnect")
    except Exception as e:
        return _failure(ref, str(e), provider="clubkonnect")


def _fetch_clubkonnect_education_packages():
    """Fetch the WAEC and JAMB e-PIN packages exposed by ClubKonnect."""
    if MOCK_MODE:
        return [
            {"name": "WAEC Result Checker PIN", "code": "waecdirect", "amount": 5350},
            {"name": "JAMB UTME PIN", "code": "utme-no-mock", "amount": 5700},
        ]

    if not CLUBKONNECT_USERID or not CLUBKONNECT_APIKEY:
        logger.error("Cannot fetch education packages: ClubKonnect credentials are missing")
        return []

    try:
        package_endpoints = (
            "APIWAECPackagesV2.asp",
            "APIJAMBPackagesV2.asp",
        )
        packages = []
        seen_codes = set()

        for endpoint in package_endpoints:
            response = _provider_request(endpoint, {})
            exam_types = response.get("EXAM_TYPE", [])
            if not isinstance(exam_types, list):
                logger.error(
                    "ClubKonnect education response has unexpected EXAM_TYPE type: endpoint=%s type=%s",
                    endpoint,
                    type(exam_types).__name__,
                )
                continue

            for item in exam_types:
                if not isinstance(item, dict):
                    continue
                code = item.get("PRODUCT_CODE")
                name = item.get("PRODUCT_DESCRIPTION")
                raw_amount = item.get("PRODUCT_AMOUNT")
                if not code or not name or raw_amount in (None, "") or code in seen_codes:
                    continue
                if "neco" in f"{code} {name}".lower():
                    continue
                try:
                    amount = float(raw_amount)
                except (TypeError, ValueError):
                    continue
                if amount < 0:
                    continue
                packages.append({"name": name, "code": code, "amount": amount})
                seen_codes.add(code)

        if not packages:
            logger.error("ClubKonnect returned no education packages")
        logger.info("Fetched %d education packages from ClubKonnect", len(packages))
        return packages
    except Exception:
        logger.exception("Failed to fetch education packages")
        return []


def _fetch_swiftbills_education_packages():
    """Fetch supported WAEC and JAMB packages from SwiftBills."""
    if not SWIFTBILLS_API_KEY:
        return []
    try:
        response = _swiftbills_request("get-exam")
        if not isinstance(response, list):
            return []
        packages = []
        for item in response:
            if not isinstance(item, dict) or item.get("id") in (None, ""):
                continue
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            try:
                amount = float(item.get("price"))
            except (TypeError, ValueError):
                continue
            packages.append({
                "name": name,
                "code": f"swiftbills:{item['id']}",
                "amount": amount,
                "comparison_key": name.lower(),
            })
        return packages
    except Exception:
        logger.exception("Failed to fetch SwiftBills education packages")
        return []


def fetch_education_packages():
    """Fetch education packages from both providers and keep the lowest price."""
    if MOCK_MODE:
        return _fetch_clubkonnect_education_packages()
    packages = []
    if CLUBKONNECT_USERID and CLUBKONNECT_APIKEY:
        packages.extend(_fetch_clubkonnect_education_packages())
    packages.extend(_fetch_swiftbills_education_packages())
    lowest_by_key = {}
    unmatched = []
    for package in packages:
        package_name = str(package.get("name", "")).lower()
        key = package.pop("comparison_key", "")
        if not key:
            key = next((exam for exam in ("waec", "jamb") if exam in package_name), package_name)
        if not key:
            unmatched.append(package)
            continue
        current = lowest_by_key.get(key)
        if current is None or float(package.get("amount", 0)) < float(current.get("amount", 0)):
            lowest_by_key[key] = package
    return list(lowest_by_key.values()) + unmatched


def process_education_pin(exam: str, quantity: int = 1, phone: str = ""):
    """Generates educational exam result checker PINs."""
    ref = generate_ref("REF_EDU")
    if MOCK_MODE:
        return {"status": "SUCCESS", "reference": ref, "provider": "mock", "pins": [f"PIN-{exam.upper()}-MOCK" for _ in range(quantity)], "reason": "", "data": {"status": "MOCK_SUCCESS"}}
    if CLUBKONNECT_USERID and CLUBKONNECT_APIKEY:
        try:
            endpoint = "APIJAMBV1.asp" if str(exam).lower().startswith("jamb") else "APIWAECV1.asp"
            response = _provider_request(
                endpoint,
                {"ExamType": str(exam).lower(), "PhoneNo": phone, "RequestID": ref},
            )
            if _is_success(response):
                pin_values = response.get("pins", response.get("pin", response.get("serial_number", "")))
                pins = pin_values if isinstance(pin_values, list) else [pin_values]
                return {"status": "SUCCESS", "reference": ref, "provider": "clubkonnect", "provider_reference": response.get("reference", response.get("requestid", ref)), "pins": pins, "reason": "", "data": response}
            club_error = response.get("msg", response.get("message", "Education PIN purchase failed"))
            if not SWIFTBILLS_API_KEY or not str(exam).startswith("swiftbills:"):
                return _failure(ref, club_error, provider="clubkonnect")
            logger.warning("ClubKonnect exam PIN purchase failed, falling back to SwiftBills: %s", club_error)
        except Exception as e:
            logger.warning("ClubKonnect exam PIN purchase failed, falling back to SwiftBills: %s", e)
            if not SWIFTBILLS_API_KEY or not str(exam).startswith("swiftbills:"):
                return _failure(ref, str(e), provider="clubkonnect")
    if str(exam).startswith("swiftbills:") and SWIFTBILLS_API_KEY:
        try:
            _, exam_id = str(exam).split(":", 1)
            response = _swiftbills_request(
                "exam",
                method="POST",
                payload={"exam": int(exam_id), "quantity": int(quantity), "request-id": ref},
            )
            if _is_success(response):
                pin = response.get("pin", response.get("serial_number", ""))
                return {"status": "SUCCESS", "reference": ref, "provider": "swiftbills", "provider_reference": response.get("reference", response.get("request_id", ref)), "pins": [pin] if pin else [], "reason": "", "data": response}
            return _failure(ref, response.get("message", "SwiftBills exam PIN purchase failed"), provider="swiftbills")
        except Exception as exc:
            logger.error("SwiftBills exam PIN purchase failed: %s", exc)
            return _failure(ref, str(exc), provider="swiftbills")
    if not CLUBKONNECT_USERID or not CLUBKONNECT_APIKEY:
        return _failure(ref, "ClubKonnect credentials are missing", provider="clubkonnect")
    return _failure(ref, "Education PIN purchase failed", provider="clubkonnect")