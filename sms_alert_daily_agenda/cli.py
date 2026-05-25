from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime

from sms_alert_daily_agenda.config import CredentialError, load_config
from sms_alert_daily_agenda.formatting import _dedup_sort, build_messages
from sms_alert_daily_agenda.gcal import fetch_lookahead_events, fetch_today_events, get_calendar_service
from sms_alert_daily_agenda.smtp import send_auth_alert, send_sms

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser(description="Send daily Google Calendar agenda via SMS.")
    parser.add_argument("--auth", action="store_true", help="Run OAuth2 setup and exit.")
    parser.add_argument("--list-calendars", action="store_true", help="List available calendars and their IDs, then exit.")
    parser.add_argument("--dry-run", action="store_true", help="Print agenda without sending SMS.")
    parser.add_argument("--port", type=int, default=0, metavar="PORT",
                        help="Port for the OAuth callback server (default: random). "
                             "Use a fixed port when SSH port-forwarding on a headless server.")
    parser.add_argument("--no-browser", action="store_true",
                        help="Do not open a browser during OAuth — print the auth URL instead. "
                             "Use with --port and an SSH tunnel on headless servers.")
    args = parser.parse_args()

    config = load_config()

    try:
        service = get_calendar_service(
            config,
            auth_port=args.port,
            open_browser=not args.no_browser,
        )
    except CredentialError as exc:
        log.error("%s", exc)
        send_auth_alert(config)
        sys.exit(1)

    if args.auth:
        log.info("Authentication successful. Token saved to %s.", config["token_file"])
        return

    if args.list_calendars:
        items = service.calendarList().list().execute().get("items", [])
        for cal in items:
            print(f"{cal['id']:<50}  {cal.get('summary', '')}")
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

        min_events = config["lookahead_min_events"]
        window = config["lookahead_days"]
        while len(upcoming) < min_events and window < 365:
            window = min(window * 2, 365)
            raw_upcoming = []
            for cid in cal_ids:
                raw_upcoming += fetch_lookahead_events(
                    service, cid, day, tz, window, config["recurring_check_weeks"],
                )
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
