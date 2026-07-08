# Issue A — Phase 1 Synthesis: Prompt Trimming Dropping URGENT Alerts

> Consolidated investigation. Replaces the 8 separate deliverables (anatomy / inventory / trim logic / cap config / trim pattern / prompt growth / Claude limits / synthesis) — same content, single document for review efficiency. All claims have file:line evidence.

## Root cause (single sentence)

The CALL_A `_build_trade_prompt` path emits the URGENT WATCHDOG ALERTS section with header `"## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED"` (`src/core/urgent_queue.py:128`), but the priority-aware trim's ESSENTIAL marker for that section is the substring `"OVERRIDE — URGENT WATCHDOG ALERTS"` (`src/brain/strategist.py:352`). The two strings have no common substring, so `_infer_section_priority` (`strategist.py:414–436`) classifies the live URGENT block as OPTIONAL and the trim drops it first when prompts exceed the 14 000-char cap. Three other consistently-dropped lines (`Equity:`, `Available:`, `Maximum concurrent positions:`) have no marker at all.

## Evidence chain

### 1. Two competing URGENT headers

```
src/core/urgent_queue.py:128
    header = (
        "\n## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED\n"
        "These positions need your attention. ..."

src/brain/strategist.py:694                    # CALL_B path (different)
    '\n\nOVERRIDE — URGENT WATCHDOG ALERTS:\n'

src/brain/strategist.py:352                    # marker tuple
    "OVERRIDE — URGENT WATCHDOG ALERTS",
```

CALL_A (`_build_trade_prompt`) injects via `urgent_queue.format_for_prompt()` at `strategist.py:2967` → header is the `## URGENT…` form. The marker tuple still references the older `OVERRIDE —` form used by a different code path. Substring `"OVERRIDE — URGENT WATCHDOG ALERTS"` does not occur in `"## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED"`. Match fails.

### 2. Live log proof

Combined log `data/logs/combined_2026-05-08_13-00_to_16-00.log`:

| Pattern | Count |
|---|---|
| `OVERRIDE — URGENT` (the marker substring) anywhere in 17.9 MiB of logs | 0 |
| `URGENT WATCHDOG` in `dropped_labels=` of `CLAUDE_PROMPT_TRIMMED` events | 14 |
| Total `CLAUDE_PROMPT_TRIMMED` events with `mode=priority` | 21 |
| Events with `dropped_important > 0` | 17 |

Two examples (paraphrased — see baseline doc for full text):

```
13:29:25  CLAUDE_PROMPT_TRIMMED  chars_before=18716 chars_after=14098  dropped_optional=32
          dropped_labels=['## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED', ...]
13:37:41  CLAUDE_PROMPT_TRIMMED  chars_before=19919 chars_after=14339  dropped_optional=35  dropped_important=2
          dropped_labels=['## URGENT WATCHDOG ALERTS — IMMEDIATE ACTION REQUIRED',
                          '## WATCHDOG EVENTS (since last review)', ...]
```

### 3. Bare-line metadata sections lack any marker

CALL_A appends three consecutive single-line sections with no leading header. Each becomes its own list element in `sections[]`, so each is classified independently:

```
src/brain/strategist.py:2861  sections.append(f"Equity: ${account.total_equity:,.2f}")
src/brain/strategist.py:2862  sections.append(f"Available: ${account.available_balance:,.2f}")
src/brain/strategist.py:2904  sections.append(f"Maximum concurrent positions: {limits.max_positions}")
```

The marker tuple at `strategist.py:343–391` does **not** contain `"Equity:"`, `"Available:"`, or `"Maximum concurrent positions"`. The neighbouring `## ACCOUNT` header (line 2855) is its own preceding section element — it does not protect downstream siblings.

`Per-trade size limit` (line 2901) **is** in the marker tuple (line 375) and is correctly preserved. That confirms the substring-match path works when the marker exists.

The dropped-labels in every sampled trim event show `Equity: $X`, `Available: $X`, `Maximum concurrent positions: 10` being dropped. They are dropped on every trim cycle because they are unmatched.

### 4. The 14 000 cap is consistently exceeded

