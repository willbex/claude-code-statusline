#!/usr/bin/env bash
#
# Installs the status line into your Claude Code configuration directory.
#
#   ./install.sh              copy the scripts and wire them into settings.json
#   ./install.sh --uninstall  unwire them and delete the copies
#
# The existing settings.json is backed up before every write, and only the
# entries this script itself wrote are ever rewired or removed.

set -euo pipefail

die() {
  printf 'error: %s\n' "$1" >&2
  exit 1
}

# The default matches meta_path() in statusline.py and subagent-statusline.py —
# keep the three in step.
CLAUDE_DIR="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"

while [ "${CLAUDE_DIR%/}" != "$CLAUDE_DIR" ] && [ "$CLAUDE_DIR" != "/" ]; do
  CLAUDE_DIR="${CLAUDE_DIR%/}"
done
case "$CLAUDE_DIR" in
  /*) ;;
  *) CLAUDE_DIR="$PWD/$CLAUDE_DIR" ;;
esac

# A command under $HOME is written with ~ so synced dotfiles keep working on
# every machine.
case "$CLAUDE_DIR" in
  "$HOME") CMD_DIR="~" ;;
  "$HOME"/*) CMD_DIR="~${CLAUDE_DIR#"$HOME"}" ;;
  *) CMD_DIR="$CLAUDE_DIR" ;;
esac

# CMD_DIR is the exact string settings.json will carry inside a shell command,
# so it is what gets validated — after normalization, once tilde substitution
# has already hidden whatever $HOME itself contains. Spaces, quotes, `;`, `$`
# and the rest would silently break every render.
BAD_CHARS="${CMD_DIR//\//}"
BAD_CHARS="${BAD_CHARS//[[:alnum:]_.+@,~-]/}"
if [ -n "$BAD_CHARS" ]; then
  die "the install directory would be written into settings.json as $CMD_DIR, and a shell command cannot safely carry: $BAD_CHARS"
fi

SETTINGS="$CLAUDE_DIR/settings.json"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FILES=(statusline.py subagent-statusline.py)

usage() {
  cat <<'EOF'
Usage: ./install.sh [--uninstall] [--help]

  (no flags)   Copy statusline.py and subagent-statusline.py into your Claude
               Code directory and point settings.json at them.
  --uninstall  Drop the settings.json entries this script wrote and delete the
               two copied scripts.
  --help       Show this message.

Set CLAUDE_CONFIG_DIR to choose the install directory (default ~/.claude).
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

edit_settings() {
  "$PYTHON" - "$SETTINGS" "$1" "$CMD_DIR" "$CLAUDE_DIR" "$SRC_DIR" <<'PY'
import filecmp
import json
import os
import shutil
import sys
import time

settings_path, mode, cmd_dir, claude_dir, src_dir = sys.argv[1:6]
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


def commands_for(script):
    """Both spellings of the command install writes: portable and absolute."""
    return ("%s/%s" % (cmd_dir, script), "%s/%s" % (claude_dir, script))


def owned(entry, script):
    """True for exactly the entry install writes — all uninstall may touch."""
    return (
        isinstance(entry, dict)
        and entry.get("type") == "command"
        and entry.get("command") in commands_for(script)
    )


def referenced(script):
    """A surviving entry naming the file keeps it on disk, whoever wrote it."""
    for entry in settings.values():
        if isinstance(entry, dict):
            command = entry.get("command")
            if isinstance(command, str) and any(p in command for p in commands_for(script)):
                return True
    return False


changes = []
notes = []
remove = []

if mode == "install":
    for key, script in keys.items():
        entry = settings.get(key)
        if owned(entry, script):
            continue
        command = "%s/%s" % (cmd_dir, script)
        if isinstance(entry, dict):
            # Own only type and command. refreshInterval and any other tuning
            # inside the entry is the user's and survives an update.
            settings[key] = dict(entry, type="command", command=command)
            notes.append("  repointed your previous %s, keeping its other fields" % key)
        else:
            if entry is not None:
                notes.append("  replaced your previous %s" % key)
            fresh = {"type": "command", "command": command}
            if key == "statusLine":
                fresh["refreshInterval"] = 10
            settings[key] = fresh
        changes.append(key)
else:
    for key, script in keys.items():
        entry = settings.get(key)
        if owned(entry, script):
            del settings[key]
            changes.append(key)
            remove.append(script)
        elif entry is not None:
            target = entry.get("command") if isinstance(entry, dict) else None
            shown = target if isinstance(target, str) and target else "something else"
            notes.append("  left %s alone, it points at %s" % (key, shown))
    # A copy whose settings entry is already gone is still this installer's to
    # delete when its bytes match the repo's script; anything edited stays put.
    for script in keys.values():
        path = os.path.join(claude_dir, script)
        try:
            orphan = (
                script not in remove
                and os.path.isfile(path)
                and filecmp.cmp(path, os.path.join(src_dir, script), shallow=False)
            )
        except OSError:
            orphan = False
        if orphan:
            remove.append(script)
    kept = [script for script in remove if referenced(script)]
    for script in kept:
        notes.append("  kept %s, a settings.json entry still points at it" % script)
    remove = [script for script in remove if script not in kept]

for note in notes:
    print(note)

if not changes and not remove:
    if mode == "uninstall":
        print("Nothing to remove from %s." % cmd_dir)
    sys.exit(0)

# Only worth a backup once there is something to overwrite.
if changes and os.path.exists(settings_path):
    stem = "%s.backup-%s" % (settings_path, time.strftime("%Y%m%d%H%M%S"))
    # Two runs in the same second must not cost you the older backup.
    backup_path, suffix = stem, 1
    while os.path.exists(backup_path):
        backup_path = "%s-%d" % (stem, suffix)
        suffix += 1
    # copy2 keeps the mode: a private 0600 settings.json gets a 0600 backup.
    shutil.copy2(settings_path, backup_path)
    print("  saved your old settings.json as %s" % os.path.basename(backup_path))

if changes:
    with open(settings_path, "w", encoding="utf-8") as handle:
        json.dump(settings, handle, indent=2)
        handle.write("\n")

# The settings write is the commitment point: only scripts it stopped
# referencing are deleted, and only once it has landed.
for script in remove:
    try:
        os.unlink(os.path.join(claude_dir, script))
    except OSError:
        pass

if mode == "uninstall":
    print("Removed from %s" % cmd_dir)
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
    # mv replaces a symlink sitting at the destination; cp alone would write
    # through it into whatever file the user pointed it at.
    cp "$SRC_DIR/$file" "$CLAUDE_DIR/$file.tmp.$$"
    chmod +x "$CLAUDE_DIR/$file.tmp.$$"
    mv -f "$CLAUDE_DIR/$file.tmp.$$" "$CLAUDE_DIR/$file"
  done

  edit_settings install
  printf 'Installed to %s\n' "$CMD_DIR"
else
  edit_settings uninstall
fi
