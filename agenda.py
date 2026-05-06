#!/usr/bin/env python3
"""
agenda.py — Send daily Google Calendar agenda via SMS (SMTP-to-SMS gateway).
All configuration is via environment variables; see .env.example.

Usage:
  python agenda.py            # Send today's agenda
  python agenda.py --auth     # Run OAuth2 setup flow (do this once)
  python agenda.py --dry-run  # Print agenda, do not send SMS
"""

from __future__ import annotations

import argparse
import logging
import os
import smtplib
import sys
from datetime import date, datetime, timedelta, timezone
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

GOOGLE_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
SMS_MAX_CHARS = 160

# Maps normalized carrier name → SMTP-to-SMS gateway domain.
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


# ── Config ────────────────────────────────────────────────────────────────────


class CredentialError(RuntimeError):
    pass


def _normalize_carrier(raw: str) -> str:
    # Strip spaces, hyphens, ampersands so "t-mobile", "T-Mobile", "at&t" all normalize.
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
        "recurring_check_weeks": int(os.environ.get("RECURRING_CHECK_WEEKS", "4")),
        "tz": tz,
        "agenda_date": os.environ.get("AGENDA_DATE", "").strip(),
        # Send one SMS flow per calendar instead of combining all calendars.
        "per_calendar": os.environ.get("PER_CALENDAR", "").lower() in ("1", "true", "yes"),
    }


# ── Google Calendar ───────────────────────────────────────────────────────────


def get_calendar_service(config: dict):
    """Return an authenticated Google Calendar API service, refreshing the token as needed."""
    creds: Optional[Credentials] = None
    token_path = Path(config["token_file"])

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), GOOGLE_SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                log.info("Refreshed Google OAuth token.")
            except Exception as exc:
                raise CredentialError(
                    "Google OAuth token is expired or revoked. "
                    "Run `python agenda.py --auth` to re-authenticate."
                ) from exc
        else:
            creds_path = Path(config["credentials_file"])
            if not creds_path.exists():
                raise SystemExit(
                    f"Google credentials file not found: {creds_path}\n"
                    "Download OAuth2 client credentials JSON from "
                    "https://console.cloud.google.com/ (APIs & Services → Credentials)."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(creds_path), GOOGLE_SCOPES)
            # run_local_server opens a browser tab for OAuth consent.
            # On a headless server: SSH port-forward localhost:PORT and run --auth from there.
            creds = flow.run_local_server(port=0)
            if creds is None:
                raise RuntimeError("OAuth flow returned no credentials")

        token_path.write_text(creds.to_json())
        log.info("Saved token to %s", token_path)

    return build("calendar", "v3", credentials=creds, cache_discovery=False)


# ── Event utilities ───────────────────────────────────────────────────────────


def _parse_start(event: dict) -> Optional[datetime]:
    """Return event start as a timezone-aware datetime, or None."""
    s = event.get("start", {})
    try:
        if "dateTime" in s:
            dt = datetime.fromisoformat(s["dateTime"])
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        if "date" in s:
            return datetime.combine(
                date.fromisoformat(s["date"]),
                datetime.min.time(),
                tzinfo=timezone.utc,
            )
    except (ValueError, KeyError):
        pass
    return None


def _is_all_day(event: dict) -> bool:
    return "date" in event.get("start", {}) and "dateTime" not in event.get("start", {})


def _is_declined(event: dict) -> bool:
    return any(
        a.get("self") and a.get("responseStatus") == "declined"
        for a in event.get("attendees", [])
    )


def _is_cancelled(event: dict) -> bool:
    return event.get("status") == "cancelled"


def _is_recurring(event: dict, pool: list[dict], check_weeks: int) -> bool:
    """
    Return True if the event appears to be recurring.

    Detection uses two methods:
    1. Google API fields: recurringEventId or recurrence rule present.
    2. Weekly-pattern check: same summary appears at ~7-day intervals in pool.
       Requires 2+ weekly matches to guard against coincidental same-name events.
    """
    if event.get("recurringEventId") or event.get("recurrence"):
        return True

    if check_weeks <= 0:
        return False

    summary = event.get("summary", "").strip().lower()
    if not summary:
        return False

    event_dt = _parse_start(event)
    if event_dt is None:
        return False

    event_id = event.get("id")
    event_date = event_dt.date()
    weeks_found: set[int] = set()

    for other in pool:
        if other.get("id") == event_id:
            continue
        if other.get("summary", "").strip().lower() != summary:
            continue
        other_dt = _parse_start(other)
        if other_dt is None:
            continue
        delta_days = (other_dt.date() - event_date).days
        for week in range(1, check_weeks + 1):
            if abs(delta_days - week * 7) <= 1:  # ±1-day tolerance
                weeks_found.add(week)

    return len(weeks_found) >= 2


