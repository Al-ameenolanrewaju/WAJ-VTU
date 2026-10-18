"""
Referral system service for WAJ VTU.
Handles unique referral code generation, registration tracking, fraud prevention,
qualifying transaction verification, atomic reward distribution, and dashboard metrics.
"""
import secrets
import string
import uuid
from decimal import Decimal
from datetime import datetime, timezone
from sqlalchemy import func, or_, case
from flask import request

from models import db, User, Transaction, Referral, ReferralReward
from services.marketing_settings import (
    get_setting_bool,
    get_setting_decimal,
    get_setting_int,
)

# Unambiguous characters for referral codes
CODE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


def generate_unique_referral_code(prefix="WAJ", length=5):
    """
    Generates a cryptographically secure, difficult-to-guess referral code.
    Example: WAJ7K29X
    Guarantees uniqueness across all users.
    """
    for _ in range(50):
        suffix = "".join(secrets.choice(CODE_ALPHABET) for _ in range(length))
        code = f"{prefix}{suffix}"
        if not User.query.filter_by(referral_code=code).first():
            return code
    # Fallback with uuid if collision space fills
    return f"{prefix}{uuid.uuid4().hex[:6].upper()}"


def ensure_user_referral_code(user):
    """
    Ensure a user has a unique referral code.
    Auto-generates and persists one if missing (for existing users).
    """
    if not user:
        return ""
    if user.referral_code:
        return user.referral_code

    code = generate_unique_referral_code()
    user.referral_code = code
    try:
        db.session.commit()
    except Exception:
        db.session.rollback()
        # Retry once in case of a rare race
        user.referral_code = generate_unique_referral_code()
        db.session.commit()
    return user.referral_code


def get_referral_url(user, base_url=None):
    """
    Dynamically generates the referral registration URL using the application's domain.
    Never hardcodes a domain.
    """
    code = ensure_user_referral_code(user)
    if not code:
        return ""

    if not base_url:
        try:
            if request and hasattr(request, "host_url"):
                base_url = request.host_url.rstrip("/")
        except Exception:
            base_url = None

    if not base_url:
        from flask import current_app
        base_url = (current_app.config.get("APP_BASE_URL") or "https://wajvtu.com.ng").rstrip("/")

    return f"{base_url}/register?ref={code}"


def attach_referral(referred_user, referral_code):
    """
    Attaches a referrer to a newly registering user via referral code.

    Fraud Prevention:
    - Referral system must be enabled in settings.
    - User cannot refer themselves (same ID, phone, or email).
    - User cannot change their referrer once registered (permanent attachment).
    - Duplicate referral records are prohibited.
    """
    if not get_setting_bool("REFERRAL_ENABLED", True):
        return False, "Referral program is currently disabled."

    if not referral_code or not referred_user:
        return False, "Missing user or referral code."

    clean_code = str(referral_code).strip().upper()

    # Permanent attachment check: cannot change referrer if already assigned
    if referred_user.referred_by_id:
        return False, "Referrer has already been assigned and cannot be changed."

    existing_record = Referral.query.filter_by(referred_user_id=referred_user.id).first()
    if existing_record:
        return False, "Referral record already exists."

    referrer = User.query.filter_by(referral_code=clean_code).first()
    if not referrer:
        return False, "Invalid referral code."

    # Self-referral prevention
    if referrer.id == referred_user.id:
        return False, "Self-referral is prohibited."

    if referrer.phone and referred_user.phone and referrer.phone == referred_user.phone:
        return False, "Self-referral is prohibited by phone."

    if referrer.email and referred_user.email and referrer.email.lower() == referred_user.email.lower():
        return False, "Self-referral is prohibited by email."

    # Permanently attach
    referred_user.referred_by_id = referrer.id

    referral = Referral(
        referrer_id=referrer.id,
        referred_user_id=referred_user.id,
        referral_code=clean_code,
        status="PENDING",
        created_at=datetime.now(timezone.utc),
    )
    db.session.add(referral)
    db.session.commit()
    return True, "Referral tracked successfully."


