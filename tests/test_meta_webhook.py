import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("META_VERIFY_TOKEN", "meta-token")
os.environ.setdefault("ALLOW_DB_MUTATIONS", "true")

from app import app


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as client:
        yield client


def test_meta_verification_route(client):
    response = client.get(
        "/webhook?hub.mode=subscribe&hub.challenge=abc123&hub.verify_token=meta-token"
    )

    assert response.status_code == 200
    assert response.get_data(as_text=True) == "abc123"


def test_meta_message_payload_is_accepted(client):
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "2348012345678",
                                    "id": "wamid.123",
                                    "timestamp": "1710000000",
                                    "type": "text",
                                    "text": {"body": "MENU"},
                                }
                            ]
                        }
                    }
                ]
            }
        ]
    }

    response = client.post("/webhook", json=payload)
    assert response.status_code == 200


def test_send_whatsapp_message_uses_meta_api(monkeypatch):
    import app

    captured = {}

    class FakeResponse:
        ok = True
        status_code = 200
        text = '{"success": true}'

    def fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setenv("META_API_TOKEN", "meta-token")
    monkeypatch.setenv("META_PHONE_NUMBER_ID", "1234567890")
    monkeypatch.setattr(app.requests, "post", fake_post)

    result = app.send_whatsapp_message("2348012345678", "Hello from Meta")

    assert result is True
    assert captured["url"].endswith("/1234567890/messages")
    assert captured["headers"]["Authorization"] == "Bearer meta-token"
    assert captured["json"]["to"] == "2348012345678"
    assert captured["json"]["type"] == "text"
    assert captured["json"]["text"]["body"] == "Hello from Meta"


def test_admin_dashboard_shows_timestamps(client):
    import app as app_module

    app_module.ADMIN_USERNAME = "admin"
    app_module.ADMIN_PASSWORD = "secret"

    created_at = datetime(2025, 1, 15, 12, 30, 0, tzinfo=timezone.utc)
    with app_module.app.app_context():
        app_module.db.session.query(app_module.Transaction).delete()
        app_module.db.session.query(app_module.User).delete()

        user = app_module.User(
            whatsapp_id="user-100",
            phone="2348000000000",
            name="Demo User",
            wallet_balance=Decimal("200.00"),
            current_state="IDLE",
            created_at=created_at,
            updated_at=created_at,
        )
        app_module.db.session.add(user)
        app_module.db.session.flush()

        tx = app_module.Transaction(
            user_id=user.id,
            reference="TX-1001",
            amount=Decimal("50.00"),
            type="AIRTIME",
            recipient="2348000000000",
            status="SUCCESS",
            description="Recharge",
            created_at=created_at,
        )
        app_module.db.session.add(tx)
        app_module.db.session.commit()

    dashboard_response = client.get(
        "/admin/dashboard",
        headers={"Authorization": "Basic YWRtaW46c2VjcmV0"},
    )
    users_response = client.get(
        "/admin/users",
        headers={"Authorization": "Basic YWRtaW46c2VjcmV0"},
    )

    assert dashboard_response.status_code == 200
    assert b"Timestamp" in dashboard_response.data
    assert b"2025-01-15" in dashboard_response.data

    assert users_response.status_code == 200
    assert b"Joined" in users_response.data


