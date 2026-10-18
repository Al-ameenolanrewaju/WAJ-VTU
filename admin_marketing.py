"""
Admin Marketing Blueprint for WAJ VTU.
Provides the Admin Marketing Dashboard, Referral Management, Daily Data Deals,
Customer Re-engagement, Campaign Queue, and Marketing Settings.
"""
from decimal import Decimal
from datetime import datetime, date, timezone
from flask import Blueprint, request, render_template_string, redirect, url_for, flash, jsonify
from markupsafe import escape

from models import db, User, Transaction, Referral, ReferralReward, MarketingContent, MarketingCampaign, MarketingMessage
from services.marketing_settings import (
    get_all_marketing_settings,
    get_marketing_setting,
    set_marketing_setting,
    get_setting_bool,
    get_setting_decimal,
    get_setting_int,
)
from services.referral_service import get_admin_referral_metrics
from services.deal_service import (
    generate_daily_deal,
    get_daily_deal_history,
    mark_deal_used,
    update_deal_caption,
    get_lagos_today,
)
from services.reengagement_service import (
    scan_inactive_segments,
    create_reengagement_campaign,
    approve_campaign,
    dispatch_campaign_messages,
    cancel_campaign,
)

marketing_admin_bp = Blueprint("marketing_admin", __name__, url_prefix="/admin/marketing")


def _get_app_helpers():
    from app import require_admin_auth, record_admin_audit, format_admin_datetime, ADMIN_BASE_TEMPLATE
    return require_admin_auth, record_admin_audit, format_admin_datetime, ADMIN_BASE_TEMPLATE


def _marketing_subnav(active_subpage):
    tabs = [
        ("overview", "📊 Overview", "/admin/marketing"),
        ("referrals", "🤝 Referrals", "/admin/marketing/referrals"),
        ("daily_deals", "🏷️ Daily Deals", "/admin/marketing/daily-deals"),
        ("reengagement", "🔁 Re-engagement", "/admin/marketing/reengagement"),
        ("campaigns", "📬 Campaigns", "/admin/marketing/campaigns"),
        ("settings", "⚙️ Settings", "/admin/marketing/settings"),
    ]
    html = '<div class="admin-subnav-bar">'
    for key, label, url in tabs:
        active_class = "active" if active_subpage == key else ""
        html += f'<a href="{url}" class="admin-subnav-item {active_class}">{label}</a>'
    html += '</div>'
    return html


# ----------------------------------------------------------------------
# 1. MAIN MARKETING DASHBOARD
# ----------------------------------------------------------------------
@marketing_admin_bp.route("")
@marketing_admin_bp.route("/")
def dashboard():
    require_admin_auth, _, format_admin_datetime, ADMIN_BASE_TEMPLATE = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    ref_metrics = get_admin_referral_metrics()
    seg_data = scan_inactive_segments()
    counts = seg_data["counts"]

    deals_generated = MarketingContent.query.filter_by(content_type="DAILY_DEAL").count()
    messages_queued = MarketingMessage.query.filter(MarketingMessage.status.in_(["DRAFT", "QUEUED", "APPROVED"])).count()
    today_deal, _ = generate_daily_deal(target_date=get_lagos_today())

    body = f"""
    {_marketing_subnav('overview')}
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:20px;">
        <h2 style="font-family:'Space Grotesk',sans-serif; font-size:24px; font-weight:700;">🚀 Marketing Automation Hub</h2>
    </div>

    <!-- 9 Metric Cards -->
    <div class="card-grid" style="grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));">
        <div class="card">
            <h3>📈 Total Referrals</h3>
            <p>{ref_metrics['total_referrals']}</p>
            <small>All invitations tracked</small>
        </div>
        <div class="card">
            <h3>👥 Successful Referrals</h3>
            <p style="color:#16a34a;">{ref_metrics['successful_referrals']}</p>
            <small>Completed qualifying purchase</small>
        </div>
        <div class="card">
            <h3>💰 Referral Rewards Paid</h3>
            <p>₦{ref_metrics['total_rewards_paid']:,.2f}</p>
            <small>Wallet credits awarded</small>
        </div>
        <div class="card">
            <h3>🟢 Active Customers</h3>
            <p style="color:#16a34a;">{counts['active']}</p>
            <small>Transacted within 7 days</small>
        </div>
        <div class="card">
            <h3>🟡 7-Day Inactive</h3>
            <p style="color:#d97706;">{counts['7_days']}</p>
            <small>No purchase in 7–13 days</small>
        </div>
        <div class="card">
            <h3>🟠 14-Day Inactive</h3>
            <p style="color:#ea580c;">{counts['14_days']}</p>
            <small>No purchase in 14–29 days</small>
        </div>
        <div class="card">
            <h3>🔴 30-Day Inactive</h3>
            <p style="color:#dc2626;">{counts['30_days']}</p>
            <small>30+ days without purchase</small>
        </div>
        <div class="card">
            <h3>📝 Content Generated</h3>
            <p>{deals_generated}</p>
            <small>Daily deals created</small>
        </div>
        <div class="card">
            <h3>📤 Messages Queued</h3>
            <p style="color:#2563eb;">{messages_queued}</p>
            <small>Ready for review/dispatch</small>
        </div>
    </div>

    <!-- Quick Sections Grid -->
    <div class="section-grid" style="margin-top:24px;">
        <!-- Today's Deal Preview -->
        <div class="card" style="flex:1;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                <h3 style="font-size:13px; font-weight:700; color:#090909;">🏷️ Today's Deal Preview</h3>
                <a href="/admin/marketing/daily-deals" style="font-size:12px; color:#2563eb; text-decoration:none; font-weight:600;">Manage Deals →</a>
            </div>
            {f'''
            <div style="background:#fafaf9; border:1px solid #e4e4e0; border-radius:10px; padding:14px; font-size:13px;">
                <div style="font-weight:700; font-size:15px; margin-bottom:4px;">{today_deal.network} {today_deal.data_size} ({today_deal.validity})</div>
                <div style="color:#16a34a; font-weight:800; font-size:18px; margin-bottom:8px;">₦{today_deal.price:,.2f}</div>
                <pre style="background:#ffffff; border:1px solid #e4e4e0; border-radius:6px; padding:10px; font-size:12px; white-space:pre-wrap; max-height:120px; overflow-y:auto; font-family:monospace;">{escape(today_deal.full_caption)}</pre>
            </div>
            ''' if today_deal else '<p style="color:#969696; font-size:13px;">No deal generated for today yet.</p>'}
        </div>

        <!-- Quick Re-engagement Callout -->
        <div class="card" style="flex:1;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                <h3 style="font-size:13px; font-weight:700; color:#090909;">🔁 Re-engagement Opportunity</h3>
                <a href="/admin/marketing/reengagement" style="font-size:12px; color:#2563eb; text-decoration:none; font-weight:600;">View Customers →</a>
            </div>
            <p style="font-size:13px; color:#626262; margin-bottom:14px;">
                <strong>{counts['7_days'] + counts['14_days'] + counts['30_days']} customers</strong> have not made a purchase recently.
                Generate personalized re-engagement campaigns via Email or WhatsApp.
            </p>
            <div style="display:flex; gap:8px;">
                <a href="/admin/marketing/reengagement" style="display:inline-block; background:#ffc400; color:#090909; padding:8px 14px; border-radius:8px; font-size:12px; font-weight:700; text-decoration:none;">Review Inactive Segments</a>
                <a href="/admin/marketing/campaigns" style="display:inline-block; background:#ffffff; border:1px solid #e4e4e0; color:#090909; padding:8px 14px; border-radius:8px; font-size:12px; font-weight:600; text-decoration:none;">View Queue ({messages_queued})</a>
            </div>
        </div>
    </div>
    """
    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=body, active_page="marketing")


