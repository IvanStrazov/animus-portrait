#!/usr/bin/env python3
"""claude-portrait: a compact, incrementally updated portrait of the user for Claude Code.

Layout (in ~/.claude/portrait/, override with PORTRAIT_DIR):
  portrait.md     the portrait, overwritten on each update, <= PORTRAIT_MAX lines
  private.md      sensitive topics, kept apart (only when policy = "separate")
  config.json     sensitive-topic policy, approved extra sources, last update time

Commands:
  status                              config, last update, how many sessions are new
  config [--sensitive P] [--add-source S] [--remove-source S] [--reset]
  collect [--all] [--max-chars N]     new material since the last update -> stdout
  show [--private]                    print the portrait (or the private part)
  save [--private]                    stdin -> portrait.md (or private.md); marks the update
  where                               print the portrait dir

Sources read by `collect`: the user's own messages from Claude Code transcripts
(~/.claude/projects/*/*.jsonl), Claude Code memory files (memory/*.md), and
claude-journal indexes (~/journal and <project>/.journal) when present.
Nothing else is read by the script; extra sources are listed in config and
gathered by Claude only after the user approved them.

Standard library only, Python 3.9+. No network.
"""
import argparse
import datetime as dt
import json
import os
import re
import sys
import tempfile
from pathlib import Path


def _int_env(name, default):
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


PORTRAIT_MAX = _int_env("PORTRAIT_MAX_LINES", 80)
PRIVATE_MAX = _int_env("PORTRAIT_PRIVATE_MAX_LINES", 40)
COLLECT_MAX = _int_env("PORTRAIT_COLLECT_MAX_CHARS", 40000)
MSG_MAX = _int_env("PORTRAIT_MSG_MAX_CHARS", 300)
PER_SESSION = _int_env("PORTRAIT_PER_SESSION", 12)
POLICIES = ("include", "separate", "exclude")

CLAUDE_DIR = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude").expanduser()
PORTRAIT_DIR = Path(os.environ.get("PORTRAIT_DIR") or CLAUDE_DIR / "portrait").expanduser()
JOURNAL_DIR = Path(os.environ.get("JOURNAL_GLOBAL_DIR") or Path.home() / "journal").expanduser()

