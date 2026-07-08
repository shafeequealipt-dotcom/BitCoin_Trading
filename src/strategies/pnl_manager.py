"""Daily PnL Manager: tracks daily performance and adjusts system aggression.

7 modes from TARGET_HIT (best day) to HALTED (worst day), each with
specific restrictions on score thresholds, leverage, coins, and positions.
"""

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.utils import now_utc
from src.strategies.models.signal_types import EnsembleResult

log = get_logger("strategies")


class DailyPnLManager:
    """Tracks daily PnL and adjusts system aggression accordingly.

    Args:
        settings: Application settings.
        account_service: For fetching current equity.
        position_service: For fetching unrealized PnL.
        db: Database manager for persisting daily PnL.
    """

    def __init__(self, settings: Settings, account_service=None, position_service=None, db=None) -> None:
        self.settings = settings
        self.account_service = account_service
        self.position_service = position_service
        self.db = db

        self.today_date: str = ""
        self.starting_equity: float = 0.0
        self.realized_pnl: float = 0.0
        self.unrealized_pnl: float = 0.0
        self.current_pnl_pct: float = 0.0
        self.target_hit: bool = False
        self.halted: bool = False

        # Manual pause — set by operator via Telegram /pause, cleared by /resume.
        # Checked in can_trade() so every automated entry point that already
        # consults can_trade() inherits the gate without further wiring.
        self._manual_pause: bool = False
        self._manual_pause_reason: str = ""

        # Trade tracking for daily persistence
        self._trades_today: int = 0
        self._wins_today: int = 0
        self._losses_today: int = 0
        self._max_drawdown_today: float = 0.0
        self._persist_counter: int = 0

        # Dynamic values consumed by dashboard and Claude
        self.current_pnl_usd: float = 0.0
        self._best_trade_pct: float = 0.0
        self._worst_trade_pct: float = 0.0
        self._streak_count: int = 0
        self._streak_type: str = ""  # "W" or "L"
        self._daily_loss_limit_pct: float = getattr(
            getattr(settings, "pnl_targets", None), "halt_threshold_pct", -5.0
        )
        self._avg_win_pct: float = 0.0
        self._avg_loss_pct: float = 0.0
        self._max_drawdown_pct: float = 0.0  # alias for dashboard compatibility
        self._per_coin_stats: dict = {}
        self._total_win_pnl: float = 0.0
        self._total_loss_pnl: float = 0.0

    async def initialize(self) -> None:
        """Load today's starting equity and calculate current state.

        Issue I5 (F-32, 2026-05-14) — restart-resilient PnL state.
        Pre-I5 the SEGV-recovery dashboard showed 0 PnL / 0 trades for
        ~6 minutes after restart because this method zeroed the
        counters without reading them back from ``daily_pnl``. Now we
        attempt to restore today's row first; the zeroing path runs
        only when no row exists for today (genuine new-day case) or
        when the read fails (defensive — never block initialize).
        """
        today = now_utc().strftime("%Y-%m-%d")
        if today != self.today_date:
            self.today_date = today
            self.realized_pnl = 0.0
            self.target_hit = False
            self.halted = False
            self._trades_today = 0
            self._wins_today = 0
            self._losses_today = 0
            self._max_drawdown_today = 0.0
            self._best_trade_pct = 0.0
            self._worst_trade_pct = 0.0
            self._streak_count = 0
            self._streak_type = ""
            self._avg_win_pct = 0.0
            self._avg_loss_pct = 0.0
            self._per_coin_stats = {}
            self._total_win_pnl = 0.0
            self._total_loss_pnl = 0.0
            # Issue I5 — attempt restore AFTER the zero so a restore
            # only overwrites the zero on the restart case (today's
            # row exists). On a genuine new-day boot, the row for
            # today doesn't exist yet and the zeros stand.
            await self._restore_today_from_db()

        if self.account_service:
            try:
                account = await self.account_service.get_wallet_balance()
                if self.starting_equity == 0:
                    self.starting_equity = account.total_equity
                self.unrealized_pnl = account.unrealized_pnl
            except Exception as e:
                log.warning("PnL manager init failed: {err}", err=str(e))

        self._recalculate()

    async def _restore_today_from_db(self) -> None:
        """Issue I5 (F-32, 2026-05-14) — load today's persisted counters
        on startup so the dashboard reflects accumulated state instead
        of zeros after a restart.

        Reads ``daily_pnl WHERE date = today``. When the row exists,
        populates realized_pnl / trades / wins / losses / max_drawdown /
        target_hit / halted. The starting_equity is also restored so
        the post-restart sizing math sees the same baseline as the
        pre-crash session.

        Best-effort: any failure logs and leaves the zeros in place.
        """
        if not self.db or not self.today_date:
            return
        try:
            row = await self.db.fetch_one(
                "SELECT starting_equity, realized_pnl, total_trades, "
                "       wins, losses, max_drawdown_pct, target_hit, halted "
                "FROM daily_pnl WHERE date = ?",
                (self.today_date,),
            )
        except Exception as e:
            log.warning(
                f"DASHBOARD_STATE_RECOVER_FAIL | stage=pnl_query "
                f"err='{str(e)[:120]}'"
            )
            return
        if not row:
            return
        try:
            self.starting_equity = float(row.get("starting_equity") or 0.0)
            self.realized_pnl = float(row.get("realized_pnl") or 0.0)
            self._trades_today = int(row.get("total_trades") or 0)
            self._wins_today = int(row.get("wins") or 0)
            self._losses_today = int(row.get("losses") or 0)
            self._max_drawdown_today = -abs(
                float(row.get("max_drawdown_pct") or 0.0)
            )
            self.target_hit = bool(row.get("target_hit") or 0)
            self.halted = bool(row.get("halted") or 0)
            log.info(
                f"DASHBOARD_STATE_RECOVERED | scope=daily_pnl date={self.today_date} "
                f"starting_equity={self.starting_equity:.2f} "
                f"realized_pnl={self.realized_pnl:+.4f} "
                f"trades={self._trades_today} "
                f"wins={self._wins_today} losses={self._losses_today} "
                f"max_dd_pct={self._max_drawdown_today:.4f}"
            )
        except Exception as e:
            log.warning(
                f"DASHBOARD_STATE_RECOVER_FAIL | stage=pnl_build "
                f"err='{str(e)[:120]}'"
            )

    async def _persist_daily_pnl(self) -> None:
        """Save current day's PnL to database using INSERT OR REPLACE."""
        if not self.db or not self.today_date:
            return
        try:
            current_equity = self.starting_equity
            if self.account_service:
                try:
                    acc = await self.account_service.get_wallet_balance()
                    current_equity = acc.total_equity
                except Exception as e:
                    # Phase 14 (P1-13) — was silent. Persist call still
                    # uses ``starting_equity`` as a defensive fallback;
                    # log so the operator knows wallet was unreachable.
                    log.warning(f"Suppressed: {e} (wallet for daily_pnl persist)")

            mode = self.get_current_mode()
            await self.db.execute(
                """INSERT OR REPLACE INTO daily_pnl
                   (date, starting_equity, ending_equity, realized_pnl,
                    total_trades, wins, losses, max_drawdown_pct,
                    target_hit, halted)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    self.today_date,
                    round(self.starting_equity, 2),
                    round(current_equity, 2),
                    round(self.realized_pnl, 4),
                    self._trades_today,
                    self._wins_today,
                    self._losses_today,
                    round(abs(self._max_drawdown_today), 4),
                    1 if self.target_hit else 0,
                    1 if mode["mode"] == "HALTED" else 0,
                ),
            )
        except Exception as e:
            log.error("Failed to persist daily PnL: {err}", err=str(e))

    async def update(self) -> None:
        """Recalculate current PnL with latest data."""
        await self._check_new_day()
        _cur_equity = self.starting_equity
        if self.account_service:
            try:
                account = await self.account_service.get_wallet_balance()
                self.unrealized_pnl = account.unrealized_pnl
                _cur_equity = account.total_equity
                if self.starting_equity == 0:
                    self.starting_equity = account.total_equity
            except Exception as e:
                # Phase 14 (P1-13) — was silent. update() continues with
                # the previous unrealized_pnl when the fetch fails; log
                # so the staleness is visible.
                log.warning(f"Suppressed: {e} (wallet for pnl update)")
        self._recalculate()
        log.info(f"PNL_DAILY | realized={self.realized_pnl:+.2f} | unrealized={self.unrealized_pnl:+.2f} | pnl_pct={self.current_pnl_pct:+.2f} | target_pct={self.settings.pnl_targets.daily_target_pct:+.2f} | trades={self._trades_today} | wins={self._wins_today} | losses={self._losses_today} | {ctx()}")

        # PnL-truth reconciliation (2026-05-26). Tie the system's reported
        # realized PnL to the REAL wallet. The wallet's realized change
        # since the day's starting equity is (current_equity minus current
        # unrealized) minus starting_equity. The residual versus our
        # accumulated realized_pnl is the unmodelled cost — exchange fees,
        # funding, and slippage — and should be small now that per-trade
        # net booking (coordinator.close_with_authoritative_pnl) is live. A
        # large residual means the books are drifting from the money again.
        # Operator greps PNL_RECONCILE to confirm the dashboard matches the
        # wallet within an explained residual.
        if self.starting_equity > 0:
            _wallet_realized = (_cur_equity - self.unrealized_pnl) - self.starting_equity
            _residual = _wallet_realized - self.realized_pnl
            log.info(
                f"PNL_RECONCILE | reported_realized=${self.realized_pnl:+.2f} "
                f"wallet_realized=${_wallet_realized:+.2f} residual=${_residual:+.2f} "
                f"equity=${_cur_equity:.2f} start_equity=${self.starting_equity:.2f} "
                f"unrealized=${self.unrealized_pnl:+.2f} trades={self._trades_today} | {ctx()}"
            )

        # Track max drawdown
        if self.current_pnl_pct < self._max_drawdown_today:
            self._max_drawdown_today = self.current_pnl_pct

        # Persist running PnL every 10 cycles (~7.5 min at 45s interval)
        self._persist_counter += 1
        if self._persist_counter >= 10:
            self._persist_counter = 0
            await self._persist_daily_pnl()

    async def _check_new_day(self) -> None:
        today = now_utc().strftime("%Y-%m-%d")
        if today != self.today_date and self.today_date:
            log.info(f"PNL_RESET | old_date={self.today_date} | new_date={today} | {ctx()}")
            # Persist completed day before resetting
            await self._persist_daily_pnl()
            self.today_date = today
            self.realized_pnl = 0.0
            self.starting_equity = 0.0
            self.target_hit = False
            self.halted = False
            self._trades_today = 0
            self._wins_today = 0
            self._losses_today = 0
            self._max_drawdown_today = 0.0
            self._best_trade_pct = 0.0
            self._worst_trade_pct = 0.0
            self._streak_count = 0
            self._streak_type = ""
            self._avg_win_pct = 0.0
            self._avg_loss_pct = 0.0
            self._per_coin_stats = {}
            self._total_win_pnl = 0.0
            self._total_loss_pnl = 0.0
        elif not self.today_date:
            self.today_date = today

    def _recalculate(self) -> None:
        total_pnl = self.realized_pnl + self.unrealized_pnl
        self.current_pnl_usd = total_pnl
        if self.starting_equity > 0:
            self.current_pnl_pct = (total_pnl / self.starting_equity) * 100
        else:
            self.current_pnl_pct = 0.0
        self._max_drawdown_pct = self._max_drawdown_today

    def get_current_mode(self) -> dict:
        """Return current mode and its restrictions."""
        cfg = self.settings.pnl_targets
        pct = self.current_pnl_pct

        if pct >= cfg.daily_target_pct:
            return {
                "mode": "TARGET_HIT",
                "max_score_threshold": 90,
                "max_leverage": 2,
                "allowed_coins": None,  # quality-gate replaces symbol restriction
                "quality_gate": True,
                "max_positions": 1,
                "allowed_risk_levels": ["low"],
                "message": "Daily target hit! Protecting profits with quality-gate.",
            }
        elif pct >= cfg.protect_threshold_pct:
            return {
                "mode": "PROTECT",
                "max_score_threshold": 85,
                "max_leverage": 3,
                "allowed_coins": None,  # quality-gate replaces symbol restriction
                "quality_gate": True,
                "max_positions": 2,
                "allowed_risk_levels": ["low", "medium"],
                "message": "Good profit. Protecting with quality-gate.",
            }
        elif pct >= 1.0:
            return {
                "mode": "GOOD_DAY",
                "max_score_threshold": 55,
                "max_leverage": 5,
                "allowed_coins": None,  # all allowed
                "max_positions": 3,
                "allowed_risk_levels": ["low", "medium"],
                "message": "Positive day. Moderate aggression.",
            }
        elif pct >= cfg.caution_threshold_pct:
            return {
                "mode": "NORMAL",
                "max_score_threshold": 50,
                "max_leverage": 5,
                "allowed_coins": None,
                "max_positions": 10,
                "allowed_risk_levels": ["low", "medium", "high"],
                "message": "Normal mode. Full aggression.",
            }
        elif pct >= cfg.survival_threshold_pct:
            return {
                "mode": "CAUTION",
                "max_score_threshold": 80,
                "max_leverage": 3,
                "allowed_coins": None,
                "max_positions": 3,
                "allowed_risk_levels": ["low", "medium"],
                "message": "Down day. Reduced aggression.",
            }
        elif pct >= cfg.halt_threshold_pct:
            return {
                "mode": "SURVIVAL",
                "max_score_threshold": 80,
                "max_leverage": 3,
                "allowed_coins": None,  # quality-gate replaces symbol restriction
                "quality_gate": True,   # enforcer checks X-RAY quality instead
                "max_positions": 2,
                "allowed_risk_levels": ["low"],
                "message": "Risk management mode. Quality-gate: A+/A setups only.",
            }
        else:
            self.halted = True
            return {
                "mode": "HALTED",
                "max_score_threshold": 100,
                "max_leverage": 0,
                "allowed_coins": [],
                "max_positions": 0,
                "allowed_risk_levels": [],
                "message": "Daily loss limit exceeded. Trading halted.",
            }

    def can_trade(self) -> tuple[bool, str]:
        """Quick check if trading is allowed."""
        if self._manual_pause:
            return False, f"manual pause: {self._manual_pause_reason or 'operator halt'}"
        mode = self.get_current_mode()
        if mode["mode"] == "HALTED":
            return False, mode["message"]
        return True, ""

    def pause_manually(self, reason: str = "operator") -> None:
        """Manually halt all automated trading. Survives until resume_manually()."""
        self._manual_pause = True
        self._manual_pause_reason = reason
        log.warning(f"PNL_MANUAL_PAUSE | rsn='{reason}' | {ctx()}")

    def resume_manually(self) -> None:
        """Clear manual pause set by pause_manually()."""
        prev_reason = self._manual_pause_reason
        self._manual_pause = False
        self._manual_pause_reason = ""
        log.warning(f"PNL_MANUAL_RESUME | prev_rsn='{prev_reason}' | {ctx()}")

    @property
    def is_manually_paused(self) -> bool:
        return self._manual_pause

    def apply_restrictions(
        self, setups: list[EnsembleResult], mode: dict,
    ) -> list[EnsembleResult]:
        """Filter setups based on current mode restrictions."""
        if mode["mode"] == "HALTED":
            return []

        threshold = mode["max_score_threshold"]
        allowed_coins = mode.get("allowed_coins")
        allowed_risk = mode.get("allowed_risk_levels", [])

        filtered: list[EnsembleResult] = []
        for setup in setups:
            signal = setup.scored_setup.raw_signal
            if setup.scored_setup.total_score < threshold:
                continue
            if allowed_coins is not None and signal.symbol not in allowed_coins:
                continue
            if allowed_risk and signal.strategy_category not in ("scalping",):
                # Check risk level from strategy category as proxy
                pass
            filtered.append(setup)

        return filtered

    def reset(self) -> None:
        """Manual reset: clear halt, reset PnL, re-capture equity baseline.

        Also clears the manual pause flag, since /enforcer_reset is documented
        as "all restrictions lifted" — a stuck pause would contradict that.
        """
        prev_pnl = self.current_pnl_pct
        prev_mode = self.get_current_mode()["mode"]
        self.halted = False
        self.target_hit = False
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0
        self.starting_equity = 0.0  # forces re-capture on next update()
        self._trades_today = 0
        self._wins_today = 0
        self._losses_today = 0
        self._manual_pause = False
        self._manual_pause_reason = ""
        self._recalculate()
        log.warning(
            f"PNL_MANUAL_RESET | prev_pnl={prev_pnl:+.2f}% prev_mode={prev_mode} "
            f"| new_pnl=0.00% new_mode=NORMAL | {ctx()}"
        )

    async def on_trade_closed(
        self, pnl_usd: float, symbol: str = "", pnl_pct: float = 0.0
    ) -> None:
        """Update realized PnL after a trade closes.

        PnL-truth fix (2026-05-26): realized PnL accumulates the NET DOLLAR
        outcome — it drives the dashboard "$" figure and the daily aggression
        / halt mode (via current_pnl_pct = realized/starting_equity). The
        best/worst/average-trade stats accumulate the per-trade PERCENT,
        because the dashboard renders them through _format_pct as "%". Mixing
        the two (the pre-fix code reused one number for both) made one of the
        two displays wrong; passing both keeps each correct. Win/loss is
        decided by the dollar outcome — the post-fee truth. ``pnl_pct``
        defaults to 0.0 so legacy callers that only hold a dollar figure keep
        working (their percent-stats simply stay flat).
        """
        await self._check_new_day()
        self.realized_pnl += pnl_usd
        self._trades_today += 1
        _is_win = pnl_usd >= 0

        # Track best/worst trade — PERCENT (shown as % on the dashboard)
        if pnl_pct > self._best_trade_pct:
            self._best_trade_pct = pnl_pct
        if pnl_pct < self._worst_trade_pct:
            self._worst_trade_pct = pnl_pct

        # Track average win/loss — PERCENT
        if _is_win:
            self._wins_today += 1
            self._total_win_pnl += pnl_pct
            self._avg_win_pct = self._total_win_pnl / self._wins_today
            if self._streak_type == "W":
                self._streak_count += 1
            else:
                self._streak_type = "W"
                self._streak_count = 1
        else:
            self._losses_today += 1
            self._total_loss_pnl += abs(pnl_pct)
            self._avg_loss_pct = self._total_loss_pnl / self._losses_today
            if self._streak_type == "L":
                self._streak_count += 1
            else:
                self._streak_type = "L"
                self._streak_count = 1

        # Track per-coin stats — pnl in DOLLARS
        if symbol:
            if symbol not in self._per_coin_stats:
                self._per_coin_stats[symbol] = {"wins": 0, "losses": 0, "pnl": 0.0}
            self._per_coin_stats[symbol]["pnl"] += pnl_usd
            if _is_win:
                self._per_coin_stats[symbol]["wins"] += 1
            else:
                self._per_coin_stats[symbol]["losses"] += 1

        self._recalculate()
        log.debug(f"PNL_TRADE_ADD | sym={symbol} | pnl=${pnl_usd:+.4f} pct={pnl_pct:+.4f}% | total=${self.realized_pnl:+.4f} | {ctx()}")

        # Persist immediately on trade close
        await self._persist_daily_pnl()

        cfg = self.settings.pnl_targets
        if self.current_pnl_pct >= cfg.daily_target_pct and not self.target_hit:
            self.target_hit = True
            log.info(
                "Daily target HIT! PnL={pnl:+.2f}%",
                pnl=self.current_pnl_pct,
            )

        if self.current_pnl_pct <= cfg.halt_threshold_pct and not self.halted:
            self.halted = True
            log.warning(
                "Daily loss limit exceeded! PnL={pnl:+.2f}%. Trading HALTED.",
                pnl=self.current_pnl_pct,
            )
            log.warning(f"PNL_LIMIT | pnl_pct={self.current_pnl_pct:+.2f} | limit={cfg.halt_threshold_pct} | rsn=daily_loss_halt | {ctx()}")

    async def on_trade_corrected(self, record: dict) -> None:
        """F5 part 3 (2026-06-09 phantom-close follow-up): reverse a provisionally
        booked outcome and apply the authoritative one when a reconcile FLIPS the
        win/loss for a close this manager already counted on the close channel.

        These running totals (``realized_pnl``, win/loss tallies, per-coin) persist
        their own daily row and restore it verbatim — unlike the DB-authoritative
        consumers (the performance enforcer, which recomputes from trade_thesis each
        tick) they do NOT self-heal when the reconcile corrects the per-trade DB row.
        Fired only on a genuine flip via the coordinator correction channel, so it
        can never double-count a normal fee-only correction. ``_trades_today`` and
        the streak are left untouched (the trade was already counted once; the
        DB-authoritative enforcer owns the behaviour-driving streak).
        """
        try:
            prior_usd = float(record.get("prior_pnl_usd") or 0.0)
            new_usd = float(record.get("pnl_usd") or 0.0)
            prior_pct = float(record.get("prior_pnl_pct") or 0.0)
            new_pct = float(record.get("pnl_pct") or 0.0)
            prior_win = bool(record.get("prior_was_win"))
            new_win = new_usd >= 0
            if prior_win == new_win:
                return  # not a flip — nothing to correct
            symbol = record.get("symbol", "") or ""
            await self._check_new_day()
            # dollar running total: swap the prior figure for the authoritative one
            self.realized_pnl += (new_usd - prior_usd)
            # reverse the prior (now-wrong) win/loss counters + percent accumulators
            if prior_win:
                self._wins_today = max(0, self._wins_today - 1)
                self._total_win_pnl = max(0.0, self._total_win_pnl - prior_pct)
            else:
                self._losses_today = max(0, self._losses_today - 1)
                self._total_loss_pnl = max(0.0, self._total_loss_pnl - abs(prior_pct))
            # apply the corrected (authoritative) outcome
            if new_win:
                self._wins_today += 1
                self._total_win_pnl += new_pct
            else:
                self._losses_today += 1
                self._total_loss_pnl += abs(new_pct)
            self._avg_win_pct = (
                self._total_win_pnl / self._wins_today if self._wins_today else 0.0
            )
            self._avg_loss_pct = (
                self._total_loss_pnl / self._losses_today if self._losses_today else 0.0
            )
            # per-coin: swap the dollar and move the win/loss tally
            if symbol and symbol in self._per_coin_stats:
                pc = self._per_coin_stats[symbol]
                pc["pnl"] += (new_usd - prior_usd)
                if prior_win:
                    pc["wins"] = max(0, pc["wins"] - 1)
                else:
                    pc["losses"] = max(0, pc["losses"] - 1)
                if new_win:
                    pc["wins"] += 1
                else:
                    pc["losses"] += 1
            self._recalculate()
            log.warning(
                f"PNL_MANAGER_CORRECTED | sym={symbol} prior_usd={prior_usd:+.4f} "
                f"corrected_usd={new_usd:+.4f} prior_win={prior_win} "
                f"corrected_win={new_win} realized_now=${self.realized_pnl:+.4f} "
                f"wins={self._wins_today} losses={self._losses_today} "
                f"| reversed a flipped phantom booking | {ctx()}"
            )
            await self._persist_daily_pnl()
            # re-evaluate the daily halt on the corrected total — a phantom win could
            # have masked a real breach of the loss limit.
            cfg = self.settings.pnl_targets
            if self.current_pnl_pct <= cfg.halt_threshold_pct and not self.halted:
                self.halted = True
                log.warning(
                    f"PNL_LIMIT | pnl_pct={self.current_pnl_pct:+.2f} "
                    f"| limit={cfg.halt_threshold_pct} | rsn=daily_loss_halt_after_correction "
                    f"| {ctx()}"
                )
        except Exception as e:
            log.warning(f"PNL_MANAGER_CORRECT_FAIL | err='{str(e)[:150]}' | {ctx()}")

    def on_exchange_switch(self) -> None:
        """Reset equity baseline after exchange mode switch.

        Forces re-capture of starting_equity from the new exchange
        on the next update() call, so daily PnL % is calculated
        against the correct baseline.
        """
        self.starting_equity = 0.0
        self.unrealized_pnl = 0.0
        self._recalculate()
        log.info("PnL manager: equity baseline reset for exchange switch")

    def get_summary(self) -> dict:
        """Return current PnL state."""
        mode = self.get_current_mode()
        return {
            "date": self.today_date,
            "starting_equity": round(self.starting_equity, 2),
            "realized_pnl": round(self.realized_pnl, 2),
            "unrealized_pnl": round(self.unrealized_pnl, 2),
            "total_pnl_pct": round(self.current_pnl_pct, 2),
            "mode": mode["mode"],
            "target_hit": self.target_hit,
            "halted": self.halted,
            "manual_pause": self._manual_pause,
            "manual_pause_reason": self._manual_pause_reason,
        }
