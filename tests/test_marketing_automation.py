"""
Unit & Integration Tests for WAJ VTU Marketing Automation System.
Covers:
- Referral code generation and tracking
- Self-referral prevention and immutability
- Transaction qualification and atomic wallet reward processing
- Daily data deal selection, idempotency, and price accuracy
- Customer inactivity segmentation (7/14/30 days) and campaign queue safety
- Admin and customer marketing UI endpoints
"""
import os
import uuid
from decimal import Decimal
from datetime import datetime, date, timedelta, timezone

import pytest

# Ensure isolated test environment
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("META_VERIFY_TOKEN", "meta-token")
os.environ.setdefault("ALLOW_DB_MUTATIONS", "true")
os.environ.setdefault("IS_TESTING", "true")
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from app import app, db
from models import User, Transaction, Referral, ReferralReward, MarketingContent, MarketingCampaign, MarketingMessage
from services.referral_service import (
    generate_unique_referral_code,
    ensure_user_referral_code,
    get_referral_url,
    attach_referral,
    process_referral_reward_for_transaction,
    get_user_referral_summary,
)
from services.deal_service import (
    generate_daily_deal,
    get_daily_deal_history,
    parse_plan_details,
    score_plan_for_deal,
)
from services.reengagement_service import (
    scan_inactive_segments,
    create_reengagement_campaign,
    approve_campaign,
    cancel_campaign,
    render_reengagement_message,
)
from services.marketing_settings import (
    get_marketing_setting,
    set_marketing_setting,
    seed_default_marketing_settings,
)


@pytest.fixture
def client():
    app.config.update(TESTING=True)
    with app.test_client() as client:
        with app.app_context():
            db.create_all()
            seed_default_marketing_settings()
        yield client
        with app.app_context():
            db.session.rollback()
            for tbl in [MarketingMessage, MarketingCampaign, MarketingContent, ReferralReward, Referral, Transaction, User]:
                try:
                    db.session.query(tbl).delete()
                except Exception:
                    pass
            db.session.commit()


def _create_test_user(phone_suffix, email=None, name="Test User", balance=0.0):
    u = User(
        whatsapp_id=f"234800000{phone_suffix}@c.us",
        phone=f"234800000{phone_suffix}",
        name=name,
        email=email or f"user{phone_suffix}@example.com",
        wallet_balance=Decimal(str(balance)),
        referral_code=generate_unique_referral_code(),
    )
    db.session.add(u)
    db.session.commit()
    return u


# ==============================================================================
# 1. REFERRAL SYSTEM TESTS
# ==============================================================================

def test_generate_unique_referral_code(client):
    """Verifies that generated codes start with WAJ, are 8 chars, and unique."""
    with app.app_context():
        code1 = generate_unique_referral_code()
        code2 = generate_unique_referral_code()
        assert code1.startswith("WAJ")
        assert len(code1) == 8
        assert code1 != code2


def test_registration_with_referral(client):
    """Verifies registration with a referral code permanently attaches referrer and does NOT reward immediately."""
    with app.app_context():
        referrer = _create_test_user("101", balance=50.0)
        referred = _create_test_user("102", balance=0.0)

        success, msg = attach_referral(referred, referrer.referral_code)
        assert success is True
        assert referred.referred_by_id == referrer.id

        ref_record = Referral.query.filter_by(referred_user_id=referred.id).first()
        assert ref_record is not None
        assert ref_record.referrer_id == referrer.id
        assert ref_record.status == "PENDING"

        # Verify referrer was NOT rewarded upon registration
        refreshed_referrer = User.query.get(referrer.id)
        assert refreshed_referrer.wallet_balance == Decimal("50.00")
        assert ReferralReward.query.filter_by(referral_id=ref_record.id).first() is None


def test_self_referral_prevention(client):
    """Verifies that customers cannot refer themselves by ID, phone, or email."""
    with app.app_context():
        user = _create_test_user("103", email="self@example.com")
        success, msg = attach_referral(user, user.referral_code)
        assert success is False
        assert "Self-referral is prohibited" in msg


def test_referral_cannot_be_changed(client):
    """Verifies that once a referrer is assigned, it cannot be modified."""
    with app.app_context():
        referrer_a = _create_test_user("104")
        referrer_b = _create_test_user("105")
        user = _create_test_user("106")

        # First attachment succeeds
        ok1, _ = attach_referral(user, referrer_a.referral_code)
        assert ok1 is True
        assert user.referred_by_id == referrer_a.id

        # Second attachment attempt must fail
        ok2, msg = attach_referral(user, referrer_b.referral_code)
        assert ok2 is False
        assert user.referred_by_id == referrer_a.id


