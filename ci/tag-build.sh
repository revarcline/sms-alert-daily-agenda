#!/usr/bin/env bash
# Build tagging script — updates pyproject.toml version, commits, and pushes a git tag
# that triggers the GitHub Actions release workflow.
#
# Usage:
#   ./ci/tag-build.sh dev <local-label>   # 0.1.0+some.dev.build.1  (no hyphens in label)
#   ./ci/tag-build.sh rc <N>              # 0.1.0rc1
#   ./ci/tag-build.sh release             # 0.1.0  (strips any pre/local suffix)

set -euo pipefail

MODE="${1:-}"

usage() {
    echo "Usage: $0 {dev <label>|rc <n>|release}" >&2
    exit 1
}

[[ -z "$MODE" ]] && usage

# Must run from repo root
if [[ ! -f pyproject.toml ]]; then
    echo "Error: pyproject.toml not found. Run from the repo root." >&2
    exit 1
fi

# Require clean working tree
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Error: working tree has uncommitted changes. Commit or stash first." >&2
    exit 1
fi

# Extract X.X.X base from current version (strips any existing rc/local suffix)
BASE_VERSION=$(python3 -c "
import re, sys, tomllib
with open('pyproject.toml', 'rb') as f:
    data = tomllib.load(f)
v = data['project']['version']
m = re.match(r'^(\d+\.\d+\.\d+)', v)
if not m:
    sys.exit(f'Cannot parse base version from: {v!r}')
print(m.group(1))
")

case "$MODE" in
    dev)
        LABEL="${2:-}"
        [[ -z "$LABEL" ]] && { echo "Usage: $0 dev <local-label>" >&2; exit 1; }
        if [[ ! "$LABEL" =~ ^[A-Za-z0-9][A-Za-z0-9.]*$ ]]; then
            echo "Error: local label must match [A-Za-z0-9][A-Za-z0-9.]* (no hyphens allowed)." >&2
            exit 1
        fi
        NEW_VERSION="${BASE_VERSION}+${LABEL}"
        ;;
    rc)
        N="${2:-}"
        if [[ ! "$N" =~ ^[1-9][0-9]*$ ]]; then
            echo "Error: rc requires a positive integer (e.g. $0 rc 1)." >&2
            exit 1
        fi
        NEW_VERSION="${BASE_VERSION}rc${N}"
        ;;
    release)
        NEW_VERSION="${BASE_VERSION}"
        ;;
    *)
        echo "Unknown mode: '$MODE'. Use: dev, rc, or release." >&2
        usage
        ;;
esac

echo "New version: ${NEW_VERSION}"

# Returns 0 if the installed uv is >= MAJOR.MINOR
uv_version_gte() {
    local req_major=$1 req_minor=$2
    local ver major minor
    ver=$(uv --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    [[ -z "$ver" ]] && return 1
    major=$(echo "$ver" | cut -d. -f1)
    minor=$(echo "$ver" | cut -d. -f2)
    [[ "$major" -gt "$req_major" ]] && return 0
    [[ "$major" -eq "$req_major" && "$minor" -ge "$req_minor" ]] && return 0
    return 1
}

# uv version <value> (project version management) was added in 0.6.0
if command -v uv &>/dev/null && uv_version_gte 0 6; then
    uv version "${NEW_VERSION}"
else
    if ! command -v uv &>/dev/null; then
        echo "Warning: uv not found; falling back to sed." >&2
    else
        UV_VER=$(uv --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
        echo "Warning: uv ${UV_VER} < 0.6.0; falling back to sed." >&2
    fi
    sed -i "s/^version = \"[^\"]*\"/version = \"${NEW_VERSION}\"/" pyproject.toml
fi

# Verify pyproject.toml was updated correctly
ACTUAL=$(python3 -c "
import tomllib
with open('pyproject.toml', 'rb') as f:
    print(tomllib.load(f)['project']['version'])
")
if [[ "$ACTUAL" != "$NEW_VERSION" ]]; then
    echo "Error: version update failed (got '${ACTUAL}', expected '${NEW_VERSION}')." >&2
    exit 1
fi

# Sync lock file
if command -v uv &>/dev/null; then
    uv lock --quiet
    git add uv.lock
fi

TAG="v${NEW_VERSION}"

git add pyproject.toml
git commit -m "chore: bump version to ${NEW_VERSION}"
git push
git tag "$TAG"
git push origin "$TAG"

echo "Tagged and pushed: ${TAG}"
