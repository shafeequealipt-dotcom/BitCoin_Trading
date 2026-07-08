# CLAUDE.md — Rules for This Project

## MANDATORY: Analyse Before Touching Anything

Before removing, modifying, or moving ANY variable, function, block, or import:

1. **Grep all usages across the entire file first** — every reference must be accounted for
2. **Grep callers in other files** — check if anything external depends on it
3. **Map all dependencies** — if a variable is defined in block A and used in block B, deleting block A breaks block B even if they look unrelated
4. **Never assume a block is self-contained** — always verify

**This rule exists because:** A variable assignment (`thesis_mgr_early = self.services.get("thesis_manager")`) was deleted along with a duplicate lessons block, but the market data section 60 lines lower still referenced that variable. This caused a NameError caught silently, injected into Claude's prompt, and broke strategic reviews. A single `grep thesis_mgr_early strategist.py` before deleting would have caught it.

---

## MANDATORY: Zero Pending Work at Session End

At the end of every work session the repo must be in a clean shipped state:

- **Zero unmerged feature branches** — work either lands on `main` or the branch gets explicitly deferred with the operator's acknowledgement
- **Zero uncommitted code** — every change must be committed. The only exception is auto-updated runtime files (`data/layer_state.json`, `data/logs/*`) which the trading system writes during operation
- **Zero unpushed commits on main** — everything on local `main` must be pushed to `origin/main`

**Default workflow is direct-to-`main` with atomic commits.** Do not create feature branches unless the operator explicitly asks for one. If a feature branch IS created, it gets merged and deleted within the same session.

Before declaring a session "done", run the audit:

```bash
git status --short                    # uncommitted state (only runtime files OK)
git log origin/main..main --oneline   # unpushed commits — must be empty
git branch --no-merged main           # unmerged branches — must be empty
```

**This rule exists because:** On 2026-05-20 the operator discovered 14 unpushed commits on local main, 3 unmerged feature branches containing valuable work assumed-shipped (the wd_brain_scoring system never reached main; the 5-min reentry cooldown never reached main), and ~40 untracked clutter files. The state mismatched what later planning documents claimed was shipped, causing planning errors downstream.

**Operator interaction protocol:** The operator (Inshad) is a blind developer using a screen reader and does NOT know git internals. When narrating git operations, use plain language:

- "upload" for push
- "combine" for merge
- "replay" for cherry-pick
- "save" for commit
- "side-branch" for feature branch
- Print branch tip SHAs before any branch deletion so the operator can verify what's about to be destroyed

---

## General Rules

- Professional, industry standard, enterprise level — always
- Do not assume anything — verify by reading the actual code
- No band-aid fixes — root cause analysis first, then implement
- Do not touch any file without fully understanding its wiring, integration, and connections
- Read every file listed before writing any code
- Analyse, then implement — never the other way around
