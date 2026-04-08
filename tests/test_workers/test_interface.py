"""Tests for the WorkerManager and WorkerResult protocols."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from core.workers.interface import WorkerManager, WorkerResult
from core.workers.resume import ResumeContext
from core.workers.serialization import WorkerConfig


# ---------------------------------------------------------------------------
# Concrete implementations for testing protocol compliance
# ---------------------------------------------------------------------------


@dataclass
class MockWorkerResult:
    """A mock implementation satisfying the WorkerResult protocol."""

    _summary: str = "Test completed"
    _artifacts: list[str] = field(default_factory=list)
    _token_cost: int = 100
    _session_history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def summary(self) -> str:
        return self._summary

    @property
    def artifacts(self) -> list[str]:
        return self._artifacts

    @property
    def token_cost(self) -> int:
        return self._token_cost

    @property
    def session_history(self) -> list[dict[str, Any]]:
        return self._session_history


class MockWorkerManager:
    """A mock implementation satisfying the WorkerManager protocol."""

    def __init__(self):
        self.spawned: list[str] = []
        self.completed: list[str] = []
        self.errors: list[tuple[str, Exception]] = []
        self.resumed: list[str] = []

    def spawn_worker(
        self,
        goal: str,
        context: str | None = None,
        config: WorkerConfig | None = None,
    ) -> str:
        sa_id = f"sa-mock-{len(self.spawned)}"
        self.spawned.append(sa_id)
        return sa_id

    def on_worker_complete(
        self,
        subagent_id: str,
        result: WorkerResult,
    ) -> None:
        self.completed.append(subagent_id)

    def on_worker_error(
        self,
        subagent_id: str,
        error: Exception,
    ) -> None:
        self.errors.append((subagent_id, error))

    def resume_worker(
        self,
        subagent_id: str,
        new_message: str | None = None,
    ) -> ResumeContext:
        self.resumed.append(subagent_id)
        from core.workers.serialization import WorkerMetadata
        return ResumeContext(
            subagent_id=subagent_id,
            project_manager="pm-mock",
            task_goal="Mock task",
            session_history=[],
            config=WorkerConfig(),
            metadata=WorkerMetadata(
                subagent_id=subagent_id,
                project_manager="pm-mock",
                task_goal="Mock task",
                status="running",
                created_at="2026-01-01",
                updated_at="2026-01-01",
            ),
        )


# ---------------------------------------------------------------------------
# Non-compliant implementations for negative testing
# ---------------------------------------------------------------------------


class IncompleteManager:
    """Does NOT satisfy WorkerManager — missing methods."""

    def spawn_worker(self, goal, context=None, config=None):
        return "sa-incomplete"

    # Missing: on_worker_complete, on_worker_error, resume_worker


class IncompleteResult:
    """Does NOT satisfy WorkerResult — missing properties."""

    @property
    def summary(self):
        return "partial"

    # Missing: artifacts, token_cost, session_history


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWorkerManagerProtocol:
    """Tests for the WorkerManager runtime-checkable protocol."""

    def test_mock_satisfies_protocol(self):
        manager = MockWorkerManager()
        assert isinstance(manager, WorkerManager)

    def test_spawn_worker(self):
        manager = MockWorkerManager()
        sa_id = manager.spawn_worker("Build feature X")
        assert sa_id.startswith("sa-mock-")
        assert len(manager.spawned) == 1

    def test_on_worker_complete(self):
        manager = MockWorkerManager()
        result = MockWorkerResult()
        manager.on_worker_complete("sa-1", result)
        assert "sa-1" in manager.completed

    def test_on_worker_error(self):
        manager = MockWorkerManager()
        error = RuntimeError("Something broke")
        manager.on_worker_error("sa-1", error)
        assert len(manager.errors) == 1
        assert manager.errors[0][1] is error

    def test_resume_worker(self):
        manager = MockWorkerManager()
        ctx = manager.resume_worker("sa-1")
        assert isinstance(ctx, ResumeContext)
        assert ctx.subagent_id == "sa-1"

    def test_spawn_with_config(self):
        manager = MockWorkerManager()
        config = WorkerConfig(model="gpt-4", toolsets=["terminal"])
        sa_id = manager.spawn_worker("Task", config=config)
        assert sa_id is not None

    def test_incomplete_does_not_satisfy(self):
        """A class missing required methods should not satisfy the protocol."""
        manager = IncompleteManager()
        assert not isinstance(manager, WorkerManager)


class TestWorkerResultProtocol:
    """Tests for the WorkerResult runtime-checkable protocol."""

    def test_mock_satisfies_protocol(self):
        result = MockWorkerResult()
        assert isinstance(result, WorkerResult)

    def test_result_properties(self):
        result = MockWorkerResult(
            _summary="Done",
            _artifacts=["file.py"],
            _token_cost=500,
            _session_history=[{"role": "user", "content": "hi"}],
        )
        assert result.summary == "Done"
        assert result.artifacts == ["file.py"]
        assert result.token_cost == 500
        assert len(result.session_history) == 1

    def test_incomplete_does_not_satisfy(self):
        """A class missing required properties should not satisfy the protocol."""
        result = IncompleteResult()
        assert not isinstance(result, WorkerResult)
