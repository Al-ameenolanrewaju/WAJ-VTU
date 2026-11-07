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
        ("social", "📢 Social Studio", "/admin/marketing/social"),
        ("reengagement", "🔁 Re-engagement", "/admin/marketing/reengagement"),
        ("campaigns", "📬 Campaigns", "/admin/marketing/campaigns"),
        ("push", "🔔 Web Push", "/admin/marketing/push"),
        ("settings", "⚙️ Settings", "/admin/marketing/settings"),
    ]
    html = '<div class="admin-subnav-bar">'
    for key, label, url in tabs:
        active_class = "active" if active_subpage == key else ""
        html += f'<a href="{url}" class="admin-subnav-item {active_class}">{label}</a>'
    html += '</div>'
    return html


def _render_attribution_dashboard(attribution_data):
    summary = attribution_data["summary"]
    channels = attribution_data["channels"]

    ch_cards = ""
    for ch in channels:
        ch_cards += f"""
        <div style="background:{ch['bg']}; border:1px solid {ch['border']}; border-radius:12px; padding:16px;">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
                <div style="display:flex; align-items:center; gap:8px;">
                    <span style="font-size:20px;">{ch['icon']}</span>
                    <strong style="font-size:13px; color:#09090b;">{ch['name']}</strong>
                </div>
                <span style="font-size:11px; font-weight:700; color:{ch['color']}; background:#ffffff; padding:2px 8px; border-radius:12px; border:1px solid {ch['border']};">
                    {ch['conversion_rate']}% conv
                </span>
            </div>
            <div style="display:grid; grid-template-columns:1fr 1fr; gap:8px; font-size:12px;">
                <div>
                    <span style="color:#71717a;">Clicks:</span>
                    <div style="font-weight:800; font-size:16px; color:#09090b;">{ch['clicks']}</div>
                </div>
                <div>
                    <span style="color:#71717a;">Signups:</span>
                    <div style="font-weight:800; font-size:16px; color:#16a34a;">{ch['signups']}</div>
                </div>
                <div>
                    <span style="color:#71717a;">Orders:</span>
                    <div style="font-weight:700; color:#09090b;">{ch['orders']}</div>
                </div>
                <div>
                    <span style="color:#71717a;">Revenue:</span>
                    <div style="font-weight:800; color:#16a34a;">₦{ch['revenue']:,.0f}</div>
                </div>
            </div>
        </div>
        """

    return f"""
    <div class="card" style="margin-top:24px; background:#ffffff; border:1px solid #e4e4e7; border-radius:14px; padding:22px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px; margin-bottom:16px;">
            <div>
                <h3 style="font-size:17px; font-weight:800; color:#09090b; margin:0 0 4px 0;">🎯 Social Traffic & Revenue Attribution</h3>
                <p style="font-size:13px; color:#71717a; margin:0;">
                    Live conversion and sales performance tracked from your WhatsApp, Twitter, Instagram, and Facebook links.
                </p>
            </div>
            <div style="display:flex; gap:12px; font-size:12px;">
                <span style="background:#f4f4f5; padding:6px 12px; border-radius:8px; font-weight:700; color:#09090b;">
                    🏆 Top Channel: <strong style="color:#16a34a;">{summary['top_channel']}</strong>
                </span>
            </div>
        </div>

        <!-- 4 Summary Stat Pills -->
        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:12px; margin-bottom:18px;">
            <div style="background:#fafaf9; border:1px solid #e4e4e7; border-radius:10px; padding:12px;">
                <span style="font-size:11px; text-transform:uppercase; color:#71717a; font-weight:700;">Tracked Social Clicks</span>
                <div style="font-size:20px; font-weight:800; color:#09090b; margin-top:2px;">{summary['total_clicks']}</div>
            </div>
            <div style="background:#f0fdf4; border:1px solid #bbf7d0; border-radius:10px; padding:12px;">
                <span style="font-size:11px; text-transform:uppercase; color:#15803d; font-weight:700;">Attributed Signups</span>
                <div style="font-size:20px; font-weight:800; color:#16a34a; margin-top:2px;">{summary['total_signups']}</div>
            </div>
            <div style="background:#eff6ff; border:1px solid #bfdbfe; border-radius:10px; padding:12px;">
                <span style="font-size:11px; text-transform:uppercase; color:#1d4ed8; font-weight:700;">Attributed Orders</span>
                <div style="font-size:20px; font-weight:800; color:#2563eb; margin-top:2px;">{summary['total_orders']}</div>
            </div>
            <div style="background:#fefce8; border:1px solid #fef08a; border-radius:10px; padding:12px;">
                <span style="font-size:11px; text-transform:uppercase; color:#a16207; font-weight:700;">Total Social Revenue</span>
                <div style="font-size:20px; font-weight:800; color:#ca8a04; margin-top:2px;">₦{summary['total_revenue']:,.2f}</div>
            </div>
        </div>

        <!-- Platforms Grid -->
        <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:12px;">
            {ch_cards}
        </div>
    </div>
    """


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

    from services.attribution_service import get_social_attribution_metrics
    attribution_data = get_social_attribution_metrics()
    attribution_html = _render_attribution_dashboard(attribution_data)
    summary = attribution_data["summary"]

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
        <div class="card">
            <h3>🎯 Social Signups</h3>
            <p style="color:#db2777;">{summary['total_signups']}</p>
            <small>Attributed from campaigns</small>
        </div>
        <div class="card">
            <h3>📊 Social Revenue</h3>
            <p style="color:#ca8a04;">₦{summary['total_revenue']:,.0f}</p>
            <small>From attributed users</small>
        </div>
    </div>

    {attribution_html}

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
                <a href="/admin/marketing/daily-deals/poster/{today_deal.id}" target="_blank" download="waj-vtu-{today_deal.network.lower()}-promo.png" style="background:#10b981; color:#ffffff; padding:10px 18px; font-size:13px; font-weight:700; border-radius:8px; text-decoration:none; display:inline-flex; align-items:center; gap:6px;">
                    🎨 DOWNLOAD STATUS POSTER
                </a>
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
            "DAILY_PUSH_ENABLED",
            "LINKEDIN_AUTO_POST_ENABLED",
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

        <!-- Automated Distribution & Social Channels -->
        <div class="card" style="margin-bottom:24px; padding:24px;">
            <h3 style="font-size:16px; font-weight:700; margin-bottom:14px; color:#090909;">🚀 Automated Daily Deal Distribution (8:00 AM Lagos)</h3>
            <p style="font-size:12px; color:#626262; margin-bottom:16px;">
                Every morning at 8:00 AM (Africa/Lagos), WAJ VTU automatically broadcasts today's data deal to active users and social channels.
            </p>
            <div style="display:grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap:16px;">
                <div style="background:#f9fafb; border:1px solid #e5e7eb; border-radius:8px; padding:16px;">
                    <label style="display:block; font-size:13px; font-weight:700; margin-bottom:6px;">🔔 Web Push & In-App Notification</label>
                    <p style="font-size:11px; color:#6b7280; margin-bottom:10px;">Broadcast today's deal to all subscribed browsers and in-app bell.</p>
                    <select name="DAILY_PUSH_ENABLED">
                        <option value="true" {'selected' if _val('DAILY_PUSH_ENABLED', 'true') == 'true' else ''}>Enabled</option>
                        <option value="false" {'selected' if _val('DAILY_PUSH_ENABLED', 'true') == 'false' else ''}>Disabled</option>
                    </select>
                </div>
                <div style="background:#f0f9ff; border:1px solid #bae6fd; border-radius:8px; padding:16px;">
                    <label style="display:block; font-size:13px; font-weight:700; color:#0369a1; margin-bottom:6px;">💼 LinkedIn Automated Post</label>
                    <p style="font-size:11px; color:#0284c7; margin-bottom:10px;">Auto-post today's deal copy & flyer to your connected LinkedIn profile.</p>
                    <select name="LINKEDIN_AUTO_POST_ENABLED">
                        <option value="true" {'selected' if _val('LINKEDIN_AUTO_POST_ENABLED', 'false') == 'true' else ''}>Enabled</option>
                        <option value="false" {'selected' if _val('LINKEDIN_AUTO_POST_ENABLED', 'false') == 'false' else ''}>Disabled</option>
                    </select>
                    <div style="margin-top:10px;">
                        <a href="/admin/marketing/social#linkedin-section" style="font-size:11px; font-weight:700; color:#0284c7; text-decoration:none;">View LinkedIn Status in Social Studio →</a>
                    </div>
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


