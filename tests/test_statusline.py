"""Edge-case tests for both status line scripts.

Each test runs the script as a subprocess with a throwaway CLAUDE_CONFIG_DIR,
feeds it one harness payload on stdin and asserts on the rendered output —
the same contract the harness holds the scripts to.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MAIN = os.path.join(REPO, "statusline.py")
SUBAGENT = os.path.join(REPO, "subagent-statusline.py")
ANSI = re.compile(r"\033\[[0-9;]*m")


def run(script, payload, config_dir):
    env = {**os.environ, "CLAUDE_CONFIG_DIR": config_dir}
    proc = subprocess.run(
        [sys.executable, script], input=json.dumps(payload),
        capture_output=True, text=True, env=env,
    )
    return ANSI.sub("", proc.stdout)


def context_payload(session_id="s", usage=None, **ctx):
    return {
        "session_id": session_id,
        "model": {"display_name": "Opus 5"},
        "context_window": {
            "used_percentage": 5,
            "total_input_tokens": 10000,
            "context_window_size": 200000,
            **({"current_usage": usage} if usage else {}),
            **ctx,
        },
    }


class StatusLineEdgeCases(unittest.TestCase):
    def setUp(self):
        self.config = tempfile.TemporaryDirectory()
        self.addCleanup(self.config.cleanup)
        self.meta_dir = os.path.join(self.config.name, "cache", "statusline")
        os.makedirs(self.meta_dir)

    def meta_file(self, session_id):
        return os.path.join(self.meta_dir, f"session-meta-{session_id}.json")

    def test_meta_file_holding_a_json_list_keeps_the_line(self):
        with open(self.meta_file("t"), "w") as f:
            f.write("[1, 2, 3]")
        out = run(MAIN, context_payload("t", usage={"cache_read_input_tokens": 9900,
                                                    "input_tokens": 100}), self.config.name)
        self.assertIn("cache 99%", out)

    def test_usage_numbers_arriving_as_strings_still_render(self):
        out = run(MAIN, context_payload(usage={"cache_read_input_tokens": "9900",
                                               "input_tokens": "100"}), self.config.name)
        self.assertIn("cache 99%", out)

    def test_token_totals_arriving_as_strings_still_render(self):
        out = run(MAIN, context_payload(total_input_tokens="10000",
                                        context_window_size="200000"), self.config.name)
        self.assertIn("10k/200k", out)

    def test_cost_arriving_as_a_string_still_renders(self):
        payload = context_payload()
        payload["cost"] = {"total_cost_usd": "1.23"}
        out = run(MAIN, payload, self.config.name)
        self.assertIn("$1.23", out)

    def test_numeric_session_id_keeps_the_line(self):
        out = run(MAIN, context_payload(12345), self.config.name)
        self.assertIn("Opus 5", out)

    def test_float_token_total_renders_without_a_decimal(self):
        out = run(MAIN, context_payload(total_input_tokens=1.2e4), self.config.name)
        self.assertIn("12k/200k", out)

    def test_warm_latch_set_between_runs_is_never_written_back_off(self):
        # Run 1 latches the flag; run 2 sees no cache read and must leave it lit.
        run(MAIN, context_payload("t", usage={"cache_read_input_tokens": 9900,
                                              "input_tokens": 100}), self.config.name)
        run(MAIN, context_payload("t", usage={"cache_read_input_tokens": 0,
                                              "input_tokens": 10000}), self.config.name)
        with open(self.meta_file("t")) as f:
            self.assertTrue(json.load(f)["cache_warmed"])


class LatchRace(unittest.TestCase):
    """The harness runs concurrent copies; a copy that read the meta file before
    a sibling latched cache_warmed must pick the flag up again before writing."""

    def test_latch_landed_during_the_run_survives_the_write(self):
        import importlib.util
        from unittest import mock

        with tempfile.TemporaryDirectory() as config:
            with mock.patch.dict(os.environ, {"CLAUDE_CONFIG_DIR": config}):
                spec = importlib.util.spec_from_file_location("statusline_module", MAIN)
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)

                path = mod.meta_path("t")
                os.makedirs(os.path.dirname(path), exist_ok=True)

                # Stands in for the sibling copy: lands the latch at the point
                # between this run's initial read and its own write.
                def sibling_latches(directory, max_age=0):
                    with open(path, "w") as f:
                        json.dump({"cache_warmed": True}, f)

                mod.prune_meta = sibling_latches
                mod.session_meta("t", "Opus 5", None, 0)
                with open(path) as f:
                    self.assertTrue(json.load(f)["cache_warmed"])


class SubagentEdgeCases(unittest.TestCase):
    def setUp(self):
        self.config = tempfile.TemporaryDirectory()
        self.addCleanup(self.config.cleanup)
        os.makedirs(os.path.join(self.config.name, "cache", "statusline"))

    def run_tasks(self, payload):
        return run(SUBAGENT, payload, self.config.name)

    def test_meta_file_holding_a_json_list_keeps_the_panel(self):
        path = os.path.join(self.config.name, "cache", "statusline", "session-meta-t.json")
        with open(path, "w") as f:
            f.write("[1, 2, 3]")
        out = self.run_tasks({"session_id": "t", "columns": 120, "tasks": [
            {"id": "a1", "name": "Explore", "status": "running",
             "tokenCount": 50000, "contextWindowSize": 200000}]})
        self.assertIn('"a1"', out)

    def test_numeric_session_id_keeps_the_panel(self):
        out = self.run_tasks({"session_id": 12345, "columns": 120, "tasks": [
            {"id": "a1", "name": "Explore", "status": "running",
             "tokenCount": 50000, "contextWindowSize": 200000}]})
        self.assertIn('"a1"', out)

    def test_token_counts_arriving_as_strings_still_render(self):
        out = self.run_tasks({"columns": 120, "tasks": [
            {"id": "a1", "name": "Explore", "status": "running",
             "tokenCount": "50000", "contextWindowSize": "200000"}]})
        self.assertIn("25%", out)


class RenderedLineContracts(unittest.TestCase):
    """One full typical payload rendered end to end, plus the failure contracts
    the scripts promise: main degrades to an error marker on garbage input,
    the subagent script exits 0 and stays quiet."""

    def setUp(self):
        self.config = tempfile.TemporaryDirectory()
        self.addCleanup(self.config.cleanup)
        self.env = {**os.environ, "CLAUDE_CONFIG_DIR": self.config.name}

    def test_typical_payload_renders_both_lines(self):
        payload = {
            "session_id": "golden",
            "model": {"display_name": "Opus 5 (1M context)"},
            "effort": {"level": "high"},
            "workspace": {"current_dir": "/x/repo/src"},
            "cost": {"total_cost_usd": 1.234, "total_duration_ms": 125000},
            "context_window": {
                "used_percentage": 30,
                "total_input_tokens": 60000,
                "context_window_size": 200000,
                "current_usage": {"cache_read_input_tokens": 9000, "input_tokens": 1000},
            },
            "rate_limits": {"seven_day": {"used_percentage": 20}},
        }
        line1, line2 = run(MAIN, payload, self.config.name).splitlines()
        self.assertEqual(line1, "Opus 5 · high · 📁 src")
        self.assertEqual(
            line2, "███░░░░░░░ 30% · 60k/200k · cache 90% · $1.23 · 2m 05s · 7d 20%")

    def test_garbage_stdin_degrades_to_an_error_marker(self):
        proc = subprocess.run([sys.executable, MAIN], input="not json",
                              capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("status line: JSONDecodeError", ANSI.sub("", proc.stdout))

    def test_subagent_garbage_stdin_exits_zero_and_prints_nothing(self):
        proc = subprocess.run([sys.executable, SUBAGENT], input="not json",
                              capture_output=True, text=True, env=self.env)
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout, "")

    def test_one_broken_task_keeps_the_other_rows(self):
        payload = {"columns": 120, "tasks": [
            {"id": "bad", "name": "X", "status": "running",
             "description": {"not": "a string"}},
            {"id": "good", "name": "Explore", "status": "running",
             "tokenCount": 1000, "contextWindowSize": 200000},
        ]}
        out = run(SUBAGENT, payload, self.config.name)
        self.assertNotIn('"bad"', out)
        self.assertIn('"good"', out)


if __name__ == "__main__":
    unittest.main()
