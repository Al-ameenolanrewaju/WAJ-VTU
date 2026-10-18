"""
Customer Re-engagement System Service for WAJ VTU.
Analyzes actual transaction activity, segments inactive customers (7, 14, 30 days),
builds personalized campaigns, and manages the campaign message queue.

CRITICAL: Default behavior is GENERATE/QUEUE, NEVER auto-spam.
Admin retains complete control to review, edit, approve, or cancel before sending.
"""
from collections import Counter
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from sqlalchemy import func, or_

from models import db, User, Transaction, MarketingCampaign, MarketingMessage
from services.marketing_settings import get_marketing_setting, get_setting_bool
from services.notification_service import NotificationService


def get_customer_activity_profile(user_id):
    """
    Computes activity metrics for a specific customer based on actual transaction records.
    """
    successful_txs = (
        Transaction.query.filter_by(user_id=user_id, status="SUCCESS")
        .filter(Transaction.type != "DEPOSIT")
        .order_by(Transaction.created_at.desc())
        .all()
    )

    total_spent = sum((tx.amount for tx in successful_txs), Decimal("0.00"))
    total_tx_count = len(successful_txs)

    last_tx_date = successful_txs[0].created_at if successful_txs else None

    # Detect preferred service
    service_counter = Counter((tx.type for tx in successful_txs))
    preferred_service = service_counter.most_common(1)[0][0] if service_counter else "DATA"

    # Detect preferred network
    networks_found = []
    for tx in successful_txs:
        desc = (tx.description or "").upper()
        for net in ("MTN", "GLO", "AIRTEL", "9MOBILE"):
            if net in desc:
                networks_found.append(net)
                break
    preferred_network = Counter(networks_found).most_common(1)[0][0] if networks_found else "MTN"

    return {
        "user_id": user_id,
        "last_tx_date": last_tx_date,
        "total_tx_count": total_tx_count,
        "total_spent": total_spent,
        "preferred_service": preferred_service,
        "preferred_network": preferred_network,
    }


def scan_inactive_segments():
    """
    Scans all registered users and categorizes them into inactive segments:
    - 7 days inactive: 7 to 13 days since last transaction
    - 14 days inactive: 14 to 29 days since last transaction
    - 30 days inactive: 30+ days since last transaction (or registered > 30 days with no transactions)
    """
    now = datetime.now(timezone.utc)
    all_users = User.query.all()

    # Pre-fetch latest successful non-deposit transaction for each user
    latest_tx_subquery = (
        db.session.query(
            Transaction.user_id,
            func.max(Transaction.created_at).label("last_activity"),
            func.count(Transaction.id).label("tx_count"),
            func.coalesce(func.sum(Transaction.amount), 0).label("total_spent"),
        )
        .filter(Transaction.status == "SUCCESS", Transaction.type != "DEPOSIT")
        .group_by(Transaction.user_id)
        .all()
    )

    user_activity_map = {
        row.user_id: {
            "last_activity": row.last_activity,
            "tx_count": row.tx_count,
            "total_spent": row.total_spent,
        }
        for row in latest_tx_subquery
    }

    segment_7 = []
    segment_14 = []
    segment_30 = []
    active = []

    for user in all_users:
        act = user_activity_map.get(user.id)
        if act and act["last_activity"]:
            last_date = act["last_activity"]
            if last_date.tzinfo is None:
                last_date = last_date.replace(tzinfo=timezone.utc)
            days_inactive = (now - last_date).days
        else:
            # Users with no successful transactions: calculate from registration date
            reg_date = user.created_at
            if reg_date.tzinfo is None:
                reg_date = reg_date.replace(tzinfo=timezone.utc)
            days_inactive = (now - reg_date).days

        customer_summary = {
            "id": user.id,
            "name": user.name or "Customer",
            "phone": user.phone,
            "email": user.email,
            "days_inactive": days_inactive,
            "wallet_balance": float(user.wallet_balance),
            "whatsapp_linked": NotificationService.is_whatsapp_linked(user),
            "has_email": bool(user.email and "@" in user.email),
            "tx_count": act["tx_count"] if act else 0,
            "total_spent": float(act["total_spent"]) if act else 0.0,
        }

        if days_inactive < 7:
            active.append(customer_summary)
        elif 7 <= days_inactive < 14:
            segment_7.append(customer_summary)
        elif 14 <= days_inactive < 30:
            segment_14.append(customer_summary)
        else:
            segment_30.append(customer_summary)

    return {
        "active": active,
        "7_days": segment_7,
        "14_days": segment_14,
        "30_days": segment_30,
        "counts": {
            "active": len(active),
            "7_days": len(segment_7),
            "14_days": len(segment_14),
            "30_days": len(segment_30),
        }
    }


def render_reengagement_message(template_str, user, activity_profile=None, website_url=None):
    """
    Safely substitutes personalization tags into the template.
    Guarantees no sensitive data is leaked.
    """
    if not website_url:
        website_url = (get_marketing_setting("APP_BASE_URL", "") or "https://wajvtu.com.ng").rstrip("/")

    first_name = (user.name or "there").strip().split()[0] if user.name else "there"
    if first_name.lower() in ("user", "admin", "unknown", ""):
        first_name = "there"

    preferred_net = (activity_profile or {}).get("preferred_network", "MTN")
    preferred_srv = (activity_profile or {}).get("preferred_service", "data").lower()

    text = template_str.replace("{name}", first_name)
    text = text.replace("{preferred_network}", preferred_net)
    text = text.replace("{preferred_service}", preferred_srv)
    text = text.replace("{website_url}", website_url)
    return text.strip()


