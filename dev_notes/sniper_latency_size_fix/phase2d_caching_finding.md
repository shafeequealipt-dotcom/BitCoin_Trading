# Phase 2D — Anthropic Prompt Caching Finding

## Status: Cache flag incompatible with current invocation; deferred to follow-up

The plan called for adding `--exclude-dynamic-system-prompt-sections` to the Claude CLI subprocess invocation at `src/brain/claude_code_client.py:993` to improve prompt-cache reuse for the 6.6K `TRADE_SYSTEM_PROMPT`.

## Why it's not implemented in this fix

`claude --help` output for the flag (verbatim):

> --exclude-dynamic-system-prompt-sections          Move per-machine sections (cwd, env info, memory paths, git status) from the system prompt into the first user message. Improves cross-user prompt-cache reuse. **Only applies with the default system prompt (ignored with --system-prompt).**

The current invocation at `src/brain/claude_code_client.py:993` reads:

```python
cmd = [self._claude_path, "-p", "--output-format", "text"]
if system_prompt:
    cmd += ["--system-prompt", system_prompt]
```

Both CALL_A and CALL_B pass `--system-prompt` (TRADE_SYSTEM_PROMPT and POSITION_SYSTEM_PROMPT respectively). The cache flag is therefore IGNORED by the CLI in the current invocation pattern.

## What would be required

To make the cache flag effective, the system prompt would need to flow through `claude --print`'s default-system-prompt path (i.e., delivered via the project's `.claude/CLAUDE.md` or implicitly resolved by the CLI), not via the `--system-prompt` flag.

That refactor is a larger architectural change because:

1. The current `--system-prompt` flag delivers our bespoke `TRADE_SYSTEM_PROMPT` / `POSITION_SYSTEM_PROMPT` constants directly. Switching to `.claude/CLAUDE.md`-based system prompt delivery would require a different prompt assembly mechanism.
2. The two prompts (CALL_A and CALL_B) need different system prompts; the CLI's default system prompt is per-invocation, not per-call-type.
3. An alternative — `--input-format stream-json` with explicit `cache_control` blocks — would change the entire invocation contract.

Each of these is more than a one-line change and introduces risk to the working CALL_A and CALL_B paths.

## What this fix delivers instead

Phase 2 ships the compression sub-phases that work without any subprocess change:

- `enable_prompt_compression` feature flag in `Stage2Settings`
- Identity-preserving compression in `_format_packages_for_prompt_full` (Components line, Active categories line)
- `PROMPT_COMPRESS` observability event for trial measurement

Estimated saving when flag is on: ~600-1000 chars per CALL_A on 10-coin top-N (~5-7% of 15K total). Not the 30%+ target the plan called for, but real and recoverable via flag flip.

## Recommendation for follow-up

After Phase 4 trial measures the actual latency impact of compression alone, decide whether the larger system-prompt restructure is warranted. Options:

1. Restructure to flow `TRADE_SYSTEM_PROMPT` through the default-system-prompt path so the cache flag becomes effective.
2. Switch to `--input-format stream-json` with explicit `cache_control` so prompt caching is configured per-block.
3. Accept the current latency floor as bounded by Anthropic's CLI subprocess characteristics and focus on prompt-content reductions.

This decision belongs to the operator after they see Phase 4 trial data.
