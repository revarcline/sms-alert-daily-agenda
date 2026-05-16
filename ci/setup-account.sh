#!/usr/bin/env bash
# Interactive wizard to add a daily-agenda account instance.
#
# Creates ~/agenda/accounts/<label>/ with a tailored .env, walks through
# Google OAuth, and installs + enables the per-account systemd timer.
#
# Usage:
#   ./ci/setup-account.sh               # uses ~/agenda/venv
#   ./ci/setup-account.sh --venv PATH   # non-default venv location

set -euo pipefail

DEFAULT_VENV_PATH="${HOME}/agenda/venv"
ACCOUNTS_BASE="${HOME}/agenda/accounts"
VENV_PATH=""

SUPPORTED_CARRIERS="tmobile, att, verizon, sprint, boost, cricket, metro, metropcs, uscellular, virgin, googlefi, fi"

# ── Helpers ───────────────────────────────────────────────────────────────────

hr()   { printf '─%.0s' {1..60}; echo; }
ok()   { echo "  ✓ $*"; }
warn() { echo "  ! $*" >&2; }
die()  { echo "Error: $*" >&2; exit 1; }

# prompt VARNAME "Question" [default]
prompt() {
    local -n _ref=$1
    local question="$2" default="${3:-}"
    local input
    if [[ -n "$default" ]]; then
        read -r -p "  ${question} [${default}]: " input
        _ref="${input:-$default}"
    else
        while true; do
            read -r -p "  ${question}: " input
            [[ -n "$input" ]] && break
            warn "Required — please enter a value."
        done
        _ref="$input"
    fi
}

prompt_secret() {
    local -n _sref=$1
    local question="$2"
    local input
    while true; do
        read -r -s -p "  ${question}: " input; echo
        [[ -n "$input" ]] && break
        warn "Required — please enter a value."
    done
    _sref="$input"
}

# prompt_yn "Question" — returns 0 for yes, 1 for no
prompt_yn() {
    local input
    read -r -p "  $1 [y/N] " input
    [[ "$input" =~ ^[Yy]$ ]]
}

# Read a single value from a .env file (handles bare, quoted, and export-prefixed forms)
env_get() {
    local key="$1" file="$2"
    (grep -E "^(export )?${key}=" "$file" 2>/dev/null || true) | head -1 \
        | sed -E "s/^(export )?${key}=//; s/^['\"]//; s/['\"]$//"
}

# ── Argument parsing ──────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --venv|-V)
            [[ -z "${2:-}" ]] && die "--venv requires a path."
            VENV_PATH="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: $0 [--venv PATH]"; exit 0 ;;
        *)
            die "Unknown argument: '$1'" ;;
    esac
done

VENV_PATH="${VENV_PATH:-${DEFAULT_VENV_PATH}}"
VENV_BIN="${VENV_PATH}/bin"
DAILY_AGENDA="${VENV_BIN}/daily-agenda"

# ── Preflight ─────────────────────────────────────────────────────────────────

echo
hr
echo "  daily-agenda — account setup wizard"
hr
echo

[[ -x "$DAILY_AGENDA" ]] \
    || die "daily-agenda not found at ${DAILY_AGENDA}.\nRun ci/install-from-whl.sh first, or pass --venv PATH."

# ── Account label ─────────────────────────────────────────────────────────────

echo "  Each account becomes a systemd instance (daily-agenda@<label>)."
echo "  Labels: letters, digits, hyphens, underscores."
echo

LABEL=""
while true; do
    prompt LABEL "Account label (e.g. alice, work, personal)"
    [[ "$LABEL" =~ ^[A-Za-z0-9][A-Za-z0-9_-]*$ ]] && break
    warn "Invalid label '${LABEL}' — use letters, digits, hyphens, underscores."
done

ACCOUNT_DIR="${ACCOUNTS_BASE}/${LABEL}"
ENV_FILE="${ACCOUNT_DIR}/.env"

