"""
Web authentication for WAJ VTU.

Reuses the existing `users` table (models.User) instead of creating a
separate web-user table, so a wallet is shared between WhatsApp and the
website. Signup links to an existing WhatsApp account by phone number
when one exists; otherwise it creates a fresh user with a placeholder
whatsapp_id (a real one is attached automatically if/when they message
the bot with a matching phone number, if you add that lookup on the
webhook side).
"""
import re
import uuid
import requests
from functools import wraps
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from flask import Blueprint, current_app, request, session, redirect, url_for, render_template, flash
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy.exc import IntegrityError

from models import db, User

auth_bp = Blueprint("auth", __name__)

PHONE_RE = re.compile(r"^\+?[0-9]{10,14}$")


def normalize_phone(raw):
    """
    Store/compare phone numbers in the exact same format the WhatsApp
    webhook uses, so account-linking by phone always matches.
    """
    if raw is None:
        return ""
    normalized = str(raw).strip().replace(" ", "").replace("+", "")
    if normalized.startswith("234"):
        return normalized
    if normalized.startswith("0") and len(normalized) == 11:
        return "234" + normalized[1:]
    return normalized


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return User.query.get(user_id)


def reset_serializer():
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="waj-vtu-password-reset")


def send_password_reset_email(user, reset_url):
    api_key = current_app.config.get("RESEND_API_KEY", "").strip()
    from_email = current_app.config.get("RESEND_FROM_EMAIL", "").strip()
    if not api_key or not from_email:
        current_app.logger.error("Password reset email is not configured: set RESEND_API_KEY and RESEND_FROM_EMAIL")
        return False

    html = f"""
    <div style=\"font-family:Arial,sans-serif;max-width:560px;margin:0 auto;color:#111;\">
      <h1 style=\"color:#090909;\">WAJ VTU</h1>
      <p>We received a request to reset your WAJ VTU password.</p>
      <p><a href=\"{reset_url}\" style=\"display:inline-block;background:#FFC400;color:#090909;padding:12px 18px;text-decoration:none;font-weight:700;border-radius:6px;\">Reset password</a></p>
      <p style=\"color:#666;font-size:13px;\">This link expires in 1 hour. If you did not request this, you can ignore this email.</p>
    </div>
    """
    try:
        response = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "from": from_email,
                "to": [user.email],
                "subject": "Reset your WAJ VTU password",
                "html": html,
            },
            timeout=15,
        )
        if response.ok:
            return True
        current_app.logger.error("Resend rejected password reset email: status=%s response=%s", response.status_code, response.text[:300])
    except requests.RequestException:
        current_app.logger.exception("Unable to send password reset email through Resend")
    return False


@auth_bp.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "GET":
        return render_template("web/signup.html")

    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    phone = normalize_phone(request.form.get("phone") or "")
    name = (request.form.get("name") or "User").strip()

    if not email or "@" not in email:
        flash("Enter a valid email address.", "error")
        return redirect(url_for("auth.signup"))
    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return redirect(url_for("auth.signup"))
    if not PHONE_RE.match(phone):
        flash("Enter a valid phone number.", "error")
        return redirect(url_for("auth.signup"))

    if User.query.filter_by(email=email).first():
        flash("An account with that email already exists. Log in instead.", "error")
        return redirect(url_for("auth.login"))

    # Link to an existing WhatsApp-created account with the same phone number,
    # rather than creating a second wallet for the same person.
    existing = User.query.filter_by(phone=phone).first()

    if existing:
        if existing.email:
            flash("That phone number is already linked to an account. Log in or reset your password.", "error")
            return redirect(url_for("auth.login"))
        existing.email = email
        existing.password_hash = generate_password_hash(password)
        if name:
            existing.name = name
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("That email or phone number is already in use. Log in instead.", "error")
            return redirect(url_for("auth.login"))
        session["user_id"] = existing.id
        session["is_admin"] = bool(current_app.config.get("ADMIN_EMAIL") and email == current_app.config["ADMIN_EMAIL"])
        flash("Your existing wallet has been linked to this website login.", "success")
        return redirect(url_for("web.dashboard"))

    try:
        user = User(
            whatsapp_id=f"web_{uuid.uuid4().hex}",  # placeholder until they use the bot
            phone=phone,
            name=name,
            email=email,
            password_hash=generate_password_hash(password),
        )
        db.session.add(user)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            flash("That email is already in use. Log in instead.", "error")
            return redirect(url_for("auth.login"))
        session["user_id"] = user.id
        session["is_admin"] = bool(current_app.config.get("ADMIN_EMAIL") and email == current_app.config["ADMIN_EMAIL"])
        return redirect(url_for("web.dashboard"))
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise e


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("web/login.html")

    email = (request.form.get("email") or "").strip().lower()
    password = request.form.get("password") or ""
    admin_email = (current_app.config.get("ADMIN_EMAIL") or "").strip().lower()

    user = User.query.filter_by(email=email).first()
    is_admin_email = bool(admin_email and email == admin_email)
    password_valid = user and user.password_hash and check_password_hash(user.password_hash, password)
    if not user or (not is_admin_email and not password_valid):
        flash("Incorrect email or password.", "error")
        return redirect(url_for("auth.login"))

    session["user_id"] = user.id
    session["is_admin"] = bool(admin_email and email == admin_email)
    next_url = request.args.get("next")
    return redirect(next_url or url_for("web.dashboard"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = (request.form.get("email") or "").strip().lower()
        user = User.query.filter_by(email=email).first() if email else None
        if user:
            token = reset_serializer().dumps({"user_id": user.id, "email": user.email})
            reset_url = url_for("auth.reset_password", token=token, _external=True)
            send_password_reset_email(user, reset_url)
        flash("If that email has a WAJ VTU account, a reset link has been sent.", "message")
    return render_template("web/forgot_password.html")


@auth_bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    try:
        payload = reset_serializer().loads(token, max_age=3600)
    except (BadSignature, SignatureExpired):
        flash("That password reset link is invalid or has expired.", "error")
        return redirect(url_for("auth.forgot_password"))

    user = User.query.filter_by(id=payload.get("user_id"), email=payload.get("email")).first()
    if not user:
        flash("That password reset link is invalid.", "error")
        return redirect(url_for("auth.forgot_password"))

    if request.method == "POST":
        password = request.form.get("password") or ""
        confirmation = request.form.get("password_confirmation") or ""
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
        elif password != confirmation:
            flash("Passwords do not match.", "error")
        else:
            user.password_hash = generate_password_hash(password)
            db.session.commit()
            flash("Your password has been reset. You can now log in.", "success")
            return redirect(url_for("auth.login"))
    return render_template("web/reset_password.html")


@auth_bp.route("/logout")
def logout():
    session.pop("user_id", None)
    return redirect(url_for("auth.login"))