# ----------------------------------------------------------------------
# 6. WEB PUSH NOTIFICATIONS
# ----------------------------------------------------------------------
@marketing_admin_bp.route("/push")
def push_dashboard():
    require_admin_auth, _, format_admin_datetime, ADMIN_BASE_TEMPLATE = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    from models import PushSubscription
    total_subscribers = PushSubscription.query.count()
    user_subscribers = PushSubscription.query.filter(PushSubscription.user_id.isnot(None)).count()
    guest_subscribers = PushSubscription.query.filter(PushSubscription.user_id.is_(None)).count()

    recent_subs = PushSubscription.query.order_by(PushSubscription.created_at.desc()).limit(15).all()
    sub_rows = ""
    for s in recent_subs:
        if s.user_id:
            user_label = f'<span style="background:#e0e7ff; color:#3730a3; padding:2px 8px; border-radius:12px; font-size:11px; font-weight:600;">👤 User #{s.user_id}</span>'
        else:
            user_label = '<span style="background:#f1f5f9; color:#475569; padding:2px 8px; border-radius:12px; font-size:11px; font-weight:600;">👁️ Guest Visitor</span>'

        ua_clean = (s.user_agent or "Unknown Browser")
        if len(ua_clean) > 60:
            ua_clean = ua_clean[:57] + "..."

        sub_rows += f"""
        <tr>
            <td style="font-size:12px; white-space:nowrap;">{escape(format_admin_datetime(s.created_at))}</td>
            <td>{user_label}</td>
            <td style="font-size:12px; color:#475569; font-family:monospace;">{escape(ua_clean)}</td>
            <td style="font-size:11px; color:#94a3b8; font-family:monospace;">{escape(s.endpoint[:45])}...</td>
        </tr>
        """

    body = f"""
    {_marketing_subnav('push')}
    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:18px;">
        <div>
            <h2 style="font-family:'Space Grotesk',sans-serif; font-size:22px; font-weight:700; margin-bottom:4px;">🔔 Web Push Notifications</h2>
            <p style="font-size:13px; color:#626262;">Broadcast free, instant alerts directly to customer Android, Windows, and Mac screens.</p>
        </div>
    </div>

    <!-- Device Activation & Live Test Card -->
    <div class="card" style="margin-bottom:20px; background:#eff6ff; border:1px solid #bfdbfe; border-radius:12px; padding:18px 22px;">
        <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:14px;">
            <div style="flex:1; min-width:260px;">
                <div style="font-weight:700; font-size:14px; color:#1e3a8a; margin-bottom:4px;">📱 Test Push Notifications On This Current Device</div>
                <div style="font-size:12px; color:#3b82f6; line-height:1.4;">Click to subscribe this browser. The subscriber count below will immediately increase to 1, and you can test receiving broadcasts.</div>
            </div>
            <div style="display:flex; align-items:center; gap:12px; flex-wrap:wrap;">
                <button type="button" id="admin-test-sub-btn" onclick="subscribeCurrentAdminDevice()" style="background:#2563eb; color:#ffffff; font-weight:700; border:none; padding:10px 20px; border-radius:8px; font-size:13px; cursor:pointer;">
                    🔔 Subscribe This Device
                </button>
                <span id="admin-sub-status" style="font-size:12px; font-weight:600; color:#1e40af;"></span>
            </div>
        </div>
    </div>

    <script>
    async function subscribeCurrentAdminDevice() {{
        const btn = document.getElementById('admin-test-sub-btn');
        const status = document.getElementById('admin-sub-status');
        btn.disabled = true;
        btn.textContent = 'Enabling...';
        status.style.color = '#1e40af';
        status.textContent = 'Requesting browser permission...';

        try {{
            if (!('serviceWorker' in navigator) || !('PushManager' in window)) {{
                throw new Error('This browser does not support Web Push notifications.');
            }}

            const perm = await Notification.requestPermission();
            if (perm !== 'granted') {{
                throw new Error('Permission ' + perm + '. Click the lock icon 🔒 next to the website address to Allow notifications.');
            }}

            status.textContent = 'Registering service worker...';
            const reg = await navigator.serviceWorker.register('/service-worker.js', {{ scope: '/' }});
            await navigator.serviceWorker.ready;

            status.textContent = 'Fetching VAPID key...';
            const keyResp = await fetch('/api/push/vapid-public-key');
            const keyData = await keyResp.json();
            const vapidPublicKey = keyData.publicKey;
            if (!vapidPublicKey) throw new Error('VAPID public key could not be retrieved from server.');

            status.textContent = 'Creating device subscription...';
            const padding = '='.repeat((4 - (vapidPublicKey.length % 4)) % 4);
            const base64 = (vapidPublicKey + padding).split('-').join('+').split('_').join('/');
            const rawData = window.atob(base64);
            const keyArr = new Uint8Array(rawData.length);
            for (let i = 0; i < rawData.length; ++i) keyArr[i] = rawData.charCodeAt(i);

            const sub = await reg.pushManager.subscribe({{
                userVisibleOnly: true,
                applicationServerKey: keyArr
            }});

            const rawJson = sub.toJSON ? sub.toJSON() : {{}};
            const p256dhKey = sub.getKey ? sub.getKey('p256dh') : null;
            const authKey = sub.getKey ? sub.getKey('auth') : null;

            function bufToBase64(b) {{
                let bin = '';
                const bytes = new Uint8Array(b);
                for (let i = 0; i < bytes.byteLength; i++) bin += String.fromCharCode(bytes[i]);
                return window.btoa(bin).replaceAll('+', '-').replaceAll('/', '_').replaceAll('=', '');
            }}

            const payload = {{
                endpoint: sub.endpoint,
                keys: {{
                    p256dh: (rawJson.keys && rawJson.keys.p256dh) || (p256dhKey ? bufToBase64(p256dhKey) : ''),
                    auth: (rawJson.keys && rawJson.keys.auth) || (authKey ? bufToBase64(authKey) : '')
                }}
            }};

            status.textContent = 'Saving subscription on server...';
            const postResp = await fetch('/api/push/subscribe', {{
                method: 'POST',
                headers: {{ 'Content-Type': 'application/json' }},
                body: JSON.stringify(payload)
            }});

            const resJson = await postResp.json();
            if (!postResp.ok) throw new Error(resJson.message || 'Server returned error');

            status.style.color = '#16a34a';
            status.textContent = '✅ Subscribed successfully! Refreshing...';
            btn.textContent = 'Subscribed! 🔔';
            setTimeout(() => {{ window.location.reload(); }}, 1200);
        }} catch (err) {{
            btn.disabled = false;
            btn.textContent = 'Retry Subscribe';
            status.style.color = '#dc2626';
            status.textContent = '❌ ' + (err.message || err);
        }}
    }}
    </script>

        <!-- Subscriber Metric Cards -->
    <div class="card-grid" style="grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); margin-bottom:24px;">
        <div class="card">
            <h3>🔔 Total Subscribers</h3>
            <p style="color:#2563eb;">{total_subscribers:,}</p>
            <small>Active device push endpoints</small>
        </div>
        <div class="card">
            <h3>👤 Registered Customers</h3>
            <p style="color:#16a34a;">{user_subscribers:,}</p>
            <small>Linked to account wallets</small>
        </div>
        <div class="card">
            <h3>👁️ Guest Subscribers</h3>
            <p style="color:#d97706;">{guest_subscribers:,}</p>
            <small>Subscribed before creating account</small>
        </div>
    </div>


    <!-- Automated Daily Deal Push Engine -->
    <div class="card" style="margin-bottom:28px; background:#ffffff; border-radius:14px; padding:22px 24px; border-left:4px solid #10b981; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:16px;">
        <div style="flex:1; min-width:280px;">
            <div style="display:flex; align-items:center; gap:8px; margin-bottom:4px;">
                <span style="font-size:18px;">⚡</span>
                <h3 style="font-size:16px; font-weight:700; color:#090909; margin:0;">Automated Daily Deal Push Broadcast</h3>
            </div>
            <p style="font-size:13px; color:#64748b; margin:0; line-height:1.4;">The automated background engine generates today's hottest data deal and broadcasts it to all active subscriber devices every morning. You can also trigger today's broadcast manually right now.</p>
        </div>
        <form method="POST" action="/admin/marketing/push/daily-deal" style="margin:0;">
            <button type="submit" onclick="return confirm('Auto-generate today\'s deal and broadcast to all active devices now?');" style="background:#10b981; color:#ffffff; font-weight:700; padding:12px 22px; border-radius:8px; font-size:13px; border:none; cursor:pointer; white-space:nowrap; display:flex; align-items:center; gap:6px;">
                <span>🚀</span> Send Today's Deal Now
            </button>
        </form>
    </div>

        <!-- Broadcast Composer Form -->
    <div class="card" style="margin-bottom:28px; background:#ffffff; border-radius:14px; padding:24px; border-left:4px solid #ffc400;">
        <h3 style="font-size:17px; font-weight:700; margin-bottom:6px; color:#090909;">🚀 Send Instant Push Broadcast</h3>
        <p style="font-size:13px; color:#64748b; margin-bottom:18px;">Sends an immediate notification to all <strong>{total_subscribers:,}</strong> subscribed devices.</p>

        <form method="POST" action="/admin/marketing/push/broadcast">
            <div style="margin-bottom:16px;">
                <label style="display:block; font-size:12px; font-weight:700; margin-bottom:6px; text-transform:uppercase;">Notification Title</label>
                <input type="text" name="title" required placeholder="e.g. 🔥 1GB MTN for ₦280 - Flash Sale Today!" style="width:100%; padding:10px 12px; font-size:14px; border-radius:8px; border:1px solid #cbd5e1;">
            </div>

            <div style="margin-bottom:16px;">
                <label style="display:block; font-size:12px; font-weight:700; margin-bottom:6px; text-transform:uppercase;">Notification Body</label>
                <textarea name="body" required rows="3" placeholder="e.g. Data prices just crashed! Tap to top up your line instantly on WAJ VTU." style="width:100%; padding:10px 12px; font-size:13px; border-radius:8px; border:1px solid #cbd5e1;"></textarea>
            </div>

            <div style="margin-bottom:20px;">
                <label style="display:block; font-size:12px; font-weight:700; margin-bottom:6px; text-transform:uppercase;">Click Target Link (URL)</label>
                <input type="text" name="target_url" value="/buy/data" placeholder="/buy/data or /" style="width:100%; padding:10px 12px; font-size:14px; border-radius:8px; border:1px solid #cbd5e1;">
                <small style="color:#64748b; font-size:11px;">Where the user lands when they tap the notification (e.g. /buy/data, /, or https://...)</small>
            </div>

            <button type="submit" onclick="return confirm('Broadcast this notification to all active subscribers now?');" style="background:#ffc400; color:#090909; font-weight:700; padding:12px 28px; border-radius:8px; font-size:14px; border:none; cursor:pointer;">
                🚀 Broadcast Notification to All Devices
            </button>
        </form>
    </div>

    <!-- Recent Subscribers -->
    <div class="card" style="padding:22px;">
        <h3 style="font-size:16px; font-weight:700; margin-bottom:14px;">🕒 Recent Push Device Subscriptions (Last 15)</h3>
        <table>
            <thead>
                <tr><th>Subscribed</th><th>User Type</th><th>Browser / OS</th><th>Endpoint</th></tr>
            </thead>
            <tbody>
                {sub_rows if sub_rows else '<tr><td colspan="4" style="text-align:center; padding:24px; color:#94a3b8;">No push subscriptions recorded yet.</td></tr>'}
            </tbody>
        </table>
    </div>
    """
    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=body, active_page="marketing")


