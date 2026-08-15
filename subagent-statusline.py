#!/usr/bin/env python3
"""Claude Code subagent status line.

Replaces each row in the agent panel with:
    ● name  description…  Model · effort · ctx%

Receives every visible task in one JSON object on stdin; writes one
{"id", "content"} JSON line per row to stdout.
"""
import json
import os
import re
import sys

DIM = "\033[2m"
CYAN = "\033[38;5;110m"
GREEN = "\033[38;5;108m"
AMBER = "\033[38;5;179m"
RED = "\033[38;5;167m"
RESET = "\033[0m"
SEP = f" {DIM}·{RESET} "

# Same two-scale thresholds as the main status line — see statusline.py.
ROT_AMBER, ROT_RED = 120_000, 350_000       # tokens
COMPACT_AMBER, COMPACT_RED = 55, 75         # percent of window


def context_level(pct, tokens):
    """0 fine, 1 warn, 2 critical — the harsher of the two scales wins.

    Duplicated verbatim in statusline.py. Rows stay uncoloured at level 0; a
    panel full of green would be noise.
    """
    rot = 2 if tokens >= ROT_RED else 1 if tokens >= ROT_AMBER else 0
    compaction = 2 if pct >= COMPACT_RED else 1 if pct >= COMPACT_AMBER else 0
    return max(rot, compaction)

STATUS = {
    "running": (CYAN, "●"),
    "in_progress": (CYAN, "●"),
    "active": (CYAN, "●"),
    "pending": (DIM, "○"),
    "queued": (DIM, "○"),
    "completed": (GREEN, "✓"),
    "done": (GREEN, "✓"),
    "success": (GREEN, "✓"),
    "failed": (RED, "✗"),
    "error": (RED, "✗"),
    "cancelled": (DIM, "✗"),
    "canceled": (DIM, "✗"),
}

ANSI = re.compile(r"\033\[[0-9;]*m")


def visible_len(s):
    return len(ANSI.sub("", s))


def as_int(value, default=0):
    """Coerce a number off the wire — statusline.py explains the shapes."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def fmt_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n // 1000}k"
    return str(n)


def pretty_model(model_id):
    """claude-haiku-4-5-20251001 -> Haiku 4.5; claude-opus-5[1m] -> Opus 5.

    The window size is dropped here and on the main status line — the row
    already carries a context percentage, and the tag was pure width.
    """
    if not model_id:
        return None
    m = model_id.split("/")[-1]
    m = re.sub(r"^(us|eu|apac)\.", "", m)
    m = re.sub(r"^anthropic\.", "", m)
    m = m.replace("[1m]", "")
    m = re.sub(r"^claude-", "", m)
    # Bedrock version and release date can both be present; strip until stable.
    while (stripped := re.sub(r"(-v\d+:\d+|-\d{8})$", "", m)) != m:
        m = stripped
    parts = [p for p in m.split("-") if p]
    if not parts:
        return model_id
    if parts[0].isdigit():  # 3.x naming puts the version first: 3-5-sonnet
        name = parts[-1].capitalize()
        version = ".".join(parts[:-1])
    else:
        name = parts[0].capitalize()
        version = ".".join(parts[1:])
    return f"{name} {version}".strip()


def meta_path(session_id):
    """Hand-off file written by statusline.py — keep the two in step."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    safe = "".join(c if c.isalnum() or c == "-" else "_" for c in str(session_id))
    return os.path.join(base, "cache", "statusline", f"session-meta-{safe}.json")


def session_effort(session_id):
    """Effort a subagent inherited from the session, exported by statusline.py."""
    if not session_id:
        return None
    try:
        with open(meta_path(session_id)) as f:
            meta = json.load(f)
    except (OSError, ValueError):
        return None
    # Shared state on disk: valid JSON of the wrong shape reads as absent.
    return meta.get("effort") if isinstance(meta, dict) else None


def fmt_effort(task, inherited):
    """Own effort renders plain; an inherited one renders dim."""
    own = task.get("effort")
    if own is None:
        return f"{DIM}{inherited}{RESET}" if inherited else None
    if isinstance(own, (int, float)):
        return fmt_tokens(int(own))
    return str(own)


def render(task, columns, inherited):
    color, glyph = STATUS.get(str(task.get("status", "")).lower(), (DIM, "●"))

    # `name` is the allocated agent name (Explore, Explore-2) — the only thing
    # that tells parallel rows apart. `label` is the live progress summary, but
    # falls back to the description, so it can never take the name slot.
    name = task.get("name") or task.get("type") or "agent"

    metrics = []
    model = pretty_model(task.get("model"))
    if model:
        metrics.append(model)
    effort = fmt_effort(task, inherited)
    if effort:
        metrics.append(effort)

    tokens = as_int(task.get("tokenCount"))
    size = as_int(task.get("contextWindowSize"))
    if not tokens:
        # Nothing measured yet — queued, or no API response has landed. See statusline.py.
        metrics.append(f"{DIM}—{RESET}")
    elif size:
        pct = int(tokens * 100 / size)
        pct_color = ("", AMBER, RED)[context_level(pct, tokens)]
        metrics.append(f"{pct_color}{pct}%{RESET if pct_color else ''} {DIM}({fmt_tokens(tokens)}){RESET}")
    elif tokens:
        metrics.append(f"{DIM}{fmt_tokens(tokens)}{RESET}")

    left = f"{color}{glyph}{RESET} {name}"
    right = SEP.join(metrics)

    # Trailing text fills whatever space is left between the two. Once a progress
    # summary has landed, `label` diverges from the static description and is the
    # more useful of the two.
    desc = task.get("label") or task.get("description") or ""
    desc = desc.strip().replace("\n", " ")
    room = columns - visible_len(left) - visible_len(right) - 6
    if desc and room >= 8:
        if len(desc) > room:
            desc = desc[: room - 1].rstrip() + "…"
        left = f"{left}  {DIM}{desc}{RESET}"

    return f"{left}  {right}" if right else left


def main():
    data = json.load(sys.stdin)
    columns = data.get("columns")
    if columns is None:
        columns = 100  # 0 is a real width the harness sends on a narrow terminal
    inherited = session_effort(data.get("session_id"))

    for task in data.get("tasks") or []:
        task_id = task.get("id")
        if not task_id:
            continue
        try:
            content = render(task, columns, inherited)
        except Exception:
            # One malformed task must not cost the other rows their decoration.
            continue
        print(json.dumps({"id": task_id, "content": content}))


# Non-UTF-8 locales would otherwise blow up on the glyphs below, taking the
# whole panel with them.
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass

try:
    main()
except Exception:
    # The harness discards *every* row's decoration on a non-zero exit, so a
    # failure here must still exit 0 and keep whatever was already printed.
    pass
