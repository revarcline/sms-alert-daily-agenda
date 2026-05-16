#!/usr/bin/env bash
# Install daily-agenda from a GitHub Releases wheel into a dedicated venv.
#
# Usage:
#   ./ci/install-from-whl.sh                           # latest → ~/agenda/venv
#   ./ci/install-from-whl.sh -v 0.2.0                  # specific version
#   ./ci/install-from-whl.sh -p /opt/agenda/venv       # custom venv path
#   ./ci/install-from-whl.sh -v 0.2.0 -p /opt/agenda/venv

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

DEFAULT_VENV_PATH="${HOME}/agenda/venv"
TARGET_VERSION=""
VENV_PATH=""
CUSTOM_PATH=false

# ── Argument parsing ──────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        -v|--version)
            [[ -z "${2:-}" ]] && { echo "Error: -v/--version requires a value." >&2; exit 1; }
            TARGET_VERSION="$2"; shift 2 ;;
        -p|--path)
            [[ -z "${2:-}" ]] && { echo "Error: -p/--path requires a value." >&2; exit 1; }
            VENV_PATH="$2"; CUSTOM_PATH=true; shift 2 ;;
        -h|--help)
            echo "Usage: $0 [-v VERSION] [-p VENV_PATH]"; exit 0 ;;
        *)
            echo "Error: unknown argument '$1'" >&2; exit 1 ;;
    esac
done

VENV_PATH="${VENV_PATH:-${DEFAULT_VENV_PATH}}"
VENV_BIN="${VENV_PATH}/bin"

# ── Resolve GitHub repo slug from git remote ──────────────────────────────────

REPO=$(git -C "${REPO_ROOT}" remote get-url origin 2>/dev/null | \
    sed -E -e 's|.*github\.com[:/]||' -e 's|\.git$||')

if [[ -z "$REPO" || ! "$REPO" =~ ^[^/]+/[^/]+$ ]]; then
    echo "Error: could not parse a GitHub repo slug from git remote 'origin'." >&2
    exit 1
fi

# ── Fetch release list ────────────────────────────────────────────────────────

echo "Fetching releases from github.com/${REPO}..."
if ! RELEASES_JSON=$(curl -fsSL "https://api.github.com/repos/${REPO}/releases" 2>&1); then
    echo "Error: GitHub API request failed:" >&2
    echo "  ${RELEASES_JSON}" >&2
    exit 1
fi

# ── Resolve version and download URL ─────────────────────────────────────────

