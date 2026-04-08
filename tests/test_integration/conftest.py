"""Fixtures for the integration test suite."""

from __future__ import annotations

from pathlib import Path
from typing import Generator

import pytest

from core.registry.profile_registry import ProfileRegistry
from core.ipc.message_bus import MessageBus


@pytest.fixture
def registry_db(tmp_path: Path) -> str:
    """Temporary path for the registry database."""
    return str(tmp_path / "registry.db")


@pytest.fixture
def bus_db(tmp_path: Path) -> str:
    """Temporary path for the IPC bus database."""
    return str(tmp_path / "bus.db")


@pytest.fixture
def registry(registry_db: str) -> Generator[ProfileRegistry, None, None]:
    """ProfileRegistry with a full hierarchy: CEO -> CTO -> PM.

    Structure::

        hermes (CEO) [auto-created]
        └── cto (department_head, dept=engineering)
            └── pm (project_manager, dept=engineering)
    """
    reg = ProfileRegistry(db_path=registry_db)

    reg.create_profile(
        name="cto",
        display_name="CTO",
        role="department_head",
        parent="hermes",
        department="engineering",
        description="Chief Technology Officer",
        _skip_onboarding=True,
    )
    reg.create_profile(
        name="pm",
        display_name="PM",
        role="project_manager",
        parent="cto",
        department="engineering",
        description="Project Manager",
        _skip_onboarding=True,
    )

    yield reg
    reg.close()


@pytest.fixture
def bus(bus_db: str) -> Generator[MessageBus, None, None]:
    """MessageBus instance backed by a temporary database."""
    mb = MessageBus(db_path=bus_db)
    yield mb
    mb.close()