| Sample | chars_before | chars_after | sections_before | sections_after |
|---|---|---|---|---|
| 13:06:14 | 16 910 | 13 845 | 41 | 17 |
| 13:14:12 | 17 736 | 14 057 | 42 | 13 |
| 13:21:13 | 18 456 | 14 083 | 45 | 10 |
| 13:29:25 | 18 716 | 14 098 | 41 | 10 |
| 13:37:41 | 19 919 | 14 339 | 44 |  8 |

Average overshoot ~4 700 chars. None of the sampled events hit the 80-section cap (`sections_before` 41–45) — the trim trigger is always `reason=chars`. Section count never drives the trim. After dropping all OPTIONAL, 17 of 21 events still had to drop IMPORTANT-tagged sections too, meaning even the IMPORTANT category is overcapped.

### 5. Cap origin

```
$ git blame -L 3017,3018 src/brain/strategist.py
fbd13dea (inshadaliqbal786 2026-04-27 04:15:09)         _SECTION_CAP = 80
fbd13dea (inshadaliqbal786 2026-04-27 04:15:09)         _CHAR_CAP = 14000
```

The cap was added with the original priority-aware trim (commit `fbd13dea`). No surrounding comment quantifies the original choice; the in-file note (`strategist.py:3010`) cites "Phase-7 guidance (80 / 14k)". Since 2026-04-27 the prompt has grown by multiple sections (callb-1B/1C/1D/1E, top-10 candidate widening, aggressive-framing rewrites) — none lifted the cap.

### 6. Claude model context budget

The CALL_A path posts via Claude Code CLI subprocess (`src/brain/claude_code_client.py`):
- Binary: `/usr/lib/node_modules/@anthropic-ai/claude-code/cli.js`
- Model: defaults to whatever the CLI is configured for. The 14 000-char cap (~3 500–4 000 tokens) is roughly an order of magnitude below the smallest production Claude model's context window (Sonnet 200 k tokens; Opus also 200 k; Opus 1M variant 1 000 000 tokens). Token cost differences for an additional 5–10 k characters per CALL_A are negligible relative to the value of getting URGENT alerts to Claude.

The 14 000-char cap is therefore a self-imposed budget, not a model-imposed one.

### 7. Trim algorithm (priority-aware path)

`src/brain/strategist.py:3032–3082`:

```
priorities = [_infer_section_priority(s, i) for i, s in enumerate(sections)]
for target_pri in (OPTIONAL, IMPORTANT):
    i = len(sections) - 1
    while i >= 0 and (len(sections) > _SECTION_CAP or sum(len(s) for s in sections) > _CHAR_CAP):
        if priorities[i] == target_pri:
            drop sections[i]
        i -= 1
# ESSENTIAL never dropped
```

Two sequential passes. First pass removes only OPTIONAL; if still over cap, second pass removes IMPORTANT. ESSENTIAL is permanently retained. Bug surface: anything misclassified as OPTIONAL is dropped first regardless of operator-intent priority.

`_infer_section_priority` (`strategist.py:414–436`):

```
def _infer_section_priority(content: str, index: int) -> int:
    if index == 0:
        return ESSENTIAL                       # first section (coaching) always kept
    head = content[:200] if content else ""
    for marker in _TRIM_ESSENTIAL_MARKERS:
        if marker in head: return ESSENTIAL
    for marker in _TRIM_IMPORTANT_MARKERS:
        if marker in head: return IMPORTANT
    return OPTIONAL                            # default
```

Notes:
- Window is the first 200 chars of the section content.
- `marker in head` is plain Python `in` (substring check, case-sensitive).
- Default-OPTIONAL means ANY section without an explicit marker drops first.

### 8. Legacy trim path (active when `enable_priority_trim=false`)

`strategist.py:3084–3110`: pops from the tail with a 30-section floor (`len(sections) > 30`). Has no char floor — once 30 sections remain, the loop stops even if `chars > _CHAR_CAP`. Not active in the captured logs (events show `mode=priority`), but a regression risk if the config flag flips.

## Cleanly-categorized sections in CALL_A

