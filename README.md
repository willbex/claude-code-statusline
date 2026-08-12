# claude-code-statusline

A two-line status line for [Claude Code](https://code.claude.com), plus a matching renderer for the subagent panel. Pure Python, standard library only, no subprocess spawns.

<!-- TODO: screenshot -->

**Line 1:** model · reasoning effort · directory · git branch
**Line 2:** context bar · % · tokens · cache hit rate · cost · elapsed time · weekly limit

The subagent status line renders each row in the agent panel as: status glyph, agent name, live progress summary, model, inherited effort, context percentage.

## Design notes

- **Two context scales.** The bar colour tracks the harsher of two signals: absolute tokens (context rot starts around the same counts whatever the window size) and percentage of the window (auto-compact fires at ~83%, so red starts at 75%).
- **Cache hit rate.** A drop from the 95–99% steady state points at whatever rewrote the prompt prefix: an edited CLAUDE.md, a model or effort switch, an expired TTL, an MCP server that changed its tool list.
- **Graceful degradation.** Every payload field is treated as optional, the scripts always print non-empty output and exit 0 — a single failed invocation would otherwise blank the status line with no error shown.
- **Fast.** Git branch is read straight from `.git/HEAD`; there are no subprocess calls, so the script stays well under the harness's refresh cadence even on slow machines.

## Requirements

- Python 3.8+
- Claude Code 2.x. Subagent rows show model and context window on 2.1.205+, reasoning effort on 2.1.214+; older versions simply omit those fields.

## Install

```bash
cp statusline.py subagent-statusline.py ~/.claude/
chmod +x ~/.claude/statusline.py ~/.claude/subagent-statusline.py
```

Add to `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "~/.claude/statusline.py",
    "refreshInterval": 10
  },
  "subagentStatusLine": {
    "type": "command",
    "command": "~/.claude/subagent-statusline.py"
  }
}
```

`CLAUDE_CONFIG_DIR` is honoured throughout if you keep your config elsewhere.

## Configuration

Thresholds live as constants at the top of each script: token/percentage bands for the context bar (`ROT_*`, `COMPACT_*`), the weekly-quota bands (`WEEK_*`), and the cache-hit floors (`CACHE_*`).

## Toggle script

`statusline-toggle.py` switches `settings.json` between this status line and [ccstatusline](https://github.com/sirmalloc/ccstatusline):

```bash
statusline-toggle.py          # toggle
statusline-toggle.py mine     # force this status line
statusline-toggle.py cc       # force ccstatusline
statusline-toggle.py status   # print which one is active
```

## Troubleshooting

<!-- TODO: expand each entry -->

- **Windows: status line never appears.** The command is run through Git Bash and backslashes are eaten as escapes — use forward slashes in `statusLine.command`. Typical symptom under `--debug` is exit 126/127. ([anthropics/claude-code#79236](https://github.com/anthropics/claude-code/issues/79236))
- **`"tui": "fullscreen"` silently disables custom status lines.** ([#76411](https://github.com/anthropics/claude-code/issues/76411))
- **Managed/enterprise settings:** `allowManagedHooksOnly: true` disables custom status line commands. ([#86042](https://github.com/anthropics/claude-code/issues/86042))
- **1M-context sessions may report `context_window_size: 200000`,** pinning the percentage at 100. The absolute-token scale keeps the bar colour meaningful regardless. ([#76751](https://github.com/anthropics/claude-code/issues/76751))
- **`used_percentage` is computed against the full model window;** Claude Code's own "context low" warnings subtract an auto-compact buffer, so the numbers differ by design. ([#17959](https://github.com/anthropics/claude-code/issues/17959))

## Payload reference

Official schema: https://code.claude.com/docs/en/statusline.md

## License

MIT
