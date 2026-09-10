import os
import uuid
import requests

# Base configuration from environment variables
CK_USERID = os.getenv("CLUBKONNECT_USERID", "CK101290660")
CK_APIKEY = os.getenv("CLUBKONNECT_APIKEY", "")
CK_BASE_URL = os.getenv("CLUBKONNECT_BASE_URL", "https://www.clubkonnect.com/API")
MOCK_MODE = os.getenv("MOCK_MODE", "False").lower() == "true"

# Mapping network names to ClubKonnect codes
NETWORK_CODES = {
    "MTN": "01",
    "GLO": "02",
    "9MOBILE": "03",
    "AIRTEL": "04"
}


def _generate_reference(prefix="VTU"):
    """Generates a unique transaction reference."""
    return f"{prefix}_{uuid.uuid4().hex[:10].upper()}"


def process_airtime_purchase(phone, network, amount):
    """Executes Airtime purchase via ClubKonnect."""
    ref = _generate_reference("AIR")
    net_code = NETWORK_CODES.get(network.upper())

    if MOCK_MODE:
        return {"status": "SUCCESS", "reference": ref, "data": {"status": "MOCK_SUCCESS"}}

    if not net_code:
        return {"status": "FAILED", "reason": f"Invalid network specified: {network}", "reference": ref}

    url = (
        f"{CK_BASE_URL}/Airtime.asp?"
        f"UserID={CK_USERID}&APIKey={CK_APIKEY}"
        f"&MobileNetwork={net_code}&Amount={int(amount)}"
        f"&MobileNo={phone}&RequestID={ref}"
    )

    try:
        response = requests.get(url, timeout=15)
        res = response.json()

        if res.get("status") in ["ORDER_RECEIVED", "ORDER_COMPLETED", "200"]:
            return {"status": "SUCCESS", "reference": ref, "data": res}
        return {"status": "FAILED", "reason": res.get("msg", res.get("status", "Airtime purchase failed")),
                "reference": ref}
    except Exception as e:
        return {"status": "FAILED", "reason": f"Provider connection error: {str(e)}", "reference": ref}


def fetch_data_variations(network):
    """Fetches dynamic data plan variations for the specified network from ClubKonnect."""
    net_code = NETWORK_CODES.get(network.upper())

    if net_code:
        try:
            url = f"{CK_BASE_URL}/DataBundleBundles.asp?MobileNetwork={net_code}"
            response = requests.get(url, timeout=10)
            res = response.json()
            if "VAR" in res:
                return [
                    {
                        "name": plan.get("name", plan.get("plan")),
                        "variation_code": plan.get("variation_code", plan.get("datacode")),
                        "variation_amount": str(plan.get("amount", plan.get("price")))
                    }
                    for plan in res["VAR"]
                ]
        except Exception:
            pass

    # Fallback static plan codes for reference
    return [
        {"name": "500MB SME / Direct", "variation_code": "500MB", "variation_amount": "150"},
        {"name": "1GB SME / Direct", "variation_code": "1GB", "variation_amount": "300"},
        {"name": "2GB SME / Direct", "variation_code": "2GB", "variation_amount": "600"},
        {"name": "3GB SME / Direct", "variation_code": "3GB", "variation_amount": "900"},
        {"name": "5GB SME / Direct", "variation_code": "5GB", "variation_amount": "1500"}
    ]


def process_data_purchase(phone, network, plan_code, amount=None):
    """Executes Data bundle purchase via ClubKonnect."""
    ref = _generate_reference("DAT")
    net_code = NETWORK_CODES.get(network.upper())

    if MOCK_MODE:
        return {"status": "SUCCESS", "reference": ref, "data": {"status": "MOCK_SUCCESS"}}

    if not net_code:
        return {"status": "FAILED", "reason": f"Invalid network specified: {network}", "reference": ref}

    url = (
        f"{CK_BASE_URL}/DataBundle.asp?"
        f"UserID={CK_USERID}&APIKey={CK_APIKEY}"
        f"&MobileNetwork={net_code}&DataPlan={plan_code}"
        f"&MobileNo={phone}&RequestID={ref}"
    )

    try:
        response = requests.get(url, timeout=15)
        res = response.json()

        if res.get("status") in ["ORDER_RECEIVED", "ORDER_COMPLETED", "200"]:
            return {"status": "SUCCESS", "reference": ref, "data": res}
        return {"status": "FAILED", "reason": res.get("msg", res.get("status", "Data subscription failed")),
                "reference": ref}
    except Exception as e:
        return {"status": "FAILED", "reason": f"Provider connection error: {str(e)}", "reference": ref}