@marketing_admin_bp.route("/push/broadcast", methods=["POST"])
def push_broadcast_route():
    require_admin_auth, record_admin_audit, _, _ = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    from services.push_service import broadcast_push
    from flask import flash, redirect, url_for

    title = (request.form.get("title") or "").strip()
    body_text = (request.form.get("body") or "").strip()
    target_url = (request.form.get("target_url") or request.form.get("url") or "/buy/data").strip()

    if not title or not body_text:
        flash("Title and body are required for push notification.", "error")
        return redirect(url_for("marketing_admin.push_dashboard"))

    result = broadcast_push(title=title, body=body_text, url=target_url)
    record_admin_audit("admin", f"Broadcast Push: {title[:40]}", success=True)
    flash(f"Push notification sent to {result['sent']} devices ({result['failed']} failed).", "success")
    return redirect(url_for("marketing_admin.push_dashboard"))

@marketing_admin_bp.route("/push/daily-deal", methods=["POST"])
def push_daily_deal_route():
    require_admin_auth, record_admin_audit, _, _ = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    from services.push_service import send_daily_deal_push
    from flask import flash, redirect, url_for

    res = send_daily_deal_push(force=True)
    if res.get("status") == "success":
        record_admin_audit("admin", f"Manual Trigger Daily Deal Push: {res.get('title')}", success=True)
        flash(f"Daily deal broadcasted to {res.get('sent', 0)} devices: {res.get('title')}", "success")
    else:
        flash(f"Daily push skipped or error: {res.get('reason', 'Unknown error')}", "error")
    return redirect(url_for("marketing_admin.push_dashboard"))

@marketing_admin_bp.route("/daily-deals/poster/<int:deal_id>")
@marketing_admin_bp.route("/daily-deals/poster")
def download_deal_poster(deal_id=None):
    require_admin_auth, _, _, _ = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    from flask import send_file, abort
    from models import MarketingContent
    from services.poster_service import generate_status_poster
    from services.deal_service import get_lagos_today

    if deal_id:
        deal = db.session.get(MarketingContent, deal_id)
    else:
        deal = MarketingContent.query.filter_by(content_type="DAILY_DEAL", target_date=get_lagos_today()).first()

    if not deal:
        abort(404)

    fmt = request.args.get("format", "story").lower()
    buf = generate_status_poster(
        network=deal.network or "MTN",
        plan_name=deal.data_size or deal.plan_name or "5GB",
        price=deal.price or 0,
        validity=deal.validity or "30 Days",
        website_url=request.host_url,
        format=fmt,
    )
    clean_net = (deal.network or "network").lower()
    return send_file(buf, mimetype="image/png", as_attachment=True, download_name=f"waj-vtu-{clean_net}-{fmt}-deal.png")


