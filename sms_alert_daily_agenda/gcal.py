from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from sms_alert_daily_agenda.config import CredentialError, GOOGLE_SCOPES

log = logging.getLogger(__name__)


def get_calendar_service(
    config: dict,
    auth_port: int = 0,
    open_browser: bool = True,
) -> object:
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
                    "Run `daily-agenda --auth` to re-authenticate."
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
            creds = flow.run_local_server(port=auth_port, open_browser=open_browser)
            if creds is None:
                raise RuntimeError("OAuth flow returned no credentials")

        token_path.write_text(creds.to_json())
        log.info("Saved token to %s", token_path)

    return build("calendar", "v3", credentials=creds, cache_discovery=False)


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
    Return True if the event appears to be a regular weekly recurring event.

    Uses behavioral pattern detection only: same summary at ~7-day intervals in
    the pool, requiring 2+ weekly matches. This intentionally skips the API
    recurringEventId field so that instances with modified titles (e.g. "Final
    Dress Rehearsal" in an otherwise weekly series) still appear in Soon.
    """
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
    fetch_end = max(lookahead_end, window_start + timedelta(weeks=max(check_weeks, 1)))

    try:
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
    _far = datetime.max.replace(tzinfo=timezone.utc)
    lookahead = [e for e in pool if (_parse_start(e) or _far) < lookahead_end]
    return [e for e in lookahead if not _is_recurring(e, pool, check_weeks)]
