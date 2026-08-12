# claude-code-statusline

A two-line status line for [Claude Code](https://code.claude.com), plus a matching renderer for the subagent panel. Pure Python, standard library only.

<!-- TODO: screenshot -->

**Line 1:** model · reasoning effort · directory · git branch
**Line 2:** context bar · % · tokens · cache hit rate · cost · elapsed time · weekly limit

The subagent panel shows one row per agent: status, name, what it's doing right now, its model, and how full its context is.

## Design notes

- **The context bar warns you early.** It turns amber and then red before the conversation gets long enough to hurt: long context makes the model noticeably worse, and Claude Code auto-compacts near the top of the window, which loses detail. When the bar goes red, it's a good moment to wrap up or start a fresh session.
- **The cache percentage watches your costs.** Claude charges a fraction of the price for parts of the conversation it has already processed. In a healthy session this number sits at 95–99%. A sudden drop means something invalidated that saved prefix — the next requests get slower and more expensive, and the status line makes it visible the moment it happens.
- **It never leaves you with a blank bar.** Missing data renders as placeholders and any internal error shows up as a short note, so the status line keeps working whatever the payload looks like.
- **It's instant.** Everything is read from files already on disk, with zero external commands, so rendering takes a few milliseconds even on slow machines.

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

`statusline-toggle.py` is included for anyone who also uses [ccstatusline](https://github.com/sirmalloc/ccstatusline): it flips `settings.json` between the two (`mine` / `cc` / `status` arguments).

## License

MIT
