# Changelog

## 0.3.0 — 2026-11-17
- Renamed to **animus-portrait**, part of the Animus ecosystem (was claude-portrait). New repository: `IvanStrazov/animus-portrait` (the old `IvanStrazov/claude-portrait` stays as 0.2.0).
- To upgrade from 0.2.0: `/plugin marketplace add IvanStrazov/animus-portrait`, then `/plugin install animus-portrait@animus-portrait`, then remove `claude-portrait`. Your portrait in `~/.claude/portrait/` is untouched.
- The shared `~/.claude/ecosystem.json` is migrated automatically: an existing link with the journal carries over.

## 0.2.0 — 2026-11-17
- Link with [animus-journal](https://github.com/IvanStrazov/animus-journal) through a shared `~/.claude/ecosystem.json`, offered on first use (`link on|off`). Journal indexes are read only when linked.
- Digest sizes for the journal's session start (off / short / medium / long) and `digest-cost`, which measures each one's token cost with prompt caching from your own sessions.
- `show --section` / `save --section` to change one section cheaply (used by the journal at wrap-up).

## 0.1.0 — 2026-11-17
- First release: `portrait` skill and `scripts/portrait.py` (status, config, collect, show, save).
- Incremental updates: `collect` returns only messages newer than the last saved portrait.
- First-run questions: sensitive-topic policy (include / separate / exclude) and extra sources.
