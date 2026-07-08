#!/usr/bin/env python3
"""Read-only verification for the TIAS/APEX category fix (issues #2 and #3).

Proves the corrected behavior against the live code and database WITHOUT writing
anything. Run from the project root:

    .venv/bin/python verify_tias_apex_category_fix.py

It checks, per Part D of the spec:
  * Issue #3 contract: 18 definitions, 2 success / 16 failure, sets consistent,
    normalize_category behaves (ok / normalized / invalid), prompt carries the
    definitions and the corrected CORRECT_TRADE_BAD_LUCK meaning.
  * Issue #2 filter: the situation query source now filters win = 0, and over the
    live database a win=0 list can never contain a success category (the general
    invariant), with a concrete OLD-vs-NEW demonstration for a live-style window.
  * Coupling: FAILURE_CATEGORIES == ALL - SUCCESS, and success categories never
    occur on losses, so the win=0 filter and the failure set agree by construction.
  * Consumer integrity: the optimizer prompt builder, the assembler, and the brain
    lesson composer all import cleanly.
  * Forward-only: prints the historical row count and category distribution; the
    script writes nothing, so history is untouched.

The DB is opened read-only (mode=ro). Nothing here mutates data.
"""

from __future__ import annotations

import os
import sqlite3
import sys

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "trading.db")
REPO_PATH = os.path.join(os.path.dirname(__file__), "src", "tias", "repository.py")

_results: list[tuple[str, bool, str]] = []


def check(name: str, passed: bool, detail: str = "") -> None:
    _results.append((name, passed, detail))
    print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" -- {detail}" if detail else ""))


def ro_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Issue #3 — the contract module and the prompt
# ---------------------------------------------------------------------------

def verify_contract() -> None:
    from src.tias.categories import (
        ALL_CATEGORIES,
        CATEGORY_DEFINITIONS,
        FAILURE_CATEGORIES,
        SUCCESS_CATEGORIES,
        is_failure,
        normalize_category,
    )

    check("contract: 18 definitions", len(CATEGORY_DEFINITIONS) == 18,
          f"got {len(CATEGORY_DEFINITIONS)}")
    check("contract: 2 success / 16 failure",
          len(SUCCESS_CATEGORIES) == 2 and len(FAILURE_CATEGORIES) == 16,
          f"success={sorted(SUCCESS_CATEGORIES)} failure_count={len(FAILURE_CATEGORIES)}")
    check("contract: sets consistent (FAILURE == ALL - SUCCESS)",
          FAILURE_CATEGORIES == (ALL_CATEGORIES - SUCCESS_CATEGORIES))
    check("contract: BAD_LUCK is a failure (corrected meaning)",
          is_failure("CORRECT_TRADE_BAD_LUCK") and
          "CORRECT_TRADE_BAD_LUCK" not in SUCCESS_CATEGORIES)
    check("contract: CORRECT_ENTRY / CORRECT_EXIT are the only successes",
          SUCCESS_CATEGORIES == frozenset({"CORRECT_ENTRY", "CORRECT_EXIT"}))

    # normalize_category behaviors
    ok_val, ok_status = normalize_category("CORRECT_ENTRY")
    norm_val, norm_status = normalize_category("correct_entry")
    bad_val, bad_status = normalize_category("FOO_NOT_A_CATEGORY")
    none_val, none_status = normalize_category(None)
    check("normalize: exact value -> ok", ok_val == "CORRECT_ENTRY" and ok_status == "ok")
    check("normalize: lowercase -> normalized",
          norm_val == "CORRECT_ENTRY" and norm_status == "normalized")
    check("normalize: unknown -> invalid (value kept, not dropped)",
          bad_status == "invalid" and bad_val == "FOO_NOT_A_CATEGORY")
    check("normalize: None -> invalid", none_val is None and none_status == "invalid")


def verify_prompt() -> None:
    from src.tias.categories import CATEGORY_DEFINITIONS
    from src.tias.prompts import TIAS_SYSTEM_PROMPT

    check("prompt: contains CATEGORY DEFINITIONS block",
          "CATEGORY DEFINITIONS" in TIAS_SYSTEM_PROMPT)
    check("prompt: contains the corrected BAD_LUCK meaning (loss-only)",
          "never apply it to a winning trade" in TIAS_SYSTEM_PROMPT.replace("\n", " "))
    check("prompt: contains the win tie-break (default CORRECT_ENTRY)",
          "default" in TIAS_SYSTEM_PROMPT and "CORRECT_EXIT only" in TIAS_SYSTEM_PROMPT)
    # Every category name appears in the rendered prompt
    missing = [c for c in CATEGORY_DEFINITIONS if c not in TIAS_SYSTEM_PROMPT]
    check("prompt: every category name rendered", not missing,
          f"missing={missing}" if missing else "all 18 present")


