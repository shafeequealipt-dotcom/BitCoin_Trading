"""Self-verification for E20 — extend the chain validator to altdata.

The altdata funding_rates offset is independent of the 6-worker data chain,
but it must fire strictly BEFORE the scanner consumes the window. E20 adds
that single edge to SweetSpotsSettings.__post_init__ (altdata < scanner only;
NOT altdata < strategy, which is the known-benign #10 staleness).

Confirms:
  A. STATIC: the validator references altdata.funding_rates + scanner and
     raises with the "must fire strictly BEFORE" message.
  B. CURRENT CONFIG PASSES: default 1:45 < 4:00 — SweetSpotsSettings() and the
     full Settings._load_fresh() both construct without error.
  C. PERTURBATIONS TRIP: funding_rates == scanner (4:00) and after scanner
     (4:30) both raise ConfigError; a value before scanner (3:00) passes.
  D. BENIGN #10 PRESERVED: 1:45 (after strategy 1:30) still passes — the
     altdata<strategy edge is intentionally NOT enforced.
"""


def static_check():
    s = open("src/config/settings.py").read()
    return {
        "validator references altdata + scanner":
            "altdata.funding_rates" in s and "scanner_worker" in s,
        "raises 'must fire strictly BEFORE'":
            "must fire strictly BEFORE" in s,
        "altdata<strategy NOT enforced (comment present)":
            "do NOT require funding_rates < strategy_worker" in s
            or "known-benign #10" in s,
    }


def main():
    from src.config.settings import (
        AltDataSweetSpotsSettings, Settings, SweetSpotsSettings,
    )
    from src.core.exceptions import ConfigError

    s = static_check()

    # B. current config passes
    default_ok = SweetSpotsSettings().altdata.funding_rates == "1:45"
    load_ok = Settings._load_fresh().workers.sweet_spots.altdata.funding_rates == "1:45"

    # C. perturbations
    def _raises(fr: str) -> bool:
        try:
            SweetSpotsSettings(altdata=AltDataSweetSpotsSettings(funding_rates=fr))
            return False
        except ConfigError:
            return True

    equal_trips = _raises("4:00")   # == scanner
    after_trips = _raises("4:30")   # after scanner
    before_passes = not _raises("3:00")  # before scanner
    benign10_passes = not _raises("1:45")  # after strategy 1:30 but before scanner

    print("E20 VERIFICATION — extend chain validator to altdata funding_rates")
    print("  STATIC:")
    for k, v in s.items():
        print(f"    {k}: {v}")
    print(f"  CURRENT CONFIG passes (default 1:45): {default_ok}")
    print(f"  Settings._load_fresh() passes: {load_ok}")
    print(f"  PERTURB == scanner (4:00) trips: {equal_trips}")
    print(f"  PERTURB after scanner (4:30) trips: {after_trips}")
    print(f"  before scanner (3:00) passes: {before_passes}")
    print(f"  BENIGN #10 (1:45 after strategy 1:30) still passes: {benign10_passes}")

    ok = (all(s.values()) and default_ok and load_ok and equal_trips
          and after_trips and before_passes and benign10_passes)
    print(f"\nRESULT: {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