# ----------------------------------------------------------------------
# 2. REFERRAL SYSTEM MANAGEMENT
# ----------------------------------------------------------------------
@marketing_admin_bp.route("/referrals")
def referrals():
    require_admin_auth, _, format_admin_datetime, ADMIN_BASE_TEMPLATE = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    q = request.args.get("q", "").strip()
    status_filter = request.args.get("status", "").strip()
    data = get_admin_referral_metrics(search_query=q, status_filter=status_filter)

    top_rows = ""
    for r in data["top_referrers"]:
        top_rows += f"""
        <tr>
            <td><strong>{escape(r.name or 'User')}</strong><br><small style="color:#969696;">{escape(r.phone or '')}</small></td>
            <td><code>{escape(r.referral_code or '')}</code></td>
            <td>{r.total_referrals}</td>
            <td style="color:#16a34a; font-weight:700;">{r.successful_referrals}</td>
            <td style="font-weight:700;">₦{float(r.total_rewards):,.2f}</td>
        </tr>
        """

    ref_rows = ""
    for ref in data["referrals"]:
        ref_rows += f"""
        <tr>
            <td><code>{escape(ref.referral_code)}</code></td>
            <td>{escape(ref.referrer.name if ref.referrer else 'User')} ({escape(ref.referrer.phone if ref.referrer else '')})</td>
            <td>{escape(ref.referred_user.name if ref.referred_user else 'Customer')} ({escape(ref.referred_user.phone if ref.referred_user else '')})</td>
            <td>
                <span class="{'badge-success' if ref.status == 'REWARDED' else ('badge-pending' if ref.status == 'PENDING' else '')}">
                    {ref.status}
                </span>
            </td>
            <td>{'₦' + str(ref.reward.amount) if ref.reward else '—'}</td>
            <td>{escape(format_admin_datetime(ref.created_at))}</td>
        </tr>
        """

    body = f"""
    {_marketing_subnav('referrals')}
    <h2 style="font-family:'Space Grotesk',sans-serif; font-size:22px; font-weight:700; margin-bottom:18px;">🤝 Referral Program Overview</h2>

    <div class="card-grid" style="grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); margin-bottom:24px;">
        <div class="card"><h3>Total Referrals</h3><p>{data['total_referrals']}</p></div>
        <div class="card"><h3>Successful</h3><p style="color:#16a34a;">{data['successful_referrals']}</p></div>
        <div class="card"><h3>Pending</h3><p style="color:#d97706;">{data['pending_referrals']}</p></div>
        <div class="card"><h3>Total Paid</h3><p>₦{data['total_rewards_paid']:,.2f}</p></div>
        <div class="card"><h3>Conversion Rate</h3><p style="color:#2563eb;">{data['conversion_rate']}%</p></div>
    </div>

    <!-- Top Referrers -->
    <h3 style="font-size:16px; font-weight:700; margin:24px 0 12px;">🏆 Top Referrers Leaderboard</h3>
    <table>
        <thead>
            <tr><th>Referrer</th><th>Referral Code</th><th>Total Invites</th><th>Successful</th><th>Rewards Earned</th></tr>
        </thead>
        <tbody>
            {top_rows if top_rows else '<tr><td colspan="5" style="text-align:center;">No top referrers recorded yet.</td></tr>'}
        </tbody>
    </table>

    <!-- All Referrals Filter & Table -->
    <h3 style="font-size:16px; font-weight:700; margin:32px 0 12px;">All Referrals History</h3>
    <form method="GET" action="/admin/marketing/referrals" style="display:flex; flex-wrap:wrap; gap:10px; margin-bottom:16px;">
        <input type="text" name="q" value="{escape(q)}" placeholder="Search by phone, name, or referral code..." style="flex:1; min-width:220px;">
        <select name="status" style="width:160px;">
            <option value="">All Statuses</option>
            <option value="PENDING" {'selected' if status_filter == 'PENDING' else ''}>Pending</option>
            <option value="QUALIFIED" {'selected' if status_filter == 'QUALIFIED' else ''}>Qualified</option>
            <option value="REWARDED" {'selected' if status_filter == 'REWARDED' else ''}>Rewarded</option>
        </select>
        <button type="submit">Filter Referrals</button>
        {'<a href="/admin/marketing/referrals" style="padding:9px 12px; background:#e4e4e0; border-radius:9px; text-decoration:none; font-size:13px; color:#090909; display:inline-flex; align-items:center;">Reset</a>' if q or status_filter else ''}
    </form>

    <table>
        <thead>
            <tr><th>Code</th><th>Referrer</th><th>Referred Customer</th><th>Status</th><th>Reward</th><th>Date</th></tr>
        </thead>
        <tbody>
            {ref_rows if ref_rows else '<tr><td colspan="6" style="text-align:center;">No referrals matching your query.</td></tr>'}
        </tbody>
    </table>
    """
    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=body, active_page="marketing")


