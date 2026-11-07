"""
WhatsApp Status & Social Media Promo Poster Generator for WAJ VTU.
Generates ultra-crisp, branded 1080x1920 (9:16 portrait) graphical posters
ready for 1-click sharing on WhatsApp Status, Instagram Stories, and Facebook.
"""
from io import BytesIO
import os
from decimal import Decimal
from PIL import Image, ImageDraw, ImageFont

# Network brand styling palette
NETWORK_THEMES = {
    "MTN": {
        "primary": "#FFCC00",
        "primary_dark": "#DDAA00",
        "text_color": "#09090b",
        "glow": (255, 204, 0, 40),
        "tag": "MTN SME & DIRECT DATA",
    },
    "GLO": {
        "primary": "#16a34a",
        "primary_dark": "#15803d",
        "text_color": "#ffffff",
        "glow": (22, 163, 74, 40),
        "tag": "GLO 4G HIGH-SPEED DATA",
    },
    "AIRTEL": {
        "primary": "#dc2626",
        "primary_dark": "#b91c1c",
        "text_color": "#ffffff",
        "glow": (220, 38, 38, 40),
        "tag": "AIRTEL FAST CG DATA",
    },
    "9MOBILE": {
        "primary": "#047857",
        "primary_dark": "#065f46",
        "text_color": "#ffffff",
        "glow": (4, 120, 87, 40),
        "tag": "9MOBILE DIRECT DATA",
    },
}

DEFAULT_THEME = {
    "primary": "#FFC400",
    "primary_dark": "#E5B000",
    "text_color": "#09090b",
    "glow": (255, 196, 0, 40),
    "tag": "NATIONWIDE DATA BUNDLE",
}


def _get_font(size):
    """Load default PIL font scaled or fallback safely."""
    try:
        # Standard TrueType fonts on Windows or Linux
        for font_name in ["arialbd.ttf", "arial.ttf", "segoeui.ttf", "DejaVuSans-Bold.ttf"]:
            try:
                return ImageFont.truetype(font_name, size)
            except Exception:
                continue
    except Exception:
        pass
    return ImageFont.load_default(size=size)


