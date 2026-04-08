"""Tests for the SubagentRegistry CRUD operations."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.workers.exceptions import (
    InvalidProjectManager,
    InvalidSubagentStatus,
    SubagentNotFound,
)
from core.workers.models import SubagentStatus
from core.workers.subagent_registry import SubagentRegistry


class TestRegister:
    """Tests for subagent registration."""

    def test_register_creates_subagent(self, registry: SubagentRegistry):
        sa = registry.register(
            project_manager="pm-alpha",
            task_goal="Build feature X",
        )
        assert sa.subagent_id.startswith("sa-")
        assert sa.project_manager == "pm-alpha"
        assert sa.task_goal == "Build feature X"
        assert sa.status == "running"
        assert sa.token_cost == 0
        assert sa.artifacts == []

    def test_register_with_parent_request(self, registry: SubagentRegistry):
        sa = registry.register(
            project_manager="pm-alpha",
            task_goal="Build feature X",
            parent_request_id="req-123",
        )
        assert sa.parent_request_id == "req-123"

    def test_register_with_conversation_path(self, registry: SubagentRegistry):
        sa = registry.register(
            project_manager="pm-alpha",
            task_goal="Build feature X",
            conversation_path="/tmp/sessions/sa-123",
        )
        assert sa.conversation_path == "/tmp/sessions/sa-123"

    def test_register_unique_ids(self, registry: SubagentRegistry):
        ids = set()
        for _ in range(20):
            sa = registry.register(
                project_manager="pm-alpha",
                task_goal="Task",
            )
            ids.add(sa.subagent_id)
        assert len(ids) == 20

    def test_register_sets_timestamps(self, registry: SubagentRegistry):
        sa = registry.register(
            project_manager="pm-alpha",
            task_goal="Task",
        )
        assert sa.created_at is not None
        assert sa.updated_at is not None


class TestGet:
    """Tests for fetching subagents."""

    def test_get_existing(self, registry: SubagentRegistry):
        created = registry.register(
            project_manager="pm-alpha",
            task_goal="Task",
        )
        fetched = registry.get(created.subagent_id)
        assert fetched.subagent_id == created.subagent_id
        assert fetched.task_goal == "Task"

    def test_get_nonexistent_raises(self, registry: SubagentRegistry):
        with pytest.raises(SubagentNotFound):
            registry.get("sa-nonexistent")

    def test_get_with_project_manager(self, registry: SubagentRegistry):
        created = registry.register(
            project_manager="pm-alpha",
            task_goal="Task",
        )
        fetched = registry.get(created.subagent_id, project_manager="pm-alpha")
        assert fetched.subagent_id == created.subagent_id


class TestUpdateStatus:
    """Tests for status transitions."""

    def test_running_to_sleeping(self, registry: SubagentRegistry):
        sa = registry.register(project_manager="pm-alpha", task_goal="Task")
        updated = registry.update_status(sa.subagent_id, "sleeping")
        assert updated.status == "sleeping"

    def test_running_to_completed(self, registry: SubagentRegistry):
        sa = registry.register(project_manager="pm-alpha", task_goal="Task")
        updated = registry.update_status(sa.subagent_id, SubagentStatus.COMPLETED)
        assert updated.status == "completed"

    def test_sleeping_to_running(self, registry: SubagentRegistry):
        sa = registry.register(project_manager="pm-alpha", task_goal="Task")
        registry.sleep(sa.subagent_id)
        updated = registry.update_status(sa.subagent_id, "running")
        assert updated.status == "running"

    def test_completed_to_archived(self, registry: SubagentRegistry):
        sa = registry.register(project_manager="pm-alpha", task_goal="Task")
        registry.update_status(sa.subagent_id, "completed")
        updated = registry.update_status(sa.subagent_id, "archived")
        assert updated.status == "archived"

    def test_invalid_transition_raises(self, registry: SubagentRegistry):
        sa = registry.register(project_manager="pm-alpha", task_goal="Task")
        with pytest.raises(InvalidSubagentStatus):
            registry.update_status(sa.subagent_id, "archived")  # running -> archived

    def test_archived_is_terminal(self, registry: SubagentRegistry):
        sa = registry.register(project_manager="pm-alpha", task_goal="Task")
        registry.update_status(sa.subagent_id, "completed")
        registry.update_status(sa.subagent_id, "archived")
        with pytest.raises(InvalidSubagentStatus):
            registry.update_status(sa.subagent_id, "running")

    def test_sleeping_cannot_complete(self, registry: SubagentRegistry):
        sa = registry.register(project_manager="pm-alpha", task_goal="Task")
        registry.sleep(sa.subagent_id)
        with pytest.raises(InvalidSubagentStatus):
            registry.update_status(sa.subagent_id, "completed")

    def test_updates_timestamp(self, registry: SubagentRegistry):
        sa = registry.register(project_manager="pm-alpha", task_goal="Task")
        original_updated = sa.updated_at
        updated = registry.sleep(sa.subagent_id)
        assert updated.updated_at >= original_updated


class TestComplete:
    """Tests for the complete() convenience method."""

    def test_complete_with_summary(self, registry: SubagentRegistry):
        sa = registry.register(project_manager="pm-alpha", task_goal="Task")
        completed = registry.complete(sa.subagent_id, "Done successfully")
        assert completed.status == "completed"
        assert completed.result_summary == "Done successfully"

    def test_complete_with_artifacts(self, registry: SubagentRegistry):
        sa = registry.register(project_manager="pm-alpha", task_goal="Task")
        completed = registry.complete(
            sa.subagent_id,
            "Built the feature",
            artifacts=["src/feature.py", "tests/test_feature.py"],
        )
        assert completed.artifacts == ["src/feature.py", "tests/test_feature.py"]

    def test_complete_with_token_cost(self, registry: SubagentRegistry):
        sa = registry.register(project_manager="pm-alpha", task_goal="Task")
        completed = registry.complete(
            sa.subagent_id,
            "Done",
            token_cost=2500,
        )
        assert completed.token_cost == 2500

    def test_complete_from_sleeping_raises(self, registry: SubagentRegistry):
        sa = registry.register(project_manager="pm-alpha", task_goal="Task")
        registry.sleep(sa.subagent_id)
        with pytest.raises(InvalidSubagentStatus):
            registry.complete(sa.subagent_id, "Done")

    def test_complete_already_completed_raises(self, registry: SubagentRegistry):
        sa = registry.register(project_manager="pm-alpha", task_goal="Task")
        registry.complete(sa.subagent_id, "First completion")
        with pytest.raises(InvalidSubagentStatus):
            registry.complete(sa.subagent_id, "Second completion")


class TestSleepAndArchive:
    """Tests for sleep() and archive() convenience methods."""

    def test_sleep(self, registry: SubagentRegistry):
        sa = registry.register(project_manager="pm-alpha", task_goal="Task")
        slept = registry.sleep(sa.subagent_id)
        assert slept.status == "sleeping"

    def test_archive(self, registry: SubagentRegistry):
        sa = registry.register(project_manager="pm-alpha", task_goal="Task")
        registry.complete(sa.subagent_id, "Done")
        archived = registry.archive(sa.subagent_id)
        assert archived.status == "archived"

    def test_archive_running_raises(self, registry: SubagentRegistry):
        sa = registry.register(project_manager="pm-alpha", task_goal="Task")
        with pytest.raises(InvalidSubagentStatus):
            registry.archive(sa.subagent_id)


class TestList:
    """Tests for listing subagents with filters."""

    def test_list_all(self, sample_subagents, registry: SubagentRegistry):
        results = registry.list()
        assert len(results) == 4

    def test_list_by_status(self, sample_subagents, registry: SubagentRegistry):
        running = registry.list(status="running")
        assert len(running) == 1
        assert running[0].status == "running"

        sleeping = registry.list(status=SubagentStatus.SLEEPING)
        assert len(sleeping) == 1

    def test_list_by_project_manager(self, sample_subagents, registry: SubagentRegistry):
        # In :memory: mode, PM filter works via SQL WHERE
        results = registry.list(project_manager="pm-alpha")
        # pm-alpha has running + sleeping
        assert all(r.project_manager == "pm-alpha" for r in results)

    def test_list_by_parent_request(self, sample_subagents, registry: SubagentRegistry):
        results = registry.list(parent_request_id="req-001")
        assert len(results) == 1
        assert results[0].parent_request_id == "req-001"

    def test_list_with_limit(self, sample_subagents, registry: SubagentRegistry):
        results = registry.list(limit=2)
        assert len(results) == 2

    def test_list_with_offset(self, sample_subagents, registry: SubagentRegistry):
        all_results = registry.list()
        offset_results = registry.list(offset=2)
        assert len(offset_results) == len(all_results) - 2

    def test_list_ordered_by_created_at_desc(self, registry: SubagentRegistry):
        # Create in order
        sa1 = registry.register(project_manager="pm", task_goal="First")
        sa2 = registry.register(project_manager="pm", task_goal="Second")
        sa3 = registry.register(project_manager="pm", task_goal="Third")

        results = registry.list()
        # Most recent first
        assert results[0].subagent_id == sa3.subagent_id
        assert results[2].subagent_id == sa1.subagent_id

    def test_list_empty(self, registry: SubagentRegistry):
        results = registry.list()
        assert results == []


class TestDelete:
    """Tests for hard-deleting subagents."""

    def test_delete_existing(self, registry: SubagentRegistry):
        sa = registry.register(project_manager="pm-alpha", task_goal="Task")
        registry.delete(sa.subagent_id)
        with pytest.raises(SubagentNotFound):
            registry.get(sa.subagent_id)

    def test_delete_nonexistent_raises(self, registry: SubagentRegistry):
        with pytest.raises(SubagentNotFound):
            registry.delete("sa-nonexistent")


class TestGetStats:
    """Tests for aggregate statistics."""

    def test_stats_empty(self, registry: SubagentRegistry):
        stats = registry.get_stats()
        assert stats["total"] == 0
        assert stats["by_status"] == {}
        assert stats["total_token_cost"] == 0

    def test_stats_with_data(self, sample_subagents, registry: SubagentRegistry):
        stats = registry.get_stats()
        assert stats["total"] == 4
        assert stats["by_status"]["running"] == 1
        assert stats["by_status"]["sleeping"] == 1
        assert stats["by_status"]["completed"] == 1
        assert stats["by_status"]["archived"] == 1
        assert stats["total_token_cost"] == 1500 + 800


class TestFileBackedRegistry:
    """Tests specific to file-backed registry mode."""

    def test_creates_pm_directory(self, file_registry: SubagentRegistry):
        file_registry.register(project_manager="pm-test", task_goal="Task")
        db_dir = Path(file_registry._base_path) / "pm-test"
        assert db_dir.exists()
        assert (db_dir / "subagents.db").exists()

    def test_multiple_pms_isolated(self, file_registry: SubagentRegistry):
        sa1 = file_registry.register(project_manager="pm-one", task_goal="Task 1")
        sa2 = file_registry.register(project_manager="pm-two", task_goal="Task 2")

        # Each PM has their own DB
        assert sa1.project_manager == "pm-one"
        assert sa2.project_manager == "pm-two"

        # Can fetch from specific PM
        fetched1 = file_registry.get(sa1.subagent_id, project_manager="pm-one")
        assert fetched1.task_goal == "Task 1"

    def test_cross_pm_search(self, file_registry: SubagentRegistry):
        file_registry.register(project_manager="pm-one", task_goal="Task 1")
        sa2 = file_registry.register(project_manager="pm-two", task_goal="Task 2")

        # Search without specifying PM should find across all
        found = file_registry.get(sa2.subagent_id)
        assert found.task_goal == "Task 2"

    def test_stats_aggregate_across_pms(self, file_registry: SubagentRegistry):
        file_registry.register(project_manager="pm-one", task_goal="Task 1")
        file_registry.register(project_manager="pm-two", task_goal="Task 2")

        stats = file_registry.get_stats()
        assert stats["total"] == 2


class TestPMValidation:
    """Tests for project manager validation with a profile registry."""

    def test_validates_pm_exists(self):
        """When profile_registry is provided, PM must exist."""
        from core.registry.profile_registry import ProfileRegistry

        profile_reg = ProfileRegistry(db_path=":memory:")
        try:
            # Create a PM in the profile registry
            profile_reg.create_profile(
                name="cto",
                role="department_head",
                parent="hermes",
                department="engineering",
                _skip_onboarding=True,
            )
            profile_reg.create_profile(
                name="pm-valid",
                role="project_manager",
                parent="cto",
                department="engineering",
                _skip_onboarding=True,
            )

            registry = SubagentRegistry(
                base_path=":memory:",
                profile_registry=profile_reg,
            )
            try:
                # Valid PM works
                sa = registry.register(
                    project_manager="pm-valid",
                    task_goal="Task",
                )
                assert sa.project_manager == "pm-valid"
            finally:
                registry.close()
        finally:
            profile_reg.close()

    def test_rejects_nonexistent_pm(self):
        """When profile_registry is provided, nonexistent PM is rejected."""
        from core.registry.profile_registry import ProfileRegistry

        profile_reg = ProfileRegistry(db_path=":memory:")
        try:
            registry = SubagentRegistry(
                base_path=":memory:",
                profile_registry=profile_reg,
            )
            try:
                with pytest.raises(InvalidProjectManager):
                    registry.register(
                        project_manager="pm-nonexistent",
                        task_goal="Task",
                    )
            finally:
                registry.close()
        finally:
            profile_reg.close()

    def test_rejects_non_pm_role(self):
        """When profile_registry is provided, only PMs can own subagents."""
        from core.registry.profile_registry import ProfileRegistry

        profile_reg = ProfileRegistry(db_path=":memory:")
        try:
            profile_reg.create_profile(
                name="cto",
                role="department_head",
                parent="hermes",
                department="engineering",
            )

            registry = SubagentRegistry(
                base_path=":memory:",
                profile_registry=profile_reg,
            )
            try:
                with pytest.raises(InvalidProjectManager):
                    registry.register(
                        project_manager="cto",
                        task_goal="Task",
                    )
            finally:
                registry.close()
        finally:
            profile_reg.close()

    def test_no_validation_without_registry(self, registry: SubagentRegistry):
        """Without profile_registry, any PM name is accepted."""
        sa = registry.register(
            project_manager="anything-goes",
            task_goal="Task",
        )
        assert sa.project_manager == "anything-goes"
