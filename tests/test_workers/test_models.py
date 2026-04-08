"""Tests for the subagent data model and status enum."""

from __future__ import annotations

import json

import pytest

from core.workers.models import (
    SUBAGENT_ID_PREFIX,
    VALID_TRANSITIONS,
    Subagent,
    SubagentStatus,
    generate_subagent_id,
    is_valid_transition,
)


class TestSubagentStatus:
    """Tests for the SubagentStatus enum."""

    def test_all_values(self):
        assert SubagentStatus.RUNNING.value == "running"
        assert SubagentStatus.SLEEPING.value == "sleeping"
        assert SubagentStatus.COMPLETED.value == "completed"
        assert SubagentStatus.ARCHIVED.value == "archived"

    def test_from_string(self):
        assert SubagentStatus("running") == SubagentStatus.RUNNING
        assert SubagentStatus("sleeping") == SubagentStatus.SLEEPING
        assert SubagentStatus("completed") == SubagentStatus.COMPLETED
        assert SubagentStatus("archived") == SubagentStatus.ARCHIVED

    def test_invalid_status_raises(self):
        with pytest.raises(ValueError):
            SubagentStatus("invalid")

    def test_is_string_subclass(self):
        assert isinstance(SubagentStatus.RUNNING, str)
        assert SubagentStatus.RUNNING == "running"


class TestSubagentId:
    """Tests for subagent ID generation."""

    def test_has_prefix(self):
        sa_id = generate_subagent_id()
        assert sa_id.startswith(SUBAGENT_ID_PREFIX)

    def test_unique(self):
        ids = {generate_subagent_id() for _ in range(100)}
        assert len(ids) == 100

    def test_uuid_format(self):
        sa_id = generate_subagent_id()
        # Remove prefix, should be a valid UUID
        uuid_part = sa_id[len(SUBAGENT_ID_PREFIX):]
        assert len(uuid_part) == 36  # UUID4 string length
        assert uuid_part.count("-") == 4


class TestValidTransitions:
    """Tests for the status transition state machine."""

    def test_running_can_go_to_sleeping(self):
        assert is_valid_transition(SubagentStatus.RUNNING, SubagentStatus.SLEEPING)

    def test_running_can_go_to_completed(self):
        assert is_valid_transition(SubagentStatus.RUNNING, SubagentStatus.COMPLETED)

    def test_running_cannot_go_to_archived(self):
        assert not is_valid_transition(SubagentStatus.RUNNING, SubagentStatus.ARCHIVED)

    def test_sleeping_can_go_to_running(self):
        assert is_valid_transition(SubagentStatus.SLEEPING, SubagentStatus.RUNNING)

    def test_sleeping_cannot_go_to_completed(self):
        assert not is_valid_transition(SubagentStatus.SLEEPING, SubagentStatus.COMPLETED)

    def test_sleeping_cannot_go_to_archived(self):
        assert not is_valid_transition(SubagentStatus.SLEEPING, SubagentStatus.ARCHIVED)

    def test_completed_can_go_to_archived(self):
        assert is_valid_transition(SubagentStatus.COMPLETED, SubagentStatus.ARCHIVED)

    def test_completed_cannot_go_to_running(self):
        assert not is_valid_transition(SubagentStatus.COMPLETED, SubagentStatus.RUNNING)

    def test_completed_cannot_go_to_sleeping(self):
        assert not is_valid_transition(SubagentStatus.COMPLETED, SubagentStatus.SLEEPING)

    def test_archived_is_terminal(self):
        for target in SubagentStatus:
            assert not is_valid_transition(SubagentStatus.ARCHIVED, target)

    def test_no_self_transitions(self):
        for status in SubagentStatus:
            assert not is_valid_transition(status, status)


class TestSubagentDataclass:
    """Tests for the Subagent dataclass."""

    def test_defaults(self):
        sa = Subagent(
            subagent_id="sa-test",
            project_manager="pm-alpha",
            task_goal="Do stuff",
        )
        assert sa.status == "running"
        assert sa.artifacts == []
        assert sa.token_cost == 0
        assert sa.result_summary is None
        assert sa.conversation_path is None
        assert sa.parent_request_id is None

    def test_artifacts_as_json(self):
        sa = Subagent(
            subagent_id="sa-test",
            project_manager="pm-alpha",
            task_goal="Do stuff",
            artifacts=["file1.py", "file2.py"],
        )
        result = sa.artifacts_as_json()
        assert json.loads(result) == ["file1.py", "file2.py"]

    def test_artifacts_from_json_valid(self):
        result = Subagent.artifacts_from_json('["a.py", "b.py"]')
        assert result == ["a.py", "b.py"]

    def test_artifacts_from_json_empty_string(self):
        assert Subagent.artifacts_from_json("") == []

    def test_artifacts_from_json_none(self):
        assert Subagent.artifacts_from_json(None) == []

    def test_artifacts_from_json_corrupt(self):
        assert Subagent.artifacts_from_json("not json") == []

    def test_artifacts_from_json_not_list(self):
        assert Subagent.artifacts_from_json('{"key": "value"}') == []

    def test_created_at_is_utc(self):
        sa = Subagent(
            subagent_id="sa-test",
            project_manager="pm-alpha",
            task_goal="Do stuff",
        )
        assert sa.created_at.tzinfo is not None
