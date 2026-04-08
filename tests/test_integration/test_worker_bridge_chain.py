"""Tests for WorkerBridge.on_worker_complete() chain-propagation behaviour.

Covers:
- ``on_worker_complete`` without a chain (backward-compatible path).
- ``on_worker_complete`` with a chain but *no* orchestrator (safe no-op).
- ``on_worker_complete`` with both chain and orchestrator calls
  ``propagate_result``.
- ``complete`` (legacy method) is unaffected by the new parameter.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call

import pytest

from core.workers.subagent_registry import SubagentRegistry
from integrations.hermes.worker_bridge import WorkerBridge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_bridge(
    tmp_path: Path,
    chain_orchestrator: Any = None,
) -> tuple[WorkerBridge, SubagentRegistry]:
    """Return a (WorkerBridge, SubagentRegistry) pair backed by in-memory storage.

    Parameters
    ----------
    tmp_path:
        Pytest-provided temporary directory used as the workspace root.
    chain_orchestrator:
        Optional orchestrator instance forwarded to WorkerBridge.
    """
    reg = SubagentRegistry(base_path=":memory:")
    wb = WorkerBridge(
        worker_registry_factory=lambda: reg,
        workspace_dir=tmp_path / "ws",
        chain_orchestrator=chain_orchestrator,
    )
    return wb, reg


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestOnWorkerCompleteNoChain:
    """on_worker_complete without a chain: registry updated, no propagation."""

    def test_worker_marked_completed(self, tmp_path: Path) -> None:
        """Registry status is 'completed' after on_worker_complete(chain=None)."""
        wb, reg = _make_bridge(tmp_path)
        sid = wb.spawn(pm_profile="pm1", task="task A")

        wb.on_worker_complete(pm_profile="pm1", subagent_id=sid, result="done")

        assert wb.get_status(pm_profile="pm1", subagent_id=sid) == "completed"

    def test_no_orchestrator_call_without_chain(self, tmp_path: Path) -> None:
        """No propagation is attempted when chain=None, even with an orchestrator."""
        mock_orch = MagicMock()
        wb, _ = _make_bridge(tmp_path, chain_orchestrator=mock_orch)
        sid = wb.spawn(pm_profile="pm1", task="task B")

        wb.on_worker_complete(pm_profile="pm1", subagent_id=sid, result="done", chain=None)

        mock_orch.propagate_result.assert_not_called()

    def test_no_orchestrator_set_chain_provided_no_error(self, tmp_path: Path) -> None:
        """Providing a chain without an orchestrator raises no error."""
        wb, _ = _make_bridge(tmp_path, chain_orchestrator=None)
        sid = wb.spawn(pm_profile="pm1", task="task C")
        mock_chain = MagicMock()

        # Should not raise, just silently skip propagation.
        wb.on_worker_complete(
            pm_profile="pm1",
            subagent_id=sid,
            result="done",
            chain=mock_chain,
        )

        status = wb.get_status(pm_profile="pm1", subagent_id=sid)
        assert status == "completed"


class TestOnWorkerCompleteWithChain:
    """on_worker_complete with chain AND orchestrator: propagate_result called."""

    def test_propagate_result_called(self, tmp_path: Path) -> None:
        """orchestrator.propagate_result(chain, result) is called exactly once."""
        mock_orch = MagicMock()
        wb, _ = _make_bridge(tmp_path, chain_orchestrator=mock_orch)
        sid = wb.spawn(pm_profile="pm2", task="task D")
        mock_chain = MagicMock()

        wb.on_worker_complete(
            pm_profile="pm2",
            subagent_id=sid,
            result="success output",
            chain=mock_chain,
        )

        mock_orch.propagate_result.assert_called_once_with(mock_chain, "success output")

    def test_registry_updated_before_propagation(self, tmp_path: Path) -> None:
        """Registry is updated regardless of whether propagation succeeds."""
        mock_orch = MagicMock()
        wb, _ = _make_bridge(tmp_path, chain_orchestrator=mock_orch)
        sid = wb.spawn(pm_profile="pm2", task="task E")
        mock_chain = MagicMock()

        wb.on_worker_complete(
            pm_profile="pm2",
            subagent_id=sid,
            result="the result",
            chain=mock_chain,
        )

        assert wb.get_status(pm_profile="pm2", subagent_id=sid) == "completed"

    def test_propagation_error_does_not_hide_registry_update(
        self, tmp_path: Path
    ) -> None:
        """If propagate_result raises, the registry was already updated.

        Note: the current implementation does *not* swallow propagation
        errors — they bubble up to the caller.  This test documents that
        the registry write happens first (in :meth:`complete`) and an
        exception from the orchestrator propagates outward.
        """
        mock_orch = MagicMock()
        mock_orch.propagate_result.side_effect = RuntimeError("bus offline")
        wb, _ = _make_bridge(tmp_path, chain_orchestrator=mock_orch)
        sid = wb.spawn(pm_profile="pm2", task="task F")
        mock_chain = MagicMock()

        with pytest.raises(RuntimeError, match="bus offline"):
            wb.on_worker_complete(
                pm_profile="pm2",
                subagent_id=sid,
                result="partial result",
                chain=mock_chain,
            )

        # Registry write already happened before the exception.
        assert wb.get_status(pm_profile="pm2", subagent_id=sid) == "completed"


class TestLegacyCompleteUnchanged:
    """The legacy complete() method must not be affected by new parameters."""

    def test_complete_without_chain_orchestrator(self, tmp_path: Path) -> None:
        """WorkerBridge constructed without orchestrator: complete() still works."""
        wb, _ = _make_bridge(tmp_path)
        sid = wb.spawn(pm_profile="pm3", task="legacy task")
        wb.complete(pm_profile="pm3", subagent_id=sid, result="legacy done")
        assert wb.get_status(pm_profile="pm3", subagent_id=sid) == "completed"

    def test_complete_with_orchestrator_does_not_propagate(self, tmp_path: Path) -> None:
        """complete() never calls propagate_result, even when orchestrator is set."""
        mock_orch = MagicMock()
        wb, _ = _make_bridge(tmp_path, chain_orchestrator=mock_orch)
        sid = wb.spawn(pm_profile="pm3", task="another legacy task")
        wb.complete(pm_profile="pm3", subagent_id=sid, result="done")
        mock_orch.propagate_result.assert_not_called()

    def test_chain_orchestrator_default_is_none(self, tmp_path: Path) -> None:
        """WorkerBridge constructed without chain_orchestrator has None stored."""
        reg = SubagentRegistry(base_path=":memory:")
        wb = WorkerBridge(
            worker_registry_factory=lambda: reg,
            workspace_dir=tmp_path / "ws",
        )
        assert wb._chain_orchestrator is None
