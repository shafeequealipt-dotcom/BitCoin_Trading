"""Tests for logging setup: ensure logs go to files only, never stdout/stderr."""

import os
import sys
import time

import pytest
from loguru import logger

from src.core.logging import get_logger, setup_logging


@pytest.fixture(autouse=True)
def cleanup_loguru_handlers():
    """Remove all loguru handlers after each test to release file locks on Windows."""
    yield
    logger.remove()
    # Small delay for Windows to release file handles
    time.sleep(0.05)


class TestSetupLogging:
    def test_no_stderr_handler(self, tmp_dir):
        """After setup, there should be no stderr/stdout handlers."""
        setup_logging(log_level="DEBUG", log_dir=tmp_dir)
        # Check that no handler writes to stderr or stdout
        for handler_id, handler in logger._core.handlers.items():
            sink = handler._sink
            assert sink is not sys.stderr
            assert sink is not sys.stdout

    def test_log_files_created(self, tmp_dir):
        """Log files should be created when messages are logged."""
        setup_logging(log_level="DEBUG", log_dir=tmp_dir)

        get_logger("worker").info("worker test message")
        get_logger("mcp").info("mcp test message")
        get_logger("brain").info("brain test message")
        get_logger("other").info("general test message")

        logger.complete()

        assert os.path.exists(os.path.join(tmp_dir, "workers.log"))
        assert os.path.exists(os.path.join(tmp_dir, "mcp.log"))
        assert os.path.exists(os.path.join(tmp_dir, "brain.log"))
        assert os.path.exists(os.path.join(tmp_dir, "general.log"))

    def test_component_routing(self, tmp_dir):
        """Worker messages should go to workers.log, not mcp.log."""
        setup_logging(log_level="DEBUG", log_dir=tmp_dir)

        get_logger("worker").info("only in workers")
        logger.complete()

        workers_content = open(os.path.join(tmp_dir, "workers.log")).read()
        assert "only in workers" in workers_content

        mcp_path = os.path.join(tmp_dir, "mcp.log")
        if os.path.exists(mcp_path):
            mcp_content = open(mcp_path).read()
            assert "only in workers" not in mcp_content

    def test_get_logger_returns_bound(self, tmp_dir):
        """get_logger should return a logger bound to the component."""
        setup_logging(log_level="DEBUG", log_dir=tmp_dir)
        log = get_logger("test_component")
        log.info("test message from bound logger")

    def test_log_directory_created(self, tmp_dir):
        """setup_logging should create the log directory if it doesn't exist."""
        log_dir = os.path.join(tmp_dir, "nested", "logs")
        setup_logging(log_level="INFO", log_dir=log_dir)
        assert os.path.isdir(log_dir)