for _s in (sys.stdin, sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Messages that are harness noise rather than the user's own words.
NOISE_PREFIXES = ("<", "[Request interrupted", "[Image", "Base directory for this skill",
                  "Caveat:", "This session is being continued", "Your response above was stopped")


def now_iso():
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def parse_iso(s):
    if not s:
        return None
    try:
        d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return None


# ---------- config ----------

def cfg_path():
    return PORTRAIT_DIR / "config.json"


def load_cfg():
    try:
        c = json.loads(cfg_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        c = {}
    c.setdefault("sensitive", None)
    c.setdefault("extra_sources", [])
    c.setdefault("sources_asked", False)
    c.setdefault("last_update", None)
    return c


def atomic_write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    os.replace(tmp, path)


def save_cfg(c):
    atomic_write(cfg_path(), json.dumps(c, ensure_ascii=False, indent=2) + "\n")


# ---------- sources ----------

def transcripts():
    root = CLAUDE_DIR / "projects"
    if not root.is_dir():
        return []
    return sorted(root.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime)


def _text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(x.get("text", "") for x in content
                        if isinstance(x, dict) and x.get("type") == "text")
    return ""


def _clean(text):
    text = re.sub(r"<pasted_content[^>]*>.*?</pasted_content>", " [pasted text] ", text, flags=re.S)
    text = re.sub(r"<system-reminder>.*?</system-reminder>", " ", text, flags=re.S)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def user_messages(path: Path, since):
    """(timestamp, cwd, text) of the user's own messages in one transcript, newer than `since`."""
    out, seen = [], set()
    try:
        fh = open(path, encoding="utf-8", errors="replace")
    except OSError:
        return out
    with fh:
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if d.get("type") != "user" or d.get("isMeta") or d.get("isSidechain"):
                continue
            msg = d.get("message") or {}
            raw = _text_of(msg.get("content"))
            if not raw or raw.lstrip().startswith(NOISE_PREFIXES):
                continue
            ts = parse_iso(d.get("timestamp"))
            if since and ts and ts <= since:
                continue
            text = _clean(raw)
            if len(text) < 2 or text in seen:
                continue
            seen.add(text)
            if len(text) > MSG_MAX:
                text = text[:MSG_MAX].rstrip() + "…"
            out.append((ts, d.get("cwd") or "", text))
    return out


def memory_files():
    root = CLAUDE_DIR / "projects"
    files = sorted(root.glob("*/memory/*.md")) if root.is_dir() else []
    return [f for f in files if f.name != "MEMORY.md"]


def journal_indexes(cwds):
    dirs = [JOURNAL_DIR] + [Path(c) / ".journal" for c in sorted(cwds) if c]
    seen, out = set(), []
    for d in dirs:
        f = d / "INDEX.md"
        try:
            key = str(f.resolve())
        except OSError:
            continue
        if key not in seen and f.is_file():
            seen.add(key)
            out.append(f)
    return out


# ---------- commands ----------

def cmd_where(_):
    print(PORTRAIT_DIR)


def cmd_status(_):
    c = load_cfg()
    since = parse_iso(c["last_update"])
    new = [p for p in transcripts() if not since or
           dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc) > since]
    pf = PORTRAIT_DIR / "portrait.md"
    lines = len(pf.read_text(encoding="utf-8").splitlines()) if pf.is_file() else 0
    print(f"dir: {PORTRAIT_DIR}")
    print(f"sensitive policy: {c['sensitive'] or 'NOT SET - ask the user'}")
    print(f"extra sources asked: {'yes' if c['sources_asked'] else 'NO - ask the user'}")
    print("approved extra sources: " + (", ".join(c["extra_sources"]) or "none"))
    print(f"portrait: {lines} lines" if lines else "portrait: none yet")
    print(f"last update: {c['last_update'] or 'never'}")
    print(f"sessions with new activity: {len(new)}")


def cmd_config(a):
    c = load_cfg()
    if a.reset:
        c = {"sensitive": None, "extra_sources": [], "sources_asked": False,
             "last_update": c["last_update"]}
    if a.sensitive:
        c["sensitive"] = a.sensitive
    for s in a.add_source or []:
        if s not in c["extra_sources"]:
            c["extra_sources"].append(s)
    for s in a.remove_source or []:
        c["extra_sources"] = [x for x in c["extra_sources"] if x != s]
    if a.sources_asked or a.add_source or a.remove_source:
        c["sources_asked"] = True
    save_cfg(c)
    print(json.dumps({k: c[k] for k in ("sensitive", "extra_sources", "sources_asked")},
                     ensure_ascii=False))


def cmd_collect(a):
    c = load_cfg()
    since = None if a.all else parse_iso(c["last_update"])
    budget = a.max_chars
    parts, cwds = [], set()

    sessions = []
    for p in transcripts():
        if since and dt.datetime.fromtimestamp(p.stat().st_mtime, dt.timezone.utc) <= since:
            continue
        msgs = user_messages(p, since)
        if msgs:
            sessions.append((p, msgs))
            cwds.update(m[1] for m in msgs)

    head = [f"# collect: since {since.isoformat() if since else 'the beginning'}; "
            f"{len(sessions)} session(s) with new user messages"]
    if c["sensitive"]:
        head.append(f"# sensitive policy: {c['sensitive']}")

    for f in journal_indexes(cwds):
        txt = f.read_text(encoding="utf-8", errors="replace").strip()
        if txt:
            parts.append(f"## journal index: {f.parent}\n{txt[-3000:]}")
    for f in memory_files():
        if since and dt.datetime.fromtimestamp(f.stat().st_mtime, dt.timezone.utc) <= since:
            continue
        txt = f.read_text(encoding="utf-8", errors="replace").strip()
        txt = re.sub(r"\A---\n.*?\n---\n", "", txt, flags=re.S).strip()  # frontmatter costs tokens
        parts.append(f"## memory: {f.parent.parent.name[-40:]}/{f.name}\n{txt[:2000]}")

    # Newest sessions first, so a tight budget keeps the freshest material.
    for p, msgs in reversed(sessions):
        ts = next((m[0] for m in msgs if m[0]), None)
        cwd = next((m[1] for m in msgs if m[1]), "")
        title = f"## session {ts.date() if ts else '?'} | {Path(cwd).name or cwd or '?'}"
        picked = msgs if len(msgs) <= PER_SESSION else (
            msgs[: PER_SESSION - 2] + msgs[-2:])  # opening asks + how it ended
        body = "\n".join(f"- {m[2]}" for m in picked)
        if len(msgs) > len(picked):
            body += f"\n- (+{len(msgs) - len(picked)} more messages)"
        parts.append(f"{title}\n{body}")

    out, used, dropped = head, sum(len(h) for h in head), 0
    for part in parts:
        if used + len(part) > budget:
            dropped += 1
            continue
        out.append(part)
        used += len(part)
    if dropped:
        out.append(f"# {dropped} block(s) dropped by the {budget}-char budget; rerun with --max-chars")
    print("\n\n".join(out))


def cmd_show(a):
    f = PORTRAIT_DIR / ("private.md" if a.private else "portrait.md")
    print(f.read_text(encoding="utf-8") if f.is_file() else "(empty)")


def cmd_save(a):
    text = sys.stdin.read().strip() + "\n"
    limit = PRIVATE_MAX if a.private else PORTRAIT_MAX
    n = len(text.splitlines())
    if n > limit:
        sys.exit(f"refused: {n} lines > {limit}. Compress and retry.")
    c = load_cfg()
    if a.private and c["sensitive"] != "separate":
        sys.exit(f"refused: sensitive policy is {c['sensitive']!r}, private.md is used only with 'separate'.")
    atomic_write(PORTRAIT_DIR / ("private.md" if a.private else "portrait.md"), text)
    if not a.private:
        c["last_update"] = now_iso()
        save_cfg(c)
    print(f"saved {n} lines -> {PORTRAIT_DIR / ('private.md' if a.private else 'portrait.md')}")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="portrait.py", description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("where").set_defaults(fn=cmd_where)
    sub.add_parser("status").set_defaults(fn=cmd_status)
    p = sub.add_parser("config")
    p.add_argument("--sensitive", choices=POLICIES)
    p.add_argument("--add-source", action="append")
    p.add_argument("--remove-source", action="append")
    p.add_argument("--sources-asked", action="store_true", help="record that the user was asked (even if none approved)")
    p.add_argument("--reset", action="store_true")
    p.set_defaults(fn=cmd_config)
    p = sub.add_parser("collect")
    p.add_argument("--all", action="store_true", help="ignore the last update time")
    p.add_argument("--max-chars", type=int, default=COLLECT_MAX)
    p.set_defaults(fn=cmd_collect)
    p = sub.add_parser("show")
    p.add_argument("--private", action="store_true")
    p.set_defaults(fn=cmd_show)
    p = sub.add_parser("save")
    p.add_argument("--private", action="store_true")
    p.set_defaults(fn=cmd_save)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