# ----------------------------------------------------------------------
# 3. DAILY DATA DEALS
# ----------------------------------------------------------------------
@marketing_admin_bp.route("/daily-deals")
def daily_deals():
    require_admin_auth, _, format_admin_datetime, ADMIN_BASE_TEMPLATE = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    today = get_lagos_today()
    today_deal, _ = generate_daily_deal(target_date=today)
    history = get_daily_deal_history(limit=30)

    history_rows = ""
    for d in history:
        status_color = "#16a34a" if d.status == "USED" else "#2563eb"
        history_rows += f"""
        <tr>
            <td><strong>{d.target_date.strftime('%d %b %Y')}</strong></td>
            <td>{escape(d.network)}</td>
            <td>{escape(d.data_size or d.plan_name)}</td>
            <td style="font-weight:700;">₦{d.price:,.2f}</td>
            <td><span style="color:{status_color}; font-weight:700;">{escape(d.status)}</span></td>
            <td>{escape(format_admin_datetime(d.created_at))}</td>
            <td>
                <button type="button" onclick="navigator.clipboard.writeText(`{escape(d.full_caption)}`).then(()=>alert('Caption copied!'));" style="padding:4px 8px; font-size:11px;">Copy</button>
            </td>
        </tr>
        """

    deal_card_html = ""
    if today_deal:
        deal_card_html = f"""
        <div class="card" style="margin-bottom:28px; background:#ffffff; border:2px solid #ffc400; border-radius:14px; padding:24px;">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; margin-bottom:14px;">
                <div>
                    <span style="background:#ffc400; color:#090909; font-weight:800; font-size:11px; padding:3px 8px; border-radius:6px; text-transform:uppercase;">TODAY'S DEAL</span>
                    <h3 style="font-size:20px; font-weight:700; margin-top:6px; color:#090909;">{today_deal.network} {today_deal.data_size} ({today_deal.validity})</h3>
                </div>
                <div style="text-align:right;">
                    <div style="font-size:24px; font-weight:800; color:#16a34a;">₦{today_deal.price:,.2f}</div>
                    <small style="color:#626262;">Generated: {today_deal.target_date.strftime('%d %B %Y')}</small>
                </div>
            </div>

            <!-- Preformatted Caption Display -->
            <label style="font-size:11px; text-transform:uppercase; font-weight:700; color:#626262; display:block; margin-bottom:6px;">Generated Promotional Caption</label>
            <textarea id="today-deal-caption" readonly style="width:100%; min-height:160px; background:#fafaf9; border:1px solid #e4e4e0; border-radius:8px; padding:12px; font-size:13px; font-family:monospace; line-height:1.5; resize:vertical; margin-bottom:16px;">{escape(today_deal.full_caption)}</textarea>

            <!-- Action Buttons -->
            <div style="display:flex; flex-wrap:wrap; gap:10px;">
                <button type="button" id="copy-caption-btn" onclick="copyCaption()" style="background:#ffc400; color:#090909; padding:10px 18px; font-size:13px; font-weight:700; border-radius:8px; border:none; cursor:pointer;">
                    📋 COPY CAPTION
                </button>
                <form method="POST" action="/admin/marketing/daily-deals/regenerate" style="display:inline;">
                    <button type="submit" style="background:#090909; color:#ffffff; padding:10px 18px; font-size:13px; font-weight:600; border-radius:8px; border:none; cursor:pointer;">
                        🔄 REGENERATE
                    </button>
                </form>
                <button type="button" onclick="toggleEditModal()" style="background:#e4e4e0; color:#090909; padding:10px 18px; font-size:13px; font-weight:600; border-radius:8px; border:none; cursor:pointer;">
                    ✏️ EDIT
                </button>
                <form method="POST" action="/admin/marketing/daily-deals/mark-used/{today_deal.id}" style="display:inline;">
                    <button type="submit" style="background:{'#16a34a' if today_deal.status == 'USED' else '#2563eb'}; color:#ffffff; padding:10px 18px; font-size:13px; font-weight:600; border-radius:8px; border:none; cursor:pointer;">
                        {'✓ MARKED AS USED' if today_deal.status == 'USED' else '📌 MARK AS USED'}
                    </button>
                </form>
            </div>

            <!-- Edit Modal Form (Hidden by default) -->
            <div id="edit-deal-modal" style="display:none; margin-top:20px; padding:16px; background:#f5f5f2; border-radius:10px; border:1px solid #e4e4e0;">
                <h4 style="font-size:14px; font-weight:700; margin-bottom:10px;">Edit Promotional Caption</h4>
                <form method="POST" action="/admin/marketing/daily-deals/edit/{today_deal.id}">
                    <textarea name="full_caption" style="width:100%; min-height:140px; padding:10px; border-radius:8px; border:1px solid #ccc; font-family:monospace; font-size:13px; margin-bottom:10px;">{escape(today_deal.full_caption)}</textarea>
                    <button type="submit" style="background:#16a34a; color:#ffffff; padding:8px 16px; border-radius:6px; font-size:13px; font-weight:700;">Save Changes</button>
                    <button type="button" onclick="toggleEditModal()" style="background:#626262; color:#ffffff; padding:8px 14px; border-radius:6px; font-size:13px; margin-left:8px;">Cancel</button>
                </form>
            </div>
        </div>
        """
    else:
        deal_card_html = """
        <div class="card" style="margin-bottom:28px; text-align:center; padding:32px;">
            <p style="color:#626262; font-size:14px; margin-bottom:12px;">No deal has been generated for today yet.</p>
            <form method="POST" action="/admin/marketing/daily-deals/generate">
                <button type="submit" style="background:#ffc400; color:#090909; padding:10px 20px; font-weight:700; border-radius:8px;">Generate Today's Deal Now</button>
            </form>
        </div>
        """

    body = f"""
    {_marketing_subnav('daily_deals')}
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:18px; flex-wrap:wrap; gap:10px;">
        <h2 style="font-family:'Space Grotesk',sans-serif; font-size:22px; font-weight:700;">🏷️ Daily Data Deal Generator</h2>
        
        <!-- Manual Generation by Date -->
        <form method="POST" action="/admin/marketing/daily-deals/generate" style="display:flex; gap:8px;">
            <input type="date" name="target_date" value="{today.isoformat()}" style="padding:7px 10px; font-size:12px;">
            <button type="submit" style="padding:7px 14px; font-size:12px;">Generate for Date</button>
        </form>
    </div>

    {deal_card_html}

    <!-- Content History Table -->
    <h3 style="font-size:16px; font-weight:700; margin:32px 0 12px;">📜 Daily Deal Content History</h3>
    <table>
        <thead>
            <tr><th>Date</th><th>Network</th><th>Plan</th><th>Price</th><th>Status</th><th>Created</th><th>Action</th></tr>
        </thead>
        <tbody>
            {history_rows if history_rows else '<tr><td colspan="7" style="text-align:center;">No deal history recorded yet.</td></tr>'}
        </tbody>
    </table>

    <script>
        function copyCaption() {{
            const ta = document.getElementById("today-deal-caption");
            ta.select();
            ta.setSelectionRange(0, 99999);
            navigator.clipboard.writeText(ta.value).then(function() {{
                const btn = document.getElementById("copy-caption-btn");
                btn.textContent = "✓ COPIED!";
                setTimeout(() => btn.textContent = "📋 COPY CAPTION", 2500);
            }});
        }}

        function toggleEditModal() {{
            const m = document.getElementById("edit-deal-modal");
            m.style.display = m.style.display === "none" ? "block" : "none";
        }}
    </script>
    """
    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=body, active_page="marketing")