def _build_linkedin_panel(li_configured, li_connected, li_author_urn, flyer_net, flyer_plan, flyer_price, flyer_val, pack, li_member_name=""):
    """Build the LinkedIn direct-posting card HTML for the Social Studio."""
    if not li_configured:
        return f"""
        <div class="card" style="background:#f0f9ff; border:1.5px dashed #0a66c2; border-radius:14px; padding:22px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
            <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px;">
                <div style="display:flex; align-items:center; gap:10px;">
                    <span style="font-size:22px;">💼</span>
                    <div>
                        <h4 style="font-size:16px; font-weight:800; color:#09090b; margin:0;">LinkedIn — Direct API Posting</h4>
                        <span style="font-size:12px; color:#0a66c2; font-weight:600;">Post directly to your LinkedIn profile from here</span>
                    </div>
                </div>
                <span style="font-size:11px; background:#dbeafe; color:#1d4ed8; padding:4px 10px; border-radius:6px; font-weight:700;">NOT CONFIGURED</span>
            </div>
            <div style="background:#fffbeb; border:1px solid #fde68a; border-radius:8px; padding:12px 16px; font-size:13px; color:#92400e; margin-bottom:14px;">
                ⚙️ <strong>Setup required:</strong> Add these 3 variables to your <code>.env</code> file, then restart the app:
                <ul style="margin:8px 0 0 16px; font-size:12px; font-family:monospace; line-height:2;">
                    <li>LINKEDIN_CLIENT_ID=<em>your_app_client_id</em></li>
                    <li>LINKEDIN_CLIENT_SECRET=<em>your_app_client_secret</em></li>
                    <li>LINKEDIN_REDIRECT_URI=https://your-domain.com/admin/marketing/linkedin/callback</li>
                </ul>
            </div>
            <a href="https://www.linkedin.com/developers/apps" target="_blank" style="display:inline-flex; align-items:center; gap:6px; background:#0a66c2; color:#ffffff; font-weight:700; font-size:13px; padding:10px 18px; border-radius:8px; text-decoration:none;">
                🔗 Create a LinkedIn App →
            </a>
        </div>"""

    li_text = pack.get("linkedin", {}).get("text", pack.get("facebook", {}).get("text", ""))
    auto_post_controls = ""
    if li_connected:
        from services.marketing_settings import get_setting_bool, get_marketing_setting
        from services.deal_service import get_lagos_today

        auto_enabled = get_setting_bool("LINKEDIN_AUTO_POST_ENABLED", False)
        today_str = str(get_lagos_today())
        posted_today = get_marketing_setting(f"LINKEDIN_POST_SENT_{today_str}") == "true"

        auto_badge = '<span style="font-size:11px; background:#dcfce7; color:#15803d; padding:2px 8px; border-radius:4px; font-weight:700;">ACTIVE</span>' if auto_enabled else '<span style="font-size:11px; background:#f1f5f9; color:#64748b; padding:2px 8px; border-radius:4px; font-weight:700;">DISABLED</span>'
        today_note = f"✓ Today's deal already auto-posted ({today_str})" if posted_today else ("Scheduled for 8:00 AM Lagos" if auto_enabled else "Automatic posting is turned off")
        auto_btn_text = "Turn Off Auto-Post" if auto_enabled else "Turn On Auto-Post"
        auto_btn_style = "background:#fee2e2; color:#991b1b; border:1px solid #fca5a5;" if auto_enabled else "background:#e0f2fe; color:#0369a1; border:1px solid #bae6fd;"

        auto_post_controls = f"""
        <div style="background:#f8fafc; border:1px solid #e2e8f0; border-radius:10px; padding:14px 16px; margin-bottom:18px;">
            <div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
                <div>
                    <div style="display:flex; align-items:center; gap:8px;">
                        <span style="font-size:14px;">🤖</span>
                        <strong style="font-size:13px; color:#0f172a;">Daily Auto-Post (8:00 AM Lagos)</strong>
                        {auto_badge}
                    </div>
                    <div style="font-size:12px; color:#64748b; margin-top:3px;">
                        {today_note}
                    </div>
                </div>
                <div style="display:flex; align-items:center; gap:8px;">
                    <form method="POST" action="/admin/marketing/linkedin/toggle-auto" style="margin:0;">
                        <button type="submit" style="{auto_btn_style} font-size:12px; font-weight:700; padding:6px 14px; border-radius:6px; cursor:pointer;">
                            {auto_btn_text}
                        </button>
                    </form>
                    <form method="POST" action="/admin/marketing/linkedin/auto-post-now" style="margin:0;">
                        <button type="submit" onclick="return confirm('Immediately generate today\\'s deal and auto-post to LinkedIn?')" style="background:#0a66c2; color:#ffffff; border:none; font-size:12px; font-weight:700; padding:6px 14px; border-radius:6px; cursor:pointer; display:inline-flex; align-items:center; gap:5px;">
                            ⚡ Run Auto-Post Now
                        </button>
                    </form>
                </div>
            </div>
        </div>"""

        status_badge = '<span style="font-size:11px; background:#dcfce7; color:#15803d; padding:4px 10px; border-radius:6px; font-weight:700;">✓ CONNECTED</span>'
        name_display = f'<strong style="font-size:13px; color:#0f172a;">{escape(li_member_name)}</strong>' if li_member_name else ""
        urn_display = f'<span style="font-size:11px; color:#64748b; font-family:monospace;">({li_author_urn})</span>' if li_author_urn else ""
        connect_action = f"""
            <div style="display:flex; align-items:center; gap:12px; margin-bottom:16px;">
                {status_badge} {name_display} {urn_display}
                <form method="POST" action="/admin/marketing/linkedin/disconnect" style="margin:0;">
                    <button type="submit" style="background:#fee2e2; color:#991b1b; border:1px solid #fca5a5; font-size:12px; font-weight:600; padding:5px 12px; border-radius:6px; cursor:pointer;">
                        Disconnect
                    </button>
                </form>
            </div>"""
        post_form = f"""
            <form method="POST" action="/admin/marketing/linkedin/post">
                <input type="hidden" name="flyer_network" value="{flyer_net}">
                <input type="hidden" name="flyer_plan" value="{flyer_plan}">
                <input type="hidden" name="flyer_price" value="{flyer_price}">
                <input type="hidden" name="flyer_validity" value="{flyer_val}">

                <label style="font-size:12px; font-weight:700; color:#52525b; display:block; margin-bottom:6px;">
                    Post Text <span style="font-weight:400; color:#71717a;">(editable — LinkedIn supports up to 3,000 chars)</span>
                </label>
                <textarea name="linkedin_text" id="linkedin-post-text" rows="6" style="width:100%; padding:12px; border-radius:8px; border:1px solid #d4d4d8; font-size:13px; font-family:monospace; line-height:1.5; resize:vertical; margin-bottom:12px;">{li_text}</textarea>

                <div style="display:flex; align-items:center; gap:10px; margin-bottom:14px; background:#f0fdf4; border:1px solid #bbf7d0; border-radius:8px; padding:10px 14px;">
                    <label style="display:flex; align-items:center; gap:8px; font-size:13px; font-weight:700; color:#15803d; cursor:pointer;">
                        <input type="checkbox" name="attach_flyer" value="1" checked style="width:18px; height:18px; accent-color:#16a34a;">
                        🖼️ Attach 1:1 branded promo flyer image with post (recommended)
                    </label>
                </div>

                <div style="display:flex; flex-wrap:wrap; gap:10px;">
                    <button type="submit" style="background:#0a66c2; color:#ffffff; font-weight:800; font-size:13px; padding:12px 22px; border-radius:8px; border:none; cursor:pointer; display:inline-flex; align-items:center; gap:7px;">
                        💼 Post to LinkedIn Now
                    </button>
                    <button type="button" onclick="copyStudioText('linkedin-post-text', this)" style="background:#f4f4f5; color:#18181b; font-weight:700; font-size:13px; padding:12px 18px; border-radius:8px; border:1px solid #d4d4d8; cursor:pointer;">
                        📋 Copy Text
                    </button>
                </div>
            </form>"""
    else:
        status_badge = '<span style="font-size:11px; background:#fef3c7; color:#92400e; padding:4px 10px; border-radius:6px; font-weight:700;">NOT CONNECTED</span>'
        connect_action = f"""
            <div style="display:flex; align-items:center; gap:12px; margin-bottom:16px;">
                {status_badge}
                <a href="/admin/marketing/linkedin/connect" style="display:inline-flex; align-items:center; gap:6px; background:#0a66c2; color:#ffffff; font-weight:700; font-size:13px; padding:9px 18px; border-radius:8px; text-decoration:none;">
                    🔗 Connect LinkedIn Account
                </a>
            </div>"""
        post_form = '<div style="background:#f4f4f5; border-radius:8px; padding:16px; font-size:13px; color:#52525b; text-align:center;">Connect your LinkedIn account above to post directly.</div>'

    return f"""
    <div class="card" style="background:#ffffff; border:1.5px solid #0a66c2; border-radius:14px; padding:22px; box-shadow:0 1px 3px rgba(10,102,194,0.08);">
        <div style="display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:14px;">
            <div style="display:flex; align-items:center; gap:10px;">
                <span style="font-size:22px;">💼</span>
                <div>
                    <h4 style="font-size:16px; font-weight:800; color:#09090b; margin:0;">LinkedIn — Direct API Posting</h4>
                    <span style="font-size:12px; color:#0a66c2; font-weight:600;">Posts directly to your LinkedIn profile feed</span>
                </div>
            </div>
            <span style="font-size:11px; background:#dbeafe; color:#1d4ed8; padding:4px 8px; border-radius:6px; font-weight:700;">UTM: linkedin</span>
        </div>
        {connect_action}
        {auto_post_controls}
        {post_form}
    </div>"""


