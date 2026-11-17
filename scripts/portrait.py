#!/usr/bin/env python3
"""animus-portrait: a compact, incrementally updated portrait of the user for Claude Code.

Layout (in ~/.claude/portrait/, override with PORTRAIT_DIR):
  portrait.md     the portrait, overwritten on each update, <= PORTRAIT_MAX lines
  private.md      sensitive topics, kept apart (only when policy = "separate")
  config.json     sensitive-topic policy, approved extra sources, last update time

Commands:
  status                              config, last update, how many sessions are new
  config [--sensitive P] [--add-source S] [--remove-source S] [--reset]
  collect [--all] [--max-chars N]     new material since the last update -> stdout
  show [--private] [--section S]      print the portrait (or the private part, or one section)
  save [--private] [--section S]      stdin -> portrait.md (or private.md, or one section)
  link on|off [--digest PRESET]       link with animus-journal via ~/.claude/ecosystem.json
  digest-cost                         token cost of each digest size (off/short/medium/long), with caching
  where                               print the portrait dir

Sources read by `collect`: the user's own messages from Claude Code transcripts
(~/.claude/projects/*/*.jsonl), Claude Code memory files (memory/*.md), and
animus-journal indexes (~/journal and <project>/.journal) when linked.
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

SECTION_MAX = _int_env("PORTRAIT_SECTION_MAX_LINES", 20)
SCRIPT = Path(__file__).resolve()

# ---------- ecosystem: links with sibling plugins (shared file, same schema in each plugin) ----------
ECO_FILE = Path(os.environ.get("CLAUDE_ECOSYSTEM_FILE") or CLAUDE_DIR / "ecosystem.json").expanduser()
SELF, PEER = "animus-portrait", "animus-journal"
LINK = "+".join(sorted((SELF, PEER)))
# Names before the Animus rename (<= 0.2.0); entries under them are migrated on load.
LEGACY_NAMES = {"claude-journal": "animus-journal", "claude-portrait": "animus-portrait"}
LEGACY_LINK = "claude-journal+claude-portrait"
DIGEST_PRESETS = {  # what the linked journal shows at session start
    "off": ([], 0),
    "short": (["Communication & preferences"], 8),
    "medium": (["Communication & preferences", "Who"], 15),
    "long": (["Communication & preferences", "Who", "Work style with AI"], 25),
}
DEFAULT_DIGEST = "medium"
LINK_DEFAULTS = {"auto_sections": ["Projects", "Communication & preferences"],
                 "digest_sections": DIGEST_PRESETS[DEFAULT_DIGEST][0],
                 "digest_lines": DIGEST_PRESETS[DEFAULT_DIGEST][1]}
# Prompt caching: the digest is written to cache once (x1.25), then read on every later request (x0.1).
CACHE_WRITE, CACHE_READ = 1.25, 0.1

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


def eco_load():
    try:
        e = json.loads(ECO_FILE.read_text(encoding="utf-8"))
        if not isinstance(e, dict):
            e = {}
    except (OSError, ValueError):
        e = {}
    e.setdefault("version", 1)
    e.setdefault("plugins", {})
    e.setdefault("links", {})
    for old, new in LEGACY_NAMES.items():
        if old in e["plugins"]:
            e["plugins"].setdefault(new, e["plugins"].pop(old))
    if LEGACY_LINK in e["links"]:
        e["links"].setdefault(LINK, e["links"].pop(LEGACY_LINK))
    return e


def eco_register():
    """Record where this plugin's script and data are, so sibling plugins can find them."""
    e = eco_load()
    me = {"cmd": f'"{sys.executable}" "{SCRIPT}"', "portrait_file": str(PORTRAIT_DIR / "portrait.md")}
    if e["plugins"].get(SELF) != me:
        e["plugins"][SELF] = me
        try:
            atomic_write(ECO_FILE, json.dumps(e, ensure_ascii=False, indent=2) + "\n")
        except OSError:
            pass
    return e


def peer_installed(e):
    cache = CLAUDE_DIR / "plugins" / "cache"
    old = [k for k, v in LEGACY_NAMES.items() if v == PEER]
    return PEER in e["plugins"] or any((cache / n).is_dir() for n in [PEER] + old)


def link_enabled(e):
    return bool((e["links"].get(LINK) or {}).get("enabled"))


# ---------- portrait sections ----------

def split_sections(text):
    """[(heading or None, lines)] where heading is the '## ' line."""
    out, cur = [], (None, [])
    for line in text.splitlines():
        if line.startswith("## "):
            out.append(cur)
            cur = (line, [])
        else:
            cur[1].append(line)
    out.append(cur)
    return out


def _match(heading, name):
    return heading is not None and heading[3:].strip().lower().startswith(name.strip().lower())


def get_section(text, name):
    for h, lines in split_sections(text):
        if _match(h, name):
            return "\n".join([h] + lines).strip()
    return None


def put_section(text, name, body):
    body = body.strip()
    if not body.startswith("## "):
        body = f"## {name}\n{body}"
    parts, done = [], False
    for h, lines in split_sections(text):
        if _match(h, name) and not done:
            parts.append(body)
            done = True
        elif h is None:
            if "\n".join(lines).strip():
                parts.append("\n".join(lines).strip())
        else:
            parts.append("\n".join([h] + lines).strip())
    if not done:
        parts.append(body)
    return "\n\n".join(parts) + "\n"


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
    e = eco_register()
    if peer_installed(e):
        link = e["links"].get(LINK)
        if link is None:
            print(f"link with {PEER}: NOT DECIDED - offer it (`link on` / `link off`)")
        else:
            print(f"link with {PEER}: {'on' if link.get('enabled') else 'off'}")


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
        head.append(f"# sensitive policy: {c['sensitive']}" + {
            "exclude": " - skip health, mental state, substances, sex life, religion, politics, finances, legal "
                       "trouble: write them nowhere and do not repeat them to the user",
            "separate": " - those topics go to private.md only",
        }.get(c["sensitive"], ""))

    for f in (journal_indexes(cwds) if link_enabled(eco_register()) else []):
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
    text = f.read_text(encoding="utf-8") if f.is_file() else ""
    if a.section:
        text = get_section(text, a.section) or f"(no section {a.section!r})"
    print(text or "(empty)")


