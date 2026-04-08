"""Fixtures for the Subagent/Worker Registry test suite."""

from __future__ import annotations

from pathlib import Path
from typing import Generator

import pytest

from core.workers.models import Subagent, SubagentStatus
from core.workers.schema import init_subagent_db
from core.workers.subagent_registry import SubagentRegistry


@pytest.fixture
def registry() -> Generator[SubagentRegistry, None, None]:
    """Provide a SubagentRegistry backed by an in-memory SQLite database."""
    reg = SubagentRegistry(base_path=":memory:")
    yield reg
    reg.close()


@pytest.fixture
def file_registry(tmp_path: Path) -> Generator[SubagentRegistry, None, None]:
    """Provide a SubagentRegistry backed by file-based SQLite databases."""
    reg = SubagentRegistry(base_path=str(tmp_path / "subagents"))
    yield reg
    reg.close()


@pytest.fixture
def sample_subagents(registry: SubagentRegistry) -> dict[str, Subagent]:
    """Create a set of sample subagents in various states.

    Returns a dict mapping descriptive keys to Subagent records:
    - 'running': A currently running worker
    - 'sleeping': A paused worker
    - 'completed': A finished worker
    - 'archived': An archived worker
    """
    running = registry.register(
        project_manager="pm-alpha",
        task_goal="Implement feature X",
        parent_request_id="req-001",
    )

    sleeping_sa = registry.register(
        project_manager="pm-alpha",
        task_goal="Write tests for feature X",
        parent_request_id="req-002",
    )
    sleeping_sa = registry.sleep(sleeping_sa.subagent_id)

    completed = registry.register(
        project_manager="pm-beta",
        task_goal="Fix bug in authentication",
        parent_request_id="req-003",
    )
    completed = registry.complete(
        completed.subagent_id,
        result_summary="Fixed the auth bug by updating token validation",
        artifacts=["src/auth/token.py", "tests/test_auth.py"],
        token_cost=1500,
    )

    archived = registry.register(
        project_manager="pm-beta",
        task_goal="Research caching strategies",
        parent_request_id="req-004",
    )
    archived = registry.complete(
        archived.subagent_id,
        result_summary="Evaluated Redis vs Memcached, recommended Redis",
        token_cost=800,
    )
    archived = registry.archive(archived.subagent_id)

    return {
        "running": running,
        "sleeping": sleeping_sa,
        "completed": completed,
        "archived": archived,
    }


@pytest.fixture
def state_dir(tmp_path: Path) -> Path:
    """Provide a temporary directory for serialization tests."""
    return tmp_path / "state"
