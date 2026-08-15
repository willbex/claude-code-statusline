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


def as_float(value, default=0.0):
    """float-preserving twin of as_int, for the one field with real decimals."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_int(value, default=0):
    """Coerce a number off the wire.

    A JSON number's Python type follows the producer's serializer: a duration
    in milliseconds lands as a float the moment someone writes it 9.55e4, and a
    percentage as "61.7" the moment it gains a decimal place. float() accepts
    every one of those spellings, and int() hands back the type the arithmetic
    downstream assumes. A shape nobody anticipated costs the caller a default.
    """
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


def fmt_duration(ms):
    # Fixing the type here settles it for h/m/s below: // is floor division,
    # which keeps a float a float, and the :02d codes accept only int.
    total = as_int(ms) // 1000
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
    # Missing and unparsable collapse into the same outcome: drop the segment,
    # the way a format change costs only the countdown further down.
    pct = as_int(week.get("used_percentage"), None)
    if pct is None:
        return None

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
    rot = 2 if tokens >= ROT_RED else 1 if tokens >= ROT_AMBER else 0
    compaction = 2 if pct >= COMPACT_RED else 1 if pct >= COMPACT_AMBER else 0
    return max(rot, compaction)


def progress_bar(pct, width=10):
    """Eighth-of-a-block resolution, so single-digit percentages still show."""
    eighths = max(1, round(pct * width * 8 / 100)) if pct > 0 else 0
    full, rem = divmod(min(eighths, width * 8), 8)
    partial = " ▏▎▍▌▋▊▉"[rem] if rem else ""
    return "█" * full + partial + DIM + "░" * (width - full - len(partial))


def git_info(cwd):
    """Return (repo_root, branch), read from .git/HEAD.

    This runs on every message and on the idle refresh tick, where importing
    subprocess alone costs more than everything else here put together.
    Once .git exists the root is known, so an unreadable HEAD still reports it.
    """
    path = os.path.abspath(cwd or ".")
    while not os.path.exists(os.path.join(path, ".git")):
        parent = os.path.dirname(path)
        if parent == path:
            return "", ""
        path = parent

    git_dir = os.path.join(path, ".git")
    if os.path.isfile(git_dir):  # worktree or submodule: a pointer, not a dir
        try:
            with open(git_dir) as f:
                git_dir = os.path.join(path, f.read().split("gitdir:", 1)[1].strip())
        except (OSError, IndexError):
            return path, ""
    try:
        with open(os.path.join(git_dir, "HEAD")) as f:
            head = f.read().strip()
    except OSError:
        return path, ""
    return path, head[16:] if head.startswith("ref: refs/heads/") else head[:7]


def fmt_dir(cwd, root):
    """Locate cwd within its repo: full path to two levels deep, repo/…/leaf below.

    A git-tracked home directory (chezmoi-style dotfiles) would relabel every
    path under it repo-relative, and a repo rooted at / has no basename to show
    — both keep the plain basename of cwd.
    """
    if not root or root == os.path.expanduser("~") or not os.path.basename(root):
        return os.path.basename(cwd.rstrip("/")) or cwd
    rel = os.path.relpath(os.path.abspath(cwd), root)
    repo = os.path.basename(root)
    if rel == ".":
        return repo
    parts = rel.split(os.sep)
    if len(parts) <= 2:
        return "/".join([repo, *parts])
    return f"{repo}/…/{parts[-1]}"


def meta_path(session_id):
    """Hand-off file read by subagent-statusline.py — keep the two in step."""
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    safe = "".join(c if c.isalnum() or c == "-" else "_" for c in str(session_id))
    return os.path.join(base, "cache", "statusline", f"session-meta-{safe}.json")


def read_meta(path):
    """The file is shared mutable state on disk; anything unexpected in it —
    unreadable, truncated, or valid JSON of the wrong shape — reads as absent."""
    try:
        with open(path) as f:
            meta = json.load(f)
        return meta if isinstance(meta, dict) else {}
    except (OSError, ValueError):
        return {}


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
    warmed = bool(read_meta(path).get("cache_warmed")) or cache_read > 0
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
    read = as_int(usage.get("cache_read_input_tokens"))
    fresh = as_int(usage.get("input_tokens")) + as_int(usage.get("cache_creation_input_tokens"))
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
    cost = as_float((data.get("cost") or {}).get("total_cost_usd"))
    duration_ms = (data.get("cost") or {}).get("total_duration_ms") or 0

    ctx = data.get("context_window") or {}
    pct = as_int(ctx.get("used_percentage"))
    used = as_int(ctx.get("total_input_tokens"))
    size = as_int(ctx.get("context_window_size"))
    usage = ctx.get("current_usage") or {}

    warmed = session_meta(data.get("session_id"), model, effort,
                          as_int(usage.get("cache_read_input_tokens")))

    # ── line 1 ────────────────────────────────────────────────────────────────
    head = [f"{CYAN}{model}{RESET}" + (f" {AMBER}⚡{RESET}" if data.get("fast_mode") else "")]
    if effort:
        head.append(effort)
    root, branch = git_info(cwd)
    if cwd:
        head.append(f"📁 {fmt_dir(cwd, root)}")
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