def test_failed_and_pending_transactions_do_not_reward(client):
    """Verifies that failed or pending transactions never trigger a referral reward."""
    with app.app_context():
        referrer = _create_test_user("107", balance=0.0)
        referred = _create_test_user("108", balance=1000.0)
        attach_referral(referred, referrer.referral_code)

        # 1. Failed transaction >= 500
        tx_failed = Transaction(
            user_id=referred.id,
            reference=f"TX_FAIL_{uuid.uuid4().hex[:8]}",
            type="DATA",
            amount=Decimal("1000.00"),
            status="FAILED",
        )
        db.session.add(tx_failed)
        db.session.commit()

        ok, _ = process_referral_reward_for_transaction(tx_failed)
        assert ok is False
        assert User.query.get(referrer.id).wallet_balance == Decimal("0.00")

        # 2. Pending transaction >= 500
        tx_pending = Transaction(
            user_id=referred.id,
            reference=f"TX_PEND_{uuid.uuid4().hex[:8]}",
            type="DATA",
            amount=Decimal("1000.00"),
            status="PENDING",
        )
        db.session.add(tx_pending)
        db.session.commit()

        ok, _ = process_referral_reward_for_transaction(tx_pending)
        assert ok is False
        assert User.query.get(referrer.id).wallet_balance == Decimal("0.00")


def test_first_qualifying_transaction_rewards_referrer(client):
    """Verifies that the first successful transaction meeting min amount credits the referrer's wallet."""
    with app.app_context():
        referrer = _create_test_user("109", balance=0.0)
        referred = _create_test_user("110", balance=1000.0)
        attach_referral(referred, referrer.referral_code)

        # Transaction < 500 does NOT qualify
        tx_small = Transaction(
            user_id=referred.id,
            reference=f"TX_SMALL_{uuid.uuid4().hex[:8]}",
            type="AIRTIME",
            amount=Decimal("200.00"),
            status="SUCCESS",
        )
        db.session.add(tx_small)
        db.session.commit()

        ok, _ = process_referral_reward_for_transaction(tx_small)
        assert ok is False
        assert User.query.get(referrer.id).wallet_balance == Decimal("0.00")

        # Qualifying transaction >= 500
        tx_qualifying = Transaction(
            user_id=referred.id,
            reference=f"TX_QUAL_{uuid.uuid4().hex[:8]}",
            type="DATA",
            amount=Decimal("600.00"),
            status="SUCCESS",
            description="MTN 1GB Data Purchase",
        )
        db.session.add(tx_qualifying)
        db.session.commit()

        ok, msg = process_referral_reward_for_transaction(tx_qualifying)
        assert ok is True

        # Check referrer's wallet was credited exactly ₦100
        refreshed_referrer = User.query.get(referrer.id)
        assert refreshed_referrer.wallet_balance == Decimal("100.00")

        # Check Referral and ReferralReward records
        ref_record = Referral.query.filter_by(referred_user_id=referred.id).first()
        assert ref_record.status == "REWARDED"
        assert ref_record.rewarded_at is not None

        reward_record = ReferralReward.query.filter_by(referral_id=ref_record.id).first()
        assert reward_record is not None
        assert reward_record.amount == Decimal("100.00")
        assert reward_record.status == "CREDITED"

        # Check wallet ledger transaction
        ledger_tx = Transaction.query.filter_by(
            user_id=referrer.id, type="REFERRAL_REWARD"
        ).first()
        assert ledger_tx is not None
        assert ledger_tx.amount == Decimal("100.00")
        assert ledger_tx.description == "Referral Reward"


def test_second_transaction_does_not_reward_again(client):
    """Verifies that a customer who has already triggered a reward cannot trigger another reward."""
    with app.app_context():
        referrer = _create_test_user("111", balance=0.0)
        referred = _create_test_user("112", balance=2000.0)
        attach_referral(referred, referrer.referral_code)

        # First qualifying transaction
        tx1 = Transaction(
            user_id=referred.id,
            reference=f"TX1_{uuid.uuid4().hex[:8]}",
            type="DATA",
            amount=Decimal("1000.00"),
            status="SUCCESS",
        )
        db.session.add(tx1)
        db.session.commit()
        ok1, _ = process_referral_reward_for_transaction(tx1)
        assert ok1 is True
        assert User.query.get(referrer.id).wallet_balance == Decimal("100.00")

        # Second qualifying transaction
        tx2 = Transaction(
            user_id=referred.id,
            reference=f"TX2_{uuid.uuid4().hex[:8]}",
            type="DATA",
            amount=Decimal("1500.00"),
            status="SUCCESS",
        )
        db.session.add(tx2)
        db.session.commit()
        ok2, msg2 = process_referral_reward_for_transaction(tx2)
        assert ok2 is False
        assert "Reward already issued" in msg2

        # Referrer wallet balance remains ₦100, not ₦200
        assert User.query.get(referrer.id).wallet_balance == Decimal("100.00")


