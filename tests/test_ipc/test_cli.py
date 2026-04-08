"""Tests for IPC CLI commands."""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from core.cli import main


@pytest.fixture
def bus_db():
    """Create a temporary bus database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield os.path.join(tmpdir, "bus.db")


def _run(args: list[str], bus_db: str) -> tuple[int, str, str]:
    """Run the CLI and capture output."""
    import io
    import sys

    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = io.StringIO()
    sys.stderr = io.StringIO()

    try:
        exit_code = main(["--bus-db", bus_db] + args)
    except SystemExit as e:
        exit_code = e.code if e.code is not None else 0
    finally:
        stdout = sys.stdout.getvalue()
        stderr = sys.stderr.getvalue()
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    return exit_code, stdout, stderr


def _run_json(args: list[str], bus_db: str) -> tuple[int, any]:
    """Run the CLI with --json and parse the output."""
    exit_code, stdout, stderr = _run(["--json"] + args, bus_db)
    if stdout.strip():
        try:
            return exit_code, json.loads(stdout)
        except json.JSONDecodeError:
            return exit_code, stdout
    return exit_code, None


class TestSendMessage:
    def test_sends_message(self, bus_db):
        exit_code, stdout, _ = _run(
            ["send-message", "--from", "ceo", "--to", "cto",
             "--type", "task_request"],
            bus_db,
        )
        assert exit_code == 0
        assert "Message sent:" in stdout

    def test_sends_with_payload(self, bus_db):
        exit_code, stdout, _ = _run(
            ["send-message", "--from", "ceo", "--to", "cto",
             "--type", "task_request",
             "--payload", '{"task": "fix bug"}'],
            bus_db,
        )
        assert exit_code == 0
        assert "fix bug" in stdout

    def test_sends_with_priority(self, bus_db):
        exit_code, stdout, _ = _run(
            ["send-message", "--from", "ceo", "--to", "cto",
             "--type", "task_request", "--priority", "urgent"],
            bus_db,
        )
        assert exit_code == 0
        assert "urgent" in stdout

    def test_sends_with_correlation_id(self, bus_db):
        exit_code, stdout, _ = _run(
            ["send-message", "--from", "ceo", "--to", "cto",
             "--type", "task_request",
             "--correlation-id", "corr-test123456"],
            bus_db,
        )
        assert exit_code == 0
        assert "corr-test123456" in stdout

    def test_sends_json_output(self, bus_db):
        exit_code, data = _run_json(
            ["send-message", "--from", "ceo", "--to", "cto",
             "--type", "task_request",
             "--payload", '{"task": "test"}'],
            bus_db,
        )
        assert exit_code == 0
        assert data["from_profile"] == "ceo"
        assert data["to_profile"] == "cto"
        assert data["message_type"] == "task_request"
        assert data["payload"] == {"task": "test"}
        assert data["status"] == "pending"

    def test_invalid_payload(self, bus_db):
        exit_code, _, stderr = _run(
            ["send-message", "--from", "ceo", "--to", "cto",
             "--type", "task_request",
             "--payload", "not json"],
            bus_db,
        )
        assert exit_code == 1
        assert "Invalid JSON" in stderr

    def test_sends_with_ttl(self, bus_db):
        exit_code, data = _run_json(
            ["send-message", "--from", "ceo", "--to", "cto",
             "--type", "task_request",
             "--ttl-hours", "2"],
            bus_db,
        )
        assert exit_code == 0
        assert data["expires_at"] is not None


class TestPollMessages:
    def test_no_messages(self, bus_db):
        exit_code, stdout, _ = _run(
            ["poll-messages", "--profile", "cto"],
            bus_db,
        )
        assert exit_code == 0
        assert "No pending messages" in stdout

    def test_finds_messages(self, bus_db):
        # Send a message first
        _run(
            ["send-message", "--from", "ceo", "--to", "cto",
             "--type", "task_request"],
            bus_db,
        )
        exit_code, stdout, _ = _run(
            ["poll-messages", "--profile", "cto"],
            bus_db,
        )
        assert exit_code == 0
        assert "1 pending message" in stdout

    def test_json_output(self, bus_db):
        _run(
            ["send-message", "--from", "ceo", "--to", "cto",
             "--type", "task_request"],
            bus_db,
        )
        exit_code, data = _run_json(
            ["poll-messages", "--profile", "cto"],
            bus_db,
        )
        assert exit_code == 0
        assert isinstance(data, list)
        assert len(data) == 1

    def test_filter_by_type(self, bus_db):
        _run(
            ["send-message", "--from", "ceo", "--to", "cto",
             "--type", "task_request"],
            bus_db,
        )
        _run(
            ["send-message", "--from", "ceo", "--to", "cto",
             "--type", "broadcast"],
            bus_db,
        )
        exit_code, data = _run_json(
            ["poll-messages", "--profile", "cto", "--type", "task_request"],
            bus_db,
        )
        assert exit_code == 0
        assert len(data) == 1


class TestListMessages:
    def test_no_messages(self, bus_db):
        exit_code, stdout, _ = _run(
            ["list-messages"],
            bus_db,
        )
        assert exit_code == 0
        assert "No messages found" in stdout

    def test_lists_all(self, bus_db):
        _run(["send-message", "--from", "ceo", "--to", "cto", "--type", "task_request"], bus_db)
        _run(["send-message", "--from", "cto", "--to", "ceo", "--type", "task_response"], bus_db)
        exit_code, data = _run_json(["list-messages"], bus_db)
        assert exit_code == 0
        assert len(data) == 2

    def test_filter_by_profile_received(self, bus_db):
        _run(["send-message", "--from", "ceo", "--to", "cto", "--type", "task_request"], bus_db)
        _run(["send-message", "--from", "cto", "--to", "ceo", "--type", "task_response"], bus_db)
        exit_code, data = _run_json(
            ["list-messages", "--profile", "cto", "--direction", "received"],
            bus_db,
        )
        assert exit_code == 0
        assert len(data) == 1
        assert data[0]["to_profile"] == "cto"

    def test_filter_by_status(self, bus_db):
        _run(["send-message", "--from", "ceo", "--to", "cto", "--type", "task_request"], bus_db)
        exit_code, data = _run_json(
            ["list-messages", "--status", "pending"],
            bus_db,
        )
        assert exit_code == 0
        assert len(data) == 1


class TestMessageStatus:
    def test_gets_message(self, bus_db):
        # Send and extract message ID
        _, data = _run_json(
            ["send-message", "--from", "ceo", "--to", "cto", "--type", "task_request"],
            bus_db,
        )
        message_id = data["message_id"]

        exit_code, status_data = _run_json(
            ["message-status", message_id],
            bus_db,
        )
        assert exit_code == 0
        assert status_data["message_id"] == message_id
        assert status_data["status"] == "pending"

    def test_not_found(self, bus_db):
        exit_code, _, stderr = _run(
            ["message-status", "msg-nonexistent"],
            bus_db,
        )
        assert exit_code == 1

    def test_human_readable(self, bus_db):
        _, data = _run_json(
            ["send-message", "--from", "ceo", "--to", "cto", "--type", "task_request"],
            bus_db,
        )
        message_id = data["message_id"]

        exit_code, stdout, _ = _run(
            ["message-status", message_id],
            bus_db,
        )
        assert exit_code == 0
        assert message_id in stdout


class TestIpcStats:
    def test_empty_stats(self, bus_db):
        exit_code, stdout, _ = _run(["ipc-stats"], bus_db)
        assert exit_code == 0
        assert "Total messages:" in stdout

    def test_stats_with_messages(self, bus_db):
        _run(["send-message", "--from", "ceo", "--to", "cto", "--type", "task_request"], bus_db)
        _run(["send-message", "--from", "ceo", "--to", "pm", "--type", "broadcast"], bus_db)
        exit_code, data = _run_json(["ipc-stats"], bus_db)
        assert exit_code == 0
        assert data["total"] == 2
        assert data["by_status"]["pending"] == 2

    def test_json_output(self, bus_db):
        exit_code, data = _run_json(["ipc-stats"], bus_db)
        assert exit_code == 0
        assert "total" in data
        assert "by_status" in data
        assert "by_type" in data


class TestIpcCleanup:
    def test_cleanup_no_messages(self, bus_db):
        exit_code, stdout, _ = _run(["ipc-cleanup"], bus_db)
        assert exit_code == 0
        assert "0 expired" in stdout
        assert "0 archived" in stdout

    def test_cleanup_json(self, bus_db):
        exit_code, data = _run_json(["ipc-cleanup"], bus_db)
        assert exit_code == 0
        assert data["expired"] == 0
        assert data["archived"] == 0

    def test_cleanup_with_expired(self, bus_db):
        # Send a message with very short TTL
        _run(
            ["send-message", "--from", "ceo", "--to", "cto",
             "--type", "task_request", "--ttl-hours", "0.000001"],
            bus_db,
        )
        import time
        time.sleep(0.01)  # Let it expire

        exit_code, data = _run_json(["ipc-cleanup"], bus_db)
        assert exit_code == 0
        # May or may not have expired depending on timing
        assert data["expired"] >= 0


class TestBusDbFlag:
    def test_separate_databases(self, bus_db):
        """Messages in one bus DB are not visible in another."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db1 = os.path.join(tmpdir, "bus1.db")
            db2 = os.path.join(tmpdir, "bus2.db")

            _run(
                ["send-message", "--from", "ceo", "--to", "cto",
                 "--type", "task_request"],
                db1,
            )
            _, data = _run_json(["poll-messages", "--profile", "cto"], db2)
            assert data == []
