# Changelog

## 0.4.1 — 2026-11-17
- Skill: `config` with no flags shows the settings; describe locations in plain words, full paths only if asked.

## 0.4.0 — 2026-11-17
- New `forget --yes`: deletes portrait.md and private.md and keeps the privacy settings (`--settings` also deletes config.json). The skill confirms first and never uses rm.
- With policy `exclude`, `collect` masks messages on sensitive topics, so they never reach the model.
- Skill: section names stay in English (translate only when talking); when linked, the digest is sent to the model each session; cost explained plainly to non-technical users.

## 0.3.3 — 2026-11-17
- `save --section` drops a repeated header and refuses a body that contains other `## ` sections.
- Skill: ask first, save after; sensitive topics are never written to the linked sections (the journal does not filter them); Windows: take the interpreter from ecosystem.json when `python`/`python3` are Store stubs.

## 0.3.2 — 2026-11-17
- Skill: first-run setup leads with a one-line default the user can accept with "ok".
- Skill: with no portrait yet, "what do you know about me" names the past sessions available instead of "nothing".
- `status` shows how many past sessions there are and their dates; `config` prints a readable line instead of JSON.

## 0.3.1 — 2026-11-17
- Skill: with `exclude`, sensitive facts are never named back to the user either; with `separate`, only on request.
- Skill: the current working folder is not a source unless approved; name exactly the sources that were read; `show` is the only way to look at the files.
- `collect` header spells out what the sensitive policy means.

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