def digest_lines(text, sections, n):
    """Same selection the journal uses: chosen sections in order, non-empty lines, capped."""
    names = [s.strip().lower() for s in sections if s.strip()]
    found, cur = {}, None
    for line in text.splitlines():
        if line.startswith("## "):
            head = line[3:].strip().lower()
            cur = next((s for s in names if head.startswith(s)), None)
            if cur is not None:
                found.setdefault(cur, [])
        elif cur is not None and line.strip():
            found[cur].append(line)
    return [l for s in names for l in found.get(s, [])][:n]


def est_tokens(s):
    """Rough Claude token count: ~4 chars/token for ASCII, ~2.5 for other scripts (e.g. Cyrillic)."""
    ascii_n = sum(1 for ch in s if ord(ch) < 128)
    return round(ascii_n / 4 + (len(s) - ascii_n) / 2.5)


def avg_requests(last=30):
    """Mean number of model requests per session, measured from the user's own transcripts."""
    counts = []
    for p in transcripts()[-last:]:
        ids = set()
        try:
            with open(p, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    if '"assistant"' not in line:
                        continue
                    try:
                        d = json.loads(line)
                    except ValueError:
                        continue
                    if d.get("type") == "assistant" and not d.get("isSidechain"):
                        mid = (d.get("message") or {}).get("id") or d.get("uuid")
                        if mid:  # one API response can span several lines; count it once
                            ids.add(mid)
        except OSError:
            continue
        if ids:
            counts.append(len(ids))
    return (sum(counts) / len(counts), len(counts)) if counts else (30.0, 0)


def cmd_digest_cost(_):
    f = PORTRAIT_DIR / "portrait.md"
    text = f.read_text(encoding="utf-8") if f.is_file() else ""
    reqs, n_sessions = avg_requests()
    src = (f"measured over your last {n_sessions} sessions" if n_sessions
           else "no transcripts found, assumed")
    print(f"requests per session: {reqs:.0f} ({src})")
    print("cost model: written to cache once (x1.25), then read from cache on each later request (x0.1)")
    print(f"{'preset':<8} {'sections':<52} {'lines':>5} {'tokens':>6} {'per session':>11} {'per 100':>8}")
    for name, (secs, n) in DIGEST_PRESETS.items():
        if text:
            lines = digest_lines(text, secs, n)
            body = "\n".join(["About the user (from the portrait):"] + lines) if lines else ""
            tok, note = est_tokens(body), ""
        else:
            lines, tok, note = [None] * n, round(n * 20), " (estimate: no portrait yet)"
        per = tok * CACHE_WRITE + tok * CACHE_READ * max(reqs - 1, 0)
        mark = " *" if name == DEFAULT_DIGEST else ""
        print(f"{name + mark:<8} {', '.join(secs) or '-':<52} {len(lines):>5} {tok:>6} "
              f"{per:>11.0f} {per * 100:>8.0f}{note}")
    print("tokens = input-token equivalents; * = default. Exact plan-limit accounting is not public.")


def cmd_link(a):
    e = eco_register()
    link = dict(LINK_DEFAULTS, **(e["links"].get(LINK) or {}))
    if a.digest:
        link["digest_sections"], link["digest_lines"] = DIGEST_PRESETS[a.digest]
        link["digest_preset"] = a.digest
    link["enabled"] = a.state == "on"
    link["decided"] = dt.date.today().isoformat()
    e["links"][LINK] = link
    atomic_write(ECO_FILE, json.dumps(e, ensure_ascii=False, indent=2) + "\n")
    print(f"link {LINK}: {a.state}")


def cmd_save(a):
    if a.section:
        if a.private:
            sys.exit("refused: --section works on portrait.md only.")
        body = sys.stdin.read().strip()
        n = len(body.splitlines())
        if n > SECTION_MAX:
            sys.exit(f"refused: section is {n} lines > {SECTION_MAX}. Compress and retry.")
        f = PORTRAIT_DIR / "portrait.md"
        new = put_section(f.read_text(encoding="utf-8") if f.is_file() else "", a.section, body)
        if len(new.splitlines()) > PORTRAIT_MAX:
            sys.exit(f"refused: portrait would be {len(new.splitlines())} lines > {PORTRAIT_MAX}.")
        atomic_write(f, new)  # a section save is not a full update: last_update stays
        print(f"saved section {a.section!r} ({n} lines) -> {f}")
        return
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
    p.add_argument("--section", help="print only this '## ' section")
    p.set_defaults(fn=cmd_show)
    p = sub.add_parser("save")
    p.add_argument("--private", action="store_true")
    p.add_argument("--section", help="replace only this '## ' section (does not mark a full update)")
    p.set_defaults(fn=cmd_save)
    p = sub.add_parser("link", help=f"link with {PEER} (shared ecosystem.json)")
    p.add_argument("state", choices=("on", "off"))
    p.add_argument("--digest", choices=tuple(DIGEST_PRESETS), help="digest size shown by the journal at session start")
    p.set_defaults(fn=cmd_link)
    sub.add_parser("digest-cost", help="token cost of each digest size, with caching").set_defaults(fn=cmd_digest_cost)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
