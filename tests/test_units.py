"""Unit tests for the pure logic in both status line scripts.

The scripts are imported as modules (main() stays behind the __main__ guard),
so these run without subprocesses, in milliseconds. Contracts that live at the
harness boundary — stdin/stdout, exit codes — stay in test_statusline.py.
"""
import importlib.util
import json
import os
import re
import tempfile
import time
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANSI = re.compile(r"\033\[[0-9;]*m")


def _load(alias, filename):
    spec = importlib.util.spec_from_file_location(alias, os.path.join(REPO, filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


sl = _load("statusline_under_test", "statusline.py")
sub = _load("subagent_statusline_under_test", "subagent-statusline.py")


def plain(s):
    return ANSI.sub("", s)


class Coercions(unittest.TestCase):
    def test_as_int_accepts_every_wire_spelling(self):
        for value, expected in [(12, 12), (12.9, 12), ("12", 12), ("12.9", 12),
                                ("9.55e4", 95500), (None, 0), ("x", 0), ({}, 0)]:
            with self.subTest(value=value):
                self.assertEqual(sl.as_int(value), expected)

    def test_as_int_hands_back_the_callers_default(self):
        self.assertIsNone(sl.as_int("garbage", None))

    def test_as_float_keeps_the_decimals(self):
        self.assertEqual(sl.as_float("1.23"), 1.23)
        self.assertEqual(sl.as_float(None), 0.0)


class FmtTokens(unittest.TestCase):
    def test_boundaries(self):
        for n, expected in [(0, "0"), (999, "999"), (1000, "1k"), (1999, "1k"),
                            (12000, "12k"), (999_999, "999k"), (1_000_000, "1M"),
                            (1_500_000, "1.5M")]:
            with self.subTest(n=n):
                self.assertEqual(sl.fmt_tokens(n), expected)

    def test_both_modules_agree(self):
        for n in (0, 999, 1000, 999_999, 1_000_000, 1_500_000):
            self.assertEqual(sl.fmt_tokens(n), sub.fmt_tokens(n))


class FmtDuration(unittest.TestCase):
    def test_boundaries(self):
        for ms, expected in [(0, "0s"), (59_000, "59s"), (59_999, "59s"),
                             (60_000, "1m 00s"), (125_000, "2m 05s"),
                             (3_600_000, "1h 00m"), (3_661_000, "1h 01m")]:
            with self.subTest(ms=ms):
                self.assertEqual(sl.fmt_duration(ms), expected)

    def test_float_and_string_milliseconds(self):
        self.assertEqual(sl.fmt_duration(9.55e4), "1m 35s")
        self.assertEqual(sl.fmt_duration("95500"), "1m 35s")


class FmtCountdown(unittest.TestCase):
    def test_boundaries(self):
        for seconds, expected in [(0, "now"), (-5, "now"), (30, "1m"),
                                  (3599, "59m"), (3600, "1h"), (86399, "23h"),
                                  (86400, "1d"), (86400 * 2 + 3600, "2d 1h")]:
            with self.subTest(seconds=seconds):
                self.assertEqual(sl.fmt_countdown(seconds), expected)


class ProgressBar(unittest.TestCase):
    def bar(self, pct):
        return plain(sl.progress_bar(pct))

    def test_empty_full_and_overflow(self):
        self.assertEqual(self.bar(0), "░" * 10)
        self.assertEqual(self.bar(100), "█" * 10)
        self.assertEqual(self.bar(150), "█" * 10)

    def test_single_digit_percent_still_shows(self):
        self.assertEqual(self.bar(1), "▏" + "░" * 9)

    def test_half_way(self):
        self.assertEqual(self.bar(50), "█" * 5 + "░" * 5)

    def test_width_never_drifts(self):
        for pct in range(0, 101, 7):
            self.assertEqual(len(self.bar(pct)), 10, pct)


class ContextLevelThresholds(unittest.TestCase):
    def test_compaction_scale(self):
        for pct, level in [(0, 0), (54, 0), (55, 1), (74, 1), (75, 2)]:
            with self.subTest(pct=pct):
                self.assertEqual(sl.context_level(pct, 0), level)

    def test_rot_scale(self):
        for tokens, level in [(0, 0), (119_999, 0), (120_000, 1),
                              (349_999, 1), (350_000, 2)]:
            with self.subTest(tokens=tokens):
                self.assertEqual(sl.context_level(0, tokens), level)

    def test_the_harsher_scale_wins(self):
        self.assertEqual(sl.context_level(75, 0), 2)
        self.assertEqual(sl.context_level(0, 350_000), 2)
        self.assertEqual(sl.context_level(55, 350_000), 2)


class ModuleSync(unittest.TestCase):
    """The two scripts duplicate thresholds and the meta-file path on purpose;
    these tests turn the "keep the two in step" comments into assertions."""

    def test_threshold_constants_match(self):
        self.assertEqual(
            (sl.ROT_AMBER, sl.ROT_RED, sl.COMPACT_AMBER, sl.COMPACT_RED),
            (sub.ROT_AMBER, sub.ROT_RED, sub.COMPACT_AMBER, sub.COMPACT_RED))

    def test_context_level_matches_across_a_grid(self):
        for pct in (0, 54, 55, 74, 75, 100):
            for tokens in (0, 119_999, 120_000, 349_999, 350_000, 1_000_000):
                with self.subTest(pct=pct, tokens=tokens):
                    self.assertEqual(sl.context_level(pct, tokens),
                                     sub.context_level(pct, tokens))

    def test_meta_path_matches_for_the_same_session(self):
        with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": "/cfg"}):
            for session_id in ("plain", "a b/c!", 12345):
                with self.subTest(session_id=session_id):
                    self.assertEqual(sl.meta_path(session_id), sub.meta_path(session_id))
                    self.assertTrue(sl.meta_path(session_id).startswith("/cfg/"))


class WeeklyUsage(unittest.TestCase):
    def week(self, **fields):
        return sl.weekly_usage({"rate_limits": {"seven_day": fields}})

    def test_absent_and_unparsable_both_drop_the_segment(self):
        self.assertIsNone(sl.weekly_usage({}))
        self.assertIsNone(self.week(used_percentage="soon"))

    def test_color_thresholds(self):
        for pct, color in [(49, sl.GREEN), (50, sl.AMBER), (84, sl.AMBER), (85, sl.RED)]:
            with self.subTest(pct=pct):
                out = self.week(used_percentage=pct)
                self.assertTrue(out.startswith(color))
                self.assertIn(f"7d {pct}%", plain(out))

    def test_string_percentage_still_renders(self):
        self.assertIn("7d 61%", plain(self.week(used_percentage="61.7")))

    def test_countdown_only_appears_once_worth_acting_on(self):
        resets = int(time.time()) + 7200
        self.assertIn("↻", plain(self.week(used_percentage=60, resets_at=resets)))
        self.assertNotIn("↻", plain(self.week(used_percentage=20, resets_at=resets)))

    def test_garbage_resets_at_costs_only_the_countdown(self):
        out = plain(self.week(used_percentage=60, resets_at="soon"))
        self.assertNotIn("↻", out)
        self.assertIn("7d 60%", out)


class CacheHit(unittest.TestCase):
    def hit(self, read, fresh, warmed=True):
        usage = {"cache_read_input_tokens": read, "input_tokens": fresh}
        return sl.cache_hit(usage, warmed)

    def test_no_tokens_at_all_drops_the_segment(self):
        self.assertIsNone(sl.cache_hit({}, True))

    def test_color_thresholds(self):
        for read, fresh, color in [(90, 10, sl.GREEN), (89, 11, sl.AMBER),
                                   (60, 40, sl.AMBER), (59, 41, sl.RED)]:
            with self.subTest(read=read):
                out = self.hit(read, fresh)
                self.assertTrue(out.startswith(color))
                self.assertIn(f"cache {read}%", plain(out))

    def test_cold_prefix_renders_dim_never_red(self):
        out = self.hit(0, 10000, warmed=False)
        self.assertTrue(out.startswith(sl.DIM))
        self.assertIn("cache 0%", plain(out))

    def test_cache_creation_counts_as_fresh_input(self):
        usage = {"cache_read_input_tokens": 50,
                 "input_tokens": 25, "cache_creation_input_tokens": 25}
        self.assertIn("cache 50%", plain(sl.cache_hit(usage, True)))


class FmtDir(unittest.TestCase):
    def test_no_repo_keeps_the_basename(self):
        self.assertEqual(sl.fmt_dir("/a/b/c", ""), "c")
        self.assertEqual(sl.fmt_dir("/a/b/", ""), "b")

    def test_home_as_repo_root_keeps_the_basename(self):
        home = os.path.expanduser("~")
        self.assertEqual(sl.fmt_dir(os.path.join(home, "sub"), home), "sub")

    def test_root_of_filesystem_as_repo_keeps_the_basename(self):
        self.assertEqual(sl.fmt_dir("/etc", "/"), "etc")

    def test_depth_within_the_repo(self):
        for cwd, expected in [("/x/repo", "repo"),
                              ("/x/repo/src", "repo/src"),
                              ("/x/repo/src/app", "repo/src/app"),
                              ("/x/repo/src/app/deep", "repo/…/deep")]:
            with self.subTest(cwd=cwd):
                self.assertEqual(sl.fmt_dir(cwd, "/x/repo"), expected)


class GitInfo(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = self.tmp.name

    def write_head(self, git_dir, content):
        """A dir-form .git carries a refs/ beside HEAD, and repo_marker wants both."""
        os.makedirs(os.path.join(git_dir, "refs"), exist_ok=True)
        with open(os.path.join(git_dir, "HEAD"), "w") as f:
            f.write(content)

    def test_no_repo_anywhere_above(self):
        # Only the temp tree is ours. Whatever sits above it belongs to the machine
        # — a developer whose TMPDIR lives inside a checkout has a real root up
        # there — so the contract is that nothing in the tree claims to be one.
        deep = os.path.join(self.root, "a", "b")
        os.makedirs(deep)
        self.assertFalse(sl.git_info(deep)[0].startswith(self.root))

    def test_branch_from_a_plain_repo(self):
        self.write_head(os.path.join(self.root, ".git"), "ref: refs/heads/main\n")
        self.assertEqual(sl.git_info(self.root), (self.root, "main"))

    def test_root_found_from_a_subdirectory(self):
        self.write_head(os.path.join(self.root, ".git"), "ref: refs/heads/main\n")
        sub_dir = os.path.join(self.root, "a", "b")
        os.makedirs(sub_dir)
        self.assertEqual(sl.git_info(sub_dir), (self.root, "main"))

    def test_detached_head_shows_the_short_hash(self):
        self.write_head(os.path.join(self.root, ".git"),
                        "0123456789abcdef0123456789abcdef01234567\n")
        self.assertEqual(sl.git_info(self.root), (self.root, "0123456"))

    def test_a_git_directory_without_a_head_is_not_a_repo(self):
        # A stray .git left behind by some other tool — /tmp/.git is a real
        # example. git itself refuses to call it a repository, and calling it one
        # relabels every path underneath repo-relative.
        os.makedirs(os.path.join(self.root, ".git"))
        self.assertNotEqual(sl.git_info(self.root)[0], self.root)

    def test_a_git_directory_without_refs_is_not_a_repo(self):
        # A leftover carrying a stray HEAD and nothing else: git wants objects/ and
        # refs/ beside it before it calls the directory a repository.
        git_dir = os.path.join(self.root, ".git")
        os.makedirs(git_dir)
        with open(os.path.join(git_dir, "HEAD"), "w") as f:
            f.write("garbage\n")
        self.assertNotEqual(sl.git_info(self.root)[0], self.root)

    def test_a_symlinked_head_is_still_a_repo(self):
        # git accepts a symref HEAD, and on an unborn branch its target does not
        # exist yet — which a follow-the-link check reads as no repo at all.
        git_dir = os.path.join(self.root, ".git")
        os.makedirs(os.path.join(git_dir, "refs"))
        os.symlink(os.path.join(git_dir, "refs", "heads", "main"),
                   os.path.join(git_dir, "HEAD"))
        self.assertEqual(sl.git_info(self.root), (self.root, ""))

    def test_unreadable_head_still_reports_the_root(self):
        git_dir = os.path.join(self.root, ".git")
        self.write_head(git_dir, "ref: refs/heads/main\n")
        head = os.path.join(git_dir, "HEAD")
        os.chmod(head, 0)
        self.addCleanup(os.chmod, head, 0o644)
        if os.access(head, os.R_OK):  # root ignores the mode bits
            self.skipTest("running as a user that can read a 0-mode file")
        self.assertEqual(sl.git_info(self.root), (self.root, ""))

    def test_worktree_pointer_file(self):
        real = os.path.join(self.root, "real-gitdir")
        self.write_head(real, "ref: refs/heads/wt-branch\n")
        wt = os.path.join(self.root, "wt")
        os.makedirs(wt)
        with open(os.path.join(wt, ".git"), "w") as f:
            f.write(f"gitdir: {real}\n")
        self.assertEqual(sl.git_info(wt), (wt, "wt-branch"))

    def test_garbage_pointer_file_still_reports_the_root(self):
        wt = os.path.join(self.root, "wt")
        os.makedirs(wt)
        with open(os.path.join(wt, ".git"), "w") as f:
            f.write("garbage\n")
        self.assertEqual(sl.git_info(wt), (wt, ""))


class PruneMeta(unittest.TestCase):
    def test_only_stale_meta_files_age_out(self):
        with tempfile.TemporaryDirectory() as directory:
            stale = os.path.join(directory, "session-meta-old.json")
            fresh = os.path.join(directory, "session-meta-new.json")
            unrelated = os.path.join(directory, "unrelated.json")
            for path in (stale, fresh, unrelated):
                with open(path, "w") as f:
                    f.write("{}")
            old = time.time() - 8 * 86400
            os.utime(stale, (old, old))
            os.utime(unrelated, (old, old))

            sl.prune_meta(directory)
            self.assertFalse(os.path.exists(stale))
            self.assertTrue(os.path.exists(fresh))
            self.assertTrue(os.path.exists(unrelated))

    def test_missing_directory_is_quiet(self):
        sl.prune_meta("/nonexistent/statusline-test")


class PrettyModel(unittest.TestCase):
    def test_known_id_shapes(self):
        for model_id, expected in [
            ("claude-opus-5", "Opus 5"),
            ("claude-opus-5[1m]", "Opus 5"),
            ("claude-haiku-4-5-20251001", "Haiku 4.5"),
            ("claude-3-5-sonnet-20241022", "Sonnet 3.5"),
            ("us.anthropic.claude-opus-5-v1:0", "Opus 5"),
            ("anthropic.claude-3-5-sonnet-20241022-v2:0", "Sonnet 3.5"),
            ("anthropic/claude-sonnet-5", "Sonnet 5"),
        ]:
            with self.subTest(model_id=model_id):
                self.assertEqual(sub.pretty_model(model_id), expected)

    def test_empty_and_degenerate_ids(self):
        self.assertIsNone(sub.pretty_model(None))
        self.assertIsNone(sub.pretty_model(""))
        self.assertEqual(sub.pretty_model("claude-"), "claude-")


class FmtEffort(unittest.TestCase):
    def test_inherited_effort_renders_dim(self):
        out = sub.fmt_effort({}, "high")
        self.assertTrue(out.startswith(sub.DIM))
        self.assertEqual(plain(out), "high")

    def test_nothing_to_show(self):
        self.assertIsNone(sub.fmt_effort({}, None))

    def test_own_effort_renders_plain_and_beats_inherited(self):
        self.assertEqual(sub.fmt_effort({"effort": "max"}, "high"), "max")

    def test_numeric_effort_renders_as_a_token_budget(self):
        self.assertEqual(sub.fmt_effort({"effort": 32000}, None), "32k")


class SessionEffort(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": self.tmp.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def write_meta(self, session_id, content):
        path = sub.meta_path(session_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)

    def test_effort_round_trips(self):
        self.write_meta("s", json.dumps({"model": "Opus 5", "effort": "high"}))
        self.assertEqual(sub.session_effort("s"), "high")

    def test_wrong_shape_and_missing_file_read_as_absent(self):
        self.write_meta("wrong", "[1, 2, 3]")
        self.assertIsNone(sub.session_effort("wrong"))
        self.assertIsNone(sub.session_effort("never-written"))
        self.assertIsNone(sub.session_effort(""))


class RenderRow(unittest.TestCase):
    def task(self, **fields):
        return {"id": "a1", "name": "Explore", "status": "running",
                "tokenCount": 50000, "contextWindowSize": 200000, **fields}

    def test_status_glyphs_and_colors(self):
        for status, color, glyph in [("completed", sub.GREEN, "✓"),
                                     ("failed", sub.RED, "✗"),
                                     ("cancelled", sub.DIM, "✗"),
                                     ("pending", sub.DIM, "○"),
                                     ("something-new", sub.DIM, "●")]:
            with self.subTest(status=status):
                row = sub.render(self.task(status=status), 120, None)
                self.assertTrue(row.startswith(color))
                self.assertTrue(plain(row).startswith(f"{glyph} Explore"))

    def test_context_percentage_with_token_count(self):
        self.assertIn("25% (50k)", plain(sub.render(self.task(), 120, None)))

    def test_no_measurement_yet_renders_a_dash(self):
        row = plain(sub.render(self.task(tokenCount=0), 120, None))
        self.assertIn("—", row)
        self.assertNotIn("%", row)

    def test_tokens_without_a_window_size_render_bare(self):
        row = plain(sub.render(self.task(contextWindowSize=0), 120, None))
        self.assertIn("50k", row)
        self.assertNotIn("%", row)

    def test_model_joins_the_metrics(self):
        row = plain(sub.render(self.task(model="claude-haiku-4-5-20251001"), 120, None))
        self.assertIn("Haiku 4.5", row)

    def test_long_description_is_truncated_with_an_ellipsis(self):
        row = plain(sub.render(self.task(description="D" * 200), 120, None))
        self.assertIn("DDD", row)
        self.assertIn("…", row)

    def test_wide_terminal_keeps_the_whole_description(self):
        row = plain(sub.render(self.task(description="D" * 200), 400, None))
        self.assertIn("D" * 200, row)
        self.assertNotIn("…", row)

    def test_description_needs_at_least_eight_columns_of_room(self):
        # left "● Explore" and right "25% (50k)" are 9 visible columns each;
        # with the fixed gap of 6 that puts room at columns - 24.
        desc = "12345678"
        self.assertIn(desc, plain(sub.render(self.task(description=desc), 32, None)))
        self.assertNotIn(desc, plain(sub.render(self.task(description=desc), 31, None)))

    def test_zero_columns_drops_the_description_and_keeps_the_metrics(self):
        row = plain(sub.render(self.task(description="D" * 20), 0, None))
        self.assertIn("Explore", row)
        self.assertIn("25% (50k)", row)
        self.assertNotIn("D", row)

    def test_newlines_in_the_description_flatten_to_spaces(self):
        row = plain(sub.render(self.task(description="line1\nline2"), 120, None))
        self.assertIn("line1 line2", row)

    def test_label_wins_over_description(self):
        row = plain(sub.render(self.task(label="live progress",
                                         description="static text"), 120, None))
        self.assertIn("live progress", row)
        self.assertNotIn("static text", row)


if __name__ == "__main__":
    unittest.main()
