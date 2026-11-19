import pytest
import requests


@pytest.mark.parametrize(
    ("meter_type", "expected_meter_type"),
    [("PREPAID", "prepaid"), ("POSTPAID", "postpaid")],
)
def test_verify_meter_uses_documented_swiftbills_get_contract(
    monkeypatch, meter_type, expected_meter_type
):
    import provider

    monkeypatch.setattr(provider, "MOCK_MODE", False)
    monkeypatch.setattr(provider, "SWIFTBILLS_API_KEY", "test-key")
    calls = []

    def fake_swiftbills_request(endpoint, method="GET", payload=None):
        calls.append((endpoint, method, payload))
        if endpoint == "get-bill":
            return [{"id": "6", "name": "Ibadan Electric", "abb": "IBEDC"}]
        return {
            "status": "success",
            "name": "Test Customer",
            "customer_address": "Test address",
            "message": "verified",
        }

    monkeypatch.setattr(provider, "_swiftbills_request", fake_swiftbills_request)

    result = provider.verify_meter("IBEDC", " 12345678901 ", meter_type)

    assert result["valid"] is True
    assert result["customer_name"] == "Test Customer"
    assert calls == [
        ("get-bill", "GET", None),
        (
            f"bill/bill-validation?meter_number=12345678901&meter_type={expected_meter_type}&disco=6",
            "GET",
            None,
        ),
    ]


@pytest.mark.parametrize(
    ("waj_code", "swiftbills_abb", "expected_id"),
    [
        ("IKEDC", "IE", "1"),
        ("IE", "IE", "1"),
        ("EKEDC", "EKEDC", "2"),
        ("KEDCO", "KEDCO", "3"),
        ("PHEDC", "PHEDC", "4"),
        ("JED", "JED", "5"),
        ("IBEDC", "IBEDC", "6"),
        ("KEDC", "KEDC", "7"),
        ("AEDC", "AEDC", "8"),
        ("EEDC", "ENUGU", "9"),
        ("ENUGU", "ENUGU", "9"),
        ("BENIN", "BENIN", "10"),
        ("YOLA", "YOLA", "11"),
        ("ABEDC", "ABEDC", "12"),
    ],
)
def test_swiftbills_disco_id_resolves_documented_catalog_codes(
    monkeypatch, waj_code, swiftbills_abb, expected_id
):
    import provider

    monkeypatch.setattr(
        provider,
        "_swiftbills_request",
        lambda endpoint, method="GET", payload=None: [
            {"id": expected_id, "name": "Distributor", "abb": swiftbills_abb}
        ],
    )

    assert provider._swiftbills_disco_id(waj_code) == expected_id


def test_verify_meter_returns_invalid_meter_response(monkeypatch):
    import provider

    monkeypatch.setattr(provider, "MOCK_MODE", False)
    monkeypatch.setattr(provider, "SWIFTBILLS_API_KEY", "test-key")
    calls = []

    def fake_request(endpoint, method="GET", payload=None):
        calls.append((endpoint, method, payload))
        if endpoint == "get-bill":
            return [{"id": "6", "abb": "IBEDC"}]
        return {"status": "failed", "message": "Invalid meter number"}

    monkeypatch.setattr(provider, "_swiftbills_request", fake_request)

    result = provider.verify_meter("IBEDC", "12345678901", "PREPAID")

    assert result["valid"] is False
    assert result["message"] == "Invalid meter number"
    assert len(calls) == 2
    assert calls[1][1] == "GET"


def test_verify_meter_rejects_placeholder_customer_names(monkeypatch):
    import provider

    monkeypatch.setattr(provider, "MOCK_MODE", False)
    monkeypatch.setattr(provider, "SWIFTBILLS_API_KEY", "test-key")
    monkeypatch.setattr(
        provider,
        "_swiftbills_request",
        lambda endpoint, method="GET", payload=None: (
            [{"id": "6", "abb": "IBEDC"}]
            if endpoint == "get-bill"
            else {"status": "success", "name": "Unable to verify", "message": "Unable to verify"}
        ),
    )

    result = provider.verify_meter("IBEDC", "12345678901", "PREPAID")

    assert result["valid"] is False
    assert result["customer_name"] == ""
    assert result["message"] == "Unable to verify"


def test_verify_meter_requires_swiftbills_credentials(monkeypatch):
    import provider

    monkeypatch.setattr(provider, "MOCK_MODE", False)
    monkeypatch.setattr(provider, "SWIFTBILLS_API_KEY", "")
    monkeypatch.setattr(
        provider,
        "_swiftbills_request",
        lambda *args, **kwargs: pytest.fail("API should not be called without credentials"),
    )

    result = provider.verify_meter("IBEDC", "12345678901", "PREPAID")

    assert result == {
        "valid": False,
        "message": "SwiftBills credentials are missing",
    }