@marketing_admin_bp.route("/daily-deals/generate", methods=["POST"])
def generate_deal_route():
    require_admin_auth, record_admin_audit, _, _ = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    target_date_str = request.form.get("target_date")
    target_date = None
    if target_date_str:
        try:
            target_date = datetime.strptime(target_date_str, "%Y-%m-%d").date()
        except ValueError:
            target_date = get_lagos_today()

    deal, created = generate_daily_deal(target_date=target_date, force=True)
    record_admin_audit("admin", f"Generate Daily Deal for {target_date or 'today'}", success=bool(deal))
    return redirect(url_for("marketing_admin.daily_deals"))


@marketing_admin_bp.route("/daily-deals/regenerate", methods=["POST"])
def regenerate_deal_route():
    require_admin_auth, record_admin_audit, _, _ = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    today = get_lagos_today()
    deal, _ = generate_daily_deal(target_date=today, force=True)
    record_admin_audit("admin", "Regenerate Daily Deal", success=bool(deal))
    return redirect(url_for("marketing_admin.daily_deals"))


@marketing_admin_bp.route("/daily-deals/edit/<int:content_id>", methods=["POST"])
def edit_deal_route(content_id):
    require_admin_auth, record_admin_audit, _, _ = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    new_caption = request.form.get("full_caption", "").strip()
    success = update_deal_caption(content_id, new_caption)
    record_admin_audit("admin", f"Edit Daily Deal Caption {content_id}", success=success)
    return redirect(url_for("marketing_admin.daily_deals"))