def generate_status_poster(network, plan_name, price, validity="30 Days", website_url="wajvtu.com.ng", phone="0810 231 4725"):
    """
    Generates a high-impact 1080x1920 (9:16 portrait) WhatsApp Status flyer.
    Returns BytesIO object containing the PNG image.
    """
    width = 1080
    height = 1920

    # 1. Background (sleek dark gradient #09090b with modern grid and glow)
    poster = Image.new("RGBA", (width, height), (9, 9, 11, 255))
    draw = ImageDraw.Draw(poster)

    net_key = str(network or "MTN").strip().upper()
    theme = NETWORK_THEMES.get(net_key, DEFAULT_THEME)

    # 2. Subtle decorative corner arcs / glow
    draw.ellipse((-200, -200, 500, 500), fill=(255, 196, 0, 18))
    draw.ellipse((width - 400, height - 500, width + 300, height + 400), fill=theme["glow"])

    # Outer border accent
    draw.rectangle((24, 24, width - 24, height - 24), outline="#27272a", width=3)
    draw.rectangle((32, 32, width - 32, height - 32), outline=theme["primary"], width=2)

    # 3. Load & embed WAJ VTU Logo if available
    logo_path = os.path.join("static", "images", "waj-vtu-logo.jpeg")
    y_cursor = 100
    if os.path.exists(logo_path):
        try:
            logo = Image.open(logo_path).convert("RGBA")
            logo = logo.resize((140, 140))
            # Create rounded circle mask for logo
            mask = Image.new("L", (140, 140), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, 140, 140), fill=255)
            poster.paste(logo, (int((width - 140) / 2), y_cursor), mask)
            y_cursor += 160
        except Exception:
            pass

    # Brand Title: "WAJ VTU"
    brand_font = _get_font(52)
    brand_text = "WAJ VTU"
    draw.text((width / 2, y_cursor), brand_text, fill="#ffffff", font=brand_font, anchor="mm")
    y_cursor += 50

    tag_font = _get_font(26)
    draw.text((width / 2, y_cursor), "⚡ FAST • CHEAP • INSTANT DELIVERY ⚡", fill="#FFC400", font=tag_font, anchor="mm")
    y_cursor += 90

    # 4. Top Announcement Pill Badge: "🔥 DEAL OF THE DAY"
    pill_w = 460
    pill_h = 70
    pill_x1 = int((width - pill_w) / 2)
    pill_y1 = y_cursor
    pill_x2 = pill_x1 + pill_w
    pill_y2 = pill_y1 + pill_h
    draw.rounded_rectangle((pill_x1, pill_y1, pill_x2, pill_y2), radius=35, fill="#1c1917", outline="#FFC400", width=3)
    pill_font = _get_font(32)
    draw.text((width / 2, y_cursor + 35), "🔥 DEAL OF THE DAY", fill="#FFC400", font=pill_font, anchor="mm")
    y_cursor += 140

    # 5. Main Network Card
    card_margin = 80
    card_w = width - (card_margin * 2)
    card_h = 860
    card_x1 = card_margin
    card_y1 = y_cursor
    card_x2 = card_x1 + card_w
    card_y2 = card_y1 + card_h

    # Card background with deep rounded corners
    draw.rounded_rectangle((card_x1, card_y1, card_x2, card_y2), radius=28, fill="#18181b", outline="#27272a", width=3)

    # Network badge header inside card
    badge_w = 340
    badge_h = 75
    badge_x = int(width / 2)
    badge_y = card_y1 + 75
    draw.rounded_rectangle(
        (badge_x - int(badge_w / 2), badge_y - int(badge_h / 2), badge_x + int(badge_w / 2), badge_y + int(badge_h / 2)),
        radius=16,
        fill=theme["primary"]
    )
    badge_font = _get_font(38)
    draw.text((badge_x, badge_y), net_key, fill=theme["text_color"], font=badge_font, anchor="mm")

    # Network subtag
    subtag_font = _get_font(24)
    draw.text((width / 2, card_y1 + 145), theme["tag"], fill="#a1a1aa", font=subtag_font, anchor="mm")

    # Large Plan Size Text (e.g. "5GB")
    plan_font = _get_font(130)
    clean_plan = str(plan_name or "DATA").strip()
    draw.text((width / 2, card_y1 + 290), clean_plan, fill="#ffffff", font=plan_font, anchor="mm")

    # Validity badge
    val_font = _get_font(32)
    draw.text((width / 2, card_y1 + 400), f"⏳ Valid for {validity}", fill="#93c5fd", font=val_font, anchor="mm")

    # Price Box inside card
    price_box_w = card_w - 120
    price_box_h = 160
    pbox_x1 = int((width - price_box_w) / 2)
    pbox_y1 = card_y1 + 480
    pbox_x2 = pbox_x1 + price_box_w
    pbox_y2 = pbox_y1 + price_box_h

    draw.rounded_rectangle(
        (pbox_x1, pbox_y1, pbox_x2, pbox_y2),
        radius=20,
        fill="#09090b",
        outline=theme["primary"],
        width=3
    )

    try:
        price_num = float(price or 0)
        price_str = f"₦{price_num:,.0f}"
    except (ValueError, TypeError):
        price_str = f"₦{price}"

    draw.text((width / 2, pbox_y1 + 45), "SPECIAL DISCOUNT PRICE", fill="#71717a", font=_get_font(22), anchor="mm")
    draw.text((width / 2, pbox_y1 + 105), price_str, fill="#22c55e", font=_get_font(72), anchor="mm")

    # Features list inside card
    feats_y = card_y1 + 685
    feat_font = _get_font(26)
    draw.text((width / 2, feats_y), "✓ Instant Delivery within 10 Seconds", fill="#e4e4e7", font=feat_font, anchor="mm")
    draw.text((width / 2, feats_y + 45), "✓ Works on All Android, iPhone, Laptops & MiFi", fill="#e4e4e7", font=feat_font, anchor="mm")
    draw.text((width / 2, feats_y + 90), "✓ 24/7 Automated Wallet Funding", fill="#e4e4e7", font=feat_font, anchor="mm")

    y_cursor = card_y2 + 60

    # 6. Bottom Call to Action Section
    cta_box_w = width - 140
    cta_box_h = 170
    cta_x1 = int((width - cta_box_w) / 2)
    cta_y1 = y_cursor
    cta_x2 = cta_x1 + cta_box_w
    cta_y2 = cta_y1 + cta_box_h

    draw.rounded_rectangle((cta_x1, cta_y1, cta_x2, cta_y2), radius=22, fill="#FFC400")
    draw.text((width / 2, cta_y1 + 55), "👉 ORDER NOW ON WEBSITE", fill="#09090b", font=_get_font(42), anchor="mm")
    draw.text((width / 2, cta_y1 + 115), website_url.replace("https://", "").replace("http://", "").rstrip("/"), fill="#18181b", font=_get_font(34), anchor="mm")

    # Footer contact info
    footer_y = cta_y2 + 70
    footer_font = _get_font(28)
    draw.text((width / 2, footer_y), f"💬 WhatsApp Bot & Customer Care: {phone}", fill="#ffffff", font=footer_font, anchor="mm")
    draw.text((width / 2, footer_y + 45), "WAJ VTU — Nigeria's Most Reliable Telecoms Platform", fill="#71717a", font=_get_font(22), anchor="mm")

    # Convert to RGB and save to buffer
    rgb_poster = poster.convert("RGB")
    buf = BytesIO()
    rgb_poster.save(buf, format="PNG", quality=95)
    buf.seek(0)
    return buf

