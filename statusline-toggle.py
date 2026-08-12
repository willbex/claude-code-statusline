#!/usr/bin/env python3
"""Toggle the Claude Code status line between the custom script and ccstatusline.

Usage:
    statusline-toggle.py            # toggle
    statusline-toggle.py mine       # force the custom Python status line
    statusline-toggle.py cc         # force ccstatusline
    statusline-toggle.py status     # print which one is active

Only ~/.claude/settings.json is touched, and only its "statusLine" key.
"""
import json
import os
import sys

CONFIG_DIR = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
SETTINGS = os.path.join(CONFIG_DIR, "settings.json")

MINE = {
    "type": "command",
    "command": "~/.claude/statusline.py",
    "refreshInterval": 10,
}
CC = {
    "type": "command",
    "command": "ccstatusline",
    "padding": 0,
    "refreshInterval": 10,
}


def load():
    with open(SETTINGS) as f:
        return json.load(f)


def active(cfg):
    return "cc" if "ccstatusline" in cfg.get("statusLine", {}).get("command", "") else "mine"


def main():
    cfg = load()
    now = active(cfg)
    arg = sys.argv[1] if len(sys.argv) > 1 else None

    if arg == "status":
        print(now)
        return

    target = arg if arg in ("mine", "cc") else ("mine" if now == "cc" else "cc")
    cfg["statusLine"] = dict(MINE if target == "mine" else CC)

    with open(SETTINGS, "w") as f:
        json.dump(cfg, f, indent=2)
        f.write("\n")

    print(f"{now} -> {target}   (restart Claude Code or /config to pick it up)")


if __name__ == "__main__":
    main()
