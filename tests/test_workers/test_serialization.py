"""Tests for worker state serialization and deserialization."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.workers.exceptions import SerializationError
from core.workers.serialization import (
    ARTIFACTS_DIR,
    CONFIG_FILE,
    METADATA_FILE,
    SESSION_FILE,
    SUMMARY_FILE,
    WorkerConfig,
    WorkerMetadata,
    WorkerState,
    deserialize_state,
    get_state_path,
    load_session,
    load_summary,
    save_session,
    save_summary,
    serialize_state,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_session() -> list[dict]:
    return [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Implement feature X"},
        {"role": "assistant", "content": "I'll implement feature X now."},
    ]


@pytest.fixture
def sample_config() -> WorkerConfig:
    return WorkerConfig(
        model="claude-sonnet-4-20250514",
        provider="anthropic",
        toolsets=["terminal", "file"],
        system_prompt="You are a coding assistant.",
        extra={"max_tokens": 4096},
    )


@pytest.fixture
def sample_metadata() -> WorkerMetadata:
    return WorkerMetadata(
        subagent_id="sa-test-123",
        project_manager="pm-alpha",
        task_goal="Implement feature X",
        status="running",
        created_at="2026-04-03T12:00:00+00:00",
        updated_at="2026-04-03T12:30:00+00:00",
        parent_request_id="req-001",
        token_cost=1500,
        artifacts=["src/feature.py"],
    )


# ---------------------------------------------------------------------------
# Path resolution tests
# ---------------------------------------------------------------------------

class TestGetStatePath:
    def test_basic_path(self):
        path = get_state_path("/base", "pm-alpha", "sa-123")
        assert path == Path("/base/pm-alpha/sa-123")

    def test_path_object_input(self):
        path = get_state_path(Path("/base"), "pm-alpha", "sa-123")
        assert path == Path("/base/pm-alpha/sa-123")


# ---------------------------------------------------------------------------
# Serialization tests
# ---------------------------------------------------------------------------

class TestSerializeState:
    def test_creates_directory(self, state_dir: Path, sample_metadata: WorkerMetadata):
        result = serialize_state(
            state_dir, "pm-alpha", "sa-123",
            metadata=sample_metadata,
        )
        assert result.exists()
        assert result.is_dir()

    def test_creates_artifacts_dir(self, state_dir: Path, sample_metadata: WorkerMetadata):
        result = serialize_state(
            state_dir, "pm-alpha", "sa-123",
            metadata=sample_metadata,
        )
        assert (result / ARTIFACTS_DIR).exists()

    def test_saves_session(self, state_dir: Path, sample_session: list):
        result = serialize_state(
            state_dir, "pm-alpha", "sa-123",
            session=sample_session,
        )
        session_file = result / SESSION_FILE
        assert session_file.exists()
        loaded = json.loads(session_file.read_text())
        assert loaded == sample_session

    def test_saves_config(self, state_dir: Path, sample_config: WorkerConfig):
        result = serialize_state(
            state_dir, "pm-alpha", "sa-123",
            config=sample_config,
        )
        config_file = result / CONFIG_FILE
        assert config_file.exists()
        loaded = json.loads(config_file.read_text())
        assert loaded["model"] == "claude-sonnet-4-20250514"
        assert loaded["toolsets"] == ["terminal", "file"]

    def test_saves_metadata(self, state_dir: Path, sample_metadata: WorkerMetadata):
        result = serialize_state(
            state_dir, "pm-alpha", "sa-123",
            metadata=sample_metadata,
        )
        meta_file = result / METADATA_FILE
        assert meta_file.exists()
        loaded = json.loads(meta_file.read_text())
        assert loaded["subagent_id"] == "sa-test-123"
        assert loaded["task_goal"] == "Implement feature X"

    def test_saves_summary(self, state_dir: Path):
        result = serialize_state(
            state_dir, "pm-alpha", "sa-123",
            summary="# Work Summary\n\nImplemented feature X successfully.",
        )
        summary_file = result / SUMMARY_FILE
        assert summary_file.exists()
        assert "Implemented feature X" in summary_file.read_text()

    def test_skips_none_components(self, state_dir: Path):
        result = serialize_state(
            state_dir, "pm-alpha", "sa-123",
            session=None,
            config=None,
            metadata=None,
            summary=None,
        )
        # Directory and artifacts dir should exist
        assert result.exists()
        assert (result / ARTIFACTS_DIR).exists()
        # But no data files
        assert not (result / SESSION_FILE).exists()
        assert not (result / CONFIG_FILE).exists()
        assert not (result / METADATA_FILE).exists()
        assert not (result / SUMMARY_FILE).exists()

    def test_full_round_trip(
        self,
        state_dir: Path,
        sample_session: list,
        sample_config: WorkerConfig,
        sample_metadata: WorkerMetadata,
    ):
        serialize_state(
            state_dir, "pm-alpha", "sa-123",
            session=sample_session,
            config=sample_config,
            metadata=sample_metadata,
            summary="All done!",
        )
        state = deserialize_state(state_dir, "pm-alpha", "sa-123")
        assert state.session == sample_session
        assert state.config.model == sample_config.model
        assert state.config.toolsets == sample_config.toolsets
        assert state.metadata.subagent_id == sample_metadata.subagent_id
        assert state.summary == "All done!"
        assert state.state_path is not None


# ---------------------------------------------------------------------------
# Deserialization tests
# ---------------------------------------------------------------------------

class TestDeserializeState:
    def test_missing_directory_raises(self, state_dir: Path):
        with pytest.raises(SerializationError, match="state directory not found"):
            deserialize_state(state_dir, "pm-alpha", "sa-nonexistent")

    def test_missing_metadata_raises(self, state_dir: Path):
        # Create dir but no metadata file
        path = get_state_path(state_dir, "pm-alpha", "sa-123")
        path.mkdir(parents=True)
        with pytest.raises(SerializationError, match="metadata file not found"):
            deserialize_state(state_dir, "pm-alpha", "sa-123")

    def test_corrupt_json_raises(self, state_dir: Path):
        path = get_state_path(state_dir, "pm-alpha", "sa-123")
        path.mkdir(parents=True)
        (path / METADATA_FILE).write_text("not valid json{{{")
        with pytest.raises(SerializationError, match="corrupt JSON"):
            deserialize_state(state_dir, "pm-alpha", "sa-123")

    def test_missing_session_returns_empty(self, state_dir: Path, sample_metadata: WorkerMetadata):
        serialize_state(
            state_dir, "pm-alpha", "sa-123",
            metadata=sample_metadata,
        )
        state = deserialize_state(state_dir, "pm-alpha", "sa-123")
        assert state.session == []

    def test_missing_config_returns_default(self, state_dir: Path, sample_metadata: WorkerMetadata):
        serialize_state(
            state_dir, "pm-alpha", "sa-123",
            metadata=sample_metadata,
        )
        state = deserialize_state(state_dir, "pm-alpha", "sa-123")
        assert state.config.model == ""
        assert state.config.toolsets == []

    def test_missing_summary_returns_none(self, state_dir: Path, sample_metadata: WorkerMetadata):
        serialize_state(
            state_dir, "pm-alpha", "sa-123",
            metadata=sample_metadata,
        )
        state = deserialize_state(state_dir, "pm-alpha", "sa-123")
        assert state.summary is None

    def test_invalid_session_type_raises(self, state_dir: Path, sample_metadata: WorkerMetadata):
        serialize_state(
            state_dir, "pm-alpha", "sa-123",
            metadata=sample_metadata,
        )
        # Overwrite session.json with a non-array
        session_path = get_state_path(state_dir, "pm-alpha", "sa-123") / SESSION_FILE
        session_path.write_text('{"not": "a list"}')
        with pytest.raises(SerializationError, match="must contain a JSON array"):
            deserialize_state(state_dir, "pm-alpha", "sa-123")


# ---------------------------------------------------------------------------
# Individual save/load tests
# ---------------------------------------------------------------------------

class TestSaveLoadSession:
    def test_save_creates_file(self, state_dir: Path, sample_session: list):
        path = save_session(state_dir, "pm-alpha", "sa-123", sample_session)
        assert path.exists()
        assert path.name == SESSION_FILE

    def test_load_existing(self, state_dir: Path, sample_session: list):
        save_session(state_dir, "pm-alpha", "sa-123", sample_session)
        loaded = load_session(state_dir, "pm-alpha", "sa-123")
        assert loaded == sample_session

    def test_load_missing_returns_empty(self, state_dir: Path):
        loaded = load_session(state_dir, "pm-alpha", "sa-nonexistent")
        assert loaded == []

    def test_overwrite(self, state_dir: Path):
        save_session(state_dir, "pm-alpha", "sa-123", [{"role": "user", "content": "v1"}])
        save_session(state_dir, "pm-alpha", "sa-123", [{"role": "user", "content": "v2"}])
        loaded = load_session(state_dir, "pm-alpha", "sa-123")
        assert loaded[0]["content"] == "v2"


class TestSaveLoadSummary:
    def test_save_creates_file(self, state_dir: Path):
        path = save_summary(state_dir, "pm-alpha", "sa-123", "# Summary\n\nDone.")
        assert path.exists()
        assert path.name == SUMMARY_FILE

    def test_load_existing(self, state_dir: Path):
        save_summary(state_dir, "pm-alpha", "sa-123", "# Summary\n\nDone.")
        loaded = load_summary(state_dir, "pm-alpha", "sa-123")
        assert loaded == "# Summary\n\nDone."

    def test_load_missing_returns_none(self, state_dir: Path):
        loaded = load_summary(state_dir, "pm-alpha", "sa-nonexistent")
        assert loaded is None


# ---------------------------------------------------------------------------
# WorkerConfig tests
# ---------------------------------------------------------------------------

class TestWorkerConfig:
    def test_to_dict(self, sample_config: WorkerConfig):
        d = sample_config.to_dict()
        assert d["model"] == "claude-sonnet-4-20250514"
        assert d["extra"]["max_tokens"] == 4096

    def test_from_dict(self):
        data = {
            "model": "gpt-4",
            "provider": "openai",
            "toolsets": ["web"],
            "system_prompt": "Help me",
            "extra": {},
        }
        config = WorkerConfig.from_dict(data)
        assert config.model == "gpt-4"
        assert config.toolsets == ["web"]

    def test_from_dict_missing_fields(self):
        config = WorkerConfig.from_dict({})
        assert config.model == ""
        assert config.toolsets == []

    def test_round_trip(self, sample_config: WorkerConfig):
        restored = WorkerConfig.from_dict(sample_config.to_dict())
        assert restored.model == sample_config.model
        assert restored.toolsets == sample_config.toolsets
        assert restored.extra == sample_config.extra


# ---------------------------------------------------------------------------
# WorkerMetadata tests
# ---------------------------------------------------------------------------

class TestWorkerMetadata:
    def test_to_dict(self, sample_metadata: WorkerMetadata):
        d = sample_metadata.to_dict()
        assert d["subagent_id"] == "sa-test-123"
        assert d["artifacts"] == ["src/feature.py"]

    def test_from_dict(self):
        data = {
            "subagent_id": "sa-abc",
            "project_manager": "pm-x",
            "task_goal": "Do stuff",
            "status": "completed",
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T01:00:00",
        }
        meta = WorkerMetadata.from_dict(data)
        assert meta.subagent_id == "sa-abc"
        assert meta.status == "completed"

    def test_from_dict_missing_optional(self):
        meta = WorkerMetadata.from_dict({})
        assert meta.parent_request_id is None
        assert meta.token_cost == 0
        assert meta.artifacts == []

    def test_round_trip(self, sample_metadata: WorkerMetadata):
        restored = WorkerMetadata.from_dict(sample_metadata.to_dict())
        assert restored.subagent_id == sample_metadata.subagent_id
        assert restored.token_cost == sample_metadata.token_cost
