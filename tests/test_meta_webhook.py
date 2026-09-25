import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("META_VERIFY_TOKEN", "meta-token")
os.environ.setdefault("ALLOW_DB_MUTATIONS", "true")
os.environ.setdefault("IS_TESTING", "true")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

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


def test_malicious_payloads_are_rejected(client):
    response = client.get(
        "/admin/users?q=<script>alert(1)</script>",
        headers={"Authorization": "Basic YWRtaW46c2VjcmV0"},
    )

    assert response.status_code == 400
    assert response.get_json()["status"] == "rejected"


def test_blank_phone_does_not_create_default_user(client):
    import app as app_module

    with app_module.app.app_context():
        app_module.db.session.query(app_module.Transaction).delete()
        app_module.db.session.query(app_module.User).delete()
        app_module.db.session.commit()

        assert app_module.get_or_create_user("") is None
        assert app_module.get_or_create_user(None) is None
        assert app_module.User.query.count() == 0


def test_admin_login_and_wallet_adjustment_are_audited(client):
    import app as app_module

    app_module.ADMIN_USERNAME = "admin"
    app_module.ADMIN_PASSWORD = "secret"

    with app_module.app.app_context():
        app_module.db.session.query(app_module.AdminAuditLog).delete()
        app_module.db.session.query(app_module.Transaction).delete()
        app_module.db.session.query(app_module.User).delete()
        
        app_module.ADMIN_EMAIL = "admin@example.com"

        user = app_module.User(
            whatsapp_id="2348000000001",
            phone="2348000000001",
            name="Audit User",
            wallet_balance=Decimal("10.00"),
            email="admin@example.com"
        )
        app_module.db.session.add(user)
        app_module.db.session.commit()
        user_id = user.id

    with client.session_transaction() as session:
        session["user_id"] = user_id

    login_response = client.get(
        "/admin/dashboard",
    )
    assert login_response.status_code == 200

    bad_adjustment = client.post(
        f"/admin/user/{user_id}/fund",
        data={"amount": "999", "action_type": "DEBIT", "csrf_token": "invalid"},
    )
    assert bad_adjustment.status_code == 403

    with app_module.app.app_context():
        entries = app_module.AdminAuditLog.query.order_by(app_module.AdminAuditLog.created_at.desc()).all()
        assert any(entry.action == "login" and entry.success for entry in entries)
        assert any(entry.action == "wallet_adjustment" and not entry.success for entry in entries)


def test_admin_email_login_does_not_require_password(client):
    import app as app_module

    test_key = uuid.uuid4().hex
    admin_email = f"admin-{test_key}@example.com"
    app_module.app.config["ADMIN_EMAIL"] = admin_email
    with app_module.app.app_context():
        user = app_module.User(
            whatsapp_id=f"web_admin_{test_key}",
            phone=f"234{test_key[:10]}",
            name="Admin",
            email=admin_email,
        )
        app_module.db.session.add(user)
        app_module.db.session.commit()

    response = client.post("/login", data={"email": admin_email})

    assert response.status_code == 302
    with client.session_transaction() as session:
        assert session["is_admin"] is True


def test_saved_service_belongs_to_logged_in_user(client):
    import app as app_module

    test_key = uuid.uuid4().hex
    with app_module.app.app_context():
        user = app_module.User(
            whatsapp_id=f"web_saved_{test_key}",
            phone=f"235{test_key[:10]}",
            name="Saved Service User",
        )
        app_module.db.session.add(user)
        app_module.db.session.commit()
        user_id = user.id

    with client.session_transaction() as session:
        session["user_id"] = user_id

    response = client.post(
        "/saved-services",
        data={
            "service_type": "electricity",
            "provider": "IKEDC",
            "identifier": "MTR-12345",
            "label": "Home meter",
        },
    )

    assert response.status_code == 302
    with app_module.app.app_context():
        saved = app_module.SavedService.query.filter_by(user_id=user_id).one()
        saved_id = saved.id
        assert saved.identifier == "MTR-12345"

    response = client.post(f"/saved-services/{saved_id}/delete")
    assert response.status_code == 302
    with app_module.app.app_context():
        assert app_module.SavedService.query.get(saved_id) is None


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


def test_webhook_ignores_new_user_when_db_mutations_disabled(client):
    import app as app_module

    app_module.ALLOW_DB_MUTATIONS = False
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "2348099999999",
                                    "id": "wamid.456",
                                    "timestamp": "1710000001",
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
    assert response.get_json()["status"] == "ignored"


def test_webhook_ignores_non_message_events(client):
    payload = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {
                            "statuses": [
                                {"id": "msg_1", "status": "read", "recipient_id": "2348012345678"}
                            ]
                        }
                    }
                ]
            }
        ]
    }

    response = client.post("/webhook", json=payload)
    assert response.status_code == 200
    assert response.get_json()["status"] == "ignored"


