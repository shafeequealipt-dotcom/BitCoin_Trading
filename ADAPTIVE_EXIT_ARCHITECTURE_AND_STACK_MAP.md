# Adaptive Exit — Project Architecture And Exit-Stack Reference Map

This is the durable reference map for the Dynamic Adaptive Exit work. It records how the project is built and how the exit stack is wired, so the architecture does not have to be re-derived from four hundred source files each session. It is written in prose with heading structure for screen-reader access: no tables, no emoji, no decorative separators. Where a precise line number matters it is given, but mechanisms are referred to by file and function name first because line numbers drift; the Phase 1 inventory confirms exact lines.

This map is documentation only. It changes no code.

## Part 1 — What the system is

It is a mature asynchronous Python crypto trading-intelligence system with a Model Context Protocol server. The source tree under src holds about four hundred Python modules. It runs as separate processes: the workers process is the production heart (data collection, the brain, and trade and exit management), the MCP server exposes tools to external Claude clients, and a shadow virtual exchange supports paper trading. The single config file config.toml (about 182 kilobytes) plus a small .env for secrets drives everything.

The persistence layer is SQLite in write-ahead-logging mode at data/trading.db, accessed through repository classes. It is not Firestore and not Postgres; any earlier assumption of Firestore is incorrect. The active execution adapter is selected by a transformer mode router and is currently bybit_demo (config.toml line 22), which is paper-money execution against the Bybit demo endpoint, persisting to the same schema as live.

## Part 2 — The layered architecture and data flow

The system is organised in three layers, gated so each requires the prior one to be active. Layer state is checkpointed to data/layer_state.json.

Layer one is data. A set of workers under src/workers (kline, price, news, alternative data, regime, structure, signal, scanner) ingest market data and intelligence on their own cadences and write to caches and the database. The scanner worker assembles a per-coin package each five-minute window.

Layer two is the brain. A strategist (src/brain) runs on a cadence, builds a prompt from the coin packages, open positions, account state, and recent-trade lessons, and calls the Claude CLI as a subprocess (not the billed API) to produce a strategic plan of new trades and position actions.

Layer three is execution and exit. New trades are optimised and gate-validated, then placed through the order service. Open positions are then managed by the exit stack, which is the subject of this program. The exit stack is where the adaptive geometry lives.

The end-to-end path is therefore: market data and intelligence, to per-coin packages, to the brain's strategic plan, to trade-gate validation and order placement, to open-position management by the exit stack, to the close path and the trade-intelligence learning loop.

## Part 3 — Orchestration, services, and the dependency graph

There is no formal dependency-injection framework. The worker manager (src/workers/manager.py) is the composition root: during initialize it connects the database, runs migrations, constructs every service once, and stores each under a logical name in a single shared services dictionary (for example market, order, position, ta, brain, volatility_profiler, structure_engine). Workers receive that dictionary and look services up by name, degrading gracefully when a service is absent. This shared-dictionary-of-named-services pattern is the project's substitute for a container, and any new collaborator is reached the same way.

The transformer (src/core/transformer.py) is a mode router. It holds three parallel service sets (shadow, bybit live, bybit demo) behind order, position, and account proxies, and routes calls to the active set based on a database mode row. Changing mode redirects the whole execution path without restarting workers.

## Part 4 — Persistence and the repository pattern

The database manager (src/database) owns an async SQLite connection in write-ahead-logging mode with a versioned migration set run at boot. Each repository under src/database/repositories owns a logical domain rather than a single table (market, trading, news, sentiment, context, learning, portfolio, and others). Code reads and writes through these repositories, not raw SQL scattered across modules.

A protected-tables guard (src/database/protected_tables.py) blocks any destructive SQL on a frozen set of audit and learning tables and raises a violation with caller attribution unless an explicit force flag is set. The protected set includes tias_results, tias_analyses, trade_intelligence, trade_log, trade_history, thesis_store, virtual_positions, and sniper_log. This program never performs retention or cleanup on these tables. Recorded row counts at the start of this work in data/trading.db were trade_log 320, trade_history 320, trade_intelligence 320, and sniper_log 14855; tias_results, tias_analyses, thesis_store, and virtual_positions were absent in this database.

## Part 5 — The configuration system and how to add a section

Configuration is dataclass-based. At load (src/config/settings.py, the load and _load_fresh methods around line 4805 to 4940), config.toml is parsed with tomllib, each section is built by a dedicated builder function named _build_<section> that takes the section's dictionary and returns a typed settings dataclass, and all sections are assembled into the top-level Settings object by keyword. Some sections are nested, for example structure and volatility_profile live under the analysis table, and the sniper protection knobs live under layer4.sniper.