@marketing_admin_bp.route("/daily-deals/mark-used/<int:content_id>", methods=["POST"])
def mark_deal_used_route(content_id):
    require_admin_auth, record_admin_audit, _, _ = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    success = mark_deal_used(content_id)
    record_admin_audit("admin", f"Mark Daily Deal Used {content_id}", success=success)
    return redirect(url_for("marketing_admin.daily_deals"))


# ----------------------------------------------------------------------
# 4. CUSTOMER RE-ENGAGEMENT
# ----------------------------------------------------------------------
@marketing_admin_bp.route("/reengagement")
def reengagement():
    require_admin_auth, _, _, ADMIN_BASE_TEMPLATE = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    active_segment = request.args.get("segment", "7_days")
    if active_segment not in ("7_days", "14_days", "30_days"):
        active_segment = "7_days"

    seg_data = scan_inactive_segments()
    counts = seg_data["counts"]
    target_customers = seg_data.get(active_segment, [])

    template_key = f"REENGAGEMENT_{active_segment.upper()}_TEMPLATE"
    current_template = get_marketing_setting(template_key, "")

    customer_rows = ""
    for c in target_customers:
        wa_badge = '<span style="color:#16a34a; font-weight:700;">WhatsApp ✓</span>' if c["whatsapp_linked"] else '<span style="color:#969696;">Web only</span>'
        email_badge = f'<code>{escape(c["email"])}</code>' if c["has_email"] else '<span style="color:#969696;">No email</span>'
        customer_rows += f"""
        <tr>
            <td style="width:30px;"><input type="checkbox" name="selected_users" value="{c['id']}" class="customer-checkbox" checked></td>
            <td><strong>{escape(c['name'])}</strong><br><small style="color:#626262;">{escape(c['phone'])}</small></td>
            <td>{email_badge}</td>
            <td>{wa_badge}</td>
            <td>{c['days_inactive']} days</td>
            <td>₦{c['wallet_balance']:,.2f}</td>
            <td>{c['tx_count']} ({'₦' + f"{c['total_spent']:,.2f}"})</td>
        </tr>
        """

    body = f"""
    {_marketing_subnav('reengagement')}
    <h2 style="font-family:'Space Grotesk',sans-serif; font-size:22px; font-weight:700; margin-bottom:18px;">🔁 Customer Re-engagement</h2>
    <p style="font-size:13px; color:#626262; margin-bottom:20px;">
        Segment inactive users based on actual transaction activity. Campaigns are <strong>queued in DRAFT</strong> for your approval before anything is sent.
    </p>

    <!-- Segment Counters -->
    <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap:16px; margin-bottom:24px;">
        <a href="/admin/marketing/reengagement?segment=7_days" style="text-decoration:none; color:inherit;">
            <div class="card" style="border:{'2px solid #ffc400' if active_segment == '7_days' else '1px solid #e4e4e0'};">
                <h3 style="color:#d97706;">7 DAYS INACTIVE</h3>
                <p>{counts['7_days']} customers</p>
                <small>No purchase in 7–13 days</small>
            </div>
        </a>
        <a href="/admin/marketing/reengagement?segment=14_days" style="text-decoration:none; color:inherit;">
            <div class="card" style="border:{'2px solid #ffc400' if active_segment == '14_days' else '1px solid #e4e4e0'};">
                <h3 style="color:#ea580c;">14 DAYS INACTIVE</h3>
                <p>{counts['14_days']} customers</p>
                <small>No purchase in 14–29 days</small>
            </div>
        </a>
        <a href="/admin/marketing/reengagement?segment=30_days" style="text-decoration:none; color:inherit;">
            <div class="card" style="border:{'2px solid #ffc400' if active_segment == '30_days' else '1px solid #e4e4e0'};">
                <h3 style="color:#dc2626;">30 DAYS INACTIVE</h3>
                <p>{counts['30_days']} customers</p>
                <small>30+ days inactive or dormant</small>
            </div>
        </a>
    </div>

    <!-- Campaign Creator Form -->
    <div class="card" style="margin-bottom:28px; background:#ffffff; border-radius:14px; padding:22px;">
        <h3 style="font-size:16px; font-weight:700; margin-bottom:14px; color:#090909;">Create Campaign for {active_segment.replace('_', ' ').title()}</h3>
        <form method="POST" action="/admin/marketing/reengagement/create-campaign" id="campaign-form">
            <input type="hidden" name="segment" value="{active_segment}">
            
            <div style="display:flex; flex-wrap:wrap; gap:16px; margin-bottom:16px;">
                <div style="flex:1; min-width:240px;">
                    <label style="display:block; font-size:12px; font-weight:700; margin-bottom:6px; text-transform:uppercase;">Dispatch Channel</label>
                    <select name="channel" style="width:100%;">
                        <option value="BOTH">Both Email & WhatsApp (Recommended)</option>
                        <option value="EMAIL">Email Only</option>
                        <option value="WHATSAPP">WhatsApp Only (Linked accounts)</option>
                    </select>
                </div>
            </div>

            <div style="margin-bottom:16px;">
                <label style="display:block; font-size:12px; font-weight:700; margin-bottom:6px; text-transform:uppercase;">Message Template</label>
                <textarea name="template" id="template-editor" style="width:100%; min-height:120px; font-family:monospace; font-size:13px; line-height:1.5; padding:10px; border-radius:8px; border:1px solid #e4e4e0;">{escape(current_template)}</textarea>
                <small style="color:#626262; font-size:11px;">Supported tags: <code>{{name}}</code>, <code>{{preferred_network}}</code>, <code>{{preferred_service}}</code>, <code>{{website_url}}</code></small>
            </div>

            <!-- Customer Selection Table -->
            <div style="display:flex; justify-content:space-between; align-items:center; margin:18px 0 10px;">
                <h4 style="font-size:14px; font-weight:700;">Select Customers ({len(target_customers)} eligible)</h4>
                <div style="font-size:12px;">
                    <button type="button" onclick="selectAll(true)" style="background:transparent; color:#2563eb; border:none; padding:0; cursor:pointer;">Select All</button> |
                    <button type="button" onclick="selectAll(false)" style="background:transparent; color:#2563eb; border:none; padding:0; cursor:pointer;">Deselect All</button>
                </div>
            </div>

            <div style="max-height:350px; overflow-y:auto; border:1px solid #e4e4e0; border-radius:8px; margin-bottom:18px;">
                <table style="margin:0; box-shadow:none;">
                    <thead>
                        <tr><th></th><th>Customer</th><th>Email</th><th>WhatsApp</th><th>Inactive</th><th>Wallet</th><th>History</th></tr>
                    </thead>
                    <tbody>
                        {customer_rows if customer_rows else '<tr><td colspan="7" style="text-align:center; padding:20px;">No customers found in this segment.</td></tr>'}
                    </tbody>
                </table>
            </div>

            <button type="submit" style="background:#ffc400; color:#090909; font-weight:700; padding:12px 24px; border-radius:8px; font-size:14px;">
                Queue Campaign in Draft (Requires Approval)
            </button>
        </form>
    </div>

    <script>
        function selectAll(checked) {{
            document.querySelectorAll(".customer-checkbox").forEach(cb => cb.checked = checked);
        }}
    </script>
    """
    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=body, active_page="marketing")


