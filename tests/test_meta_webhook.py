import os

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("META_VERIFY_TOKEN", "meta-token")

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