The robust builder pattern, introduced by the 2026-06-15 owner-switch audit fix, filters incoming keys against the dataclass field names using fields(), as in _build_sl_gateway at line 5570 to 5596: it computes the set of field names from fields(SLGatewaySettings) and passes only matching keys. This correctly loads list and dictionary fields, which the older hasattr-based builders drop. Any new section this program adds uses the fields()-based pattern, not hasattr.

Adding a new configuration section therefore means: define the typed settings dataclass near the related exit dataclasses, add a field for it on the top-level Settings dataclass, write a _build_<section> builder using the fields() filter, call that builder in the load method from toml_data.get of the section name, pass the result into the Settings constructor, add validation in src/config/validators.py, and add the section to config.toml. Every coefficient is named in config; nothing is hardcoded inline. A one-shot boot sentinel log confirms the section loaded.

## Part 6 — Logging, observability, and error handling

Logging is structured. Every event follows the shape of a tag, then space-separated key equals value pairs, then a context suffix: TAG followed by k=v k=v and then the result of ctx(). Tags are bare string constants centralised in src/core/log_tags.py so the taxonomy can be grepped in one place. The context suffix injects the decision id and transaction id for lifecycle tracing. Boot sentinels are one-shot tags emitted when a component is constructed, for example PRICE_FORMATTER_WIRED. Every change in this program emits its own structured tag so each trade's computed geometry and the layer's load are visible in the logs.

Errors derive from a base exception in src/core/exceptions.py with a typed hierarchy for configuration, authentication, order, position, and protected-table violations. Boot-time configuration problems raise a configuration error rather than running with a bad value.

## Part 7 — The exit stack and the owner hierarchy

The exit stack is roughly fourteen interacting mechanisms organised under an owner hierarchy that is built and enforcing. The owner switch decides who writes the stop at any moment; the adaptive layer this program adds decides only what value is written. These concerns are orthogonal by design.

The Head is the catastrophic per-trade cap plus the force-close twins; it is always admitted and only tightens, and it is sacred. The Green Owner is the profit-fetching system: the stepped break-even ladder, the Chandelier trail, the score-action engine, the profit guards, and the graduation latch. The Red Owner is the loss-cutting system: the five-model time-decay engine, the force-close gate stack, the stall valve, the structure stop, the recovery logic, and the initial ATR stop. The Advisory systems are the brain-tighten path, the watchdog trails, and the sentinel and deadline tiers. The enforcer is the stop-loss gateway with its four rules and the owner switch.

### The file map of the exit stack

The profit sniper (src/workers/profit_sniper.py) holds the largest share: the ladder in _compute_ladder_floor (about line 1941 to 2153), the Chandelier trail in _compute_trail_stop (about line 1790 to 1939, with its break-even floor at about 1893 to 1894), the highest-stop-wins selection in _pf_select_stop and _pf_apply_spine, the score-action engine in _determine_action and _classify_score and _execute_action, the graduation latch (about 2618 to 2651), the initial ATR stop, the sacred cap force-close, the recovery candidate _lc_recovery_candidate, and the stall valve with its guard stack including the development guard.

The five-model time-decay loss engine is in src/risk/time_decay_sl.py, driven by the age dial in src/core/time_dial.py. Its behavioral gates (minimum age, the MAE-to-stop ratio, the monotonic-grind cut, the structural-invalidation gate, and the Bayesian win-probability model) are protections, not move-size thresholds, and must stay fixed; only its distance and room values, which are already partly ATR-scaled, may become R-derived.

The stop-loss gateway and owner switch are in src/core/sl_gateway.py. The four rules are tighten-only, minimum-distance, maximum-step, and rate-limit. The minimum-distance rule already consults the volatility profiler (about line 709 to 720) and already has a proven, observable break-even-floor carve-out for trusted profit sources (about line 743 to 815) that clamps toward price but never past a supplied floor, re-checks tighten-only beneath it, and guards against wrong-side stops. This carve-out is the precedent for the adaptive profit-lock exemption.

The position watchdog (src/workers/position_watchdog.py) owns the outer backstops: the minus three percent hard stop is a hardcoded literal at about line 2595, plus the deadline timeout, the deadline tiers, and a peak-lock that does not engage at these move sizes. The sentinel deadline, advisor, and firewall are in src/sentinel. Stop and take-profit geometry and validation are in src/core/sl_geometry.py, src/core/sl_tp_validator.py, and src/core/flip_tp_capper.py. The development guard threshold lives in src/risk/layer4_protection.py and is mirrored in the sniper.