From `_build_trade_prompt` (line 2235) appends and the live logs:

| Section (or category) | Append site (strategist.py) | Marker match? | Effective category |
|---|---|---|---|
| Coaching / system block | line 858 (`sections.append(f"## {coaching}")`) | index 0 → ESSENTIAL forced | ESSENTIAL |
| `## REGIME-SPECIFIC TRADING INSTRUCTIONS` | 901 | yes (line 353) | ESSENTIAL |
| `## DIRECTION PERFORMANCE` | 909 | yes (line 395) | IMPORTANT |
| Trading-Mode line | 916 | yes (line 399) | IMPORTANT |
| `## MARKET DATA` header + per-coin rows | 955 + 1046/1073/1081 | yes (line 344) | ESSENTIAL |
| X-RAY structural setups | ~1205 | yes optional (line 407) | OPTIONAL |
| `## SENTIMENT` | 1215/1217 | yes optional (line 405) | OPTIONAL |
| `## MARKET REGIME (CONTROLS YOUR TRADE DIRECTION)` | 1222 | yes (line 354) | ESSENTIAL |
| `## YOUR OPEN POSITIONS` / theses | 1249 | no (`## OPEN POSITIONS` matches at 348 — substring works) | ESSENTIAL |
| `## LESSONS FROM RECENT TRADES` | 1279 | yes optional (line 408) | OPTIONAL |
| `## BYBIT EXCHANGE POSITIONS (ground truth)` | 1294 | yes (line 350) | ESSENTIAL |
| `## ACCOUNT` header | 2855 | yes (line 345) | ESSENTIAL |
| `Equity: $X` | 2861 | **no marker** | OPTIONAL ❌ |
| `Available: $X` | 2862 | **no marker** | OPTIONAL ❌ |
| `Per-trade size limit: $X` | 2901 | yes (line 375) | ESSENTIAL |
| `Maximum concurrent positions: N` | 2904 | **no marker** | OPTIONAL ❌ |
| Event buffer (HIGH/MED/LOW since last review) | 2947 | usually no marker | OPTIONAL ❌ |
| URGENT WATCHDOG block (CALL_A path) | 2968 | **no — header is `## URGENT…` not `OVERRIDE —`** | OPTIONAL ❌ |
| Per-coin score lines | candidate-package emit (~2475) | varies | OPTIONAL by default |

The five rows marked ❌ are the consistently-dropped sections in the live logs.

## Audit-vs-code deltas

1. Audit said `_build_trade_prompt` is at line 3073. Working tree: function head at line 2235. Line 3073 is the trim emission `log.warning`. (Cosmetic — but corrects the prompt.)
2. Audit/Phase-0-baseline said "URGENT alerts dropped at least 2 events". Working tree: 14 events have URGENT in dropped_labels.
3. Audit listed `Equity:` and `Available:` as protected via substring match; working tree marker tuple has neither.
4. Audit framed the cap as defendable. Working tree shows raw prompts overshoot by 3–6 k consistently — IMPORTANT-tagged sections also being dropped in 17/21 events.

## Why current approach is wrong (broken assumption)

The priority-aware trim assumes (a) every section worth keeping starts with a recognisable header substring, and (b) the marker tuple is kept in sync with each new emit site. Both are violated by the live code:
- Bare metadata appends (Equity / Available / Maximum concurrent positions) intentionally have no header and weren't added to the marker tuple when they were introduced.
- The URGENT block was reformatted (urgent_queue refactor) without updating the marker.
- Section growth has outpaced the cap, so the OPTIONAL→IMPORTANT cascade is now also touching IMPORTANT — content the operator considers worth keeping by intent.

## What the fix must achieve

1. URGENT alerts must never be dropped by trim.
2. Equity, Available, Maximum concurrent positions must never be dropped.
3. All other content currently classified as ESSENTIAL/IMPORTANT (unchanged) stays.
4. No silent failure mode where a future marker-vs-header drift re-introduces the bug.
5. No latency or cost regression.
6. No behavioural change to CALL_A's submitted prompt structure or order.

Solution options enumerated in `issueA_phase2_report.md`.
