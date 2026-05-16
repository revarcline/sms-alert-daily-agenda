# sms-alert-daily-agenda

Sends a daily SMS with your Google Calendar agenda — no paid SMS API required. Uses carrier email-to-SMS gateways and your existing Gmail (or any SMTP server) to deliver the message.

Each morning you get a text like:

```
5/16:
  9a-10a Team sync
  2p-3p Doctor
Soon:
  5/19 4p Design review
  5/21 All-hands
```

Recurring events (standups, weeklies) are filtered from the "Soon" section so it only shows things worth knowing about.

## Features

- **No SMS API costs** — uses free carrier email-to-SMS gateways
- **Recurring event filtering** — detects and suppresses weekly repeats from the lookahead
- **Multi-calendar support** — combine calendars or send one SMS per calendar
- **Auto auth-alert** — emails you if the Google token expires so the bot doesn't silently fail
- **systemd integration** — ships with a service + timer for unattended daily delivery

## Supported carriers

T-Mobile, AT&T, Verizon, Sprint, Boost, Cricket, Metro PCS, US Cellular, Virgin Mobile, Google Fi

---

## Installation

Download the latest `.whl` from [Releases](../../releases), then install it into a dedicated venv:

```bash
mkdir -p ~/agenda
python3 -m venv ~/agenda/venv
~/agenda/venv/bin/pip install sms_alert_daily_agenda-*.whl
```

Verify the install:

```bash
~/agenda/venv/bin/daily-agenda --help
```

---

## Setup

### 1. Google Calendar credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Credentials
2. Create an **OAuth 2.0 Client ID** (Desktop app type)
3. Download the JSON and save it as `~/agenda/credentials.json`
4. Enable the **Google Calendar API** for the project

### 2. Gmail app password

In your Google account: Security → 2-Step Verification → App passwords. Generate one for "Mail".

### 3. Configure environment

```bash
cp .env.example ~/agenda/.env
```

Edit `~/agenda/.env` with your values — at minimum `SMTP_USER`, `SMTP_PASSWORD`, `PHONE_NUMBER`, and the paths to your credential files.

### 4. Authenticate with Google

```bash
cd ~/agenda && venv/bin/daily-agenda --auth
```

This opens a browser tab for OAuth consent and saves `token.json`. On a headless server, SSH port-forward `localhost:<port>` and run `--auth` from there.

### 5. Test it

```bash
cd ~/agenda && venv/bin/daily-agenda --dry-run
```

Prints the agenda to stdout without sending anything.

### 6. Send for real

```bash
cd ~/agenda && venv/bin/daily-agenda
```

---

## Automate with systemd

The wheel includes the unit files under `sms_alert_daily_agenda/data/systemd/`. Copy them from your venv's site-packages, or grab them from the repo's `systemd/` directory:

```bash
# Edit YOUR_USER in both files before copying
sudo cp systemd/daily-agenda.service /etc/systemd/system/
sudo cp systemd/daily-agenda.timer   /etc/systemd/system/

sudo systemctl daemon-reload
sudo systemctl enable --now daily-agenda.timer

# Verify
systemctl status daily-agenda.timer
journalctl -u daily-agenda.service
```

The `ExecStart` in the service file points to `~/agenda/venv/bin/daily-agenda`. Update the path if you installed to a different location.

The timer fires at 06:00 local time by default. Edit `OnCalendar=` in the `.timer` file to change it.

---

## Development

Clone the repo and use `uv` to run from source:

```bash
git clone <repo-url>
cd sms-alert-daily-agenda
uv sync
uv run daily-agenda --dry-run
```

To build a wheel locally:

```bash
uv build --wheel
# output in dist/
```

### Releasing

Releases are tagged with `ci/tag-build.sh`, which updates the version in `pyproject.toml`, commits, and pushes a tag that triggers the GitHub Actions build:

```bash
./ci/tag-build.sh rc 1       # → 0.1.0rc1
./ci/tag-build.sh release    # → 0.1.0  (strips rc/dev suffix if present)
./ci/tag-build.sh dev build.1 # → 0.1.0+build.1
```

---

## Configuration reference

All configuration is via environment variables (`.env` file).

| Variable | Default | Description |
|---|---|---|
| `GOOGLE_CREDENTIALS_FILE` | `credentials.json` | Path to OAuth client credentials JSON |
| `GOOGLE_TOKEN_FILE` | `token.json` | Path where the OAuth token is cached |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server hostname |
| `SMTP_PORT` | `587` | SMTP port (STARTTLS) |
| `SMTP_USER` | *(required)* | SMTP login / sender address |
| `SMTP_PASSWORD` | *(required)* | SMTP password or app password |
| `PHONE_NUMBER` | *(required)* | 10-digit phone number (formatting stripped automatically) |
| `CARRIER` | `tmobile` | Carrier name (see supported list above) |
| `CALENDAR_IDS` | `primary` | Comma-separated calendar IDs |
| `PER_CALENDAR` | `false` | Send one SMS per calendar instead of combining |
| `TIMEZONE` | `America/New_York` | IANA timezone for the agenda |
| `LOOKAHEAD_DAYS` | `7` | Days ahead to include one-off upcoming events |
| `RECURRING_CHECK_WEEKS` | `4` | Weeks of history to use for recurring-event detection |
| `AGENDA_DATE` | *(today)* | Override the agenda date (`YYYY-MM-DD`) |

## CLI flags

| Flag | Description |
|---|---|
| `--auth` | Run OAuth2 setup flow and exit |
| `--dry-run` | Print agenda to stdout; do not send SMS |
