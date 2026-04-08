"""Tests for the resume function and ResumeContext."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.workers.exceptions import (
    InvalidSubagentStatus,
    SerializationError,
    SubagentNotFound,
)
from core.workers.models import SubagentStatus
from core.workers.resume import ResumeContext, resume
from core.workers.serialization import (
    WorkerConfig,
    WorkerMetadata,
    serialize_state,
)
from core.workers.subagent_registry import SubagentRegistry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sleeping_subagent_setup(
    tmp_path: Path,
) -> tuple[SubagentRegistry, str, Path]:
    """Create a registry with a sleeping subagent and serialized state.

    Returns (registry, subagent_id, state_base_path).
    """
    state_base = tmp_path / "state"

    registry = SubagentRegistry(base_path=":memory:")

    # Register and put to sleep
    sa = registry.register(
        project_manager="pm-alpha",
        task_goal="Implement feature X",
        parent_request_id="req-001",
    )
    registry.sleep(sa.subagent_id)

    # Serialize state to disk
    session = [
        {"role": "system", "content": "You are a coding assistant."},
        {"role": "user", "content": "Implement feature X"},
        {"role": "assistant", "content": "Working on it..."},
    ]
    config = WorkerConfig(
        model="claude-sonnet-4-20250514",
        provider="anthropic",
        toolsets=["terminal", "file"],
    )
    metadata = WorkerMetadata(
        subagent_id=sa.subagent_id,
        project_manager="pm-alpha",
        task_goal="Implement feature X",
        status="sleeping",
        created_at=sa.created_at.isoformat(),
        updated_at=sa.updated_at.isoformat(),
        parent_request_id="req-001",
    )

    serialize_state(
        state_base,
        "pm-alpha",
        sa.subagent_id,
        session=session,
        config=config,
        metadata=metadata,
        summary="Started implementing feature X, paused for review.",
    )

    return registry, sa.subagent_id, state_base


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestResumeContext:
    """Tests for the ResumeContext dataclass."""

    def test_fields(self):
        ctx = ResumeContext(
            subagent_id="sa-123",
            project_manager="pm-alpha",
            task_goal="Build stuff",
            session_history=[{"role": "user", "content": "hi"}],
            config=WorkerConfig(model="gpt-4"),
            metadata=WorkerMetadata(
                subagent_id="sa-123",
                project_manager="pm-alpha",
                task_goal="Build stuff",
                status="running",
                created_at="2026-01-01",
                updated_at="2026-01-01",
            ),
        )
        assert ctx.subagent_id == "sa-123"
        assert len(ctx.session_history) == 1
        assert ctx.config.model == "gpt-4"


class TestResumeWithRegistry:
    """Tests for resume() with registry integration."""

    def test_resume_sleeping_subagent(self, sleeping_subagent_setup):
        registry, sa_id, state_base = sleeping_subagent_setup

        ctx = resume(
            sa_id,
            base_path=state_base,
            project_manager="pm-alpha",
            registry=registry,
        )

        # Check context is populated
        assert ctx.subagent_id == sa_id
        assert ctx.project_manager == "pm-alpha"
        assert ctx.task_goal == "Implement feature X"
        assert len(ctx.session_history) == 3
        assert ctx.config.model == "claude-sonnet-4-20250514"
        assert ctx.config.toolsets == ["terminal", "file"]
        assert ctx.summary == "Started implementing feature X, paused for review."

        # Check status was updated in registry
        sa = registry.get(sa_id)
        assert sa.status == "running"

    def test_resume_running_raises(self, tmp_path: Path):
        """Cannot resume a running subagent."""
        registry = SubagentRegistry(base_path=":memory:")
        try:
            sa = registry.register(
                project_manager="pm-alpha",
                task_goal="Task",
            )

            # Serialize minimal state
            metadata = WorkerMetadata(
                subagent_id=sa.subagent_id,
                project_manager="pm-alpha",
                task_goal="Task",
                status="running",
                created_at="2026-01-01",
                updated_at="2026-01-01",
            )
            state_base = tmp_path / "state"
            serialize_state(
                state_base, "pm-alpha", sa.subagent_id,
                metadata=metadata,
            )

            with pytest.raises(InvalidSubagentStatus):
                resume(
                    sa.subagent_id,
                    base_path=state_base,
                    project_manager="pm-alpha",
                    registry=registry,
                )
        finally:
            registry.close()

    def test_resume_completed_raises(self, tmp_path: Path):
        """Cannot resume a completed subagent."""
        registry = SubagentRegistry(base_path=":memory:")
        try:
            sa = registry.register(project_manager="pm-alpha", task_goal="Task")
            registry.complete(sa.subagent_id, "Done")

            metadata = WorkerMetadata(
                subagent_id=sa.subagent_id,
                project_manager="pm-alpha",
                task_goal="Task",
                status="completed",
                created_at="2026-01-01",
                updated_at="2026-01-01",
            )
            state_base = tmp_path / "state"
            serialize_state(
                state_base, "pm-alpha", sa.subagent_id,
                metadata=metadata,
            )

            with pytest.raises(InvalidSubagentStatus):
                resume(
                    sa.subagent_id,
                    base_path=state_base,
                    project_manager="pm-alpha",
                    registry=registry,
                )
        finally:
            registry.close()

    def test_resume_archived_raises(self, tmp_path: Path):
        """Cannot resume an archived subagent."""
        registry = SubagentRegistry(base_path=":memory:")
        try:
            sa = registry.register(project_manager="pm-alpha", task_goal="Task")
            registry.complete(sa.subagent_id, "Done")
            registry.archive(sa.subagent_id)

            metadata = WorkerMetadata(
                subagent_id=sa.subagent_id,
                project_manager="pm-alpha",
                task_goal="Task",
                status="archived",
                created_at="2026-01-01",
                updated_at="2026-01-01",
            )
            state_base = tmp_path / "state"
            serialize_state(
                state_base, "pm-alpha", sa.subagent_id,
                metadata=metadata,
            )

            with pytest.raises(InvalidSubagentStatus):
                resume(
                    sa.subagent_id,
                    base_path=state_base,
                    project_manager="pm-alpha",
                    registry=registry,
                )
        finally:
            registry.close()

    def test_resume_nonexistent_raises(self, tmp_path: Path):
        """Resume of nonexistent subagent raises SubagentNotFound."""
        registry = SubagentRegistry(base_path=":memory:")
        try:
            with pytest.raises(SubagentNotFound):
                resume(
                    "sa-nonexistent",
                    base_path=tmp_path,
                    project_manager="pm-alpha",
                    registry=registry,
                )
        finally:
            registry.close()


class TestResumeWithoutRegistry:
    """Tests for resume() without registry (standalone deserialization)."""

    def test_resume_from_disk_only(self, tmp_path: Path):
        """Resume loads state from disk without registry validation."""
        state_base = tmp_path / "state"

        session = [
            {"role": "user", "content": "Continue working"},
        ]
        config = WorkerConfig(model="gpt-4", toolsets=["web"])
        metadata = WorkerMetadata(
            subagent_id="sa-standalone",
            project_manager="pm-alpha",
            task_goal="Research topic Y",
            status="sleeping",
            created_at="2026-04-03T12:00:00",
            updated_at="2026-04-03T13:00:00",
        )

        serialize_state(
            state_base, "pm-alpha", "sa-standalone",
            session=session,
            config=config,
            metadata=metadata,
            summary="Partial research done.",
        )

        ctx = resume(
            "sa-standalone",
            base_path=state_base,
            project_manager="pm-alpha",
            registry=None,
        )

        assert ctx.subagent_id == "sa-standalone"
        assert ctx.task_goal == "Research topic Y"
        assert len(ctx.session_history) == 1
        assert ctx.config.model == "gpt-4"
        assert ctx.summary == "Partial research done."

    def test_resume_missing_state_raises(self, tmp_path: Path):
        """Resume raises when state directory doesn't exist."""
        with pytest.raises(SerializationError):
            resume(
                "sa-missing",
                base_path=tmp_path / "nonexistent",
                project_manager="pm-alpha",
                registry=None,
            )

    def test_resume_has_artifacts_path(self, tmp_path: Path):
        """Resume context includes artifacts path when it exists."""
        state_base = tmp_path / "state"

        metadata = WorkerMetadata(
            subagent_id="sa-art",
            project_manager="pm-alpha",
            task_goal="Task",
            status="sleeping",
            created_at="2026-01-01",
            updated_at="2026-01-01",
        )

        serialize_state(
            state_base, "pm-alpha", "sa-art",
            metadata=metadata,
        )

        ctx = resume(
            "sa-art",
            base_path=state_base,
            project_manager="pm-alpha",
            registry=None,
        )

        assert ctx.artifacts_path is not None
        assert ctx.artifacts_path.name == "artifacts"
        assert ctx.artifacts_path.exists()
