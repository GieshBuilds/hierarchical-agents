"""Fixtures for the IPC test suite."""

from __future__ import annotations

import os
import tempfile

import pytest


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test databases."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def bus_db_path(temp_dir):
    """Path for a temporary bus database."""
    return os.path.join(temp_dir, "bus.db")
