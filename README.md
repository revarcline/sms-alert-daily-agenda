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
- **Multi-account** — each account is an independent systemd instance with its own Google identity, phone number, and send time
- **Multi-calendar support** — combine calendars or send one SMS per calendar
- **Auto auth-alert** — emails you if the Google token expires so the bot doesn't silently fail
- **systemd integration** — ships with a template service and per-instance timers for unattended daily delivery

## Supported carriers

T-Mobile, AT&T, Verizon, Sprint, Boost, Cricket, Metro PCS, US Cellular, Virgin Mobile, Google Fi

---

## Installation

Use `ci/install-from-whl.sh` to fetch the latest wheel from GitHub Releases and install it into a shared venv:

```bash
./ci/install-from-whl.sh              # latest stable release
./ci/install-from-whl.sh -v 0.2.0    # specific version
./ci/install-from-whl.sh -p /opt/agenda/venv  # custom venv path
```

This only installs the application. Account configuration is handled separately by the setup wizard.

---

## Adding an account

Each account gets its own directory under `~/agenda/accounts/<label>/` with its own `.env`, Google credentials, and OAuth token. All accounts share the venv.

### Prerequisites

**Google Cloud** (once per Google account you want to connect):

1. Go to [Google Cloud Console](https://console.cloud.google.com/) → APIs & Services → Library
2. Enable the **Google Calendar API**
3. Go to Credentials → **Create credentials** → OAuth 2.0 Client ID (Desktop app type)
4. Download the JSON — you'll need it during setup

**Gmail app password** (once per sending address):

Google account → Security → 2-Step Verification → App passwords. Generate one for "Mail". Multiple accounts sharing a Gmail sender only need one app password.

### Run the wizard

```bash
./ci/setup-account.sh
```

The wizard will:

1. Ask for an account label (e.g. `alice`, `work`) — this becomes the systemd instance name
2. Optionally import shared settings (SMTP credentials, timezone, behaviour) from an existing account's `.env`, then only ask for the account-specific fields (phone number, carrier, calendar IDs)
3. Write `~/agenda/accounts/<label>/.env`
4. Install the `daily-agenda@.service` template unit if not already present, then create and enable a per-instance timer at the time you choose
5. Check for an existing `credentials.json` in other accounts and offer to reuse it — or wait for you to place a new one, then run the Google OAuth flow

**Headless servers:** the OAuth step opens a browser. The wizard detects SSH sessions and prints the exact `ssh -L` tunnel command to run from your local machine.

To authenticate manually after setup:

```bash
cd ~/agenda/accounts/<label>
daily-agenda --auth
```

### Adding more accounts

Run the wizard again. It will:

- Offer to import SMTP credentials, timezone, and behaviour settings from any existing account's `.env` — you'll only be asked for the phone number, carrier, calendar IDs, and send time
- Detect any existing `credentials.json` and offer to copy it into the new account directory — useful when multiple Google accounts were authorized through the same OAuth app registration

```bash
./ci/setup-account.sh
```

---

## Directory layout

```
~/agenda/
├── venv/                            # shared venv, one install
└── accounts/
    ├── alice/
    │   ├── .env
    │   ├── credentials.json         # Google OAuth client (download from Cloud Console)
    │   └── token.json               # auto-generated on first --auth
    └── bob/
        ├── .env
        ├── credentials.json
        └── token.json
```

`credentials.json` and `token.json` are resolved relative to the account directory via `WorkingDirectory` in the service unit, so no explicit paths are needed in `.env`.

---

## Managing instances

```bash
# Check timer status for an account
systemctl status daily-agenda@alice.timer

# View logs
journalctl -u daily-agenda@alice.service

# Trigger a run immediately (dry-run via env override)
systemctl start daily-agenda@alice.service

# Disable an account's timer
sudo systemctl disable --now daily-agenda@alice.timer
```

The timer `OnCalendar` is set per-instance at wizard time. To change the send time for an account, edit `/etc/systemd/system/daily-agenda@<label>.timer` and run `sudo systemctl daemon-reload`.

---

## Testing

```bash
cd ~/agenda/accounts/<label>
daily-agenda --dry-run
```

Prints the formatted agenda to stdout without sending anything.

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
./ci/tag-build.sh rc 1        # → 0.1.0rc1
./ci/tag-build.sh release     # → 0.1.0  (strips rc/dev suffix if present)
./ci/tag-build.sh dev build.1 # → 0.1.0+build.1
```

---

## Configuration reference

All configuration is via environment variables in each account's `.env` file.

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
