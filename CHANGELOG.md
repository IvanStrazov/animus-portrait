# Changelog

## 0.2.0 — 2026-11-17
- Link with [claude-journal](https://github.com/IvanStrazov/claude-journal) through a shared `~/.claude/ecosystem.json`, offered on first use (`link on|off`). Journal indexes are read only when linked.
- Digest sizes for the journal's session start (off / short / medium / long) and `digest-cost`, which measures each one's token cost with prompt caching from your own sessions.
- `show --section` / `save --section` to change one section cheaply (used by the journal at wrap-up).

## 0.1.0 — 2026-11-17
- First release: `portrait` skill and `scripts/portrait.py` (status, config, collect, show, save).
- Incremental updates: `collect` returns only messages newer than the last saved portrait.
- First-run questions: sensitive-topic policy (include / separate / exclude) and extra sources.