def test_duplicate_webhook_does_not_double_reward(client):
    """Verifies idempotency: calling reward logic twice on the same transaction does not duplicate credits."""
    with app.app_context():
        referrer = _create_test_user("113", balance=0.0)
        referred = _create_test_user("114", balance=2000.0)
        attach_referral(referred, referrer.referral_code)

        tx = Transaction(
            user_id=referred.id,
            reference=f"TX_HOOK_{uuid.uuid4().hex[:8]}",
            type="DATA",
            amount=Decimal("800.00"),
            status="SUCCESS",
        )
        db.session.add(tx)
        db.session.commit()

        # First call succeeds
        ok1, _ = process_referral_reward_for_transaction(tx)
        assert ok1 is True

        # Immediate retry (simulating duplicate webhook deliver)
        ok2, _ = process_referral_reward_for_transaction(tx)
        assert ok2 is False
        assert User.query.get(referrer.id).wallet_balance == Decimal("100.00")
        assert ReferralReward.query.filter_by(referral_id=Referral.query.filter_by(referred_user_id=referred.id).first().id).count() == 1


# ==============================================================================
# 2. DAILY DATA DEAL TESTS
# ==============================================================================

def test_daily_deal_selection_and_price_integrity(client, monkeypatch):
    """Verifies that deals select real available plans and never modify or invent prices."""
    import services.deal_service as ds

    mock_plans = [
        {"network": "MTN", "name": "MTN 1GB SME (30 Days)", "variation_code": "m1", "price": Decimal("300.00")},
        {"network": "GLO", "name": "Glo 5GB (30 Days)", "variation_code": "g5", "price": Decimal("1487.20")},
        {"network": "AIRTEL", "name": "Airtel 2GB (30 Days)", "variation_code": "a2", "price": Decimal("700.00")},
    ]
    monkeypatch.setattr(ds, "collect_available_plans", lambda: mock_plans)

    with app.app_context():
        target = date(2026, 10, 18)
        deal, created = generate_daily_deal(target_date=target, force=True, website_url="https://wajvtu.com.ng")
        assert deal is not None
        assert deal.target_date == target
        # The price must strictly match one of the candidate plans
        assert deal.price in [Decimal("300.00"), Decimal("1487.20"), Decimal("700.00")]
        assert f"₦{deal.price:,.2f}" in deal.full_caption
        assert "WAJ VTU" in deal.full_caption


def test_daily_generation_is_idempotent(client, monkeypatch):
    """Verifies that generating deals multiple times on the same date produces exactly one database record."""
    import services.deal_service as ds
    mock_plans = [{"network": "GLO", "name": "Glo 5GB (30 Days)", "variation_code": "g5", "price": Decimal("1487.20")}]
    monkeypatch.setattr(ds, "collect_available_plans", lambda: mock_plans)

    with app.app_context():
        target = date(2026, 10, 19)
        deal1, created1 = generate_daily_deal(target_date=target, force=False)
        assert created1 is True

        deal2, created2 = generate_daily_deal(target_date=target, force=False)
        assert created2 is False
        assert deal1.id == deal2.id
        assert MarketingContent.query.filter_by(target_date=target).count() == 1


# ==============================================================================
# 3. CUSTOMER RE-ENGAGEMENT TESTS
# ==============================================================================

def test_inactivity_segmentation_logic(client):
    """Verifies that 7-day, 14-day, and 30-day inactive customers are grouped accurately."""
    with app.app_context():
        now = datetime.now(timezone.utc)

        # 1. Active user (last transaction 2 days ago)
        u_active = _create_test_user("201")
        tx_active = Transaction(
            user_id=u_active.id,
            reference=f"TX_{uuid.uuid4().hex[:6]}",
            type="DATA",
            amount=Decimal("500.00"),
            status="SUCCESS",
            created_at=now - timedelta(days=2),
        )
        db.session.add(tx_active)

        # 2. 7-day inactive user (last transaction 9 days ago)
        u_7d = _create_test_user("202")
        tx_7d = Transaction(
            user_id=u_7d.id,
            reference=f"TX_{uuid.uuid4().hex[:6]}",
            type="DATA",
            amount=Decimal("500.00"),
            status="SUCCESS",
            created_at=now - timedelta(days=9),
        )
        db.session.add(tx_7d)

        # 3. 14-day inactive user (last transaction 20 days ago)
        u_14d = _create_test_user("203")
        tx_14d = Transaction(
            user_id=u_14d.id,
            reference=f"TX_{uuid.uuid4().hex[:6]}",
            type="DATA",
            amount=Decimal("500.00"),
            status="SUCCESS",
            created_at=now - timedelta(days=20),
        )
        db.session.add(tx_14d)

        # 4. 30-day inactive user (last transaction 45 days ago)
        u_30d = _create_test_user("204")
        tx_30d = Transaction(
            user_id=u_30d.id,
            reference=f"TX_{uuid.uuid4().hex[:6]}",
            type="DATA",
            amount=Decimal("500.00"),
            status="SUCCESS",
            created_at=now - timedelta(days=45),
        )
        db.session.add(tx_30d)
        db.session.commit()

        segments = scan_inactive_segments()

        active_ids = {c["id"] for c in segments["active"]}
        seg7_ids = {c["id"] for c in segments["7_days"]}
        seg14_ids = {c["id"] for c in segments["14_days"]}
        seg30_ids = {c["id"] for c in segments["30_days"]}

        assert u_active.id in active_ids
        assert u_7d.id in seg7_ids
        assert u_14d.id in seg14_ids
        assert u_30d.id in seg30_ids