# ----------------------------------------------------------------------
# 8. SOCIAL MEDIA CAMPAIGN STUDIO
# ----------------------------------------------------------------------
@marketing_admin_bp.route("/social")
def social_studio():
    require_admin_auth, _, format_admin_datetime, ADMIN_BASE_TEMPLATE = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    from services.deal_service import get_lagos_today, generate_daily_deal
    from services.social_campaign_service import generate_social_pack
    from services.linkedin_service import is_configured as li_is_configured, is_token_valid as li_token_valid
    from services.marketing_settings import get_marketing_setting
    import urllib.parse, json

    # --- LinkedIn connection status ---
    li_configured = li_is_configured()
    _li_token_json = get_marketing_setting("LINKEDIN_TOKEN", "")
    try:
        _li_token_data = json.loads(_li_token_json) if _li_token_json else {}
    except (ValueError, TypeError):
        _li_token_data = {}
    li_connected = li_token_valid(_li_token_data)
    li_author_urn = _li_token_data.get("author_urn", "") if li_connected else ""
    li_member_name = _li_token_data.get("member_name", "") if li_connected else ""

    campaign_type = request.args.get("campaign_type", "DAILY_DEAL").upper()
    deal_id = request.args.get("deal_id", type=int)

    deal = None
    if campaign_type == "DAILY_DEAL":
        if deal_id:
            deal = db.session.get(MarketingContent, deal_id)
        if not deal:
            deal, _ = generate_daily_deal(target_date=get_lagos_today())

    # Custom campaign parameters
    custom_net = request.args.get("custom_net", "MTN")
    custom_headline = request.args.get("custom_headline", "5GB Mega Data Offer")
    custom_price = request.args.get("custom_price", "1200")
    custom_validity = request.args.get("custom_validity", "30 Days")
    custom_body = request.args.get("custom_body", "Super-fast internet data at discount distributor rates on WAJ VTU.")

    custom_data = {
        "network": custom_net,
        "headline": custom_headline,
        "price": f"₦{custom_price}" if not str(custom_price).startswith("₦") else custom_price,
        "validity": custom_validity,
        "body": custom_body,
    }

    deal_dict = None
    if deal:
        deal_dict = {
            "network": deal.network or "MTN",
            "data_size": deal.data_size or deal.plan_name or "2.0 GB",
            "price": deal.price or 0,
            "validity": deal.validity or "30 Days",
        }
    elif campaign_type == "CUSTOM":
        deal_dict = {
            "network": custom_net,
            "data_size": custom_headline,
            "price": custom_price,
            "validity": custom_validity,
        }

    pack = generate_social_pack(
        campaign_type=campaign_type,
        deal=deal_dict,
        custom_data=custom_data,
        base_url=request.host_url,
    )

    flyer_net = pack["details"]["network"]
    flyer_plan = pack["details"]["data_size"]
    flyer_price = pack["details"]["price"].replace("₦", "").replace(",", "")
    flyer_val = pack["details"]["validity"]

    # Build flyer URLs
    flyer_params = urllib.parse.urlencode({
        "network": flyer_net,
        "plan_name": flyer_plan,
        "price": flyer_price,
        "validity": flyer_val,
    })
    story_flyer_preview = f"/admin/marketing/social/flyer?format=story&{flyer_params}"
    story_flyer_download = f"/admin/marketing/social/flyer?format=story&{flyer_params}&download=1"
    square_flyer_preview = f"/admin/marketing/social/flyer?format=square&{flyer_params}"
    square_flyer_download = f"/admin/marketing/social/flyer?format=square&{flyer_params}&download=1"

    # Campaign selector tabs
    campaign_tabs = [
        ("DAILY_DEAL", "🔥 Today's Deal", f"/admin/marketing/social?campaign_type=DAILY_DEAL"),
        ("WEEKEND_PROMO", "🍿 Weekend Rush", f"/admin/marketing/social?campaign_type=WEEKEND_PROMO"),
        ("REFERRAL_EARN", "💰 Referral & Earn", f"/admin/marketing/social?campaign_type=REFERRAL_EARN"),
        ("PAYDAY_FLASH", "💼 Payday Bonanza", f"/admin/marketing/social?campaign_type=PAYDAY_FLASH"),
        ("CUSTOM", "✏️ Custom Promo", f"/admin/marketing/social?campaign_type=CUSTOM"),
    ]

    pills_html = '<div style="display:flex; gap:8px; flex-wrap:wrap; margin-bottom:24px;">'
    for c_key, c_label, c_url in campaign_tabs:
        is_active = (campaign_type == c_key)
        bg = "#ffc400" if is_active else "#f4f4f5"
        col = "#09090b" if is_active else "#52525b"
        border = "#e4e4e7" if not is_active else "#ffc400"
        font_weight = "700" if is_active else "600"
        pills_html += f'<a href="{c_url}" style="text-decoration:none; background:{bg}; color:{col}; border:1px solid {border}; border-radius:8px; padding:8px 16px; font-size:13px; font-weight:{font_weight};">{c_label}</a>'
    pills_html += '</div>'

    custom_form_html = ""
    if campaign_type == "CUSTOM":
        custom_form_html = f"""
        <div class="card" style="margin-bottom:24px; background:#ffffff; border:1px solid #e4e4e0; border-radius:12px; padding:20px;">
            <h4 style="font-size:15px; font-weight:700; margin-bottom:14px; color:#090909;">Custom Campaign Configuration</h4>
            <form method="GET" action="/admin/marketing/social" style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:14px;">
                <input type="hidden" name="campaign_type" value="CUSTOM">
                <div>
                    <label style="font-size:12px; font-weight:700; color:#52525b; display:block; margin-bottom:4px;">Network</label>
                    <select name="custom_net" style="width:100%; padding:8px; border:1px solid #d4d4d8; border-radius:6px; font-size:13px;">
                        <option value="MTN" {'selected' if custom_net=='MTN' else ''}>MTN</option>
                        <option value="AIRTEL" {'selected' if custom_net=='AIRTEL' else ''}>AIRTEL</option>
                        <option value="GLO" {'selected' if custom_net=='GLO' else ''}>GLO</option>
                        <option value="9MOBILE" {'selected' if custom_net=='9MOBILE' else ''}>9MOBILE</option>
                        <option value="ALL NETWORKS" {'selected' if custom_net=='ALL NETWORKS' else ''}>ALL NETWORKS</option>
                    </select>
                </div>
                <div>
                    <label style="font-size:12px; font-weight:700; color:#52525b; display:block; margin-bottom:4px;">Offer Headline / Plan</label>
                    <input type="text" name="custom_headline" value="{escape(custom_headline)}" style="width:100%; padding:8px; border:1px solid #d4d4d8; border-radius:6px; font-size:13px;">
                </div>
                <div>
                    <label style="font-size:12px; font-weight:700; color:#52525b; display:block; margin-bottom:4px;">Price (₦)</label>
                    <input type="text" name="custom_price" value="{escape(custom_price)}" style="width:100%; padding:8px; border:1px solid #d4d4d8; border-radius:6px; font-size:13px;">
                </div>
                <div>
                    <label style="font-size:12px; font-weight:700; color:#52525b; display:block; margin-bottom:4px;">Validity</label>
                    <input type="text" name="custom_validity" value="{escape(custom_validity)}" style="width:100%; padding:8px; border:1px solid #d4d4d8; border-radius:6px; font-size:13px;">
                </div>
                <div style="grid-column: 1 / -1;">
                    <label style="font-size:12px; font-weight:700; color:#52525b; display:block; margin-bottom:4px;">Campaign Hook / Body</label>
                    <input type="text" name="custom_body" value="{escape(custom_body)}" style="width:100%; padding:8px; border:1px solid #d4d4d8; border-radius:6px; font-size:13px;">
                </div>
                <div>
                    <button type="submit" style="background:#ffc400; color:#09090b; font-weight:700; border:none; padding:10px 18px; border-radius:6px; cursor:pointer; font-size:13px;">✨ Update Studio Assets</button>
                </div>
            </form>
        </div>
        """

    body = f"""
    {_marketing_subnav("social")}

    <div style="display:flex; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; gap:16px; margin-bottom:24px;">
        <div>
            <h2 style="font-size:24px; font-weight:800; color:#09090b; margin:0 0 6px 0;">📢 Social Media Campaign Studio</h2>
            <p style="font-size:14px; color:#71717a; margin:0; max-width:700px;">
                Launch high-converting social campaigns in 1 click across WhatsApp, Twitter/X, Instagram, and Facebook.
                Includes ultra-crisp dual-format promotional flyers and automated UTM conversion tracking.
            </p>
        </div>
        <div style="text-align:right;">
            <span style="display:inline-block; background:#ecfdf5; border:1px solid #a7f3d0; color:#047857; font-size:12px; font-weight:700; padding:6px 12px; border-radius:20px;">
                ✓ 1-Click Free Web Intents (No Paid API Required)
            </span>
        </div>
    </div>

    {pills_html}
    {custom_form_html}

    <div style="display:grid; grid-template-columns: 380px 1fr; gap:24px; align-items:start;" id="social-studio-grid">

        <!-- ========================================== -->
        <!-- LEFT COLUMN: GRAPHICAL FLYER STUDIO        -->
        <!-- ========================================== -->
        <div style="display:flex; flex-direction:column; gap:24px;">

            <!-- 1. WhatsApp Status & Story Flyer (9:16) -->
            <div class="card" style="background:#ffffff; border:1px solid #e4e4e7; border-radius:14px; padding:20px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <div>
                        <span style="font-size:11px; font-weight:800; color:#10b981; background:#ecfdf5; padding:3px 8px; border-radius:6px; text-transform:uppercase;">9:16 Portrait</span>
                        <h4 style="font-size:16px; font-weight:700; color:#09090b; margin:6px 0 0 0;">WhatsApp Status & Story Flyer</h4>
                    </div>
                    <span style="font-size:11px; color:#71717a;">1080 × 1920</span>
                </div>
                <p style="font-size:12px; color:#71717a; margin-bottom:14px;">
                    Optimized for WhatsApp Status, Instagram Stories, Facebook Stories, and TikTok.
                </p>

                <!-- Phone Frame Preview -->
                <div style="background:#09090b; border-radius:16px; padding:10px; border:2px solid #27272a; text-align:center; margin-bottom:14px;">
                    <img src="{story_flyer_preview}" alt="Status Flyer Preview" style="width:100%; max-height:360px; object-fit:contain; border-radius:8px; display:block; margin:0 auto; background:#18181b;" loading="lazy">
                </div>

                <div style="display:flex; gap:8px;">
                    <a href="{story_flyer_download}" style="flex:1; background:#10b981; color:#ffffff; font-weight:700; font-size:13px; padding:10px 14px; border-radius:8px; text-align:center; text-decoration:none; display:inline-flex; align-items:center; justify-content:center; gap:6px;">
                        📥 Download 9:16 PNG
                    </a>
                    <a href="{story_flyer_preview}" target="_blank" style="background:#f4f4f5; color:#27272a; font-weight:700; font-size:13px; padding:10px 14px; border-radius:8px; text-align:center; text-decoration:none;">
                        🔍 Preview
                    </a>
                </div>
            </div>

            <!-- 2. Feed & Social Post Flyer (1:1 Square) -->
            <div class="card" style="background:#ffffff; border:1px solid #e4e4e7; border-radius:14px; padding:20px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <div>
                        <span style="font-size:11px; font-weight:800; color:#3b82f6; background:#eff6ff; padding:3px 8px; border-radius:6px; text-transform:uppercase;">1:1 Square</span>
                        <h4 style="font-size:16px; font-weight:700; color:#09090b; margin:6px 0 0 0;">Feed & Social Media Flyer</h4>
                    </div>
                    <span style="font-size:11px; color:#71717a;">1080 × 1080</span>
                </div>
                <p style="font-size:12px; color:#71717a; margin-bottom:14px;">
                    Optimized for Instagram Feed, Twitter/X Posts, Facebook Posts, and LinkedIn.
                </p>

                <div style="background:#09090b; border-radius:16px; padding:10px; border:2px solid #27272a; text-align:center; margin-bottom:14px;">
                    <img src="{square_flyer_preview}" alt="Square Flyer Preview" style="width:100%; max-height:280px; object-fit:contain; border-radius:8px; display:block; margin:0 auto; background:#18181b;" loading="lazy">
                </div>

                <div style="display:flex; gap:8px;">
                    <a href="{square_flyer_download}" style="flex:1; background:#2563eb; color:#ffffff; font-weight:700; font-size:13px; padding:10px 14px; border-radius:8px; text-align:center; text-decoration:none; display:inline-flex; align-items:center; justify-content:center; gap:6px;">
                        📥 Download 1:1 PNG
                    </a>
                    <a href="{square_flyer_preview}" target="_blank" style="background:#f4f4f5; color:#27272a; font-weight:700; font-size:13px; padding:10px 14px; border-radius:8px; text-align:center; text-decoration:none;">
                        🔍 Preview
                    </a>
                </div>
            </div>

        </div>

        <!-- ========================================== -->
        <!-- RIGHT COLUMN: 1-CLICK PLATFORM LAUNCHPADS  -->
        <!-- ========================================== -->
        <div style="display:flex; flex-direction:column; gap:20px;">

            <!-- 1. WHATSAPP STATUS -->
            <div class="card" style="background:#ffffff; border:1px solid #e4e4e7; border-radius:14px; padding:22px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <div style="display:flex; align-items:center; gap:10px;">
                        <span style="font-size:22px;">📱</span>
                        <div>
                            <h4 style="font-size:16px; font-weight:800; color:#09090b; margin:0;">WhatsApp Status (1-Click Share)</h4>
                            <span style="font-size:12px; color:#15803d; font-weight:600;">High Urgency • Punchy Hook • Direct Order Link</span>
                        </div>
                    </div>
                    <span style="font-size:11px; background:#f0fdf4; color:#166534; padding:4px 8px; border-radius:6px; font-weight:700;">UTM: whatsapp_status</span>
                </div>

                <textarea id="copy-wa-status" readonly style="width:100%; min-height:120px; background:#fafaf9; border:1px solid #d4d4d8; border-radius:8px; padding:12px; font-size:13px; font-family:monospace; line-height:1.5; resize:vertical; margin-bottom:14px;">{escape(pack['whatsapp_status']['text'])}</textarea>

                <div style="display:flex; flex-wrap:wrap; gap:10px;">
                    <a href="{pack['whatsapp_status']['share_url']}" target="_blank" style="background:#25d366; color:#ffffff; font-weight:800; font-size:13px; padding:11px 20px; border-radius:8px; text-decoration:none; display:inline-flex; align-items:center; gap:8px;">
                        💬 Open & Post to WhatsApp
                    </a>
                    <button type="button" onclick="copyStudioText('copy-wa-status', this)" style="background:#f4f4f5; color:#18181b; font-weight:700; font-size:13px; padding:11px 18px; border-radius:8px; border:1px solid #d4d4d8; cursor:pointer;">
                        📋 Copy Text
                    </button>
                    <a href="{story_flyer_download}" style="background:#f0fdf4; color:#15803d; border:1px solid #86efac; font-weight:700; font-size:13px; padding:11px 16px; border-radius:8px; text-decoration:none; display:inline-flex; align-items:center; gap:6px;">
                        🖼️ Download 9:16 Story Flyer
                    </a>
                </div>
            </div>

            <!-- 2. WHATSAPP BROADCAST & GROUPS -->
            <div class="card" style="background:#ffffff; border:1px solid #e4e4e7; border-radius:14px; padding:22px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <div style="display:flex; align-items:center; gap:10px;">
                        <span style="font-size:22px;">📢</span>
                        <div>
                            <h4 style="font-size:16px; font-weight:800; color:#09090b; margin:0;">WhatsApp Groups & Broadcast Lists</h4>
                            <span style="font-size:12px; color:#15803d; font-weight:600;">Full Value Proposition • Features • Trust Badges</span>
                        </div>
                    </div>
                    <span style="font-size:11px; background:#f0fdf4; color:#166534; padding:4px 8px; border-radius:6px; font-weight:700;">UTM: whatsapp_group</span>
                </div>

                <textarea id="copy-wa-group" readonly style="width:100%; min-height:140px; background:#fafaf9; border:1px solid #d4d4d8; border-radius:8px; padding:12px; font-size:13px; font-family:monospace; line-height:1.5; resize:vertical; margin-bottom:14px;">{escape(pack['whatsapp_group']['text'])}</textarea>

                <div style="display:flex; flex-wrap:wrap; gap:10px;">
                    <a href="{pack['whatsapp_group']['share_url']}" target="_blank" style="background:#128c7e; color:#ffffff; font-weight:800; font-size:13px; padding:11px 20px; border-radius:8px; text-decoration:none; display:inline-flex; align-items:center; gap:8px;">
                        📢 Broadcast on WhatsApp
                    </a>
                    <button type="button" onclick="copyStudioText('copy-wa-group', this)" style="background:#f4f4f5; color:#18181b; font-weight:700; font-size:13px; padding:11px 18px; border-radius:8px; border:1px solid #d4d4d8; cursor:pointer;">
                        📋 Copy Broadcast
                    </button>
                </div>
            </div>

            <!-- 3. TWITTER / X -->
            <div class="card" style="background:#ffffff; border:1px solid #e4e4e7; border-radius:14px; padding:22px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <div style="display:flex; align-items:center; gap:10px;">
                        <span style="font-size:22px;">✖️</span>
                        <div>
                            <h4 style="font-size:16px; font-weight:800; color:#09090b; margin:0;">Twitter / X (1-Click Web Intent)</h4>
                            <span style="font-size:12px; color:#52525b; font-weight:600;">100% Free • Pre-filled Tweet • Viral Hashtags</span>
                        </div>
                    </div>
                    <div style="display:flex; align-items:center; gap:8px;">
                        <span style="font-size:11px; background:#eff6ff; color:#1d4ed8; padding:4px 8px; border-radius:6px; font-weight:700;">{pack['twitter']['char_count']} / 280 chars</span>
                        <span style="font-size:11px; background:#f4f4f5; color:#52525b; padding:4px 8px; border-radius:6px; font-weight:700;">UTM: twitter</span>
                    </div>
                </div>

                <textarea id="copy-twitter" readonly style="width:100%; min-height:100px; background:#fafaf9; border:1px solid #d4d4d8; border-radius:8px; padding:12px; font-size:13px; font-family:monospace; line-height:1.5; resize:vertical; margin-bottom:14px;">{escape(pack['twitter']['text'])}</textarea>

                <div style="display:flex; flex-wrap:wrap; gap:10px;">
                    <a href="{pack['twitter']['share_url']}" target="_blank" style="background:#000000; color:#ffffff; font-weight:800; font-size:13px; padding:11px 20px; border-radius:8px; text-decoration:none; display:inline-flex; align-items:center; gap:8px;">
                        ✖️ 1-Click Tweet to X
                    </a>
                    <button type="button" onclick="copyStudioText('copy-twitter', this)" style="background:#f4f4f5; color:#18181b; font-weight:700; font-size:13px; padding:11px 18px; border-radius:8px; border:1px solid #d4d4d8; cursor:pointer;">
                        📋 Copy Tweet
                    </button>
                    <a href="{square_flyer_download}" style="background:#eff6ff; color:#1d4ed8; border:1px solid #bfdbfe; font-weight:700; font-size:13px; padding:11px 16px; border-radius:8px; text-decoration:none; display:inline-flex; align-items:center; gap:6px;">
                        🖼️ Download 1:1 Flyer for Tweet
                    </a>
                </div>
            </div>

            <!-- 4. INSTAGRAM -->
            <div class="card" style="background:#ffffff; border:1px solid #e4e4e7; border-radius:14px; padding:22px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <div style="display:flex; align-items:center; gap:10px;">
                        <span style="font-size:22px;">📸</span>
                        <div>
                            <h4 style="font-size:16px; font-weight:800; color:#09090b; margin:0;">Instagram Caption & Hashtags</h4>
                            <span style="font-size:12px; color:#d97706; font-weight:600;">Spaced Paragraphs • Bio Prompt • 18 VTU Hashtags</span>
                        </div>
                    </div>
                    <span style="font-size:11px; background:#fffbeb; color:#92400e; padding:4px 8px; border-radius:6px; font-weight:700;">UTM: instagram</span>
                </div>

                <div style="background:#fffbeb; border:1px solid #fde68a; border-radius:8px; padding:10px 14px; margin-bottom:12px; font-size:12px; color:#92400e;">
                    💡 <em>Tip: Instagram does not allow clickable links inside post captions. The copy instructs followers to click the link in your bio. Copy the caption below and upload the 1:1 square flyer!</em>
                </div>

                <textarea id="copy-instagram" readonly style="width:100%; min-height:140px; background:#fafaf9; border:1px solid #d4d4d8; border-radius:8px; padding:12px; font-size:13px; font-family:monospace; line-height:1.5; resize:vertical; margin-bottom:14px;">{escape(pack['instagram']['text'])}</textarea>
                <textarea id="copy-ig-tags" style="display:none;">{escape(pack['instagram']['hashtags'])}</textarea>

                <div style="display:flex; flex-wrap:wrap; gap:10px;">
                    <button type="button" onclick="copyStudioText('copy-instagram', this)" style="background:#e1306c; color:#ffffff; font-weight:800; font-size:13px; padding:11px 20px; border-radius:8px; border:none; cursor:pointer;">
                        📋 Copy Full Caption & Tags
                    </button>
                    <button type="button" onclick="copyStudioText('copy-ig-tags', this)" style="background:#f4f4f5; color:#18181b; font-weight:700; font-size:13px; padding:11px 18px; border-radius:8px; border:1px solid #d4d4d8; cursor:pointer;">
                        🏷️ Copy Hashtags Only
                    </button>
                    <a href="{square_flyer_download}" style="background:#fdf2f8; color:#be185d; border:1px solid #fbcfe8; font-weight:700; font-size:13px; padding:11px 16px; border-radius:8px; text-decoration:none; display:inline-flex; align-items:center; gap:6px;">
                        🖼️ Download 1:1 Square Flyer
                    </a>
                </div>
            </div>

            <!-- 5. FACEBOOK -->
            <div class="card" style="background:#ffffff; border:1px solid #e4e4e7; border-radius:14px; padding:22px; box-shadow:0 1px 3px rgba(0,0,0,0.05);">
                <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;">
                    <div style="display:flex; align-items:center; gap:10px;">
                        <span style="font-size:22px;">📘</span>
                        <div>
                            <h4 style="font-size:16px; font-weight:800; color:#09090b; margin:0;">Facebook Post (1-Click Share)</h4>
                            <span style="font-size:12px; color:#1d4ed8; font-weight:600;">Personal Profile & Groups • Trust Story • Clickable Link</span>
                        </div>
                    </div>
                    <span style="font-size:11px; background:#eff6ff; color:#1d4ed8; padding:4px 8px; border-radius:6px; font-weight:700;">UTM: facebook</span>
                </div>

                <textarea id="copy-facebook" readonly style="width:100%; min-height:110px; background:#fafaf9; border:1px solid #d4d4d8; border-radius:8px; padding:12px; font-size:13px; font-family:monospace; line-height:1.5; resize:vertical; margin-bottom:14px;">{escape(pack['facebook']['text'])}</textarea>

                <div style="display:flex; flex-wrap:wrap; gap:10px;">
                    <a href="{pack['facebook']['share_url']}" target="_blank" style="background:#1877f2; color:#ffffff; font-weight:800; font-size:13px; padding:11px 20px; border-radius:8px; text-decoration:none; display:inline-flex; align-items:center; gap:8px;">
                        📘 1-Click Share to Facebook
                    </a>
                    <button type="button" onclick="copyStudioText('copy-facebook', this)" style="background:#f4f4f5; color:#18181b; font-weight:700; font-size:13px; padding:11px 18px; border-radius:8px; border:1px solid #d4d4d8; cursor:pointer;">
                        📋 Copy Post
                    </button>
                    <a href="{square_flyer_download}" style="background:#eff6ff; color:#1d4ed8; border:1px solid #bfdbfe; font-weight:700; font-size:13px; padding:11px 16px; border-radius:8px; text-decoration:none; display:inline-flex; align-items:center; gap:6px;">
                        🖼️ Download 1:1 Flyer
                    </a>
                </div>
            </div>

            <!-- 6. LINKEDIN — DIRECT API POSTING -->
            {_build_linkedin_panel(li_configured, li_connected, li_author_urn, flyer_net, flyer_plan, flyer_price, flyer_val, pack, li_member_name=li_member_name)}

            <!-- 7. UTM ATTRIBUTION EXPLAINER -->
            <div class="card" style="background:#fafaf9; border:1px solid #e4e4e7; border-radius:14px; padding:20px;">
                <h4 style="font-size:14px; font-weight:800; color:#09090b; margin:0 0 8px 0;">🎯 Automatic Traffic Attribution & UTM Parameters</h4>
                <p style="font-size:12px; color:#52525b; margin:0 0 12px 0;">
                    Every platform link is tagged automatically so that customer visits and signups can be analyzed in the
                    <a href="/admin/marketing" style="color:#2563eb; text-decoration:none; font-weight:600;">Marketing Dashboard</a>:
                </p>
                <div style="display:grid; grid-template-columns:repeat(auto-fit, minmax(200px, 1fr)); gap:10px; font-size:12px; font-family:monospace;">
                    <div style="background:#ffffff; border:1px solid #e4e4e7; border-radius:6px; padding:8px;">
                        <strong>WhatsApp Status:</strong><br>
                        <span style="color:#15803d;">?utm_source=whatsapp_status</span>
                    </div>
                    <div style="background:#ffffff; border:1px solid #e4e4e7; border-radius:6px; padding:8px;">
                        <strong>WhatsApp Group:</strong><br>
                        <span style="color:#15803d;">?utm_source=whatsapp_group</span>
                    </div>
                    <div style="background:#ffffff; border:1px solid #e4e4e7; border-radius:6px; padding:8px;">
                        <strong>Twitter / X:</strong><br>
                        <span style="color:#0284c7;">?utm_source=twitter</span>
                    </div>
                    <div style="background:#ffffff; border:1px solid #e4e4e7; border-radius:6px; padding:8px;">
                        <strong>Instagram:</strong><br>
                        <span style="color:#d946ef;">?utm_source=instagram</span>
                    </div>
                    <div style="background:#ffffff; border:1px solid #e4e4e7; border-radius:6px; padding:8px;">
                        <strong>Facebook:</strong><br>
                        <span style="color:#2563eb;">?utm_source=facebook</span>
                    </div>
                    <div style="background:#ffffff; border:1px solid #e4e4e7; border-radius:6px; padding:8px;">
                        <strong>LinkedIn:</strong><br>
                        <span style="color:#0a66c2;">?utm_source=linkedin</span>
                    </div>
                </div>
            </div>

        </div>

    </div>

    <script>
        function copyStudioText(textareaId, btnElement) {{
            const ta = document.getElementById(textareaId);
            if (!ta) return;
            const originalText = btnElement.innerText;
            navigator.clipboard.writeText(ta.value).then(function() {{
                btnElement.innerText = "✓ Copied!";
                setTimeout(function() {{
                    btnElement.innerText = originalText;
                }}, 2500);
            }}).catch(function(err) {{
                ta.select();
                document.execCommand("copy");
                btnElement.innerText = "✓ Copied!";
                setTimeout(function() {{
                    btnElement.innerText = originalText;
                }}, 2500);
            }});
        }}
    </script>
    """
    return render_template_string(ADMIN_BASE_TEMPLATE, body_content=body, active_page="marketing")


