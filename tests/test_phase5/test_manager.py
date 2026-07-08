"""Tests for WorkerManager."""

import asyncio

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.core.types import WorkerStatus
from src.workers.base_worker import BaseWorker
from src.workers.manager import WorkerManager


class QuickWorker(BaseWorker):
    """Worker that runs one tick and stops."""
    def __init__(self, settings, db, name="quick", fail=False):
        super().__init__(name, 0.01, settings, db)
        self._fail = fail

    async def tick(self):
        if self._fail:
            raise RuntimeError("deliberate failure")
        self.running = False  # Stop after one tick


class TestWorkerManager:
    @pytest.mark.asyncio
    async def test_stop_all(self, mock_settings, test_db):
        """Workers can be stopped gracefully."""
        w1 = QuickWorker(mock_settings, test_db, "w1")
        w2 = QuickWorker(mock_settings, test_db, "w2")

        manager = WorkerManager(mock_settings, test_db)
        manager.workers = [w1, w2]
        for w in manager.workers:
            manager.health.register(w)

        # Start and immediately stop
        async def run():
            manager.tasks = [asyncio.create_task(w.start()) for w in manager.workers]
            await asyncio.sleep(0.05)
            await manager.stop_all()

        await run()
        assert w1.status == WorkerStatus.STOPPED
        assert w2.status == WorkerStatus.STOPPED

    @pytest.mark.asyncio
    async def test_error_isolation(self, mock_settings, test_db):
        """One worker crashing should not stop another."""
        mock_settings.workers.max_consecutive_failures = 1  # Fast crash
        w_good = QuickWorker(mock_settings, test_db, "good")
        w_bad = QuickWorker(mock_settings, test_db, "bad", fail=True)

        manager = WorkerManager(mock_settings, test_db)
        manager.workers = [w_good, w_bad]
        for w in manager.workers:
            manager.health.register(w)

        manager.tasks = [
            asyncio.create_task(manager._run_worker(w)) for w in manager.workers
        ]
        await asyncio.sleep(0.1)
        await manager.stop_all()

        # Good worker should have completed fine
        assert w_good.total_ticks >= 1

    @pytest.mark.asyncio
    async def test_health_report(self, mock_settings, test_db):
        w = QuickWorker(mock_settings, test_db, "test")
        manager = WorkerManager(mock_settings, test_db)
        manager.workers = [w]
        manager.health.register(w)

        health = manager.get_health()
        assert health["total_workers"] == 1
        assert "workers" in health
