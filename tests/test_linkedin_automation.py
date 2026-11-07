"""
Tests for Automated LinkedIn Daily Deal Posting.
Covers:
- Toggle behavior (enabled/disabled)
- Missing/invalid token handling
- Successful post, MarketingCampaign audit logging, and daily idempotency
- Force parameter bypass
- Cron endpoint /api/cron/daily-deal-linkedin
- Admin Social Studio auto-post toggle endpoint
"""
import os
import json
import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("META_VERIFY_TOKEN", "meta-token")
os.environ.setdefault("ALLOW_DB_MUTATIONS", "true")
os.environ.setdefault("IS_TESTING", "true")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import app as app_module
from app import app, db
from models import User, MarketingContent, MarketingCampaign
from services.marketing_settings import set_marketing_setting, get_marketing_setting
from services.deal_service import get_lagos_today
from services.linkedin_auto_service import send_daily_deal_linkedin


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            today = get_lagos_today()
            backed_up = {
                f"LINKEDIN_POST_SENT_{today}": get_marketing_setting(f"LINKEDIN_POST_SENT_{today}", ""),
                "LINKEDIN_AUTO_POST_ENABLED": get_marketing_setting("LINKEDIN_AUTO_POST_ENABLED", "false"),
                "LINKEDIN_TOKEN": get_marketing_setting("LINKEDIN_TOKEN", ""),
            }
            set_marketing_setting(f"LINKEDIN_POST_SENT_{today}", "")
            set_marketing_setting("LINKEDIN_AUTO_POST_ENABLED", "false")
            set_marketing_setting("LINKEDIN_TOKEN", "")
            MarketingContent.query.filter_by(content_type="DAILY_DEAL", target_date=today).delete()
            db.session.commit()
        yield client
        with app.app_context():
            db.session.rollback()
            for k, v in backed_up.items():
                set_marketing_setting(k, v)
            db.session.commit()


@pytest.fixture
def admin_id():
    admin_email = "admin_li_test@example.com"
    app_module.ADMIN_EMAIL = admin_email
    app_module.app.config["ADMIN_EMAIL"] = admin_email
    with app.app_context():
        user = User.query.filter_by(email=admin_email).first()
        if not user:
            user = User(
                whatsapp_id="admin_li_test",
                phone="234800000998",
                name="Admin User",
                email=admin_email,
            )
            db.session.add(user)
            db.session.commit()
        return user.id


def test_linkedin_auto_post_disabled_by_default(client):
    """When LINKEDIN_AUTO_POST_ENABLED is false, auto-post returns disabled status."""
    with app.app_context():
        set_marketing_setting("LINKEDIN_AUTO_POST_ENABLED", "false")
        result = send_daily_deal_linkedin(force=False)
        assert result["status"] == "disabled"
        assert "disabled" in result["reason"].lower()


def test_linkedin_auto_post_missing_token(client):
    """When enabled but token missing, returns error with helpful reconnect guidance."""
    with app.app_context():
        set_marketing_setting("LINKEDIN_AUTO_POST_ENABLED", "true")
        set_marketing_setting("LINKEDIN_TOKEN", "")
        result = send_daily_deal_linkedin(force=False)
        assert result["status"] == "error"
        assert "token missing" in result["reason"].lower() or "reconnect" in result["reason"].lower()


