"""End-to-end tests for scripts/portrait.py (stdlib unittest, no dependencies)."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "portrait.py"


class PortraitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.claude = self.tmp / "claude"
        self.proj = self.claude / "projects" / "C--work-demo"
        self.proj.mkdir(parents=True)
        self.env = dict(os.environ, CLAUDE_CONFIG_DIR=str(self.claude),
                        PORTRAIT_DIR=str(self.tmp / "portrait"),
                        JOURNAL_GLOBAL_DIR=str(self.tmp / "journal"),
                        PYTHONIOENCODING="utf-8")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_p(self, *args, stdin=None, ok=True):
        r = subprocess.run([sys.executable, str(SCRIPT), *args], input=stdin, env=self.env,
                           capture_output=True, text=True, encoding="utf-8")
        if ok:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def write_session(self, name, msgs):
        lines = []
        for ts, text in msgs:
            lines.append(json.dumps({"type": "user", "timestamp": ts, "cwd": "/work/demo",
                                     "message": {"role": "user", "content": text}}))
        lines.append(json.dumps({"type": "assistant", "message": {"content": "ignored reply"}}))
        lines.append(json.dumps({"type": "user", "timestamp": msgs[-1][0], "message": {
            "content": [{"type": "tool_result", "content": "ignored tool output"}]}}))
        (self.proj / f"{name}.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_status_asks_on_first_run(self):
        out = self.run_p("status").stdout
        self.assertIn("NOT SET", out)
        self.assertIn("NO - ask the user", out)

    def test_collect_filters_noise_and_keeps_user_words(self):
        self.write_session("s1", [
            ("2026-11-01T10:00:00Z", "Сделай бота для инвестиций"),
            ("2026-11-01T10:01:00Z", "<command-name>/foo</command-name>"),
            ("2026-11-01T10:02:00Z", "[Request interrupted by user]"),
            ("2026-11-01T10:03:00Z", "Сделай бота для инвестиций"),
        ])
        out = self.run_p("collect").stdout
        self.assertEqual(out.count("Сделай бота для инвестиций"), 1)
        self.assertNotIn("command-name", out)
        self.assertNotIn("interrupted", out)
        self.assertNotIn("ignored", out)

    def test_incremental_collect_after_save(self):
        self.write_session("s1", [("2020-01-01T00:00:00Z", "old message")])
        self.run_p("save", stdin="# Portrait\n- likes bots\n")
        time.sleep(1.1)
        self.write_session("s2", [("2099-01-01T00:00:00Z", "new message")])
        out = self.run_p("collect").stdout
        self.assertIn("new message", out)
        self.assertNotIn("old message", out)
        self.assertIn("old message", self.run_p("collect", "--all").stdout)

    def test_save_limits_and_private_policy(self):
        r = self.run_p("save", stdin="\n".join(["x"] * 200), ok=False)
        self.assertNotEqual(r.returncode, 0)
        r = self.run_p("save", "--private", stdin="secret", ok=False)
        self.assertNotEqual(r.returncode, 0)
        self.run_p("config", "--sensitive", "separate")
        self.run_p("save", "--private", stdin="secret")
        self.assertIn("secret", self.run_p("show", "--private").stdout)
        self.assertNotIn("secret", self.run_p("show").stdout)

    def test_config_sources(self):
        self.run_p("config", "--add-source", "github", "--add-source", "disk")
        self.run_p("config", "--remove-source", "disk")
        cfg = json.loads((self.tmp / "portrait" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["extra_sources"], ["github"])
        self.assertTrue(cfg["sources_asked"])
        self.run_p("config", "--reset")
        cfg = json.loads((self.tmp / "portrait" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["extra_sources"], [])
        self.assertIsNone(cfg["sensitive"])

    def test_budget_drops_blocks(self):
        for i in range(5):
            self.write_session(f"s{i}", [("2026-11-01T10:00:00Z", f"msg {i} " + "x" * 250)])
        out = self.run_p("collect", "--max-chars", "600").stdout
        self.assertIn("dropped", out)

    def test_memory_and_journal_are_read(self):
        mem = self.proj / "memory"
        mem.mkdir()
        (mem / "fact.md").write_text("user likes tannic wine", encoding="utf-8")
        (mem / "MEMORY.md").write_text("index only", encoding="utf-8")
        j = self.tmp / "journal"
        j.mkdir()
        (j / "INDEX.md").write_text("2026-11-16 | #x | journal line", encoding="utf-8")
        out = self.run_p("collect").stdout
        self.assertIn("tannic", out)
        self.assertIn("journal line", out)
        self.assertNotIn("index only", out)


if __name__ == "__main__":
    unittest.main()
