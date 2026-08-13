# claude-code-statusline

A two-line status line for [Claude Code](https://code.claude.com), with a renderer for the subagent panel. Pure Python, standard library only.

![Status line in a live session](assets/statusline.png)

**Line 1:** model · reasoning effort · directory · git branch

**Line 2:** context bar · % · tokens · cache hit rate · cost · elapsed time · weekly limit

## Requirements

- Linux or macOS
- Python 3.8+
- Claude Code 2.x — subagent rows show model and context window on 2.1.205+ and reasoning effort on 2.1.214+; older versions simply omit those fields

## Install

```bash
git clone https://github.com/willbex/claude-code-statusline.git
cd claude-code-statusline && ./install.sh
```

That copies both scripts to `~/.claude/` and adds two keys to your `settings.json`, keeping everything else in it. Your old `settings.json` is saved alongside it first.

Update with `git pull && ./install.sh`, remove with `./install.sh --uninstall`.

## Design notes

- **The context bar warns you early.** Either limit changes its color:

  - 🟡 **amber** — 120k tokens in the conversation, or 55% of the window
  - 🔴 **red** — 350k tokens, or 75% of the window

- **The cache percentage catches surprise costs.** It normally sits at 95–99%; a drop is your signal that requests are getting slower and more expensive. Worth a look at what caused it.
- **Errors stay visible.** Missing numbers show as dashes, and a crash prints one short line naming the error.
- **It's instant.** Everything is read from files already on disk, with zero external commands, so rendering takes a few milliseconds even on slow machines.

## AI disclosure

This project was written with [Claude Code](https://code.claude.com).

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
