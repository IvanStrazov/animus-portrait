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
        self.assertNotIn("journal line", out)  # journal is read only when linked
        self.assertNotIn("index only", out)
        self.run_p("link", "on")
        self.assertIn("journal line", self.run_p("collect", "--all").stdout)

    def test_link_offer_and_registry(self):
        self.assertNotIn("link with", self.run_p("status").stdout)  # no journal installed
        (self.claude / "plugins" / "cache" / "animus-journal").mkdir(parents=True)
        self.assertIn("NOT DECIDED", self.run_p("status").stdout)
        self.run_p("link", "off")
        self.assertIn("link with animus-journal: off", self.run_p("status").stdout)
        self.run_p("link", "on")
        eco = json.loads((self.claude / "ecosystem.json").read_text(encoding="utf-8"))
        link = eco["links"]["animus-journal+animus-portrait"]
        self.assertTrue(link["enabled"])
        self.assertIn("Projects", link["auto_sections"])
        self.assertIn("portrait.py", eco["plugins"]["animus-portrait"]["cmd"])
        self.assertTrue(eco["plugins"]["animus-portrait"]["portrait_file"].endswith("portrait.md"))

    def test_digest_presets_and_cost(self):
        self.run_p("link", "on")
        eco = json.loads((self.claude / "ecosystem.json").read_text(encoding="utf-8"))
        link = eco["links"]["animus-journal+animus-portrait"]
        self.assertEqual(link["digest_lines"], 15)  # default preset: medium
        self.run_p("link", "on", "--digest", "short")
        eco = json.loads((self.claude / "ecosystem.json").read_text(encoding="utf-8"))
        self.assertEqual(eco["links"]["animus-journal+animus-portrait"]["digest_sections"],
                         ["Communication & preferences"])
        self.write_session("s1", [("2026-11-01T10:00:00Z", "hi")])
        with open(self.proj / "s1.jsonl", "a", encoding="utf-8") as f:
            for i in range(10):
                f.write(json.dumps({"type": "assistant", "message": {"id": f"m{i}", "content": "x"}}) + "\n")
        out = self.run_p("digest-cost").stdout
        self.assertIn("requests per session: 10", out)
        self.assertIn("no portrait yet", out)
        self.run_p("save", stdin="# P\n## Communication & preferences\n- Russian, direct\n## Who\n- dev\n")
        out = self.run_p("digest-cost").stdout
        self.assertNotIn("no portrait yet", out)
        row = next(l for l in out.splitlines() if l.startswith("short"))
        tokens, per_session = int(row.split()[-3]), int(row.split()[-2])
        self.assertGreater(tokens, 0)
        self.assertEqual(per_session, round(tokens * 1.25 + tokens * 0.1 * 9))

    def test_section_save_and_show(self):
        self.run_p("save", stdin="# Portrait\n## Who\n- dev\n## Projects\n- bot\n## Unknown\n- age\n")
        last = json.loads((self.tmp / "portrait" / "config.json").read_text(encoding="utf-8"))["last_update"]
        self.run_p("save", "--section", "Projects", stdin="- bot (active)\n- journal plugin")
        text = self.run_p("show").stdout
        self.assertIn("journal plugin", text)
        self.assertIn("- dev", text)
        self.assertIn("- age", text)
        self.assertEqual(text.count("## Projects"), 1)
        self.assertNotIn("## Who", self.run_p("show", "--section", "Projects").stdout)
        self.run_p("save", "--section", "Communication & preferences", stdin="- Russian, direct")
        self.assertIn("Russian", self.run_p("show", "--section", "communication").stdout)
        cfg = json.loads((self.tmp / "portrait" / "config.json").read_text(encoding="utf-8"))
        self.assertEqual(cfg["last_update"], last)  # section saves are not full updates
        r = self.run_p("save", "--section", "Projects", stdin="\n".join(["x"] * 50), ok=False)
        self.assertNotEqual(r.returncode, 0)


    def test_legacy_ecosystem_names_are_migrated(self):
        self.claude.mkdir(parents=True, exist_ok=True)
        legacy = {"plugins": {"claude-journal": {"cmd": "py journal.py"}},
                  "links": {"claude-journal+claude-portrait": {"enabled": True, "digest_lines": 25}}}
        (self.claude / "ecosystem.json").write_text(json.dumps(legacy), encoding="utf-8")
        self.assertIn("link with animus-journal: on", self.run_p("status").stdout)
        eco = json.loads((self.claude / "ecosystem.json").read_text(encoding="utf-8"))
        self.assertNotIn("claude-journal+claude-portrait", eco["links"])
        self.assertEqual(eco["links"]["animus-journal+animus-portrait"]["digest_lines"], 25)
        self.assertIn("animus-journal", eco["plugins"])
        self.assertNotIn("claude-journal", eco["plugins"])

if __name__ == "__main__":
    unittest.main()
