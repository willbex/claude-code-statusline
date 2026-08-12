# claude-code-statusline

A two-line status line for [Claude Code](https://code.claude.com), with a renderer for the subagent panel. Pure Python, standard library only.

![Status line in a live session](assets/statusline.png)

**Line 1:** model · reasoning effort · directory · git branch
**Line 2:** context bar · % · tokens · cache hit rate · cost · elapsed time · weekly limit

## Design notes

- **The context bar warns you early.** Either limit turns it:

  - 🟡 **amber** — 120k tokens in the conversation, or 55% of the window
  - 🔴 **red** — 350k tokens, or 75% of the window

- **The cache percentage catches surprise costs.** It normally sits at 95–99%; a drop is your signal that requests are getting slower and more expensive. Worth a look at what caused it.
- **Errors stay visible.** Missing numbers show as dashes, and a crash prints one short line naming the error.
- **It's instant.** Everything is read from files already on disk, with zero external commands, so rendering takes a few milliseconds even on slow machines.

## Requirements

- Python 3.8+
- Claude Code 2.x. Subagent rows show model and context window on 2.1.205+, reasoning effort on 2.1.214+; older versions simply omit those fields.

## Install

```bash
git clone https://github.com/willbex/claude-code-statusline.git
cd claude-code-statusline
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

## AI disclosure

This project was written with [Claude Code](https://code.claude.com).

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
