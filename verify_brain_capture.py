#!/usr/bin/env python3
"""Verify System 1 — complete Call-A/Call-B brain prompt-and-response capture.

Observability-only. This script never touches the real data/stage2_dumps
directory, the trading database, or any protected table — it operates entirely
in a throwaway temp directory. It checks, with concrete values:

  1. Complete capture: a call writes one JSON record carrying the full prompt,
     full system prompt, full response, explicit call_type, call_id, decision
     id, and timestamp; the filename carries the call type.
  2. Both call types: call_a and call_b each produce a correctly-labelled
     record (the two strategist sites pass these labels).
  3. Fire-and-forget: a forced write failure does NOT raise — the brain cycle
     would proceed undisturbed.
  4. Gate: capture is off by default and turns on via configure_brain_capture
     (config) OR the legacy .enabled sentinel (live override).
  5. Retention sweep: the cleanup worker prunes only aged *.json, caps the
     directory by count, never removes the .enabled sentinel, and refuses any
     directory whose leaf name is not 'stage2_dumps'.

Usage:  .venv/bin/python verify_brain_capture.py
"""
import json
import os
import tempfile
import time
from pathlib import Path

import src.brain.claude_code_client as cc

PASS, FAIL = [], []


def ok(name, cond):
    (PASS if cond else FAIL).append(name)
    print(("  PASS " if cond else "  FAIL ") + name)


def main():
    tmp = Path(tempfile.mkdtemp(prefix="verify_capture_"))
    dump_dir = tmp / "stage2_dumps"
    dump_dir.mkdir()

    print("System 1 verification — brain prompt-and-response capture")
    print("Temp dir:", tmp)

    # --- Gate: off by default (fresh module state, no config, no sentinel) ---
    print("\nGate")
    cc._CAPTURE_ENABLED = False
    cc._DUMP_DIR = dump_dir
    cc._DUMP_SENTINEL = dump_dir / ".enabled"
    cc._maybe_dump_call(1, "p", "s", "r", 10.0, "hash", "call_a")
    ok("capture off by default writes nothing",
       len(list(dump_dir.glob("*.json"))) == 0)

    # config gate on
    cc.configure_brain_capture(True, str(dump_dir))
    ok("configure_brain_capture sets enabled + dir",
       cc._CAPTURE_ENABLED is True and cc._DUMP_DIR == dump_dir)

    # --- Complete capture, both call types ---
    print("\nComplete capture")
    big_prompt = "CANDIDATE BLOCK XRPUSDT entry=1.234 " + ("x" * 5000)
    big_system = "SYSTEM " + ("y" * 19000)
    big_resp = json.dumps({"new_trades": [{"symbol": "XRPUSDT", "side": "long"}]})
    cc._maybe_dump_call(25, big_prompt, big_system, big_resp, 19284.7, "abc123", "call_a")
    cc._maybe_dump_call(26, "pos prompt ETHUSDT", "POS SYS", '{"position_actions": {}}', 1200.0, "def456", "call_b")

    files = sorted(dump_dir.glob("*.json"))
    ok("two records written", len(files) == 2)
    a = next((f for f in files if "_call_a_" in f.name), None)
    b = next((f for f in files if "_call_b_" in f.name), None)
    ok("call_a file present with type in filename", a is not None)
    ok("call_b file present with type in filename", b is not None)

    if a:
        rec = json.loads(a.read_text())
        required = {"call_id", "call_type", "did", "ts_utc", "elapsed_ms",
                    "prompt_hash", "prompt_chars", "system_prompt_chars",
                    "response_chars", "system_prompt", "prompt", "response"}
        ok("call_a record has all required keys", required <= set(rec))
        ok("call_a call_type == 'call_a'", rec.get("call_type") == "call_a")
        ok("call_a full prompt stored untruncated (not a hash)",
           rec.get("prompt") == big_prompt and rec.get("prompt_chars") == len(big_prompt))
        ok("call_a full system prompt stored untruncated",
           rec.get("system_prompt") == big_system)
        ok("call_a full response stored untruncated", rec.get("response") == big_resp)
        ok("call_a coin data readable back (XRPUSDT entry=1.234)",
           "XRPUSDT entry=1.234" in rec.get("prompt", ""))
        ok("call_a has a timestamp", bool(rec.get("ts_utc")))
        ok("call_a has call_id", rec.get("call_id") == 25)
    if b:
        recb = json.loads(b.read_text())
        ok("call_b call_type == 'call_b'", recb.get("call_type") == "call_b")

    # --- Fire-and-forget: a forced write failure must not raise ---
    print("\nFire-and-forget")
    orig = Path.write_text
    raised = {"v": False}
    try:
        def boom(self, *a, **k):
            raise OSError("disk full (forced)")
        Path.write_text = boom
        try:
            cc._maybe_dump_call(99, "p", "s", "r", 1.0, "h", "call_a")
        except Exception:
            raised["v"] = True
    finally:
        Path.write_text = orig
    ok("forced write failure is swallowed (brain cycle undisturbed)", raised["v"] is False)

    # --- Legacy sentinel still works as a live override ---
    print("\nSentinel override")
    cc._CAPTURE_ENABLED = False
    sentinel = dump_dir / ".enabled"
    sentinel.write_text("")
    before = len(list(dump_dir.glob("*.json")))
    cc._maybe_dump_call(30, "p", "s", "r", 1.0, "h", "other")
    ok("sentinel file re-enables capture when config is off",
       len(list(dump_dir.glob("*.json"))) == before + 1)

    # --- Retention sweep (cleanup worker) ---
    print("\nRetention sweep")
    from src.workers.cleanup_worker import CleanupWorker

    class _Obs:
        capture_dir = str(dump_dir)
        capture_retention_days = 7
        capture_max_files = 3

    class _Settings:
        observability = _Obs()
        class workers:
            max_consecutive_failures = 5
            restart_delay = 1.0

    cw = CleanupWorker.__new__(CleanupWorker)   # bypass BaseWorker __init__
    cw.settings = _Settings()

    # wipe and seed: 5 fresh json + 2 aged json + the .enabled sentinel
    for f in dump_dir.glob("*.json"):
        f.unlink()
    now = time.time()
    for i in range(5):
        p = dump_dir / f"fresh_{i}.json"
        p.write_text("{}")
    aged = []
    for i in range(2):
        p = dump_dir / f"aged_{i}.json"
        p.write_text("{}")
        old = now - 9 * 86400  # 9 days old > 7-day cutoff
        os.utime(p, (old, old))
        aged.append(p)
    sentinel.write_text("")  # ensure sentinel present

    cw._sweep_stage2_dumps()
    surviving = {f.name for f in dump_dir.glob("*.json")}
    ok("aged *.json removed by age", all(not p.exists() for p in aged))
    ok("count cap enforced (<=3 json remain)", len(surviving) <= 3)
    ok(".enabled sentinel survives the sweep", sentinel.exists())

    # leaf-name fence: a non-stage2_dumps dir is refused
    wrong = tmp / "trade_log"
    wrong.mkdir()
    (wrong / "important.json").write_text("{}")
    _Obs.capture_dir = str(wrong)
    cw._sweep_stage2_dumps()
    ok("leaf-name fence refuses a non-stage2_dumps directory",
       (wrong / "important.json").exists())

    # cleanup temp
    import shutil
    shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nRESULT: {len(PASS)} passed, {len(FAIL)} failed")
    if FAIL:
        print("FAILED:", FAIL)
        raise SystemExit(1)
    print("ALL SYSTEM 1 CHECKS PASSED")


if __name__ == "__main__":
    main()