# ---------------------------------------------------------------------------
# Issue #2 — the failure-filtered situation query
# ---------------------------------------------------------------------------

def verify_query_source() -> None:
    with open(REPO_PATH, encoding="utf-8") as fh:
        src = fh.read()
    # The common-categories query must now carry the win=0 filter.
    block = ""
    if "SELECT ds_category" in src:
        block = src.split("SELECT ds_category")[1].split("LIMIT 5")[0]
    check("source: common-categories query has 'AND win = 0'",
          "win = 0" in block, "filter present in the ds_category query block")


def verify_db_invariants() -> None:
    from src.tias.categories import SUCCESS_CATEGORIES

    conn = ro_conn()
    try:
        # General invariant (the real proof for issue #2): success categories
        # NEVER occur on losing trades, so a win=0 filter can never surface one.
        placeholders = ",".join("?" for _ in SUCCESS_CATEGORIES)
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM trade_intelligence "
            f"WHERE win = 0 AND ds_category IN ({placeholders})",
            tuple(SUCCESS_CATEGORIES),
        ).fetchone()
        check("db invariant: success categories never on losses (win=0 cannot surface them)",
              row["n"] == 0, f"loss rows tagged success = {row['n']}")

        # Concrete OLD vs NEW demonstration for a live-style window.
        regime, fg_lo, fg_hi = "volatile", 20, 40

        def top5(extra_where: str) -> list[str]:
            q = (
                "SELECT ds_category FROM trade_intelligence "
                "WHERE regime = ? AND fear_greed_value BETWEEN ? AND ? "
                "AND ds_category IS NOT NULL " + extra_where +
                " GROUP BY ds_category ORDER BY COUNT(*) DESC LIMIT 5"
            )
            return [r["ds_category"] for r in conn.execute(q, (regime, fg_lo, fg_hi)).fetchall()]

        old_list = top5("")            # pre-fix behavior (no outcome filter)
        new_list = top5("AND win = 0")  # post-fix behavior
        print(f"      window: regime={regime} F&G {fg_lo}-{fg_hi}")
        print(f"      OLD (pre-fix, no filter): {old_list}")
        print(f"      NEW (post-fix, win=0):    {new_list}")
        old_had_success = [c for c in old_list if c in SUCCESS_CATEGORIES]
        new_has_success = [c for c in new_list if c in SUCCESS_CATEGORIES]
        check("db demo: OLD list contained success categories (the defect)",
              len(old_had_success) > 0, f"old success cats = {old_had_success}")
        check("db demo: NEW list contains ZERO success categories (the fix)",
              len(new_has_success) == 0, f"new success cats = {new_has_success}")
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Consumer integrity + forward-only
# ---------------------------------------------------------------------------

def verify_consumers() -> None:
    ok = True
    detail = []
    for mod in ("src.apex.prompts", "src.apex.assembler", "src.core.thesis_manager",
                "src.tias.analyzer", "src.tias.repository"):
        try:
            __import__(mod)
        except Exception as e:  # noqa: BLE001
            ok = False
            detail.append(f"{mod}: {type(e).__name__}: {str(e)[:80]}")
    check("consumers: optimizer prompt / assembler / brain lesson / analyzer / repo import cleanly",
          ok, "; ".join(detail) if detail else "all import")


def report_forward_only() -> None:
    conn = ro_conn()
    try:
        total = conn.execute("SELECT COUNT(*) AS n FROM trade_intelligence").fetchone()["n"]
        print(f"      historical rows (unchanged by this fix): {total}")
        rows = conn.execute(
            "SELECT ds_category, COUNT(*) AS n FROM trade_intelligence "
            "WHERE ds_category IS NOT NULL GROUP BY ds_category ORDER BY n DESC"
        ).fetchall()
        dist = ", ".join(f"{r['ds_category']}={r['n']}" for r in rows)
        print(f"      category distribution (forward-only; not rewritten): {dist}")
        check("forward-only: script wrote nothing (read-only connection)", True,
              "no UPDATE/DELETE issued")
    finally:
        conn.close()


def main() -> int:
    print("TIAS/APEX category fix verification (read-only)\n")
    print("-- Issue #3: contract module --")
    verify_contract()
    print("\n-- Issue #3: analysis prompt --")
    verify_prompt()
    print("\n-- Issue #2: failure-filtered situation query --")
    verify_query_source()
    verify_db_invariants()
    print("\n-- Consumer integrity --")
    verify_consumers()
    print("\n-- Forward-only --")
    report_forward_only()

    passed = sum(1 for _, ok, _ in _results if ok)
    failed = [n for n, ok, _ in _results if not ok]
    print(f"\nSUMMARY: {passed}/{len(_results)} checks passed")
    if failed:
        print("FAILED: " + "; ".join(failed))
        return 1
    print("ALL CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