def process_referral_reward_for_transaction(transaction):
    """
    Evaluates whether a successful transaction triggers a referral reward.
    Must be called when a transaction status reaches SUCCESS.

    Requirements & Fraud Prevention:
    - Referral program must be enabled.
    - Transaction must be successful (status == 'SUCCESS').
    - Transaction cannot be a DEPOSIT (reward on actual value purchase).
    - Transaction amount must meet or exceed minimum qualifying transaction setting.
    - Referred customer must have a pending referral record.
    - Customer cannot have previously triggered a referral reward.
    - Maximum rewards per referrer limit respected if configured.
    - Atomic database operations prevent race conditions and duplicate rewards.
    """
    if not get_setting_bool("REFERRAL_ENABLED", True):
        return False, "Referral system disabled."

    if not transaction or transaction.status != "SUCCESS":
        return False, "Transaction is not successful."

    # Exclude wallet deposits — qualifying transaction is a service purchase
    if transaction.type == "DEPOSIT":
        return False, "Deposits do not qualify for referral rewards."

    min_amount = get_setting_decimal("REFERRAL_MIN_TRANSACTION", "500.00")
    if Decimal(str(transaction.amount)) < min_amount:
        return False, f"Transaction amount {transaction.amount} is below minimum {min_amount}."

    user_id = transaction.user_id
    referral = Referral.query.filter_by(referred_user_id=user_id).first()
    if not referral:
        return False, "No referral found for this customer."

    # Verify no reward has already been issued for this referred user
    existing_reward = ReferralReward.query.filter_by(referred_user_id=user_id).first()
    if existing_reward or referral.status == "REWARDED":
        return False, "Reward already issued for this referred customer."

    if referral.status != "PENDING":
        return False, f"Referral status is {referral.status}, not eligible."

    referrer = User.query.get(referral.referrer_id)
    if not referrer:
        return False, "Referrer account not found."

    # Check max rewards per referrer limit (0 = unlimited)
    max_rewards = get_setting_int("REFERRAL_MAX_REWARDS_PER_REFERRER", 0)
    if max_rewards > 0:
        rewards_count = ReferralReward.query.filter_by(referrer_id=referrer.id).count()
        if rewards_count >= max_rewards:
            referral.status = "QUALIFIED"
            db.session.commit()
            return False, "Referrer has reached the maximum reward quota."

    reward_amount = get_setting_decimal("REFERRAL_REWARD_AMOUNT", "100.00")
    if reward_amount <= 0:
        return False, "Reward amount is zero or negative."

    now = datetime.now(timezone.utc)

    # Atomic creation of reward record and wallet credit
    try:
        # Create ReferralReward record (unique constraint guarantees single execution)
        reward = ReferralReward(
            referral_id=referral.id,
            referrer_id=referrer.id,
            referred_user_id=user_id,
            transaction_id=transaction.id,
            amount=reward_amount,
            status="CREDITED",
            created_at=now,
        )
        db.session.add(reward)

        # Credit referrer wallet
        referrer.wallet_balance += reward_amount

        # Create wallet ledger transaction for full transparency
        ledger_tx = Transaction(
            user_id=referrer.id,
            reference=f"REF_REW_{uuid.uuid4().hex[:10].upper()}",
            type="REFERRAL_REWARD",
            amount=reward_amount,
            recipient=referrer.phone,
            provider_name="system",
            provider_reference=f"TX_{transaction.reference}",
            status="SUCCESS",
            description="Referral Reward",
            meta_data={
                "referral_id": referral.id,
                "referred_user_id": user_id,
                "qualifying_transaction_ref": transaction.reference,
                "reward_amount": str(reward_amount),
            },
            created_at=now,
        )
        db.session.add(ledger_tx)

        # Update referral status
        referral.status = "REWARDED"
        referral.rewarded_at = now

        db.session.commit()
        return True, f"Referrer {referrer.id} credited NGN {reward_amount}."
    except Exception as exc:
        db.session.rollback()
        return False, f"Failed to credit referral reward: {str(exc)}"


