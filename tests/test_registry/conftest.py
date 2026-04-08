"""Fixtures for the Profile Registry test suite."""

from __future__ import annotations

import sqlite3
from typing import Generator

import pytest

from core.registry.schema import init_db
from core.registry.models import Profile, Role, Status
from core.registry.profile_registry import ProfileRegistry


@pytest.fixture
def registry() -> Generator[ProfileRegistry, None, None]:
    """Provide a ProfileRegistry backed by an in-memory SQLite database.

    The registry is fully initialised (schema created, CEO auto-bootstrapped).
    """
    reg = ProfileRegistry(db_path=":memory:")
    yield reg
    reg.close()


@pytest.fixture
def sample_org(registry: ProfileRegistry) -> ProfileRegistry:
    """Provide a pre-populated org chart.

    Structure::

        hermes (CEO) [auto-created]
        ├── cto (department_head, dept=engineering)
        │   ├── pm-alpha (project_manager, dept=engineering)
        │   └── pm-beta  (project_manager, dept=engineering)
        └── cmo (department_head, dept=marketing)
            └── pm-gamma (project_manager, dept=marketing)

    Returns the registry with the above profiles already created.
    """
    # Department heads (CEO 'hermes' is auto-created by __init__)
    # _skip_onboarding=True so these fixture profiles go straight to active status.
    registry.create_profile(
        name="cto",
        role="department_head",
        parent="hermes",
        department="engineering",
        description="Chief Technology Officer",
        _skip_onboarding=True,
    )
    registry.create_profile(
        name="cmo",
        role="department_head",
        parent="hermes",
        department="marketing",
        description="Chief Marketing Officer",
        _skip_onboarding=True,
    )

    # Project managers
    registry.create_profile(
        name="pm-alpha",
        role="project_manager",
        parent="cto",
        department="engineering",
        description="PM for project Alpha",
        _skip_onboarding=True,
    )
    registry.create_profile(
        name="pm-beta",
        role="project_manager",
        parent="cto",
        department="engineering",
        description="PM for project Beta",
        _skip_onboarding=True,
    )
    registry.create_profile(
        name="pm-gamma",
        role="project_manager",
        parent="cmo",
        department="marketing",
        description="PM for project Gamma",
        _skip_onboarding=True,
    )

    return registry
