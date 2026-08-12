#!/usr/bin/env bash
#
# Installs the status line into your Claude Code configuration directory.
#
#   ./install.sh              copy the scripts and wire them into settings.json
#   ./install.sh --uninstall  remove the scripts and unwire them
#
# The existing settings.json is backed up before every write.

set -euo pipefail

CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
SETTINGS="$CLAUDE_DIR/settings.json"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES=(statusline.py subagent-statusline.py)

die() {
  printf 'error: %s\n' "$1" >&2
  exit 1
}

usage() {
  cat <<'EOF'
Usage: ./install.sh [--uninstall] [--help]

  (no flags)   Copy statusline.py and subagent-statusline.py into your Claude
               Code directory and point settings.json at them.
  --uninstall  Remove both scripts and drop the settings.json keys that point
               at them.
  --help       Show this message.

Set CLAUDE_CONFIG_DIR to install somewhere other than ~/.claude.
EOF
}

MODE=install
case "${1-}" in
  --uninstall) MODE=uninstall ;;
  --help | -h)
    usage
    exit 0
    ;;
  "") ;;
  *) die "unknown option: $1 (try --help)" ;;
esac

# Locate an interpreter new enough for the status line itself.
PYTHON=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 &&
    "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
    PYTHON="$candidate"
    break
  fi
done
[ -n "$PYTHON" ] || die "Python 3.8+ is required and was not found on PATH."

# Keep the command portable across machines when the directory is the default one.
if [ "$CLAUDE_DIR" = "$HOME/.claude" ]; then
  CMD_DIR="~/.claude"
else
  CMD_DIR="$CLAUDE_DIR"
fi

edit_settings() {
  "$PYTHON" - "$SETTINGS" "$1" "$CMD_DIR" <<'PY'
import json
import os
import shutil
import sys
import time

settings_path, mode, cmd_dir = sys.argv[1], sys.argv[2], sys.argv[3]
keys = {
    "statusLine": "statusline.py",
    "subagentStatusLine": "subagent-statusline.py",
}

if os.path.exists(settings_path):
    with open(settings_path, encoding="utf-8") as handle:
        text = handle.read()
    try:
        settings = json.loads(text) if text.strip() else {}
    except json.JSONDecodeError as exc:
        sys.exit(
            "%s is not valid JSON (%s). Fix it by hand and run this again; "
            "nothing was changed." % (settings_path, exc)
        )
    if not isinstance(settings, dict):
        sys.exit("%s must contain a JSON object; nothing was changed." % settings_path)
else:
    settings = {}

if mode == "check":
    sys.exit(0)

changes = []
notes = []

if mode == "install":
    for key, script in keys.items():
        entry = {"type": "command", "command": "%s/%s" % (cmd_dir, script)}
        if key == "statusLine":
            entry["refreshInterval"] = 10
        if settings.get(key) == entry:
            continue
        previous = settings.get(key)
        settings[key] = entry
        changes.append(key)
        if previous is not None:
            notes.append("  replaced your previous %s" % key)
else:
    for key, script in keys.items():
        entry = settings.get(key)
        if entry is None:
            continue
        command = entry.get("command", "") if isinstance(entry, dict) else ""
        # Leave a status line someone else configured alone.
        if not command.endswith("/" + script):
            notes.append(
                "  left %s alone, it points at %s" % (key, command or "something else")
            )
            continue
        del settings[key]
        changes.append(key)

for note in notes:
    print(note)

if not changes:
    sys.exit(0)

# Only worth a backup once there is something to overwrite.
if os.path.exists(settings_path):
    stem = "%s.backup-%s" % (settings_path, time.strftime("%Y%m%d%H%M%S"))
    # Two runs in the same second must not cost you the older backup.
    backup_path, suffix = stem, 1
    while os.path.exists(backup_path):
        backup_path = "%s-%d" % (stem, suffix)
        suffix += 1
    shutil.copyfile(settings_path, backup_path)
    print("  saved your old settings.json as %s" % os.path.basename(backup_path))

with open(settings_path, "w", encoding="utf-8") as handle:
    json.dump(settings, handle, indent=2)
    handle.write("\n")
PY
}

# Refuse early on unreadable settings, before any file is copied or removed.
edit_settings check

if [ "$MODE" = install ]; then
  for file in "${FILES[@]}"; do
    [ -f "$SRC_DIR/$file" ] || die "$file is missing from $SRC_DIR."
  done

  mkdir -p "$CLAUDE_DIR"
  for file in "${FILES[@]}"; do
    cp "$SRC_DIR/$file" "$CLAUDE_DIR/$file"
    chmod +x "$CLAUDE_DIR/$file"
  done

  printf 'Installed to %s\n' "$CMD_DIR"
  edit_settings "$MODE"
else
  for file in "${FILES[@]}"; do
    rm -f "$CLAUDE_DIR/$file"
  done

  printf 'Removed from %s\n' "$CMD_DIR"
  edit_settings "$MODE"
fi