if [[ -z "$TARGET_VERSION" ]]; then
    RESULT=$(echo "$RELEASES_JSON" | python3 -c "
import json, sys
releases = json.load(sys.stdin)
for r in releases:
    if r.get('draft') or r.get('prerelease'):
        continue
    for a in r.get('assets', []):
        if a['name'].endswith('.whl'):
            print(r['tag_name'].lstrip('v'))
            print(a['browser_download_url'])
            sys.exit(0)
print('No stable release with a .whl asset was found.', file=sys.stderr)
sys.exit(1)
") || exit 1
    TARGET_VERSION=$(echo "$RESULT" | head -1)
    DOWNLOAD_URL=$(echo "$RESULT"  | tail -1)
else
    # Normalize: accept with or without leading 'v'
    TARGET_VERSION="${TARGET_VERSION#v}"
    TAG="v${TARGET_VERSION}"
    DOWNLOAD_URL=$(echo "$RELEASES_JSON" | python3 -c "
import json, sys
tag = sys.argv[1]
releases = json.load(sys.stdin)
for r in releases:
    if r['tag_name'] == tag:
        for a in r.get('assets', []):
            if a['name'].endswith('.whl'):
                print(a['browser_download_url'])
                sys.exit(0)
        print(f'Release {tag} found but has no .whl asset.', file=sys.stderr)
        sys.exit(1)
print(f'Version tag {tag!r} not found in GitHub releases.', file=sys.stderr)
sys.exit(1)
" "$TAG") || exit 1
fi

WHL_FILENAME="$(basename "${DOWNLOAD_URL}")"

# ── Check existing install ────────────────────────────────────────────────────

INSTALLED_VERSION=""
if [[ -x "${VENV_BIN}/python" ]]; then
    INSTALLED_VERSION=$("${VENV_BIN}/python" -c \
        "from importlib.metadata import version; print(version('sms-alert-daily-agenda'))" \
        2>/dev/null || true)
fi

if [[ -n "$INSTALLED_VERSION" ]]; then
    if [[ "$INSTALLED_VERSION" == "$TARGET_VERSION" ]]; then
        echo "Version ${TARGET_VERSION} is already installed at ${VENV_PATH}. Nothing to do."
        exit 0
    fi
    echo "Version ${INSTALLED_VERSION} is currently installed at ${VENV_PATH}."
    read -r -p "Replace with ${TARGET_VERSION}? [y/N] " REPLY
    if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 0
    fi
fi

# ── Create venv ───────────────────────────────────────────────────────────────

if [[ ! -x "${VENV_BIN}/python" ]]; then
    PARENT_DIR="$(dirname "${VENV_PATH}")"
    if [[ ! -d "$PARENT_DIR" ]]; then
        echo "Creating ${PARENT_DIR}..."
        if ! mkdir -p "$PARENT_DIR" 2>/tmp/da-install-err; then
            echo "Error: could not create ${PARENT_DIR}: $(cat /tmp/da-install-err)" >&2
            exit 1
        fi
    fi
    echo "Creating venv at ${VENV_PATH}..."
    if ! python3 -m venv "${VENV_PATH}" 2>/tmp/da-install-err; then
        echo "Error: venv creation failed: $(cat /tmp/da-install-err)" >&2
        exit 1
    fi
fi

# ── Download wheel to /tmp ────────────────────────────────────────────────────

WHL_PATH="/tmp/${WHL_FILENAME}"
echo "Downloading ${WHL_FILENAME}..."
if ! curl -fsSL -o "${WHL_PATH}" "${DOWNLOAD_URL}" 2>/tmp/da-install-err; then
    echo "Error: download failed: $(cat /tmp/da-install-err)" >&2
    exit 1
fi

# ── Install ───────────────────────────────────────────────────────────────────

echo "Installing into ${VENV_PATH}..."
if ! "${VENV_BIN}/pip" install --quiet --force-reinstall "${WHL_PATH}" 2>/tmp/da-install-err; then
    echo "Error: pip install failed: $(cat /tmp/da-install-err)" >&2
    exit 1
fi

echo "Installed: ${VENV_BIN}/daily-agenda (${TARGET_VERSION})"

# ── Update systemd unit when a custom venv path was requested ─────────────────

if $CUSTOM_PATH; then
    SYSTEMD_UNIT="/etc/systemd/system/daily-agenda.service"
    NEW_EXEC="${VENV_BIN}/daily-agenda"
    if [[ -f "$SYSTEMD_UNIT" ]]; then
        if sudo sed -i "s|^ExecStart=.*|ExecStart=${NEW_EXEC}|" "$SYSTEMD_UNIT" 2>/tmp/da-install-err; then
            echo "Updated ExecStart in ${SYSTEMD_UNIT}."
            if sudo systemctl daemon-reload 2>/dev/null; then
                echo "Reloaded systemd daemon."
            fi
        else
            echo "Warning: could not update ${SYSTEMD_UNIT} ($(cat /tmp/da-install-err))." >&2
            echo "Update ExecStart manually:" >&2
            printf '    ExecStart=%s\n' "${NEW_EXEC}" >&2
        fi
    else
        echo "Note: ${SYSTEMD_UNIT} not installed yet."
        echo "When you install the service unit, set:"
        printf '    ExecStart=%s\n' "${NEW_EXEC}"
    fi
fi

rm -f /tmp/da-install-err