if [[ -d "$ACCOUNT_DIR" ]]; then
    echo
    warn "Account '${LABEL}' already exists at ${ACCOUNT_DIR}."
    prompt_yn "Overwrite its .env and reconfigure?" || { echo "  Aborted."; exit 0; }
fi

# ── Field defaults ────────────────────────────────────────────────────────────

SMTP_HOST="smtp.gmail.com"
SMTP_PORT="587"
SMTP_USER=""
SMTP_PASSWORD=""
PHONE_NUMBER=""
CARRIER="tmobile"
CALENDAR_IDS="primary"
TIMEZONE="America/New_York"
LOOKAHEAD_DAYS="7"
RECURRING_CHECK_WEEKS="4"
TIMER_TIME="06:00:00"

# ── Import from existing .env? ────────────────────────────────────────────────

echo
hr
echo

if prompt_yn "Import shared settings from an existing account .env?"; then
    echo
    IMPORT_PATH=""
    prompt IMPORT_PATH "Path to .env file"
    IMPORT_PATH="${IMPORT_PATH/#\~/$HOME}"
    [[ -f "$IMPORT_PATH" ]] || die "File not found: ${IMPORT_PATH}"

    _v=$(env_get SMTP_HOST              "$IMPORT_PATH"); SMTP_HOST="${_v:-$SMTP_HOST}"
    _v=$(env_get SMTP_PORT              "$IMPORT_PATH"); SMTP_PORT="${_v:-$SMTP_PORT}"
    _v=$(env_get SMTP_USER              "$IMPORT_PATH"); SMTP_USER="${_v:-}"
    _v=$(env_get SMTP_PASSWORD          "$IMPORT_PATH"); SMTP_PASSWORD="${_v:-}"
    _v=$(env_get TIMEZONE               "$IMPORT_PATH"); TIMEZONE="${_v:-$TIMEZONE}"
    _v=$(env_get LOOKAHEAD_DAYS         "$IMPORT_PATH"); LOOKAHEAD_DAYS="${_v:-$LOOKAHEAD_DAYS}"
    _v=$(env_get RECURRING_CHECK_WEEKS  "$IMPORT_PATH"); RECURRING_CHECK_WEEKS="${_v:-$RECURRING_CHECK_WEEKS}"

    echo
    echo "  Imported:"
    echo "    SMTP              ${SMTP_USER:-<not set>} via ${SMTP_HOST}:${SMTP_PORT}"
    echo "    TIMEZONE          ${TIMEZONE}"
    echo "    LOOKAHEAD_DAYS    ${LOOKAHEAD_DAYS}"
    echo "    RECURRING_CHECK_WEEKS  ${RECURRING_CHECK_WEEKS}"
    echo

    if [[ -z "$SMTP_USER" ]]; then
        echo "  SMTP credentials were not found in the imported file."
        prompt SMTP_USER "Gmail address (sender)"
        prompt_secret SMTP_PASSWORD "App password"
    fi

    echo "  Account-specific fields:"
    echo
    prompt PHONE_NUMBER "Phone number"
    echo "  Supported carriers: ${SUPPORTED_CARRIERS}"
    prompt CARRIER "Carrier" "tmobile"
    prompt CALENDAR_IDS "Calendar IDs (comma-separated)" "primary"
    prompt TIMEZONE "IANA timezone" "$TIMEZONE"

else
    echo
    echo "  SMTP / sender"
    echo
    prompt SMTP_USER "Gmail address (sender)"
    prompt_secret SMTP_PASSWORD "App password"
    prompt SMTP_HOST "SMTP host" "smtp.gmail.com"
    prompt SMTP_PORT "SMTP port" "587"

    echo
    echo "  SMS recipient"
    echo
    prompt PHONE_NUMBER "Phone number (digits only)"
    echo "  Supported carriers: ${SUPPORTED_CARRIERS}"
    prompt CARRIER "Carrier" "tmobile"

    echo
    echo "  Google Calendar"
    echo
    prompt CALENDAR_IDS "Calendar IDs (comma-separated, or 'primary')" "primary"
    prompt TIMEZONE "IANA timezone" "America/New_York"

    echo
    echo "  Behaviour"
    echo
    prompt LOOKAHEAD_DAYS        "Days ahead to show upcoming one-off events" "7"
    prompt RECURRING_CHECK_WEEKS "Weeks of history for recurring-event detection" "4"