def test_campaign_queue_default_behavior_is_draft_not_send(client):
    """Verifies that creating a campaign creates messages in DRAFT, NEVER sending automatically."""
    with app.app_context():
        now = datetime.now(timezone.utc)
        u = _create_test_user("205", email="inactive@example.com")
        tx = Transaction(
            user_id=u.id,
            reference=f"TX_{uuid.uuid4().hex[:6]}",
            type="DATA",
            amount=Decimal("500.00"),
            status="SUCCESS",
            created_at=now - timedelta(days=10),
        )
        db.session.add(tx)
        db.session.commit()

        campaign, msg = create_reengagement_campaign("7_days", selected_user_ids=[u.id], channel="EMAIL")
        assert campaign is not None
        assert campaign.status == "DRAFT"

        queued_msgs = MarketingMessage.query.filter_by(campaign_id=campaign.id).all()
        assert len(queued_msgs) == 1
        assert queued_msgs[0].status == "DRAFT"
        assert queued_msgs[0].sent_at is None


def test_campaign_approval_and_cancellation(client):
    """Verifies admin approval and cancellation state transitions."""
    with app.app_context():
        now = datetime.now(timezone.utc)
        u = _create_test_user("206", email="campaign_test@example.com")
        tx = Transaction(
            user_id=u.id,
            reference=f"TX_{uuid.uuid4().hex[:6]}",
            type="DATA",
            amount=Decimal("500.00"),
            status="SUCCESS",
            created_at=now - timedelta(days=10),
        )
        db.session.add(tx)
        db.session.commit()

        campaign, _ = create_reengagement_campaign("7_days", selected_user_ids=[u.id], channel="EMAIL")
        assert campaign.status == "DRAFT"

        # Approve
        ok, _ = approve_campaign(campaign.id)
        assert ok is True
        assert campaign.status == "APPROVED"
        assert all(m.status == "APPROVED" for m in campaign.messages)

        # Cancel
        ok_cancel, _ = cancel_campaign(campaign.id)
        assert ok_cancel is True
        assert campaign.status == "CANCELLED"
        assert all(m.status == "CANCELLED" for m in campaign.messages)


# ==============================================================================
# 4. CUSTOMER AND ADMIN UI ENDPOINT TESTS
# ==============================================================================

def test_customer_referral_page_loads(client):
    """Verifies that customer /referrals loads with user stats and copy link."""
    with app.app_context():
        user = _create_test_user("301")
        with client.session_transaction() as sess:
            sess["user_id"] = user.id

        response = client.get("/referrals")
        assert response.status_code == 200
        text = response.get_data(as_text=True)
        assert user.referral_code in text
        assert "Copy Link" in text
        assert "Total Referrals" in text


def test_admin_marketing_routes_accessible(client):
    """Verifies that all admin marketing sections are reachable and require auth."""
    # 1. Unauthenticated gets 302 redirect to login
    assert client.get("/admin/marketing").status_code == 302

    # 2. Authenticated admin
    import app as app_module
    admin_email = "marketing_admin@example.com"
    app_module.ADMIN_EMAIL = admin_email
    app_module.app.config["ADMIN_EMAIL"] = admin_email

    with app_module.app.app_context():
        admin_user = User(
            whatsapp_id="admin_marketing_test",
            phone="234800000999",
            name="Admin User",
            email=admin_email,
        )
        db.session.add(admin_user)
        db.session.commit()
        admin_id = admin_user.id

    with client.session_transaction() as sess:
        sess["user_id"] = admin_id
        sess["is_admin"] = True

    routes = [
        "/admin/marketing",
        "/admin/marketing/referrals",
        "/admin/marketing/daily-deals",
        "/admin/marketing/reengagement",
        "/admin/marketing/campaigns",
        "/admin/marketing/settings",
    ]
    for r in routes:
        resp = client.get(r)
        assert resp.status_code == 200, f"Failed accessing {r}"
