import base64
import logging
import os
import smtplib
import ssl
from datetime import datetime
from email.message import EmailMessage

import requests

from database.db import execute_db, query_db

logger = logging.getLogger(__name__)


def send_gmail_otp(recipient, otp):
    """Send a registration OTP through Gmail API, with SMTP as local fallback."""
    username = os.environ.get("GMAIL_USERNAME", "").strip()
    client_id = os.environ.get("GMAIL_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("GMAIL_REFRESH_TOKEN", "").strip()
    relay_url = os.environ.get("GMAIL_APPS_SCRIPT_URL", "").strip()
    relay_token = os.environ.get("GMAIL_APPS_SCRIPT_TOKEN", "").strip()
    app_password = os.environ.get("GMAIL_APP_PASSWORD", "").replace(" ", "").strip()
    api_configured = bool(client_id and client_secret and refresh_token)
    if not username or not recipient or (not api_configured and not app_password):
        logger.error(
            "Gmail OTP configuration is incomplete: username_present=%s, api_credentials_present=%s, smtp_password_present=%s",
            bool(username), api_configured, bool(app_password),
        )

    email = EmailMessage()
    email["Subject"] = "B.I.D.S.E.T.U. registration verification code"
    email["From"] = username
    email["To"] = recipient
    email.set_content(
        "Your B.I.D.S.E.T.U. registration verification code is "
        f"{otp}. It expires in 10 minutes. Do not share this code."
    )

    # Apps Script relay uses HTTPS and sends through the Gmail account without
    # requiring Google Cloud billing or exposing Gmail credentials to Render.
    if username and recipient and relay_url and relay_token:
        try:
            relay_response = requests.post(
                relay_url,
                json={
                    "token": relay_token,
                    "to": recipient,
                    "subject": email["Subject"],
                    "body": email.get_content(),
                },
                timeout=20,
            )
            relay_response.raise_for_status()
            relay_result = relay_response.json()
            if relay_result.get("ok") is True:
                logger.info("Registration OTP email sent using Gmail Apps Script relay")
                return True
            logger.error("Gmail Apps Script relay rejected the OTP request")
        except (OSError, ValueError, requests.RequestException):
            logger.exception("Gmail Apps Script relay failed while sending the registration OTP")
        return False

    # Gmail API uses HTTPS/443 and works on hosts where outbound SMTP is blocked.
    if username and client_id and client_secret and refresh_token:
        try:
            token_response = requests.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": refresh_token,
                    "grant_type": "refresh_token",
                },
                timeout=20,
            )
            token_response.raise_for_status()
            access_token = token_response.json()["access_token"]
            raw_message = base64.urlsafe_b64encode(email.as_bytes()).decode().rstrip("=")
            gmail_response = requests.post(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages/send",
                headers={"Authorization": f"Bearer {access_token}"},
                json={"raw": raw_message},
                timeout=20,
            )
            gmail_response.raise_for_status()
            logger.info("Registration OTP email sent using Gmail HTTPS API")
            return True
        except (OSError, KeyError, ValueError, requests.RequestException):
            logger.exception("Gmail HTTPS API failed while sending the registration OTP")
            return False

    # Local fallback. Render commonly blocks outbound SMTP, so configure the
    # Gmail API variables above for hosted deployments.
    if not username or not app_password or not recipient:
        return False
    # Gmail supports STARTTLS on 587 and implicit TLS on 465. Some hosted
    # environments handle one outbound SMTP mode more reliably than the other.
    attempts = ("STARTTLS", "SSL")
    for mode in attempts:
        try:
            if mode == "STARTTLS":
                with smtplib.SMTP("smtp.gmail.com", 587, timeout=20) as server:
                    server.ehlo()
                    server.starttls(context=ssl.create_default_context())
                    server.ehlo()
                    server.login(username, app_password)
                    server.send_message(email)
            else:
                with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=20) as server:
                    server.login(username, app_password)
                    server.send_message(email)
            logger.info("Registration OTP email sent using Gmail %s", mode)
            return True
        except smtplib.SMTPAuthenticationError:
            logger.exception("Gmail rejected the SMTP credentials using %s", mode)
        except (OSError, smtplib.SMTPException, ValueError):
            logger.exception("Gmail SMTP failed using %s", mode)
    return False


def _email_notifications_enabled():
    return os.environ.get("EMAIL_NOTIFICATIONS_ENABLED", "0").lower() in (
        "1", "true", "yes", "on"
    )


def _send_email_notification(recipient, title, message):
    """Send an optional SMTP alert without interrupting the main workflow."""
    if not _email_notifications_enabled() or not recipient:
        return False

    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    smtp_username = os.environ.get("SMTP_USERNAME", "").strip()
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    smtp_from = os.environ.get("SMTP_FROM", smtp_username).strip()
    if not smtp_host or not smtp_from:
        logger.warning("Email notifications enabled but SMTP_HOST or SMTP_FROM is missing")
        return False

    try:
        smtp_port = int(os.environ.get("SMTP_PORT", "587"))
        email = EmailMessage()
        email["Subject"] = title
        email["From"] = smtp_from
        email["To"] = recipient
        email.set_content(message)

        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            if os.environ.get("SMTP_USE_TLS", "1").lower() in ("1", "true", "yes", "on"):
                server.starttls()
            if smtp_username:
                server.login(smtp_username, smtp_password)
            server.send_message(email)
        return True
    except (OSError, smtplib.SMTPException, ValueError):
        logger.exception("Failed to send notification email to %s", recipient)
        return False


def notify_user(user_id, event_type, title, message, resource_type=None, resource_id=None):
    if not user_id:
        return None
    notification_id = execute_db(
        """INSERT INTO notifications
           (recipient_user_id, event_type, title, message, resource_type, resource_id)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, event_type, title, message, resource_type, str(resource_id) if resource_id else None),
    )

    recipient = query_db("SELECT email FROM users WHERE id = ?", (user_id,), one=True)
    if recipient and _send_email_notification(recipient["email"], title, message):
        execute_db(
            "UPDATE notifications SET channel = 'IN_APP+EMAIL' WHERE id = ?",
            (notification_id,),
        )

    return notification_id


def get_user_notifications(user_id, unread_only=False, limit=50):
    where = "AND status = 'UNREAD'" if unread_only else ""
    return query_db(
        f"SELECT * FROM notifications WHERE recipient_user_id = ? {where} ORDER BY created_at DESC LIMIT ?",
        (user_id, limit),
    )


def mark_notification_read(notification_id, user_id):
    return execute_db(
        "UPDATE notifications SET status = 'READ', read_at = ? WHERE id = ? AND recipient_user_id = ?",
        (datetime.utcnow().isoformat(), notification_id, user_id),
    )