def test_data_purchase_applies_margin_from_selected_plan(monkeypatch, client):
    import app as app_module
    import chat_agent
    import provider

    with app_module.app.app_context():
        user = app_module.User(
            whatsapp_id=f"234{uuid.uuid4().hex[:10]}",
            phone=f"234{uuid.uuid4().hex[:10]}",
            wallet_balance=Decimal("1000.00"),
        )
        app_module.db.session.add(user)
        app_module.db.session.commit()

        monkeypatch.setattr(
            provider,
            "fetch_data_variations",
            lambda network: [{
                "name": "5GB plan",
                "variation_code": "5GB",
                "variation_amount": "478.92",
            }],
        )
        captured = {}

        def fake_purchase(phone, network, plan_code, amount):
            captured["amount"] = amount
            return {"status": "SUCCESS", "reference": "DATA-TEST"}

        monkeypatch.setattr(provider, "process_data_purchase", fake_purchase)
        monkeypatch.setattr(
            app_module,
            "get_markup",
            lambda service_type, base_amount: base_amount * Decimal("0.10"),
        )

        result = chat_agent.execute_tool(
            app_module.app,
            app_module.db,
            user,
            user.phone,
            "buy_data",
            {
                "network": "MTN",
                "plan_code": "5GB",
                "amount": "526.81",
                "phone": user.phone,
            },
        )

        assert result["status"] == "success"
        assert captured["amount"] == 478.92
        assert user.wallet_balance == Decimal("473.19")
        transaction = app_module.Transaction.query.filter_by(reference="DATA-TEST").one()
        assert transaction.amount == Decimal("526.81")


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

        app_module.ADMIN_EMAIL = "admin@example.com"
        user = app_module.User(
            whatsapp_id="user-100",
            phone="2348000000000",
            name="Demo User",
            wallet_balance=Decimal("200.00"),
            current_state="IDLE",
            created_at=created_at,
            updated_at=created_at,
            email="admin@example.com"
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
        user_id = user.id

    with client.session_transaction() as session:
        session["user_id"] = user_id

    dashboard_response = client.get(
        "/admin/dashboard",
    )
    users_response = client.get(
        "/admin/users",
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

        app_module.ADMIN_EMAIL = "admin@example.com"
        user = app_module.User(
            whatsapp_id="2348000000000",
            phone="2348000000000",
            name="Demo Wallet User",
            wallet_balance=Decimal("0.00"),
            email="admin@example.com"
        )
        app_module.db.session.add(user)
        app_module.db.session.commit()
        user_id = user.id
        
    with client.session_transaction() as session:
        session["user_id"] = user_id

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


def test_paystack_success_callback_redirects_website_payment_to_dashboard(client):
    import app as app_module

    with app_module.app.app_context():
        app_module.db.session.query(app_module.Transaction).delete()
        app_module.db.session.query(app_module.User).delete()

        user = app_module.User(
            whatsapp_id="2348000000000",
            phone="2348000000000",
            name="Website Wallet User",
            wallet_balance=Decimal("0.00"),
        )
        app_module.db.session.add(user)
        app_module.db.session.flush()
        app_module.db.session.add(app_module.Transaction(
            user_id=user.id,
            reference="DEP_WEBSITE_1001",
            amount=Decimal("200.00"),
            type="DEPOSIT",
            recipient=user.phone,
            status="SUCCESS",
            description="Paystack wallet funding",
            meta_data={
                "credited": True,
                "paystack": {"metadata": {"payment_source": "website"}},
            },
        ))
        app_module.db.session.commit()

    response = client.get("/payments/paystack/callback?reference=DEP_WEBSITE_1001")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/dashboard")


def test_dynamic_paystack_fee_tiers_are_admin_editable(client):
    import app as app_module
    from wallet_service import calculate_paystack_gross

    app_module.ADMIN_USERNAME = "admin"
    app_module.ADMIN_PASSWORD = "secret"

    with app_module.app.app_context():
        app_module.ADMIN_EMAIL = "admin@example.com"
        user = app_module.User(
            whatsapp_id="admin-123",
            phone="2348000000002",
            name="Admin User",
            email="admin@example.com"
        )
        app_module.db.session.add(user)
        app_module.db.session.query(app_module.PaymentFeeTier).delete()
        app_module.db.session.add_all([
            app_module.PaymentFeeTier(label="BELOW_1000", min_amount=Decimal("0.00"), max_amount=Decimal("999.99"), fee_percentage=Decimal("2.50")),
            app_module.PaymentFeeTier(label="1000_TO_20000", min_amount=Decimal("1000.00"), max_amount=Decimal("19999.99"), fee_percentage=Decimal("1.50")),
            app_module.PaymentFeeTier(label="ABOVE_20000", min_amount=Decimal("20000.00"), max_amount=None, fee_percentage=Decimal("1.00")),
        ])
        app_module.db.session.commit()
        user_id = user.id

    assert calculate_paystack_gross(Decimal("500.00")) == Decimal("512.82")
    assert calculate_paystack_gross(Decimal("5000.00")) == Decimal("5076.14")
    assert calculate_paystack_gross(Decimal("25000.00")) == Decimal("25252.53")

    with client.session_transaction() as session:
        session["user_id"] = user_id

    response = client.get(
        "/admin/settings",
    )

    assert response.status_code == 200
    assert b"Paystack Fee Tiers" in response.data
    assert b"Below 1000" in response.data
    assert b"More than 20,000" in response.data


def test_education_packages_exclude_neco(monkeypatch):
    import provider

    monkeypatch.setattr(provider, "MOCK_MODE", False)
    monkeypatch.setattr(provider, "CLUBKONNECT_USERID", "test-user")
    monkeypatch.setattr(provider, "CLUBKONNECT_APIKEY", "test-key")
    monkeypatch.setattr(
        provider,
        "_provider_request",
        lambda endpoint, params: {
            "EXAM_TYPE": [
                {"PRODUCT_CODE": "waecdirect", "PRODUCT_DESCRIPTION": "WAEC Result Checker PIN", "PRODUCT_AMOUNT": "5350"},
                {"PRODUCT_CODE": "neco-pin", "PRODUCT_DESCRIPTION": "NECO Result Checker PIN", "PRODUCT_AMOUNT": "4500"},
                {"PRODUCT_CODE": "jamb-utme", "PRODUCT_DESCRIPTION": "JAMB UTME PIN", "PRODUCT_AMOUNT": "5700"},
            ]
        },
    )

    packages = provider.fetch_education_packages()

    assert [package["code"] for package in packages] == ["waecdirect", "jamb-utme"]