# -------------------------------------------------------------------------
# LINKEDIN OAUTH + DIRECT POSTING
# -------------------------------------------------------------------------

@marketing_admin_bp.route("/linkedin/connect")
def linkedin_connect():
    """Redirect admin to LinkedIn OAuth consent screen."""
    require_admin_auth, _, _, _ = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    from services.linkedin_service import get_authorization_url, is_configured
    import secrets

    if not is_configured():
        flash(
            "LinkedIn credentials not configured. Add LINKEDIN_CLIENT_ID, LINKEDIN_CLIENT_SECRET, "
            "and LINKEDIN_REDIRECT_URI to your .env file.",
            "error",
        )
        return redirect(url_for("marketing_admin.social_studio"))

    state = secrets.token_urlsafe(16)
    # Store state in a short-lived marketing setting to verify on callback
    from services.marketing_settings import set_marketing_setting
    set_marketing_setting("LINKEDIN_OAUTH_STATE", state)

    return redirect(get_authorization_url(state))


@marketing_admin_bp.route("/linkedin/callback")
def linkedin_callback():
    """Handle LinkedIn OAuth callback — exchange code for token and store it."""
    require_admin_auth, _, _, _ = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    from services.linkedin_service import exchange_code_for_token, get_profile_urn, is_configured
    from services.marketing_settings import get_marketing_setting, set_marketing_setting
    import time, json

    error = request.args.get("error")
    if error:
        flash(f"LinkedIn OAuth denied: {request.args.get('error_description', error)}", "error")
        return redirect(url_for("marketing_admin.social_studio"))

    state_received = request.args.get("state", "")
    state_expected = get_marketing_setting("LINKEDIN_OAUTH_STATE", "")
    if not state_received or state_received != state_expected:
        flash("LinkedIn OAuth state mismatch — possible CSRF. Please try connecting again.", "error")
        return redirect(url_for("marketing_admin.social_studio"))

    code = request.args.get("code", "")
    if not code:
        flash("LinkedIn did not return an authorization code.", "error")
        return redirect(url_for("marketing_admin.social_studio"))

    token_data = exchange_code_for_token(code)
    if not token_data or not token_data.get("access_token"):
        flash("Failed to exchange LinkedIn authorization code for token. Check server logs.", "error")
        return redirect(url_for("marketing_admin.social_studio"))

    # Enrich token data with expiry timestamp, profile URN, and member name
    token_data["expires_at"] = int(time.time()) + int(token_data.get("expires_in", 5183944))
    access_token = token_data["access_token"]
    id_token = token_data.get("id_token")
    author_urn, member_name = get_profile_urn(access_token, id_token=id_token)
    if author_urn:
        token_data["author_urn"] = author_urn
    if member_name:
        token_data["member_name"] = member_name

    set_marketing_setting("LINKEDIN_TOKEN", json.dumps(token_data))
    # Clear the state nonce now that it's been used
    set_marketing_setting("LINKEDIN_OAUTH_STATE", "")

    flash(
        f"LinkedIn connected successfully for {member_name or 'profile'}! "
        "You can now post directly from the Social Studio.",
        "success",
    )
    return redirect(url_for("marketing_admin.social_studio"))