@marketing_admin_bp.route("/reengagement/create-campaign", methods=["POST"])
def create_campaign_route():
    require_admin_auth, record_admin_audit, _, _ = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    segment = request.form.get("segment", "7_days")
    channel = request.form.get("channel", "BOTH")
    custom_template = request.form.get("template", "").strip()
    selected_users = request.form.getlist("selected_users")

    campaign, msg = create_reengagement_campaign(
        segment_key=segment,
        selected_user_ids=selected_users,
        channel=channel,
        custom_template=custom_template,
    )

    record_admin_audit("admin", f"Create Campaign {segment}", success=bool(campaign))
    return redirect(url_for("marketing_admin.campaigns"))


# ----------------------------------------------------------------------
# 5. CAMPAIGN QUEUE & DISPATCH
# ----------------------------------------------------------------------
@marketing_admin_bp.route("/campaigns")
def campaigns():
    require_admin_auth, _, format_admin_datetime, ADMIN_BASE_TEMPLATE = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    all_campaigns = MarketingCampaign.query.order_by(MarketingCampaign.created_at.desc()).all()

    campaign_rows = ""
    for c in all_campaigns:
        status_badge = {
            "DRAFT": '<span style="background:#fef3c7; color:#92400e; padding:3px 8px; border-radius:6px; font-weight:700; font-size:11px;">DRAFT</span>',
            "APPROVED": '<span style="background:#dbeafe; color:#1e40af; padding:3px 8px; border-radius:6px; font-weight:700; font-size:11px;">APPROVED</span>',
            "COMPLETED": '<span style="background:#dcfce7; color:#166534; padding:3px 8px; border-radius:6px; font-weight:700; font-size:11px;">COMPLETED</span>',
            "CANCELLED": '<span style="background:#fee2e2; color:#991b1b; padding:3px 8px; border-radius:6px; font-weight:700; font-size:11px;">CANCELLED</span>',
        }.get(c.status, c.status)

        actions = ""
        if c.status == "DRAFT":
            actions += f"""
            <form method="POST" action="/admin/marketing/campaigns/{c.id}/approve" style="display:inline;">
                <button type="submit" style="padding:6px 12px; font-size:11px; background:#2563eb; color:#ffffff;">Approve</button>
            </form>
            <form method="POST" action="/admin/marketing/campaigns/{c.id}/cancel" style="display:inline;">
                <button type="submit" style="padding:6px 10px; font-size:11px; background:#dc2626; color:#ffffff;">Cancel</button>
            </form>
            """
        elif c.status in ("APPROVED", "QUEUED"):
            actions += f"""
            <form method="POST" action="/admin/marketing/campaigns/{c.id}/send" style="display:inline;">
                <button type="submit" style="padding:6px 12px; font-size:11px; background:#16a34a; color:#ffffff;">Send Messages</button>
            </form>
            <form method="POST" action="/admin/marketing/campaigns/{c.id}/cancel" style="display:inline;">
                <button type="submit" style="padding:6px 10px; font-size:11px; background:#dc2626; color:#ffffff;">Cancel</button>
            </form>
            """

        campaign_rows += f"""
        <tr>
            <td><strong>{escape(c.title)}</strong><br><small style="color:#626262;">Channel: {escape(c.channel)} • Segment: {escape(c.segment)}</small></td>
            <td>{status_badge}</td>
            <td>{c.total_recipients}</td>
            <td><span style="color:#16a34a; font-weight:700;">{c.sent_count}</span></td>
            <td><span style="color:#dc2626;">{c.failed_count}</span></td>
            <td>{escape(format_admin_datetime(c.created_at))}</td>
            <td style="white-space:nowrap;">{actions}</td>
        </tr>
        """

    body = f"""
    {_marketing_subnav('campaigns')}
    <h2 style="font-family:'Space Grotesk',sans-serif; font-size:22px; font-weight:700; margin-bottom:18px;">📬 Marketing Campaign Queue</h2>

    <table>
        <thead>
            <tr><th>Campaign</th><th>Status</th><th>Queued</th><th>Sent</th><th>Failed</th><th>Created</th><th>Actions</th></tr>
        </thead>
        <tbody>
            {campaign_rows if campaign_rows else '<tr><td colspan="7" style="text-align:center; padding:24px;">No marketing campaigns in the queue yet.</td></tr>'}
        </tbody>
    </table>
    """
    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=body, active_page="marketing")