def test_paystack_webhook_credits_balance_and_shows_in_admin_dashboard(client):
    import app as app_module
    import hashlib
    import hmac
    import json

    app_module.ADMIN_USERNAME = "admin"
    app_module.ADMIN_PASSWORD = "secret"
    app_module.PAYSTACK_SECRET_KEY = "test-secret"

    with app_module.app.app_context():
        app_module.db.session.query(app_module.Transaction).delete()
        app_module.db.session.query(app_module.User).delete()

        user = app_module.User(
            whatsapp_id="2348000000000",
            phone="2348000000000",
            name="Demo Wallet User",
            wallet_balance=Decimal("0.00"),
        )
        app_module.db.session.add(user)
        app_module.db.session.commit()

    payload = {
        "event": "charge.success",
        "data": {
            "reference": "DEP_TEST_1001",
            "amount": 20000,
            "metadata": {
                "phone_number": "2348000000000",
                "net_credit_amount": "200.00",
                "fee_amount": "5.00",
            },
        },
    }
    body = json.dumps(payload).encode("utf-8")
    signature = hmac.new(b"test-secret", body, hashlib.sha512).hexdigest()

    response = client.post(
        "/payments/paystack/webhook",
        data=body,
        headers={"Content-Type": "application/json", "x-paystack-signature": signature},
    )

    assert response.status_code == 200
    assert response.get_json()["status"] == "ok"

    with app_module.app.app_context():
        refreshed = app_module.User.query.filter_by(whatsapp_id="2348000000000").first()
        assert refreshed is not None
        assert refreshed.wallet_balance == Decimal("200.00")
        tx = app_module.Transaction.query.filter_by(reference="DEP_TEST_1001").first()
        assert tx is not None
        assert tx.status == "SUCCESS"

    dashboard_response = client.get(
        "/admin/dashboard",
        headers={"Authorization": "Basic YWRtaW46c2VjcmV0"},
    )
    assert dashboard_response.status_code == 200
    assert b"DEP_TEST_1001" in dashboard_response.data


def test_paystack_success_callback_redirects_to_whatsapp(client):
    import app as app_module

    with app_module.app.app_context():
        app_module.db.session.query(app_module.Transaction).delete()
        app_module.db.session.query(app_module.User).delete()

        user = app_module.User(
            whatsapp_id="2348000000000",
            phone="2348000000000",
            name="Demo Wallet User",
            wallet_balance=Decimal("0.00"),
        )
        app_module.db.session.add(user)
        app_module.db.session.flush()

        tx = app_module.Transaction(
            user_id=user.id,
            reference="DEP_REDIRECT_1001",
            amount=Decimal("200.00"),
            type="DEPOSIT",
            recipient="2348000000000",
            status="SUCCESS",
            description="Paystack wallet funding",
            meta_data={"credited": True},
        )
        app_module.db.session.add(tx)
        app_module.db.session.commit()

    response = client.get("/payments/paystack/callback?reference=DEP_REDIRECT_1001")

    assert response.status_code == 302
    assert response.headers["Location"].startswith("https://wa.me/")


def test_dynamic_paystack_fee_tiers_are_admin_editable(client):
    import app as app_module
    from wallet_service import calculate_paystack_gross

    app_module.ADMIN_USERNAME = "admin"
    app_module.ADMIN_PASSWORD = "secret"

    with app_module.app.app_context():
        app_module.db.session.query(app_module.PaymentFeeTier).delete()
        app_module.db.session.add_all([
            app_module.PaymentFeeTier(label="BELOW_1000", min_amount=Decimal("0.00"), max_amount=Decimal("999.99"), fee_percentage=Decimal("2.50")),
            app_module.PaymentFeeTier(label="1000_TO_20000", min_amount=Decimal("1000.00"), max_amount=Decimal("19999.99"), fee_percentage=Decimal("1.50")),
            app_module.PaymentFeeTier(label="ABOVE_20000", min_amount=Decimal("20000.00"), max_amount=None, fee_percentage=Decimal("1.00")),
        ])
        app_module.db.session.commit()

    assert calculate_paystack_gross(Decimal("500.00")) == Decimal("512.82")
    assert calculate_paystack_gross(Decimal("5000.00")) == Decimal("5076.14")
    assert calculate_paystack_gross(Decimal("25000.00")) == Decimal("25252.53")

    response = client.get(
        "/admin/settings",
        headers={"Authorization": "Basic YWRtaW46c2VjcmV0"},
    )

    assert response.status_code == 200
    assert b"Paystack Fee Tiers" in response.data
    assert b"Below 1000" in response.data
    assert b"More than 20,000" in response.data
