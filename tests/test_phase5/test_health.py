"""Tests for WorkerHealthMonitor."""

import pytest
from unittest.mock import MagicMock

from src.core.types import WorkerStatus
from src.workers.health import WorkerHealthMonitor


def _mock_worker(name, status=WorkerStatus.RUNNING, ticks=100, errors=0):
    w = MagicMock()
    w.name = name
    w.status = status
    w.get_status.return_value = {
        "name": name, "status": status.value, "running": status == WorkerStatus.RUNNING,
        "restart_count": 0, "total_ticks": ticks, "error_count": errors,
        "last_tick_time": None, "last_error": None, "uptime_seconds": 100,
    }
    return w


class TestHealthMonitor:
    def test_healthy_all_running(self):
        monitor = WorkerHealthMonitor()
        monitor.register(_mock_worker("w1"))
        monitor.register(_mock_worker("w2"))
        health = monitor.get_system_health()
        assert health["status"] == "healthy"
        assert health["running_workers"] == 2

    def test_degraded_one_error(self):
        monitor = WorkerHealthMonitor()
        monitor.register(_mock_worker("w1"))
        monitor.register(_mock_worker("w2", WorkerStatus.ERROR))
        health = monitor.get_system_health()
        assert health["status"] == "degraded"

    def test_critical_majority_stopped(self):
        monitor = WorkerHealthMonitor()
        monitor.register(_mock_worker("w1", WorkerStatus.STOPPED))
        monitor.register(_mock_worker("w2", WorkerStatus.STOPPED))
        monitor.register(_mock_worker("w3"))
        health = monitor.get_system_health()
        assert health["status"] == "critical"

    def test_is_healthy(self):
        monitor = WorkerHealthMonitor()
        monitor.register(_mock_worker("w1"))
        assert monitor.is_healthy() is True
        monitor.register(_mock_worker("w2", WorkerStatus.ERROR))
        assert monitor.is_healthy() is False

    def test_get_worker_status(self):
        monitor = WorkerHealthMonitor()
        monitor.register(_mock_worker("w1"))
        status = monitor.get_worker_status("w1")
        assert status["name"] == "w1"

    def test_unknown_worker(self):
        monitor = WorkerHealthMonitor()
        status = monitor.get_worker_status("nonexistent")
        assert "error" in status

    def test_error_rate(self):
        monitor = WorkerHealthMonitor()
        monitor.register(_mock_worker("w1", ticks=100, errors=5))
        health = monitor.get_system_health()
        assert health["error_rate_pct"] == 5.0