@marketing_admin_bp.route("/linkedin/disconnect", methods=["POST"])
def linkedin_disconnect():
    """Revoke/clear the stored LinkedIn token."""
    require_admin_auth, _, _, _ = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    from services.marketing_settings import set_marketing_setting
    set_marketing_setting("LINKEDIN_TOKEN", "")
    flash("LinkedIn account disconnected.", "success")
    return redirect(url_for("marketing_admin.social_studio"))


@marketing_admin_bp.route("/linkedin/post", methods=["POST"])
def linkedin_post():
    """Post a campaign directly to LinkedIn — called from the Social Studio."""
    require_admin_auth, record_admin_audit, _, _ = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    from services.linkedin_service import post_to_linkedin, is_token_valid
    from services.marketing_settings import get_marketing_setting
    from services.poster_service import generate_status_poster
    import json

    token_json = get_marketing_setting("LINKEDIN_TOKEN", "")
    if not token_json:
        flash("LinkedIn account is not connected. Connect it first from the Social Studio.", "error")
        return redirect(url_for("marketing_admin.social_studio"))

    try:
        token_data = json.loads(token_json)
    except (ValueError, TypeError):
        flash("LinkedIn token data is corrupt. Please reconnect.", "error")
        return redirect(url_for("marketing_admin.social_studio"))

    if not is_token_valid(token_data):
        flash(
            "LinkedIn access token has expired. Please reconnect your account.",
            "error",
        )
        return redirect(url_for("marketing_admin.social_studio"))

    access_token = token_data["access_token"]
    author_urn = token_data.get("author_urn")
    if not author_urn:
        flash("LinkedIn author URN not found. Please reconnect your account.", "error")
        return redirect(url_for("marketing_admin.social_studio"))

    post_text = request.form.get("linkedin_text", "").strip()
    if not post_text:
        flash("Post text cannot be empty.", "error")
        return redirect(url_for("marketing_admin.social_studio"))

    # Attach promo flyer image by default (unless unchecked)
    attach_flyer = request.form.get("attach_flyer", "1") in ("1", "true", "on")
    image_bytes = None
    if attach_flyer:
        try:
            network = request.form.get("flyer_network", "MTN")
            plan_name = request.form.get("flyer_plan", "Data Bundle")
            price = request.form.get("flyer_price", "")
            validity = request.form.get("flyer_validity", "30 Days")
            buf = generate_status_poster(
                network=network, plan_name=plan_name, price=price,
                validity=validity, format="square",
            )
            image_bytes = buf.getvalue()
        except Exception as flyer_err:
            logger.warning("Could not generate flyer for manual post: %s", flyer_err)

    result = post_to_linkedin(
        access_token=access_token,
        author_urn=author_urn,
        text=post_text,
        image_bytes=image_bytes,
    )

    if result.get("status") == "success":
        post_url = result.get("url", "https://linkedin.com/feed")
        record_admin_audit("admin", "LINKEDIN_POST", True, f"Posted campaign to LinkedIn: {result.get('post_urn','')}")
        flash(
            f'✅ Posted to LinkedIn! <a href="{post_url}" target="_blank" style="font-weight:700;">View post →</a>',
            "success",
        )
    else:
        record_admin_audit("admin", "LINKEDIN_POST_FAILED", False, str(result.get("message", "Unknown error"))[:255])
        flash(f"❌ LinkedIn post failed: {result.get('message', 'Unknown error')}", "error")

    return redirect(url_for("marketing_admin.social_studio"))


