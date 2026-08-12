#!/usr/bin/env python3
"""Claude Code main status line.

Line 1: model · effort · directory · git branch
Line 2: context bar · % · tokens · cache hit · cost · elapsed · weekly limit

Also writes the session's model/effort to a cache file so the subagent status
line can show which effort a subagent inherited from the session.
"""
import json
import os
import sys
import time

DIM = "\033[2m"
CYAN = "\033[38;5;110m"
GREEN = "\033[38;5;108m"
AMBER = "\033[38;5;179m"
RED = "\033[38;5;167m"
RESET = "\033[0m"
SEP = f" {DIM}·{RESET} "

# Two independent scales; the harsher one wins.
#
# Context rot tracks absolute tokens, not window fraction: a 1M window degrades
# at roughly the same token counts a 200k one does. Auto-compact, meanwhile,
# fires at ~83% of the window whatever its size, and compaction is lossy at the
# worst moment — so the red band has to start well before it.
ROT_AMBER, ROT_RED = 120_000, 350_000       # tokens
COMPACT_AMBER, COMPACT_RED = 55, 75         # percent of window

WEEK_AMBER, WEEK_RED = 50, 85               # percent of the 7-day quota

# Cache hit rate, in percent — these are floors, so lower is worse. Steady state
# on a warm prefix sits at 95-99%; anything under 90 means part of the prompt
# was rewritten, and under 60 means most of it was.
CACHE_AMBER, CACHE_RED = 90, 60