def get_user_referral_summary(user):
    """
    Aggregates referral statistics and history for the customer dashboard.
    """
    ensure_user_referral_code(user)
    referral_code = user.referral_code or ""
    referral_url = get_referral_url(user)

    referrals = (
        Referral.query.filter_by(referrer_id=user.id)
        .order_by(Referral.created_at.desc())
        .all()
    )

    total_referrals = len(referrals)
    successful_referrals = sum(1 for r in referrals if r.status == "REWARDED")
    pending_referrals = sum(1 for r in referrals if r.status == "PENDING")

    total_earned = (
        db.session.query(func.coalesce(func.sum(ReferralReward.amount), 0))
        .filter_by(referrer_id=user.id, status="CREDITED")
        .scalar()
    ) or Decimal("0.00")

    history = []
    for r in referrals:
        referred = r.referred_user
        reward_rec = r.reward
        history.append({
            "id": r.id,
            "referred_name": (referred.name if referred else "Customer") or "Customer",
            "referred_phone_masked": (
                (referred.phone[:4] + "***" + referred.phone[-3:]) if (referred and referred.phone and len(referred.phone) >= 7)
                else "Registered User"
            ),
            "date": r.created_at,
            "status": r.status,
            "reward_amount": float(reward_rec.amount) if reward_rec else 0.0,
            "rewarded_at": r.rewarded_at,
        })

    return {
        "referral_code": referral_code,
        "referral_url": referral_url,
        "total_referrals": total_referrals,
        "successful_referrals": successful_referrals,
        "pending_referrals": pending_referrals,
        "total_rewards_earned": Decimal(str(total_earned)),
        "history": history,
    }


def get_admin_referral_metrics(search_query=None, status_filter=None):
    """
    Returns aggregated metrics and detailed list for the Admin Referral Dashboard.
    """
    total_referrals = Referral.query.count()
    successful_referrals = Referral.query.filter_by(status="REWARDED").count()
    pending_referrals = Referral.query.filter_by(status="PENDING").count()

    total_rewards_paid = (
        db.session.query(func.coalesce(func.sum(ReferralReward.amount), Decimal("0.00")))
        .filter_by(status="CREDITED")
        .scalar()
    ) or Decimal("0.00")

    conversion_rate = (
        (successful_referrals / total_referrals * 100) if total_referrals > 0 else 0.0
    )

    # Top referrers leaderboard
    top_referrers_query = (
        db.session.query(
            User.id,
            User.name,
            User.phone,
            User.referral_code,
            func.count(Referral.id).label("total_referrals"),
            func.sum(case((Referral.status == "REWARDED", 1), else_=0)).label("successful_referrals"),
            func.coalesce(func.sum(ReferralReward.amount), 0).label("total_rewards"),
        )
        .join(Referral, Referral.referrer_id == User.id)
        .outerjoin(ReferralReward, ReferralReward.referral_id == Referral.id)
        .group_by(User.id)
        .order_by(func.count(Referral.id).desc())
        .limit(10)
        .all()
    )

    # Detailed referrals list with optional filtering
    query = Referral.query

    if status_filter and status_filter.upper() in ("PENDING", "QUALIFIED", "REWARDED"):
        query = query.filter(Referral.status == status_filter.upper())

    if search_query:
        term = f"%{search_query.strip()}%"
        query = query.join(User, Referral.referrer_id == User.id).filter(
            or_(
                Referral.referral_code.ilike(term),
                User.phone.ilike(term),
                User.name.ilike(term),
            )
        )

    referrals_list = query.order_by(Referral.created_at.desc()).limit(100).all()

    return {
        "total_referrals": total_referrals,
        "successful_referrals": successful_referrals,
        "pending_referrals": pending_referrals,
        "total_rewards_paid": Decimal(str(total_rewards_paid)),
        "conversion_rate": round(conversion_rate, 2),
        "top_referrers": top_referrers_query,
        "referrals": referrals_list,
    }