## Part 8 — The volatility substrate and the fee floor, which the adaptive layer reuses

The movement unit R already exists and is already wired. The canonical source is the volatility profiler in src/analysis/volatility_profile.py: VolatilityProfiler.get_profile returns a CoinVolatilityProfile carrying atr_pct_5m, the five-minute Average True Range expressed as a percentage of price, and a volatility_class in dead, low, medium, high, or extreme. The profile is cached on a roughly sixty-to-one-hundred-twenty-second time-to-live with per-symbol jitter, which makes R step-wise stable rather than tick-jittery. The raw ATR math is in src/analysis/indicators/volatility.py.

The pure-function scaling helper is src/analysis/vol_scale.py. It has no state and no input/output. It exposes scale_by_class, which multiplies a value by a per-class factor, and min_distance_for_class, which converts an ATR percentage and class into an effective minimum distance bounded by an absolute floor and a per-class ceiling. The gateway's minimum-distance rule already calls min_distance_for_class, so this helper is already a live consumer of R. This program extends this same file with additional pure functions for the rest of the exit geometry; it is the agreed home for the centralized R-geometry, chosen so the feature is woven into the existing analysis package rather than added as a separate subsystem.

The round-trip taker fee is about 0.11 percent. It appears as cap_round_trip_fee_pct equal to 0.11 in config.toml near line 2212 and as a per-side 0.00055 constant in src/core/trade_coordinator.py. This is the basis for the fee floor beneath every profit threshold.

## Part 9 — The agreed adaptive-exit integration approach

The adaptive layer converts fixed exit thresholds into bounded multiples of R, floored at the fee where the threshold is a profit threshold. It is not a new system, not a new worker, not a new service, and not a new trading gate. The geometry is a set of pure functions added to src/analysis/vol_scale.py; the existing owner functions fetch R from the already-injected profiler and the fee from config, call those functions, and feed the results into their existing logic. Their function bodies stay; only the numbers they compute with change. The calculation being pure makes it per-trade-parallel-safe and replayable offline.

Every coefficient lives in a new centralized adaptive-exit configuration section read once at boot with a boot sentinel, so no new hardcoded value is introduced while the old ones are removed. The owner hierarchy is unchanged. The catastrophic cap stays sacred and only tightens. The gateway's minimum-distance rule is made R-aware for the profit-lock path through an exemption modeled on the existing break-even carve-out, so the adaptive locks stop being clamped away, while tighten-only, maximum-step, rate-limit, and catastrophic precedence are never weakened. The behavioral gates of the loss engine are left untouched.

## Part 10 — The proven problem this program fixes

Every exit threshold is a fixed hardcoded percentage applied identically to every coin, but the entries produce moves far smaller than those thresholds assume. Trades peak at a median near plus 0.23 percent while the ladder's first rung is at 0.6 percent, the trail minimum distance is 0.30 percent, the take-profit is near 2.25 to 6 percent, and the hard stop is a hardcoded minus 3 percent. About 97.6 percent of graduated trades die in the dead band, the trail floors at break-even and almost never wins, the ladder locks only a sliver and that sliver is dropped by the minimum-distance clamp 99.6 percent of the time it is rejected, and the take-profit is unreachable. The full forensic proof is in EXIT_SYSTEMS_DEEP_FORENSIC_FINDINGS.md and the design is in ADAPTIVE_EXIT_BLUEPRINT_AND_INTEGRATION_MAP.md.

## Part 11 — Key file index

The R substrate: src/analysis/volatility_profile.py and src/analysis/vol_scale.py and src/analysis/indicators/volatility.py. The gateway and owner switch: src/core/sl_gateway.py. The profit and loss sniper: src/workers/profit_sniper.py. The loss engine and age dial: src/risk/time_decay_sl.py and src/core/time_dial.py. The watchdog backstops: src/workers/position_watchdog.py. The development guard: src/risk/layer4_protection.py. The deadline and sentinel: src/sentinel. The stop and take-profit geometry: src/core/sl_geometry.py and src/core/sl_tp_validator.py and src/core/flip_tp_capper.py. Configuration: src/config/settings.py and config.toml and src/config/validators.py. Orchestration and persistence: src/workers/manager.py and src/core/transformer.py and src/database. Logging: src/core/log_tags.py and src/core/logging.py and src/core/log_context.py. The replay and verification basis: simulate_exit_authority_live.py and verify_price_path.py and src/workers/price_path_logger.py.
