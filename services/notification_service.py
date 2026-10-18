"""
Unified Notification Service for WAJ VTU.
Provides clean service interfaces for:
1. Meta WhatsApp Cloud API / Bridge dispatch
2. Resend Email dispatch

Adheres strictly to Meta WhatsApp Business policies and spam prevention guidelines.
"""
import os
import requests
from flask import current_app
from models import User


class NotificationService:
    """Unified notification dispatcher for WhatsApp and Email."""

    @staticmethod
    def is_whatsapp_linked(user):
        """Checks if a user has an active, linked WhatsApp account."""
        if not user:
            return False
        w_id = str(user.whatsapp_id or "")
        return bool(w_id and not w_id.startswith("web_"))

    @staticmethod
    def send_whatsapp(recipient, message_text):
        """
        Sends an outbound WhatsApp message.
        Reuses existing app.send_whatsapp_message function.
        """
        try:
            from app import send_whatsapp_message
            return send_whatsapp_message(recipient, message_text)
        except Exception as e:
            if current_app:
                current_app.logger.error(f"WhatsApp dispatch failed to {recipient}: {e}")
            return False

    @staticmethod
    def send_email(to_email, subject, body_text, html_content=None):
        """
        Sends an email using Resend API (reusing project's existing configuration).
        """
        api_key = (
            current_app.config.get("RESEND_API_KEY") if current_app
            else os.getenv("RESEND_API_KEY", "")
        )
        from_email = (
            current_app.config.get("RESEND_FROM_EMAIL") if current_app
            else os.getenv("RESEND_FROM_EMAIL", "")
        )

        if not api_key or not from_email:
            if current_app:
                current_app.logger.warning("Resend credentials missing for marketing email dispatch.")
            return False

        if not html_content:
            # Wrap plain text in responsive branded template
            escaped_body = body_text.replace("\n", "<br>")
            html_content = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <meta name="viewport" content="width=device-width, initial-scale=1.0">
            </head>
            <body style="font-family: 'Segoe UI', Inter, Arial, sans-serif; background-color: #f5f5f2; margin: 0; padding: 20px;">
                <table width="100%" cellpadding="0" cellspacing="0" style="max-width: 600px; margin: 0 auto; background: #ffffff; border-radius: 12px; overflow: hidden; border: 1px solid #e4e4e0;">
                    <tr>
                        <td style="padding: 24px 32px; background: #090909; text-align: center;">
                            <h2 style="color: #ffffff; margin: 0; font-size: 20px; font-weight: 700; letter-spacing: 0.5px;">WAJ VTU</h2>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 32px; font-size: 15px; color: #1c1c1c; line-height: 1.6;">
                            {escaped_body}
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 20px 32px; background: #fafaf9; border-top: 1px solid #eeeeee; font-size: 12px; color: #888888; text-align: center;">
                            &copy; WAJ VTU. Fast. Simple. Reliable.<br>
                            You received this update regarding your WAJ VTU account.
                        </td>
                    </tr>
                </table>
            </body>
            </html>
            """

        try:
            response = requests.post(
                "https://api.resend.com/emails",
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "from": from_email,
                    "to": [to_email],
                    "subject": subject,
                    "html": html_content,
                    "text": body_text,
                },
                timeout=15,
            )
            return response.ok
        except Exception as e:
            if current_app:
                current_app.logger.error(f"Email dispatch error to {to_email}: {e}")
            return False

    @classmethod
    def dispatch_marketing_message(cls, message_record):
        """
        Dispatches a queued MarketingMessage record through its configured channel.
        Updates message status to SENT or FAILED.
        """
        from datetime import datetime, timezone
        from models import db

        if message_record.status not in ("QUEUED", "APPROVED"):
            return False, f"Message status is {message_record.status}, not ready to send."

        user = message_record.user
        channel = message_record.channel.upper()
        now = datetime.now(timezone.utc)
        success = False
        error_msg = None

        if channel == "EMAIL":
            if not message_record.recipient or "@" not in message_record.recipient:
                message_record.status = "FAILED"
                message_record.error_message = "Invalid email recipient"
                db.session.commit()
                return False, "Invalid email address."

            success = cls.send_email(
                to_email=message_record.recipient,
                subject=message_record.subject or "Special Offer from WAJ VTU",
                body_text=message_record.message_body,
            )
            if not success:
                error_msg = "Resend API error or invalid config"

        elif channel == "WHATSAPP":
            if not cls.is_whatsapp_linked(user):
                message_record.status = "FAILED"
                message_record.error_message = "User does not have an active linked WhatsApp"
                db.session.commit()
                return False, "User does not have linked WhatsApp."

            recipient_phone = user.phone or str(user.whatsapp_id).split("@", 1)[0]
            success = cls.send_whatsapp(recipient_phone, message_record.message_body)
            if not success:
                error_msg = "WhatsApp delivery failed via Meta API / Bridge"

        if success:
            message_record.status = "SENT"
            message_record.sent_at = now
            message_record.error_message = None
        else:
            message_record.status = "FAILED"
            message_record.error_message = error_msg or "Dispatch failed"

        db.session.commit()
        return success, "Sent" if success else message_record.error_message
