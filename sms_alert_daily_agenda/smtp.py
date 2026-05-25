from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText

log = logging.getLogger(__name__)


def send_sms(config: dict, messages: list[str]) -> None:
    with smtplib.SMTP(config["smtp_host"], config["smtp_port"], timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(config["smtp_user"], config["smtp_password"])
        for i, body in enumerate(messages, 1):
            msg = MIMEText(body, "plain")
            msg["From"] = config["smtp_user"]
            msg["To"] = config["sms_to"]
            msg["Subject"] = "Agenda"
            smtp.sendmail(config["smtp_user"], config["sms_to"], msg.as_string())
            log.info("SMS %d/%d → %s  (%d chars)", i, len(messages), config["sms_to"], len(body))


def send_auth_alert(config: dict) -> None:
    """Email the operator when Google credentials need renewal."""
    body = (
        "Your daily-agenda SMS bot could not authenticate with Google Calendar.\n\n"
        "Re-run on the server to refresh credentials:\n\n"
        "    daily-agenda --auth\n\n"
        "(Requires a browser; use SSH port-forwarding if the server is headless.)\n\n"
        "This is an automated message."
    )
    msg = MIMEText(body, "plain")
    msg["From"] = config["smtp_user"]
    msg["To"] = config["smtp_user"]
    msg["Subject"] = "[daily-agenda] Google re-authentication needed"
    try:
        with smtplib.SMTP(config["smtp_host"], config["smtp_port"], timeout=30) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(config["smtp_user"], config["smtp_password"])
            smtp.sendmail(config["smtp_user"], config["smtp_user"], msg.as_string())
        log.info("Auth alert sent to %s.", config["smtp_user"])
    except Exception as exc:
        log.error("Failed to send auth alert: %s", exc)
