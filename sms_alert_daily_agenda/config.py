from __future__ import annotations

import os
from zoneinfo import ZoneInfo

GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
SMS_MAX_CHARS = 160

CARRIER_GATEWAYS: dict[str, str] = {
    "tmobile": "tmomail.net",
    "att": "txt.att.net",
    "verizon": "vtext.com",
    "sprint": "messaging.sprintpcs.com",
    "boost": "sms.myboostmobile.com",
    "cricket": "sms.cricketwireless.net",
    "metro": "mymetropcs.com",
    "metropcs": "mymetropcs.com",
    "uscellular": "email.uscc.net",
    "virgin": "vmobl.com",
    "googlefi": "msg.fi.google.com",
    "fi": "msg.fi.google.com",
}


class CredentialError(RuntimeError):
    pass


def _normalize_carrier(raw: str) -> str:
    return raw.lower().translate(str.maketrans("", "", " -&"))


def load_config() -> dict:
    def require(key: str) -> str:
        val = os.environ.get(key, "").strip()
        if not val:
            raise SystemExit(f"Required environment variable not set: {key}")
        return val

    phone = require("PHONE_NUMBER").translate(str.maketrans("", "", " ()+-"))
    carrier = _normalize_carrier(os.environ.get("CARRIER", "tmobile"))

    if carrier not in CARRIER_GATEWAYS:
        raise SystemExit(
            f"Unknown carrier '{carrier}'. Supported: {', '.join(sorted(set(CARRIER_GATEWAYS)))}"
        )

    tz_name = os.environ.get("TIMEZONE", "America/New_York")
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        raise SystemExit(f"Unknown timezone: {tz_name!r}")

    return {
        "credentials_file": os.environ.get("GOOGLE_CREDENTIALS_FILE", "credentials.json"),
        "token_file": os.environ.get("GOOGLE_TOKEN_FILE", "token.json"),
        "smtp_host": os.environ.get("SMTP_HOST", "smtp.gmail.com"),
        "smtp_port": int(os.environ.get("SMTP_PORT", "587")),
        "smtp_user": require("SMTP_USER"),
        "smtp_password": require("SMTP_PASSWORD"),
        "sms_to": f"{phone}@{CARRIER_GATEWAYS[carrier]}",
        "calendar_ids": [
            c.strip()
            for c in os.environ.get("CALENDAR_IDS", "primary").split(",")
            if c.strip()
        ],
        "lookahead_days": int(os.environ.get("LOOKAHEAD_DAYS", "7")),
        "lookahead_min_events": int(os.environ.get("LOOKAHEAD_MIN_EVENTS", "5")),
        "recurring_check_weeks": int(os.environ.get("RECURRING_CHECK_WEEKS", "4")),
        "tz": tz,
        "agenda_date": os.environ.get("AGENDA_DATE", "").strip(),
        "per_calendar": os.environ.get("PER_CALENDAR", "").lower() in ("1", "true", "yes"),
    }
