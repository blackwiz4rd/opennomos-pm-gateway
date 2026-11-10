#!/usr/bin/env bash
#
# Install the pm-gateway git hooks into the current repository.
#
# Works for a normal clone and for the OpenNomos submodule layout, where the git
# directory lives outside the working tree (e.g. .git/modules/pm-gateway).
#
# Usage:
#   ./scripts/install-hooks.sh            # install ./hooks/pre-push
#   ./scripts/install-hooks.sh --force    # replace a hook we do not own
#   ./scripts/install-hooks.sh --uninstall
#   ./scripts/install-hooks.sh --check    # report status, change nothing
#
# Re-running is safe: the installed hook is rewritten atomically.

set -euo pipefail

HOOK_NAME="pre-push"
OWNER_MARKER="pm-gateway secret scan"

usage() {
  sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'
}

FORCE=0
MODE="install"
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --uninstall) MODE="uninstall" ;;
    --check) MODE="check" ;;
    -h|--help) usage; exit 0 ;;
    *) echo "install-hooks: unknown argument: $arg" >&2; usage >&2; exit 2 ;;
  esac
done

ROOT="$(git rev-parse --show-toplevel)"
GIT_DIR="$(cd "$(git rev-parse --git-dir)" && pwd)"
SOURCE="$ROOT/hooks/$HOOK_NAME"
TARGET="$GIT_DIR/hooks/$HOOK_NAME"

case "$MODE" in
  check)
    if [ -x "$TARGET" ] && grep -q "$OWNER_MARKER" "$TARGET" 2>/dev/null; then
      echo "installed: $TARGET"
      exit 0
    fi
    echo "not installed: $TARGET"
    exit 1
    ;;
  uninstall)
    if [ -f "$TARGET" ] && grep -q "$OWNER_MARKER" "$TARGET" 2>/dev/null; then
      rm -f "$TARGET"
      echo "removed: $TARGET"
    else
      echo "nothing to remove: $TARGET is absent or not managed by this script"
    fi
    exit 0
    ;;
esac

[ -f "$SOURCE" ] || { echo "install-hooks: missing $SOURCE" >&2; exit 1; }

mkdir -p "$GIT_DIR/hooks"

if [ -e "$TARGET" ] && ! grep -q "$OWNER_MARKER" "$TARGET" 2>/dev/null; then
  if [ "$FORCE" -ne 1 ]; then
    cat >&2 <<EOF
install-hooks: $TARGET already exists and is not managed by this script.
It may belong to the pre-commit framework or to another tool.

  - inspect it, then re-run with --force (a backup is kept), or
  - drop this repository's pre-push hook and add the pre-commit stage instead
    (see .pre-commit-config.yaml).
EOF
    exit 1
  fi
  BACKUP="$TARGET.backup.$(date +%Y%m%d%H%M%S)"
  cp "$TARGET" "$BACKUP"
  echo "backed up existing hook to: $BACKUP"
fi

# Copy to a temp file in the same directory, then rename: a concurrent `git push`
# never observes a half-written hook.
TMP="$TARGET.tmp.$$"
trap 'rm -f "$TMP"' EXIT
cp "$SOURCE" "$TMP"
chmod +x "$TMP"
mv "$TMP" "$TARGET"
trap - EXIT

echo "installed: $TARGET"
echo "override:  ALLOW_SECRETS=1 git push   |   git push --no-verify"
