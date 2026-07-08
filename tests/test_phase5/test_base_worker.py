"""Tests for BaseWorker: run loop, error recovery, heartbeat, lifecycle."""

import asyncio

import pytest

from src.core.exceptions import WorkerCrashError
from src.core.types import WorkerStatus
from src.workers.base_worker import BaseWorker


class SimpleWorker(BaseWorker):
    """Concrete worker for testing."""

    def __init__(self, settings, db, fail_count=0):
        super().__init__("test_worker", 0.01, settings, db)
        self.fail_count = fail_count
        self._calls = 0

    async def tick(self):
        self._calls += 1
        if self._calls <= self.fail_count:
            raise RuntimeError(f"Simulated failure #{self._calls}")


class TestBaseWorkerLifecycle:
    @pytest.mark.asyncio
    async def test_starts_and_stops(self, mock_settings, test_db):
        worker = SimpleWorker(mock_settings, test_db)

        async def run_and_stop():
            task = asyncio.create_task(worker.start())
            await asyncio.sleep(0.05)
            await worker.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await run_and_stop()
        assert worker.status == WorkerStatus.STOPPED
        assert worker.total_ticks > 0

    @pytest.mark.asyncio
    async def test_tick_increments_count(self, mock_settings, test_db):
        worker = SimpleWorker(mock_settings, test_db)
        task = asyncio.create_task(worker.start())
        await asyncio.sleep(0.05)
        await worker.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert worker.total_ticks > 0
        assert worker.error_count == 0


class TestBaseWorkerErrorRecovery:
    @pytest.mark.asyncio
    async def test_restart_on_error(self, mock_settings, test_db):
        """Worker should restart after a single failure."""
        mock_settings.workers.restart_delay = 0  # No delay in tests
        worker = SimpleWorker(mock_settings, test_db, fail_count=1)
        worker.restart_delay = 0.01  # Override for fast test
        task = asyncio.create_task(worker.start())
        await asyncio.sleep(0.1)
        await worker.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert worker.error_count == 1
        assert worker.total_ticks >= 1  # Recovered and ran at least one more tick

    @pytest.mark.asyncio
    async def test_restart_count_resets(self, mock_settings, test_db):
        """restart_count resets after a successful tick."""
        mock_settings.workers.restart_delay = 0
        worker = SimpleWorker(mock_settings, test_db, fail_count=1)
        worker.restart_delay = 0.01
        task = asyncio.create_task(worker.start())
        await asyncio.sleep(0.1)
        await worker.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert worker.restart_count == 0  # Reset after recovery

    @pytest.mark.asyncio
    async def test_max_restarts_stops_worker(self, mock_settings, test_db):
        """Worker stops after exceeding max restart attempts."""
        mock_settings.workers.max_consecutive_failures = 2
        worker = SimpleWorker(mock_settings, test_db, fail_count=999)
        with pytest.raises(WorkerCrashError):
            await worker.start()
        assert worker.status == WorkerStatus.STOPPED
        assert worker.error_count >= 2


class TestBaseWorkerStatus:
    @pytest.mark.asyncio
    async def test_get_status(self, mock_settings, test_db):
        worker = SimpleWorker(mock_settings, test_db)
        status = worker.get_status()
        assert status["name"] == "test_worker"
        assert status["status"] == "stopped"
        assert status["running"] is False
        assert status["total_ticks"] == 0

    @pytest.mark.asyncio
    async def test_status_running(self, mock_settings, test_db):
        worker = SimpleWorker(mock_settings, test_db)
        task = asyncio.create_task(worker.start())
        await asyncio.sleep(0.05)
        status = worker.get_status()
        assert status["status"] == "running"
        assert status["running"] is True
        await worker.stop()
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