def fmt_tokens(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{n // 1000}k"
    return str(n)


def fmt_duration(ms):
    total = ms // 1000
    h, m, s = total // 3600, (total % 3600) // 60, total % 60
    if h:
        return f"{h}h {m:02d}m"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def fmt_countdown(seconds):
    """Coarse time-to-reset: days and hours are all that matter over a week."""
    if seconds <= 0:
        return "now"
    d, h = seconds // 86400, (seconds % 86400) // 3600
    if d:
        return f"{d}d {h}h" if h else f"{d}d"
    if h:
        return f"{h}h"
    return f"{max(1, seconds // 60)}m"


def weekly_usage(data):
    """The 7-day subscription quota — absent for API keys and before the first reply."""
    week = (data.get("rate_limits") or {}).get("seven_day") or {}
    pct = week.get("used_percentage")
    if pct is None:
        return None

    pct = int(pct)
    color = RED if pct >= WEEK_RED else AMBER if pct >= WEEK_AMBER else GREEN
    out = f"{color}7d {pct}%{RESET}"

    # The countdown only earns its width once the number is worth acting on.
    resets_at = week.get("resets_at")
    if resets_at and pct >= WEEK_AMBER:
        try:
            # Documented as unix epoch seconds; a format change here must cost
            # only the countdown, never the whole line.
            out += f" {DIM}↻{fmt_countdown(int(resets_at) - int(time.time()))}{RESET}"
        except (TypeError, ValueError):
            pass
    return out


def context_level(pct, tokens):
    """0 fine, 1 warn, 2 critical — the harsher of the two scales wins.

    Duplicated verbatim in subagent-statusline.py, which maps the levels onto a
    quieter palette. Keep the two in step.
    """
    rot = 2 if tokens > ROT_RED else 1 if tokens >= ROT_AMBER else 0
    compaction = 2 if pct >= COMPACT_RED else 1 if pct >= COMPACT_AMBER else 0
    return max(rot, compaction)


def progress_bar(pct, width=10):
    """Eighth-of-a-block resolution, so single-digit percentages still show."""
    eighths = max(1, round(pct * width * 8 / 100)) if pct > 0 else 0
    full, rem = divmod(min(eighths, width * 8), 8)
    partial = " ▏▎▍▌▋▊▉"[rem] if rem else ""
    return "█" * full + partial + DIM + "░" * (width - full - len(partial))


def git_branch(cwd):
    """Read .git/HEAD directly rather than spawning git.

    This runs on every message and on the idle refresh tick, where importing
    subprocess alone costs more than everything else here put together.
    """
    path = os.path.abspath(cwd or ".")
    while not os.path.exists(os.path.join(path, ".git")):
        parent = os.path.dirname(path)
        if parent == path:
            return ""
        path = parent

    git_dir = os.path.join(path, ".git")
    if os.path.isfile(git_dir):  # worktree or submodule: a pointer, not a dir
        try:
            with open(git_dir) as f:
                git_dir = os.path.join(path, f.read().split("gitdir:", 1)[1].strip())
        except (OSError, IndexError):
            return ""
    try:
        with open(os.path.join(git_dir, "HEAD")) as f:
            head = f.read().strip()
    except OSError:
        return ""
    return head[16:] if head.startswith("ref: refs/heads/") else head[:7]


def meta_path(session_id):
    """Hand-off file read by subagent-statusline.py — keep the two in step."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    safe = "".join(c if c.isalnum() or c == "-" else "_" for c in session_id)
    return os.path.join(base, "cache", "statusline", f"session-meta-{safe}.json")


def prune_meta(directory, max_age=7 * 86400):
    """Sessions end without a signal, so their hand-off files age out instead."""
    cutoff = time.time() - max_age
    try:
        for entry in os.scandir(directory):
            if entry.name.startswith("session-meta-") and entry.stat().st_mtime < cutoff:
                os.unlink(entry.path)
    except OSError:
        pass


def session_meta(session_id, model, effort, cache_read):
    """Round-trip the hand-off file and report whether the cache was ever warm.

    Hands model/effort to the subagent status line, which only sees per-task
    data. Also latches the first cache read of the session: a 0% hit rate means
    something entirely different before that point than after it.
    """
    if not session_id:
        return cache_read > 0
    path = meta_path(session_id)
    warmed = False
    try:
        with open(path) as f:
            warmed = bool(json.load(f).get("cache_warmed"))
    except (OSError, ValueError):
        pass
    warmed = warmed or cache_read > 0
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        prune_meta(os.path.dirname(path))
        # The harness runs concurrent copies of this script; a torn write must
        # never be visible, so land the file with a rename.
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w") as f:
            json.dump({"model": model, "effort": effort, "cache_warmed": warmed}, f)
        os.replace(tmp, path)
    except OSError:
        pass
    return warmed


def cache_hit(usage, warmed):
    """Share of the last request's input tokens that were billed as a cache read.

    A read costs a tenth of a fresh input token and a write costs 1.25-2x, so
    across a large prefix the gap between a hit and a miss is an order of
    magnitude on the turn and several seconds of latency. A drop from steady
    state points at whatever rewrote the prefix: an edited CLAUDE.md, a model or
    effort switch, an expired TTL, an MCP server that changed the tool list.
    """
    read = usage.get("cache_read_input_tokens") or 0
    fresh = (usage.get("input_tokens") or 0) + (usage.get("cache_creation_input_tokens") or 0)
    total = read + fresh
    if not total:
        return None

    pct = int(read * 100 / total)
    # Until the first hit lands, the prefix is simply being written for the
    # first time. Colouring that red would cry wolf on every session's opening
    # turn and cost the signal its meaning.
    if not warmed:
        return f"{DIM}cache {pct}%{RESET}"
    color = GREEN if pct >= CACHE_AMBER else AMBER if pct >= CACHE_RED else RED
    return f"{color}cache {pct}%{RESET}"


def main():
    data = json.load(sys.stdin)

    # "Opus 5 (1M context)" -> "Opus 5". The window size is already on line 2,
    # spelled out in tokens.
    model = ((data.get("model") or {}).get("display_name") or "?").split(" (")[0]
    effort = (data.get("effort") or {}).get("level")
    cwd = (data.get("workspace") or {}).get("current_dir") or data.get("cwd") or ""
    cost = (data.get("cost") or {}).get("total_cost_usd") or 0
    duration_ms = (data.get("cost") or {}).get("total_duration_ms") or 0

    ctx = data.get("context_window") or {}
    pct = int(ctx.get("used_percentage") or 0)
    used = ctx.get("total_input_tokens") or 0
    size = ctx.get("context_window_size") or 0
    usage = ctx.get("current_usage") or {}

    warmed = session_meta(data.get("session_id"), model, effort,
                          usage.get("cache_read_input_tokens") or 0)

    # ── line 1 ────────────────────────────────────────────────────────────────
    head = [f"{CYAN}{model}{RESET}" + (f" {AMBER}⚡{RESET}" if data.get("fast_mode") else "")]
    if effort:
        head.append(effort)
    if cwd:
        head.append(f"📁 {os.path.basename(cwd.rstrip('/')) or cwd}")
    branch = git_branch(cwd)
    if branch:
        head.append(f"🌿 {branch}")

    # ── line 2 ────────────────────────────────────────────────────────────────
    if used:
        bar = (GREEN, AMBER, RED)[context_level(pct, used)] + progress_bar(pct) + RESET
        body = [f"{bar} {pct}%"]
        if size:
            body.append(f"{fmt_tokens(used)}/{fmt_tokens(size)}")
    else:
        # Claude Code reports context from the last API response, so there is no
        # measurement at session start or between a /compact and the next reply.
        # A literal 0% would read as "empty context" when it is merely unknown.
        body = [f"{DIM}{'░' * 10} —{RESET}"]
    cache = cache_hit(usage, warmed)
    if cache:
        body.append(cache)
    body.append(f"${cost:.2f}")
    body.append(fmt_duration(duration_ms))
    week = weekly_usage(data)
    if week:
        body.append(week)

    print(SEP.join(head))
    print(SEP.join(body))


# Non-UTF-8 locales would otherwise blow up on the glyphs above, taking the
# whole line with them.
try:
    sys.stdout.reconfigure(errors="replace")
except (AttributeError, ValueError):
    pass

try:
    main()
except Exception as exc:
    # A traceback would render as a blank status line with no clue why.
    print(f"{DIM}status line: {type(exc).__name__}{RESET}")