def create_reengagement_campaign(segment_key, selected_user_ids=None, channel="BOTH", custom_template=None, website_url=None):
    """
    Builds a marketing campaign and generates queued messages for review.
    DEFAULT BEHAVIOR: DRAFT / QUEUED. Does NOT send immediately.
    """
    segments_data = scan_inactive_segments()
    segment_list = segments_data.get(segment_key, [])

    if selected_user_ids:
        selected_ids_set = {int(uid) for uid in selected_user_ids}
        targets = [c for c in segment_list if c["id"] in selected_ids_set]
    else:
        targets = segment_list

    if not targets:
        return None, "No customers found for the specified segment selection."

    # Load template
    template_setting_key = f"REENGAGEMENT_{segment_key.upper()}_TEMPLATE"
    template_text = custom_template or get_marketing_setting(template_setting_key)
    if not template_text:
        template_text = (
            "👋 Hello {name}!\n\n"
            "We haven't seen you on WAJ VTU in a while. "
            "Need data or airtime today? Check our latest offers at {website_url}!\n\n"
            "WAJ VTU — Fast. Simple. Reliable."
        )

    title_map = {
        "7_days": "7-Day Re-engagement Campaign",
        "14_days": "14-Day Re-engagement Campaign",
        "30_days": "30-Day Win-Back Campaign",
    }
    title = f"{title_map.get(segment_key, 'Custom Campaign')} ({datetime.now().strftime('%d %b %Y')})"

    now = datetime.now(timezone.utc)
    campaign = MarketingCampaign(
        title=title,
        segment=segment_key,
        channel=channel,
        template_body=template_text,
        status="DRAFT",
        total_recipients=0,
        sent_count=0,
        failed_count=0,
        created_at=now,
    )
    db.session.add(campaign)
    db.session.flush()

    messages_created = 0
    for target in targets:
        user = User.query.get(target["id"])
        if not user:
            continue

        profile = get_customer_activity_profile(user.id)
        msg_body = render_reengagement_message(template_text, user, profile, website_url)

        # Queue Email if applicable
        if channel in ("EMAIL", "BOTH") and user.email and "@" in user.email:
            email_msg = MarketingMessage(
                campaign_id=campaign.id,
                user_id=user.id,
                channel="EMAIL",
                recipient=user.email,
                subject=f"We miss you on WAJ VTU, {user.name or 'there'}!",
                message_body=msg_body,
                status="DRAFT",
                created_at=now,
            )
            db.session.add(email_msg)
            messages_created += 1

        # Queue WhatsApp if user has a linked account
        if channel in ("WHATSAPP", "BOTH") and NotificationService.is_whatsapp_linked(user):
            wa_recipient = user.phone or str(user.whatsapp_id).split("@", 1)[0]
            wa_msg = MarketingMessage(
                campaign_id=campaign.id,
                user_id=user.id,
                channel="WHATSAPP",
                recipient=wa_recipient,
                subject=None,
                message_body=msg_body,
                status="DRAFT",
                created_at=now,
            )
            db.session.add(wa_msg)
            messages_created += 1

    campaign.total_recipients = messages_created
    db.session.commit()
    return campaign, f"Campaign created with {messages_created} queued messages for review."


def approve_campaign(campaign_id):
    """Admin approves a draft campaign, setting messages to QUEUED/APPROVED."""
    campaign = MarketingCampaign.query.get(campaign_id)
    if not campaign:
        return False, "Campaign not found."

    campaign.status = "APPROVED"
    for msg in campaign.messages:
        if msg.status == "DRAFT":
            msg.status = "APPROVED"
    db.session.commit()
    return True, "Campaign approved."


def dispatch_campaign_messages(campaign_id, max_messages=100):
    """
    Executes sending for APPROVED messages in a campaign.
    Respects rate limits and logs every dispatch.
    """
    campaign = MarketingCampaign.query.get(campaign_id)
    if not campaign:
        return False, "Campaign not found."

    pending_messages = (
        MarketingMessage.query.filter_by(campaign_id=campaign.id)
        .filter(MarketingMessage.status.in_(["APPROVED", "QUEUED"]))
        .limit(max_messages)
        .all()
    )

    sent = 0
    failed = 0
    for msg in pending_messages:
        success, _ = NotificationService.dispatch_marketing_message(msg)
        if success:
            sent += 1
        else:
            failed += 1

    campaign.sent_count = (campaign.sent_count or 0) + sent
    campaign.failed_count = (campaign.failed_count or 0) + failed

    # Check if all completed
    remaining = (
        MarketingMessage.query.filter_by(campaign_id=campaign.id)
        .filter(MarketingMessage.status.in_(["APPROVED", "QUEUED", "DRAFT"]))
        .count()
    )
    if remaining == 0:
        campaign.status = "COMPLETED"

    db.session.commit()
    return True, f"Dispatched {sent} sent, {failed} failed."


def cancel_campaign(campaign_id):
    """Cancels a campaign and all unsent messages."""
    campaign = MarketingCampaign.query.get(campaign_id)
    if not campaign:
        return False, "Campaign not found."

    campaign.status = "CANCELLED"
    for msg in campaign.messages:
        if msg.status in ("DRAFT", "QUEUED", "APPROVED"):
            msg.status = "CANCELLED"
    db.session.commit()
    return True, "Campaign cancelled."