@marketing_admin_bp.route("/campaigns/<int:campaign_id>/approve", methods=["POST"])
def approve_campaign_route(campaign_id):
    require_admin_auth, record_admin_audit, _, _ = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    success, msg = approve_campaign(campaign_id)
    record_admin_audit("admin", f"Approve Campaign {campaign_id}", success=success)
    return redirect(url_for("marketing_admin.campaigns"))


@marketing_admin_bp.route("/campaigns/<int:campaign_id>/send", methods=["POST"])
def send_campaign_route(campaign_id):
    require_admin_auth, record_admin_audit, _, _ = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    success, msg = dispatch_campaign_messages(campaign_id)
    record_admin_audit("admin", f"Send Campaign {campaign_id}", success=success)
    return redirect(url_for("marketing_admin.campaigns"))


@marketing_admin_bp.route("/campaigns/<int:campaign_id>/cancel", methods=["POST"])
def cancel_campaign_route(campaign_id):
    require_admin_auth, record_admin_audit, _, _ = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    success, msg = cancel_campaign(campaign_id)
    record_admin_audit("admin", f"Cancel Campaign {campaign_id}", success=success)
    return redirect(url_for("marketing_admin.campaigns"))


# ----------------------------------------------------------------------
# 6. MARKETING SETTINGS
# ----------------------------------------------------------------------
@marketing_admin_bp.route("/settings", methods=["GET", "POST"])
def settings():
    require_admin_auth, record_admin_audit, _, ADMIN_BASE_TEMPLATE = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    if request.method == "POST":
        editable_fields = [
            "REFERRAL_ENABLED",
            "REFERRAL_REWARD_AMOUNT",
            "REFERRAL_MIN_TRANSACTION",
            "REFERRAL_REWARD_ON_FIRST_ONLY",
            "REFERRAL_MAX_REWARDS_PER_REFERRER",
            "DAILY_DEAL_ENABLED",
            "DAILY_DEAL_GENERATION_TIME",
            "REENGAGEMENT_7_DAYS_ENABLED",
            "REENGAGEMENT_14_DAYS_ENABLED",
            "REENGAGEMENT_30_DAYS_ENABLED",
            "REENGAGEMENT_7_DAYS_TEMPLATE",
            "REENGAGEMENT_14_DAYS_TEMPLATE",
            "REENGAGEMENT_30_DAYS_TEMPLATE",
        ]
        for field in editable_fields:
            val = request.form.get(field)
            if val is not None:
                set_marketing_setting(field, val)
        record_admin_audit("admin", "Update Marketing Settings", success=True)
        return redirect(url_for("marketing_admin.settings"))

    all_settings = get_all_marketing_settings()

    def _val(k, default=""):
        return all_settings.get(k, {}).get("value", default)

    body = f"""
    {_marketing_subnav('settings')}
    <h2 style="font-family:'Space Grotesk',sans-serif; font-size:22px; font-weight:700; margin-bottom:18px;">⚙️ Marketing Automation Settings</h2>

    <form method="POST" action="/admin/marketing/settings">
        <!-- Referral Settings -->
        <div class="card" style="margin-bottom:24px; padding:24px;">
            <h3 style="font-size:16px; font-weight:700; margin-bottom:14px; color:#090909;">🤝 Referral Program Rules</h3>
            
            <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap:16px; margin-bottom:16px;">
                <div>
                    <label style="display:block; font-size:12px; font-weight:700; margin-bottom:6px;">Referral Program Status</label>
                    <select name="REFERRAL_ENABLED">
                        <option value="true" {'selected' if _val('REFERRAL_ENABLED') == 'true' else ''}>Enabled</option>
                        <option value="false" {'selected' if _val('REFERRAL_ENABLED') == 'false' else ''}>Disabled</option>
                    </select>
                </div>
                <div>
                    <label style="display:block; font-size:12px; font-weight:700; margin-bottom:6px;">Reward Amount (NGN)</label>
                    <input type="number" step="0.01" name="REFERRAL_REWARD_AMOUNT" value="{escape(_val('REFERRAL_REWARD_AMOUNT', '100.00'))}" required>
                </div>
                <div>
                    <label style="display:block; font-size:12px; font-weight:700; margin-bottom:6px;">Min. Qualifying Transaction (NGN)</label>
                    <input type="number" step="0.01" name="REFERRAL_MIN_TRANSACTION" value="{escape(_val('REFERRAL_MIN_TRANSACTION', '500.00'))}" required>
                </div>
                <div>
                    <label style="display:block; font-size:12px; font-weight:700; margin-bottom:6px;">Max Rewards per Referrer (0 = unlimited)</label>
                    <input type="number" name="REFERRAL_MAX_REWARDS_PER_REFERRER" value="{escape(_val('REFERRAL_MAX_REWARDS_PER_REFERRER', '0'))}" required>
                </div>
            </div>
        </div>

        <!-- Daily Deal Settings -->
        <div class="card" style="margin-bottom:24px; padding:24px;">
            <h3 style="font-size:16px; font-weight:700; margin-bottom:14px; color:#090909;">🏷️ Daily Data Deal Configuration</h3>
            <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap:16px;">
                <div>
                    <label style="display:block; font-size:12px; font-weight:700; margin-bottom:6px;">Daily Deals Generator</label>
                    <select name="DAILY_DEAL_ENABLED">
                        <option value="true" {'selected' if _val('DAILY_DEAL_ENABLED') == 'true' else ''}>Enabled</option>
                        <option value="false" {'selected' if _val('DAILY_DEAL_ENABLED') == 'false' else ''}>Disabled</option>
                    </select>
                </div>
                <div>
                    <label style="display:block; font-size:12px; font-weight:700; margin-bottom:6px;">Generation Time (Africa/Lagos)</label>
                    <input type="time" name="DAILY_DEAL_GENERATION_TIME" value="{escape(_val('DAILY_DEAL_GENERATION_TIME', '08:00'))}">
                </div>
            </div>
        </div>

        <!-- Re-engagement Templates -->
        <div class="card" style="margin-bottom:24px; padding:24px;">
            <h3 style="font-size:16px; font-weight:700; margin-bottom:14px; color:#090909;">🔁 Re-engagement Message Templates</h3>
            <p style="font-size:12px; color:#626262; margin-bottom:14px;">Available tags: <code>{{name}}</code>, <code>{{preferred_network}}</code>, <code>{{preferred_service}}</code>, <code>{{website_url}}</code></p>

            <div style="margin-bottom:16px;">
                <label style="display:block; font-size:12px; font-weight:700; margin-bottom:6px;">7-Day Inactivity Template</label>
                <textarea name="REENGAGEMENT_7_DAYS_TEMPLATE" style="width:100%; min-height:85px; font-family:monospace; font-size:13px; padding:8px; border-radius:6px; border:1px solid #e4e4e0;">{escape(_val('REENGAGEMENT_7_DAYS_TEMPLATE'))}</textarea>
            </div>

            <div style="margin-bottom:16px;">
                <label style="display:block; font-size:12px; font-weight:700; margin-bottom:6px;">14-Day Inactivity Template</label>
                <textarea name="REENGAGEMENT_14_DAYS_TEMPLATE" style="width:100%; min-height:85px; font-family:monospace; font-size:13px; padding:8px; border-radius:6px; border:1px solid #e4e4e0;">{escape(_val('REENGAGEMENT_14_DAYS_TEMPLATE'))}</textarea>
            </div>

            <div style="margin-bottom:16px;">
                <label style="display:block; font-size:12px; font-weight:700; margin-bottom:6px;">30-Day Inactivity Template</label>
                <textarea name="REENGAGEMENT_30_DAYS_TEMPLATE" style="width:100%; min-height:85px; font-family:monospace; font-size:13px; padding:8px; border-radius:6px; border:1px solid #e4e4e0;">{escape(_val('REENGAGEMENT_30_DAYS_TEMPLATE'))}</textarea>
            </div>
        </div>

        <button type="submit" style="background:#ffc400; color:#090909; font-weight:700; padding:12px 24px; border-radius:8px; font-size:14px;">
            Save Marketing Settings
        </button>
    </form>
    """
    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=body, active_page="marketing")