def fetch_today_events(service, calendar_id: str, day: date, tz: ZoneInfo) -> list[dict]:
    start = datetime.combine(day, datetime.min.time(), tzinfo=tz)
    end = start + timedelta(days=1)
    try:
        items = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=start.isoformat(),
                timeMax=end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
            .get("items", [])
        )
    except HttpError as exc:
        log.warning("Failed to fetch events for %s: %s", calendar_id, exc)
        return []
    return [e for e in items if not _is_declined(e) and not _is_cancelled(e)]


def fetch_lookahead_events(
    service,
    calendar_id: str,
    day: date,
    tz: ZoneInfo,
    lookahead_days: int,
    check_weeks: int,
) -> list[dict]:
    """
    Return non-recurring events in the window [day+1, day+lookahead_days].

    Fetches a wider window (up to check_weeks) so the recurring-pattern detector
    has enough data to identify weekly repetitions within a single API call.
    """
    if lookahead_days <= 0:
        return []

    window_start = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=tz)
    lookahead_end = datetime.combine(
        day + timedelta(days=lookahead_days + 1), datetime.min.time(), tzinfo=tz
    )
    # Extend fetch window to cover at least check_weeks of pattern data.
    fetch_end = max(lookahead_end, window_start + timedelta(weeks=max(check_weeks, 1)))

    try:
        # maxResults=500 is generous for a personal calendar; no pagination needed in practice.
        items = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=window_start.isoformat(),
                timeMax=fetch_end.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                maxResults=500,
            )
            .execute()
            .get("items", [])
        )
    except HttpError as exc:
        log.warning("Failed to fetch lookahead for %s: %s", calendar_id, exc)
        return []

    pool = [e for e in items if not _is_declined(e) and not _is_cancelled(e)]
    # Restrict candidate events to the actual lookahead window.
    _far = datetime.max.replace(tzinfo=timezone.utc)
    lookahead = [e for e in pool if (_parse_start(e) or _far) < lookahead_end]
    return [e for e in lookahead if not _is_recurring(e, pool, check_weeks)]


# ── Formatting ────────────────────────────────────────────────────────────────


def _fmt_time(dt: datetime, tz: ZoneInfo) -> str:
    local = dt.astimezone(tz)
    h = local.hour % 12 or 12
    m = local.minute
    period = "a" if local.hour < 12 else "p"
    return f"{h}:{m:02d}{period}" if m else f"{h}{period}"


def _event_time_str(event: dict, tz: ZoneInfo) -> str:
    """Return compact time string (e.g. '9a-10:30a'), empty string for all-day events."""
    if _is_all_day(event):
        return ""
    s = event.get("start", {})
    e = event.get("end", {})
    try:
        start_dt = datetime.fromisoformat(s["dateTime"])
        if not start_dt.tzinfo:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        end_dt = datetime.fromisoformat(e.get("dateTime", s["dateTime"]))
        if not end_dt.tzinfo:
            end_dt = end_dt.replace(tzinfo=timezone.utc)
        duration_min = (end_dt - start_dt).total_seconds() / 60
        t = _fmt_time(start_dt, tz)
        if duration_min > 1:
            t += f"-{_fmt_time(end_dt, tz)}"
        return t
    except (ValueError, KeyError):
        return ""


def _event_line(event: dict, tz: ZoneInfo, date_prefix: str = "") -> str:
    title = event.get("summary", "(no title)").strip()
    time_str = _event_time_str(event, tz)
    parts = [p for p in (date_prefix.strip(), time_str, title) if p]
    return " ".join(parts)


_FAR = datetime.max.replace(tzinfo=timezone.utc)


def _sort_key(event: dict) -> datetime:
    return _parse_start(event) or _FAR