fi

echo
echo "  Timer"
echo
prompt TIMER_TIME "Send time (HH:MM:SS, 24h)" "06:00:00"

# ── Write account directory and .env ──────────────────────────────────────────

echo
hr
echo

mkdir -p "$ACCOUNT_DIR" || die "Could not create ${ACCOUNT_DIR}."

cat > "$ENV_FILE" <<EOF
# daily-agenda account: ${LABEL}
# Generated $(date +%Y-%m-%d) by ci/setup-account.sh

# ── SMTP ──────────────────────────────────────────────────────────────────────
SMTP_HOST=${SMTP_HOST}
SMTP_PORT=${SMTP_PORT}
SMTP_USER=${SMTP_USER}
SMTP_PASSWORD=${SMTP_PASSWORD}

# ── Recipient ─────────────────────────────────────────────────────────────────
PHONE_NUMBER=${PHONE_NUMBER}
CARRIER=${CARRIER}

# ── Google Calendar ───────────────────────────────────────────────────────────
# Paths are relative to WorkingDirectory (${ACCOUNT_DIR}).
GOOGLE_CREDENTIALS_FILE=credentials.json
GOOGLE_TOKEN_FILE=token.json

# ── Schedule ──────────────────────────────────────────────────────────────────
CALENDAR_IDS=${CALENDAR_IDS}
TIMEZONE=${TIMEZONE}
LOOKAHEAD_DAYS=${LOOKAHEAD_DAYS}
RECURRING_CHECK_WEEKS=${RECURRING_CHECK_WEEKS}
EOF

ok "Wrote ${ENV_FILE}"

# ── Systemd template service + per-instance timer ─────────────────────────────

echo
hr
echo "  Systemd units"
hr
echo

SERVICE_UNIT="/etc/systemd/system/daily-agenda@.service"
TIMER_UNIT="/etc/systemd/system/daily-agenda@${LABEL}.timer"

INSTALLED_SERVICE=false
if [[ ! -f "$SERVICE_UNIT" ]]; then
    echo "  Template service unit not yet installed."
    echo
    if prompt_yn "Install ${SERVICE_UNIT}? (requires sudo)"; then
        sudo tee "$SERVICE_UNIT" > /dev/null <<EOF
[Unit]
Description=Daily Agenda SMS Alert (%i)
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=${USER}
WorkingDirectory=${ACCOUNTS_BASE}/%i
EnvironmentFile=${ACCOUNTS_BASE}/%i/.env
ExecStart=${DAILY_AGENDA}
StandardOutput=journal
StandardError=journal
SyslogIdentifier=daily-agenda@%i
EOF
        ok "Installed ${SERVICE_UNIT}"
        INSTALLED_SERVICE=true
    else
        warn "Skipped — install the template service manually before enabling the timer."
    fi
else
    ok "Template service already installed."
    INSTALLED_SERVICE=true
fi

echo
if $INSTALLED_SERVICE; then
    if prompt_yn "Install and enable timer for '${LABEL}'? (requires sudo)"; then
        sudo tee "$TIMER_UNIT" > /dev/null <<EOF
[Unit]
Description=Daily Agenda SMS Alert Timer (${LABEL})
Requires=daily-agenda@${LABEL}.service

[Timer]
OnCalendar=*-*-* ${TIMER_TIME}
Persistent=true