def test_verify_meter_reports_missing_distributor_configuration(monkeypatch):
    import provider

    monkeypatch.setattr(provider, "MOCK_MODE", False)
    monkeypatch.setattr(provider, "SWIFTBILLS_API_KEY", "test-key")
    monkeypatch.setattr(provider, "_swiftbills_request", lambda *args, **kwargs: [])

    result = provider.verify_meter("IBEDC", "12345678901", "PREPAID")

    assert result == {
        "valid": False,
        "message": "SwiftBills electricity configuration is missing",
    }


def test_verify_meter_reports_provider_api_exception(monkeypatch):
    import provider

    monkeypatch.setattr(provider, "MOCK_MODE", False)
    monkeypatch.setattr(provider, "SWIFTBILLS_API_KEY", "test-key")

    def raise_api_error(endpoint, method="GET", payload=None):
        if endpoint == "get-bill":
            return [{"id": "6", "abb": "IBEDC"}]
        raise RuntimeError("SwiftBills unavailable")

    monkeypatch.setattr(provider, "_swiftbills_request", raise_api_error)

    result = provider.verify_meter("IBEDC", "12345678901", "PREPAID")

    assert result == {"valid": False, "message": "SwiftBills unavailable"}


def test_electricity_payment_sends_documented_payload_and_trims_meter(monkeypatch):
    import provider

    monkeypatch.setattr(provider, "MOCK_MODE", False)
    monkeypatch.setattr(provider, "SWIFTBILLS_API_KEY", "test-key")
    requests_made = []

    def fake_swiftbills_request(endpoint, method="GET", payload=None):
        requests_made.append((endpoint, method, payload))
        if endpoint == "get-bill":
            return [{"id": "6", "abb": "IBEDC"}]
        return {
            "status": "success",
            "request-id": "API_Bill_66bc6a8a0bfa5",
            "token": "1234-5678-9012",
        }

    monkeypatch.setattr(provider, "_swiftbills_request", fake_swiftbills_request)

    result = provider.process_electricity_payment(
        "IBEDC", " 12345678901 ", " PREPAID ", 500, " 2348000000001 "
    )

    assert result["status"] == "SUCCESS"
    assert result["token"] == "1234-5678-9012"
    assert result["provider_reference"] == "API_Bill_66bc6a8a0bfa5"
    assert requests_made == [
        ("get-bill", "GET", None),
        (
            "bill",
            "POST",
            {
                "disco": 6,
                "meter_type": "prepaid",
                "meter_number": "12345678901",
                "amount": "500",
                "phone": "2348000000001",
            },
        ),
    ]


def test_electricity_payment_normalizes_null_token(monkeypatch):
    import provider

    monkeypatch.setattr(provider, "MOCK_MODE", False)
    monkeypatch.setattr(provider, "SWIFTBILLS_API_KEY", "test-key")
    monkeypatch.setattr(
        provider,
        "_swiftbills_request",
        lambda endpoint, method="GET", payload=None: (
            [{"id": "6", "abb": "IBEDC"}]
            if endpoint == "get-bill"
            else {"status": "success", "request-id": "API_Bill_123", "token": "null"}
        ),
    )

    result = provider.process_electricity_payment(
        "IBEDC", "12345678901", "PREPAID", 500
    )

    assert result["status"] == "SUCCESS"
    assert result["provider_reference"] == "API_Bill_123"
    assert result["token"] is None


def test_electricity_payment_returns_swiftbills_error_body(monkeypatch):
    import provider

    class ErrorResponse:
        text = '{"message":"Invalid meter number"}'

        def json(self):
            return {"message": "Invalid meter number"}

    monkeypatch.setattr(provider, "MOCK_MODE", False)
    monkeypatch.setattr(provider, "SWIFTBILLS_API_KEY", "test-key")

    def raise_api_error(endpoint, method="GET", payload=None):
        if endpoint == "get-bill":
            return [{"id": "6", "abb": "IBEDC"}]
        raise requests.HTTPError(
            "400 Client Error: Bad Request",
            response=ErrorResponse(),
        )

    monkeypatch.setattr(provider, "_swiftbills_request", raise_api_error)

    result = provider.process_electricity_payment(
        "IBEDC", "12345678901", "PREPAID", 500
    )

    assert result["status"] == "FAILED"
    assert result["reason"] == "Invalid meter number"
