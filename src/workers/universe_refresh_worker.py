"""Scheduled universe-refresh worker (Phase 3).

Fires the universe-refresh orchestration at the configured UTC hours
(default 23:00 and 11:00). The 23:00 window tunes the universe for the Asian
session ahead; the 11:00 window tunes Europe/US and finishes its warm-up
before US prime time (~13:30-14:00 UTC), landing the pause in the quieter
late-European-morning lull rather than the US open. Both sit clear of the
00:00/08:00/16:00 funding settlements so the activity data is undistorted.

Mirrors OptimizationWorker's time-of-day pattern: a periodic tick that fires
once per scheduled hour-slot (a last-run guard), invoking the shared
UniverseRefreshOrchestrator. It also reports the divergence between
consecutive scheduled selections so the operator can later judge whether two
daily refreshes are needed or one suffices.

Dormant unless ``[universe.refresh].enabled`` is true, so registering it is
safe before the feature is turned on at the Phase 5 gate.
"""

from src.config.settings import Settings
from src.core.log_context import ctx
from src.core.logging import get_logger
from src.core.utils import now_utc
from src.database.connection import DatabaseManager
from src.workers.base_worker import BaseWorker

log = get_logger("worker")


class UniverseRefreshWorker(BaseWorker):
    """Triggers the universe refresh at the configured UTC hours."""

    def __init__(self, settings: Settings, db: DatabaseManager, services: dict) -> None:
        super().__init__(
            name="universe_refresh_worker",
            # Check every 5 minutes so a fire lands within ~5 min of the hour
            # boundary; the per-slot guard ensures exactly one fire per window.
            interval_seconds=300.0,
            settings=settings,
            db=db,
        )
        self.services = services
        self._last_fired_slot: str = ""
        self._last_selection: set[str] = set()
        self._last_selection_label: str = ""

    async def tick(self) -> None:
        p = self.settings.universe.refresh
        if not p.enabled:
            return
        now = now_utc()
        if now.hour not in p.schedule_hours_utc:
            return
        slot = now.strftime("%Y-%m-%d-%H")
        if slot == self._last_fired_slot:
            return  # already fired (definitively) this scheduled hour

        orch = self.services.get("universe_refresh")
        if orch is None:
            # Don't commit the slot — retry next tick once the orchestrator is wired.
            log.warning(f"UNIVERSE_SCHEDULED_SKIP | reason=no_orchestrator slot={slot} | {ctx()}")
            return

        label = f"scheduled_{now.hour:02d}"
        log.info(f"UNIVERSE_SCHEDULED_FIRE | slot={slot} trigger={label} | {ctx()}")
        try:
            result = await orch.run_refresh(label)
        except Exception as e:
            # Transient failure — leave the slot uncommitted so the next 5-min
            # tick retries within this hour rather than forfeiting the window.
            log.error(f"UNIVERSE_SCHEDULED_FAIL | slot={slot} err='{str(e)[:150]}' "
                      f"| will retry this hour | {ctx()}")
            return

        status = result.get("status")
        reason = result.get("reason", "")
        # Transient aborts (momentary exchange/API hiccup) should retry; all
        # other outcomes (ok, too_few_selected, already_running) are definitive.
        if status == "aborted" and reason in ("positions_unconfirmed", "no_market_service"):
            log.warning(f"UNIVERSE_SCHEDULED_TRANSIENT | slot={slot} reason={reason} "
                        f"| will retry this hour | {ctx()}")
            return

        self._last_fired_slot = slot  # definitive outcome — fire once per window
        if status != "ok":
            log.warning(f"UNIVERSE_SCHEDULED_NONOK | slot={slot} status={status} reason={reason} | {ctx()}")
            return

        # Divergence vs the previous scheduled selection. With only two
        # scheduled hours, consecutive runs alternate (23 -> 11 -> 23 ...),
        # so this overlap is exactly between the two daily windows.
        new_sel = set(result.get("selected", []))
        if self._last_selection:
            inter = new_sel & self._last_selection
            union = new_sel | self._last_selection
            overlap_pct = (len(inter) / len(union) * 100.0) if union else 0.0
            only_new = sorted(new_sel - self._last_selection)
            only_prev = sorted(self._last_selection - new_sel)
            log.info(
                "UNIVERSE_DIVERGENCE | prev={pl} new={nl} overlap={ov}/{un} ({op:.0f}%) "
                "only_in_new={onn} only_in_prev={onp} | {c}",
                pl=self._last_selection_label, nl=label,
                ov=len(inter), un=len(union), op=overlap_pct,
                onn=len(only_new), onp=len(only_prev), c=ctx(),
            )
        self._last_selection = new_sel
        self._last_selection_label = label