def _dedup_sort(events: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for e in sorted(events, key=_sort_key):
        eid = e.get("id", "")
        if eid not in seen:
            seen.add(eid)
            out.append(e)
    return out


def build_messages(
    day: date,
    today: list[dict],
    upcoming: list[dict],
    tz: ZoneInfo,
) -> list[str]:
    """Pack the agenda into ≤160-char SMS strings."""
    lines: list[str] = []
    hdr = day.strftime("%-m/%-d")  # e.g. "5/7" — Linux only (%-m strips leading zero)

    lines.append(f"{hdr}:")
    if today:
        for evt in today:
            lines.append(f"  {_event_line(evt, tz)}")
    else:
        lines.append("  (free)")

    if upcoming:
        lines.append("Soon:")
        for evt in upcoming:
            dt = _parse_start(evt)
            dp = dt.astimezone(tz).strftime("%-m/%-d") if dt else ""
            lines.append(f"  {_event_line(evt, tz, date_prefix=dp)}")

    full = "\n".join(lines)
    chunks: list[str] = []
    while full:
        if len(full) <= SMS_MAX_CHARS:
            chunks.append(full.rstrip())
            break
        chunk = full[:SMS_MAX_CHARS]
        nl = chunk.rfind("\n")
        cut = nl if nl > 0 else SMS_MAX_CHARS
        chunks.append(full[:cut].rstrip())
        full = full[cut:].lstrip("\n")

    return [c for c in chunks if c]


# ── SMTP ──────────────────────────────────────────────────────────────────────


def send_sms(config: dict, messages: list[str]) -> None:
    with smtplib.SMTP(config["smtp_host"], config["smtp_port"], timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.login(config["smtp_user"], config["smtp_password"])
        for i, body in enumerate(messages, 1):
            msg = MIMEText(body, "plain")
            msg["From"] = config["smtp_user"]
            msg["To"] = config["sms_to"]
            smtp.sendmail(config["smtp_user"], config["sms_to"], msg.as_string())
            log.info("SMS %d/%d → %s  (%d chars)", i, len(messages), config["sms_to"], len(body))


def send_auth_alert(config: dict) -> None:
    """Email the operator when Google credentials need renewal."""
    body = (
        "Your daily-agenda SMS bot could not authenticate with Google Calendar.\n\n"
        "Re-run on the server to refresh credentials:\n\n"
        "    cd /path/to/agenda && python agenda.py --auth\n\n"
        "(Requires a browser; use SSH port-forwarding if the server is headless.)\n\n"
        "This is an automated message."
    )
    msg = MIMEText(body, "plain")
    msg["From"] = config["smtp_user"]
    msg["To"] = config["smtp_user"]  # alert goes to the sender's own inbox
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


# ── Entry point ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Send daily Google Calendar agenda via SMS.")
    parser.add_argument("--auth", action="store_true", help="Run OAuth2 setup and exit.")
    parser.add_argument("--dry-run", action="store_true", help="Print agenda without sending SMS.")
    args = parser.parse_args()

    config = load_config()

    try:
        service = get_calendar_service(config)
    except CredentialError as exc:
        log.error("%s", exc)
        send_auth_alert(config)
        sys.exit(1)

    if args.auth:
        log.info("Authentication successful. Token saved to %s.", config["token_file"])
        return

    tz = config["tz"]
    day = (
        date.fromisoformat(config["agenda_date"])
        if config["agenda_date"]
        else datetime.now(tz).date()
    )
    log.info("Agenda for %s  (tz=%s)", day, tz)

    groups: list[tuple[list[str], str]] = (
        [([cid], cid) for cid in config["calendar_ids"]]
        if config["per_calendar"]
        else [(config["calendar_ids"], "combined")]
    )

    any_sent = False
    for cal_ids, label in groups:
        raw_today: list[dict] = []
        raw_upcoming: list[dict] = []

        for cid in cal_ids:
            raw_today += fetch_today_events(service, cid, day, tz)
            raw_upcoming += fetch_lookahead_events(
                service, cid, day, tz,
                config["lookahead_days"],
                config["recurring_check_weeks"],
            )

        today = _dedup_sort(raw_today)
        upcoming = _dedup_sort(raw_upcoming)

        if not today and not upcoming:
            log.info("No events for [%s] — skipping SMS.", label)
            continue

        messages = build_messages(day, today, upcoming, tz)

        if args.dry_run:
            bar = f"── {label} " + "─" * max(0, 40 - len(label))
            print(bar)
            for m in messages:
                print(m)
                print("·" * 40)
        else:
            send_sms(config, messages)
            any_sent = True

    if not any_sent and not args.dry_run:
        log.info("Nothing to send (no events in any calendar).")


if __name__ == "__main__":
    main()