def test_linkedin_auto_post_success_and_idempotency(client):
    """
    When token is valid and enabled:
    - Successfully publishes deal to LinkedIn
    - Sets LINKEDIN_POST_SENT_{today} = 'true'
    - Creates MarketingCampaign record
    - Second non-forced call is skipped (idempotent)
    - Forced call publishes again
    """
    today = get_lagos_today()
    mock_token = {
        "access_token": "valid-test-access-token",
        "author_urn": "urn:li:person:MockAuthor123",
        "expires_at": int(time.time()) + 3600,
    }

    with app.app_context():
        set_marketing_setting("LINKEDIN_AUTO_POST_ENABLED", "true")
        set_marketing_setting("LINKEDIN_TOKEN", json.dumps(mock_token))

        # Seed today's deal
        deal = MarketingContent(
            content_type="DAILY_DEAL",
            target_date=today,
            network="MTN",
            plan_name="2.0 GB",
            plan_code="mtn-2gb",
            data_size="2.0 GB",
            price=500.0,
            validity="30 Days",
            headline="MTN 2.0 GB Flash Deal",
            caption="Best price today!",
            full_caption="Massive deal on MTN data today!",
            status="GENERATED",
        )
        db.session.add(deal)
        db.session.commit()

        mock_post_result = {
            "status": "success",
            "post_id": "test-post-789",
            "post_urn": "urn:li:ugcPost:test-post-789",
            "url": "https://www.linkedin.com/feed/update/urn:li:ugcPost:test-post-789/",
        }

        with patch("services.linkedin_auto_service.post_to_linkedin", return_value=mock_post_result) as mock_post:
            # 1. First execution — should post
            res1 = send_daily_deal_linkedin(force=False)
            assert res1["status"] == "success"
            assert res1["post_urn"] == "urn:li:ugcPost:test-post-789"
            assert mock_post.call_count == 1
            # Verify flyer bytes were generated and attached
            _, post_kwargs = mock_post.call_args
            assert post_kwargs.get("image_bytes") is not None
            assert len(post_kwargs["image_bytes"]) > 0

            # Verify sentinel is stored
            assert get_marketing_setting(f"LINKEDIN_POST_SENT_{today}") == "true"

            # Verify MarketingCampaign logged
            camp = MarketingCampaign.query.filter_by(channel="LINKEDIN").order_by(MarketingCampaign.id.desc()).first()
            assert camp is not None
            assert "LinkedIn Daily Deal" in camp.title
            assert camp.status == "COMPLETED"

            # 2. Second execution — idempotent skip
            res2 = send_daily_deal_linkedin(force=False)
            assert res2["status"] == "skipped"
            assert "Already posted today" in res2["reason"]
            assert mock_post.call_count == 1  # Not called again

            # 3. Third execution with force=True — posts again
            res3 = send_daily_deal_linkedin(force=True)
            assert res3["status"] == "success"
            assert mock_post.call_count == 2


def test_cron_daily_deal_linkedin_endpoint(client):
    """The cron endpoint /api/cron/daily-deal-linkedin executes and returns JSON."""
    with app.app_context():
        set_marketing_setting("LINKEDIN_AUTO_POST_ENABLED", "false")

    resp = client.get("/api/cron/daily-deal-linkedin")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "status" in data


def test_admin_linkedin_toggle_auto_route(client, admin_id):
    """Admin can toggle LINKEDIN_AUTO_POST_ENABLED on and off via POST."""
    with client.session_transaction() as sess:
        sess["user_id"] = admin_id
        sess["_fresh"] = True

    with app.app_context():
        set_marketing_setting("LINKEDIN_AUTO_POST_ENABLED", "false")

    # Toggle to true
    resp1 = client.post("/admin/marketing/linkedin/toggle-auto", follow_redirects=False)
    assert resp1.status_code == 302
    with app.app_context():
        assert get_marketing_setting("LINKEDIN_AUTO_POST_ENABLED") == "true"

    # Toggle to false
    resp2 = client.post("/admin/marketing/linkedin/toggle-auto", follow_redirects=False)
    assert resp2.status_code == 302
    with app.app_context():
        assert get_marketing_setting("LINKEDIN_AUTO_POST_ENABLED") == "false"


def test_admin_marketing_settings_page_renders_linkedin_setting(client, admin_id):
    """The admin marketing settings page displays the LinkedIn auto-post option."""
    with client.session_transaction() as sess:
        sess["user_id"] = admin_id
        sess["_fresh"] = True

    resp = client.get("/admin/marketing/settings")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "LINKEDIN_AUTO_POST_ENABLED" in html
    assert "LinkedIn Automated Post" in html
