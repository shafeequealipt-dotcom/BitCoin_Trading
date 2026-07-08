"""Self-verification for E27 — raise apex_min_trades_for_flip 5 -> 8.

Confirms:
  A. STATIC: dataclass default = 8; config.toml value = 8; both optimizer
     getattr fallbacks = 8.
  B. CONFIG LOAD: Settings._load_fresh().apex.apex_min_trades_for_flip = 8
     (the loader auto-maps the key).
  C. FUNCTIONAL GATE: with the default settings, a flip whose target
     direction has 7 trades is BLOCKED (insufficient), and one with 8 is
     ALLOWED through the gate. Confirms the binding threshold is 8.
  D. LIVE PROMPT UNTOUCHED: src/apex/prompts.py still says "fewer than 5"
     (the advisory hint was intentionally NOT changed — code gate is
     stricter and authoritative; no live-prompt change).

Read-only / in-memory; no writes.
"""

from types import SimpleNamespace


def _opt(cfg):
    from src.apex.optimizer import TradeOptimizer
    o = TradeOptimizer.__new__(TradeOptimizer)
    o._settings = cfg
    return o


def _pkg(direction: str, n: int):
    trades = [{"direction": direction} for _ in range(n)]
    return SimpleNamespace(symbol_history=SimpleNamespace(trades=trades))


def static_check():
    s = open("src/config/settings.py").read()
    c = open("config.toml").read()
    o = open("src/apex/optimizer.py").read()
    p = open("src/apex/prompts.py").read()
    return {
        "settings default = 8": "apex_min_trades_for_flip: int = 8" in s,
        "config.toml value = 8": "apex_min_trades_for_flip = 8" in c,
        "optimizer fallbacks = 8 (both, none left at 5)":
            '"apex_min_trades_for_flip", 8,' in o
            and '"apex_min_trades_for_flip", 5,' not in o,
        "live prompt advisory untouched (still 'fewer than 5')":
            "fewer than 5 trades exist" in p,
    }


def main():
    from src.config.settings import APEXSettings, Settings

    s = static_check()

    default_ok = APEXSettings().apex_min_trades_for_flip == 8
    live = Settings._load_fresh().apex
    config_ok = live.apex_min_trades_for_flip == 8

    opt = _opt(APEXSettings())  # default (8)
    insuff_7, c7 = opt._check_insufficient_data_for_flip(
        _pkg("Sell", 7), claude_direction="Buy", qwen_direction="Sell")
    insuff_8, c8 = opt._check_insufficient_data_for_flip(
        _pkg("Sell", 8), claude_direction="Buy", qwen_direction="Sell")
    gate_ok = (insuff_7 is True and c7 == 7) and (insuff_8 is False and c8 == 8)

    print("E27 VERIFICATION — raise apex_min_trades_for_flip 5 -> 8")
    print("  STATIC:")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print(f"  DEFAULT (APEXSettings) == 8: {default_ok}")
    print(f"  CONFIG LOAD (live .apex) == 8: {config_ok} "
          f"(value={live.apex_min_trades_for_flip})")
    print(f"  FUNCTIONAL GATE (7 blocked, 8 allowed): {gate_ok} "
          f"(n=7 -> insufficient={insuff_7}; n=8 -> insufficient={insuff_8})")

    ok = all(s.values()) and default_ok and config_ok and gate_ok
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