@marketing_admin_bp.route("/linkedin/toggle-auto", methods=["POST"])
def linkedin_toggle_auto():
    """Toggle automated 8:00 AM daily LinkedIn posting on or off."""
    require_admin_auth, record_admin_audit, _, _ = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    from services.marketing_settings import get_setting_bool, set_marketing_setting
    curr = get_setting_bool("LINKEDIN_AUTO_POST_ENABLED", False)
    new_val = "false" if curr else "true"
    set_marketing_setting("LINKEDIN_AUTO_POST_ENABLED", new_val)
    status_label = "enabled" if new_val == "true" else "disabled"
    flash(f"Automated 8:00 AM LinkedIn posting is now {status_label}.", "success")
    record_admin_audit("admin", "LINKEDIN_AUTO_TOGGLE", True, f"Set LINKEDIN_AUTO_POST_ENABLED={new_val}")
    return redirect(url_for("marketing_admin.social_studio"))


@marketing_admin_bp.route("/linkedin/auto-post-now", methods=["POST"])
def linkedin_auto_post_now():
    """Manually trigger today's automated LinkedIn daily deal post immediately."""
    require_admin_auth, record_admin_audit, _, _ = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    from services.linkedin_auto_service import send_daily_deal_linkedin
    result = send_daily_deal_linkedin(force=True)
    if result.get("status") == "success":
        post_url = result.get("url", "https://linkedin.com/feed")
        flash(
            f'✅ Daily deal posted to LinkedIn! <a href="{post_url}" target="_blank" style="font-weight:700;">View post →</a>',
            "success",
        )
        record_admin_audit("admin", "LINKEDIN_AUTO_POST_NOW", True, f"URN: {result.get('post_urn')}")
    else:
        err = result.get("reason", "Unknown error")
        flash(f"❌ Automated LinkedIn post failed: {err}", "error")
        record_admin_audit("admin", "LINKEDIN_AUTO_POST_NOW_FAILED", False, str(err)[:255])
    return redirect(url_for("marketing_admin.social_studio"))


@marketing_admin_bp.route("/social/flyer")
def social_flyer():
    require_admin_auth, _, _, _ = _get_app_helpers()
    auth_err = require_admin_auth()
    if auth_err:
        return auth_err

    from flask import send_file
    from services.poster_service import generate_status_poster

    fmt = request.args.get("format", "story").lower()
    if fmt not in ["story", "square"]:
        fmt = "story"
    network = request.args.get("network", "MTN")
    plan_name = request.args.get("plan_name", "2.0 GB")
    price = request.args.get("price", "500")
    validity = request.args.get("validity", "30 Days")
    download = request.args.get("download", "0") == "1"

    buf = generate_status_poster(
        network=network,
        plan_name=plan_name,
        price=price,
        validity=validity,
        website_url=request.host_url,
        format=fmt,
    )
    clean_net = network.lower()
    filename = f"waj-vtu-{clean_net}-{fmt}-flyer.png"
    return send_file(buf, mimetype="image/png", as_attachment=download, download_name=filename)