[Install]
WantedBy=timers.target
EOF
        sudo systemctl daemon-reload
        sudo systemctl enable --now "daily-agenda@${LABEL}.timer"
        ok "Timer enabled: daily-agenda@${LABEL}.timer"
    else
        echo
        echo "  Enable later with:"
        echo "    sudo systemctl enable --now daily-agenda@${LABEL}.timer"
    fi
fi

# ── Google OAuth ──────────────────────────────────────────────────────────────

echo
hr
echo "  Google Calendar authentication"
hr
echo

# Scan other accounts for an existing credentials.json to reuse
EXISTING_CREDS=()
if [[ -d "$ACCOUNTS_BASE" ]]; then
    while IFS= read -r -d '' creds_file; do
        account_name=$(basename "$(dirname "$creds_file")")
        [[ "$account_name" != "$LABEL" ]] && EXISTING_CREDS+=("$account_name")
    done < <(find "$ACCOUNTS_BASE" -mindepth 2 -maxdepth 2 -name "credentials.json" -print0)
fi

CREDS_READY=false
if [[ ${#EXISTING_CREDS[@]} -gt 0 ]]; then
    if [[ ${#EXISTING_CREDS[@]} -eq 1 ]]; then
        SOURCE_ACCOUNT="${EXISTING_CREDS[0]}"
        echo "  credentials.json detected for account '${SOURCE_ACCOUNT}'."
        if prompt_yn "Reuse it for '${LABEL}'?"; then
            cp "${ACCOUNTS_BASE}/${SOURCE_ACCOUNT}/credentials.json" "${ACCOUNT_DIR}/credentials.json"
            ok "Copied credentials.json from '${SOURCE_ACCOUNT}'."
            CREDS_READY=true
        fi
    else
        echo "  credentials.json found in the following accounts:"
        for i in "${!EXISTING_CREDS[@]}"; do
            echo "    $((i + 1))) ${EXISTING_CREDS[$i]}"
        done
        echo
        read -r -p "  Reuse one for '${LABEL}'? Enter a number, or N to skip: " REPLY
        if [[ "$REPLY" =~ ^[1-9][0-9]*$ ]] && (( REPLY >= 1 && REPLY <= ${#EXISTING_CREDS[@]} )); then
            SOURCE_ACCOUNT="${EXISTING_CREDS[$((REPLY - 1))]}"
            cp "${ACCOUNTS_BASE}/${SOURCE_ACCOUNT}/credentials.json" "${ACCOUNT_DIR}/credentials.json"
            ok "Copied credentials.json from '${SOURCE_ACCOUNT}'."
            CREDS_READY=true
        fi
    fi
    echo
fi

if ! $CREDS_READY; then
    echo "  Download your OAuth 2.0 credentials JSON from:"
    echo "    https://console.cloud.google.com/ → APIs & Services → Credentials"
    echo "  Save it as: ${ACCOUNT_DIR}/credentials.json"
    echo
    echo "  Press Enter once credentials.json is in place, or Ctrl+C to skip and auth later."
    read -r -p "  > " || true
    echo
fi

if [[ -f "${ACCOUNT_DIR}/credentials.json" ]]; then
    echo "  Running OAuth flow — a browser tab will open..."
    (
        cd "${ACCOUNT_DIR}"
        set -a
        # shellcheck source=/dev/null
        source ".env"
        set +a
        "${DAILY_AGENDA}" --auth
    )
    ok "Authentication complete. Token saved to ${ACCOUNT_DIR}/token.json"
else
    warn "credentials.json not found — authenticate manually when ready:"
    echo
    echo "    cd ${ACCOUNT_DIR}"
    echo "    ${DAILY_AGENDA} --auth"
fi

# ── Done ──────────────────────────────────────────────────────────────────────

echo
hr
ok "Account '${LABEL}' configured."
echo
echo "  Dry-run test:"
echo "    cd ${ACCOUNT_DIR} && ${DAILY_AGENDA} --dry-run"
echo
echo "  View logs:"
echo "    journalctl -u daily-agenda@${LABEL}.service"
hr
echo
