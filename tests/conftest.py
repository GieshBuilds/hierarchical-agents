"""Shared pytest fixtures for the test suite."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Generator

import pytest

from core.registry.schema import init_db


@pytest.fixture
def in_memory_db() -> Generator[sqlite3.Connection, None, None]:
    """Provide an in-memory SQLite database with the schema initialized."""
    conn = init_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def tmp_db(tmp_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Provide a file-backed SQLite database in a temporary directory."""
    db_path = tmp_path / "test_registry.db"
    conn = init_db(str(db_path))
    yield conn
    conn.close()
