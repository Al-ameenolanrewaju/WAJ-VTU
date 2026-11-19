import pytest


@pytest.mark.parametrize(
    ("meter_type", "expected_meter_type"),
    [("PREPAID", "prepaid"), ("POSTPAID", "postpaid")],
)
def test_verify_meter_posts_resolved_swiftbills_disco_and_meter_details(
    monkeypatch, meter_type, expected_meter_type
):
    import provider

    monkeypatch.setattr(provider, "MOCK_MODE", False)
    monkeypatch.setattr(provider, "SWIFTBILLS_API_KEY", "test-key")
    requests = []

    def fake_swiftbills_request(endpoint, method="GET", payload=None):
        requests.append((endpoint, method, payload))
        if endpoint == "get-bill":
            return [{"id": "6", "abb": "IBEDC"}]
        return {
            "status": "success",
            "name": "Test Customer",
            "message": "verified",
        }

    monkeypatch.setattr(provider, "_swiftbills_request", fake_swiftbills_request)

    result = provider.verify_meter("IBEDC", " 12345678901 ", meter_type)

    assert result["valid"] is True
    assert result["customer_name"] == "Test Customer"
    assert requests == [
        ("get-bill", "GET", None),
        (
            "bill/bill-validation",
            "POST",
            {
                "disco": "6",
                "meter_number": "12345678901",
                "meter_type": expected_meter_type,
            },
        ),
    ]


def test_verify_meter_returns_invalid_meter_response(monkeypatch):
    import provider

    monkeypatch.setattr(provider, "MOCK_MODE", False)
    monkeypatch.setattr(provider, "SWIFTBILLS_API_KEY", "test-key")
    monkeypatch.setattr(
        provider,
        "_swiftbills_request",
        lambda endpoint, method="GET", payload=None: (
            [{"id": "6", "abb": "IBEDC"}]
            if endpoint == "get-bill"
            else {"status": "failed", "message": "Invalid meter number"}
        ),
    )

    result = provider.verify_meter("IBEDC", "12345678901", "PREPAID")

    assert result["valid"] is False
    assert result["message"] == "Invalid meter number"


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