def process_cable_tv(phone, cable_code, package_code, smartcard_number):
    """Executes Cable TV subscription renewal via ClubKonnect."""
    ref = _generate_reference("CAB")

    if MOCK_MODE:
        return {"status": "SUCCESS", "reference": ref, "data": {"status": "MOCK_SUCCESS"}}

    url = (
        f"{CK_BASE_URL}/CableTV.asp?"
        f"UserID={CK_USERID}&APIKey={CK_APIKEY}"
        f"&CableTV={cable_code.upper()}&Package={package_code}"
        f"&SmartCardNo={smartcard_number}&PhoneNo={phone}&RequestID={ref}"
    )

    try:
        response = requests.get(url, timeout=15)
        res = response.json()

        if res.get("status") in ["ORDER_RECEIVED", "ORDER_COMPLETED", "200"]:
            return {"status": "SUCCESS", "reference": ref, "data": res}
        return {"status": "FAILED", "reason": res.get("msg", res.get("status", "Cable activation failed")),
                "reference": ref}
    except Exception as e:
        return {"status": "FAILED", "reason": f"Provider connection error: {str(e)}", "reference": ref}


def process_electricity_payment(phone, disco_code, meter_type, meter_number, amount):
    """Executes Electricity bill payment via ClubKonnect."""
    ref = _generate_reference("ELE")

    if MOCK_MODE:
        return {"status": "SUCCESS", "reference": ref, "token": "MOCK-1234-5678-9012",
                "data": {"status": "MOCK_SUCCESS"}}

    url = (
        f"{CK_BASE_URL}/Electricity.asp?"
        f"UserID={CK_USERID}&APIKey={CK_APIKEY}"
        f"&ElectricCompany={disco_code.upper()}&MeterType={meter_type}"
        f"&MeterNo={meter_number}&Amount={int(amount)}"
        f"&PhoneNo={phone}&RequestID={ref}"
    )

    try:
        response = requests.get(url, timeout=15)
        res = response.json()

        if res.get("status") in ["ORDER_RECEIVED", "ORDER_COMPLETED", "200"]:
            token = res.get("token", res.get("metertoken", "N/A"))
            return {"status": "SUCCESS", "reference": ref, "token": token, "data": res}
        return {"status": "FAILED", "reason": res.get("msg", res.get("status", "Electricity payment failed")),
                "reference": ref}
    except Exception as e:
        return {"status": "FAILED", "reason": f"Provider connection error: {str(e)}", "reference": ref}


def process_betting_topup(phone, platform_code, user_id, amount):
    """Executes Betting wallet funding via ClubKonnect."""
    ref = _generate_reference("BET")

    if MOCK_MODE:
        return {"status": "SUCCESS", "reference": ref, "data": {"status": "MOCK_SUCCESS"}}

    url = (
        f"{CK_BASE_URL}/Betting.asp?"
        f"UserID={CK_USERID}&APIKey={CK_APIKEY}"
        f"&BettingCompany={platform_code}&CustomerId={user_id}"
        f"&Amount={int(amount)}&PhoneNo={phone}&RequestID={ref}"
    )

    try:
        response = requests.get(url, timeout=15)
        res = response.json()

        if res.get("status") in ["ORDER_RECEIVED", "ORDER_COMPLETED", "200"]:
            return {"status": "SUCCESS", "reference": ref, "data": res}
        return {"status": "FAILED", "reason": res.get("msg", res.get("status", "Betting topup failed")),
                "reference": ref}
    except Exception as e:
        return {"status": "FAILED", "reason": f"Provider connection error: {str(e)}", "reference": ref}


def process_education_pin(phone, exam_type, quantity=1):
    """Purchases education PINs (WAEC / JAMB) via ClubKonnect."""
    ref = _generate_reference("EDU")

    if MOCK_MODE:
        return {"status": "SUCCESS", "reference": ref, "pin": "PIN-1234-5678-9012", "data": {"status": "MOCK_SUCCESS"}}

    url = (
        f"{CK_BASE_URL}/Epin.asp?"
        f"UserID={CK_USERID}&APIKey={CK_APIKEY}"
        f"&ExamType={exam_type.upper()}&Quantity={int(quantity)}"
        f"&PhoneNo={phone}&RequestID={ref}"
    )

    try:
        response = requests.get(url, timeout=15)
        res = response.json()

        if res.get("status") in ["ORDER_RECEIVED", "ORDER_COMPLETED", "200"]:
            pin = res.get("pin", res.get("serial_number", "Check Account History"))
            return {"status": "SUCCESS", "reference": ref, "pin": pin, "data": res}
        return {"status": "FAILED", "reason": res.get("msg", res.get("status", "PIN purchase failed")),
                "reference": ref}
    except Exception as e:
        return {"status": "FAILED", "reason": f"Provider connection error: {str(e)}", "reference": ref}