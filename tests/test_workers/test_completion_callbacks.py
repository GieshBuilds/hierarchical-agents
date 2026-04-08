"""Tests for SubagentRegistry completion callbacks (Stream C).

Verifies that registered callbacks are fired automatically when a worker
is marked as completed, that multiple callbacks all receive the call, and
that a failing callback does not prevent other callbacks from running.
"""
from __future__ import annotations

import threading
from pathlib import Path
from typing import List, Tuple
from unittest.mock import MagicMock

import pytest

from core.workers.subagent_registry import SubagentRegistry
from integrations.hermes.worker_bridge import WorkerBridge


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_registry() -> SubagentRegistry:
    """Return a fresh in-memory SubagentRegistry for each test."""
    return SubagentRegistry(base_path=":memory:")


def _register_and_complete(
    registry: SubagentRegistry,
    pm: str = "pm-alpha",
    task: str = "do work",
    result: str = "done",
) -> Tuple[str, str]:
    """Register a worker then complete it; return (subagent_id, result)."""
    subagent = registry.register(project_manager=pm, task_goal=task)
    registry.complete(subagent.subagent_id, result_summary=result)
    return subagent.subagent_id, result


# ---------------------------------------------------------------------------
# SubagentRegistry callback tests
# ---------------------------------------------------------------------------

class TestSingleCallback:
    """A single registered callback fires with the right arguments."""

    def test_callback_fires_on_complete(self) -> None:
        """Completing a worker invokes the registered callback."""
        reg = _make_registry()
        calls: List[Tuple[str, str]] = []
        reg.register_completion_callback(lambda sid, res: calls.append((sid, res)))

        sid, result = _register_and_complete(reg)

        assert len(calls) == 1
        assert calls[0] == (sid, result)

    def test_callback_receives_correct_subagent_id(self) -> None:
        """Callback receives the exact subagent_id of the completed worker."""
        reg = _make_registry()
        received_ids: List[str] = []
        reg.register_completion_callback(lambda sid, _res: received_ids.append(sid))

        subagent = reg.register(project_manager="pm-alpha", task_goal="task A")
        reg.complete(subagent.subagent_id, result_summary="result A")

        assert received_ids == [subagent.subagent_id]

    def test_callback_receives_correct_result_summary(self) -> None:
        """Callback receives the exact result_summary passed to complete()."""
        reg = _make_registry()
        received_results: List[str] = []
        reg.register_completion_callback(
            lambda _sid, res: received_results.append(res)
        )

        expected = "finished task with flying colours"
        subagent = reg.register(project_manager="pm-alpha", task_goal="task")
        reg.complete(subagent.subagent_id, result_summary=expected)

        assert received_results == [expected]

    def test_no_callback_fires_on_register(self) -> None:
        """Registering a worker (not completing it) does NOT fire callbacks."""
        reg = _make_registry()
        calls: List[Tuple[str, str]] = []
        reg.register_completion_callback(lambda sid, res: calls.append((sid, res)))

        reg.register(project_manager="pm-alpha", task_goal="task")

        assert calls == []

    def test_no_callback_fires_on_sleep(self) -> None:
        """Transitioning to sleeping does NOT fire callbacks."""
        reg = _make_registry()
        calls: List[Tuple[str, str]] = []
        reg.register_completion_callback(lambda sid, res: calls.append((sid, res)))

        subagent = reg.register(project_manager="pm-alpha", task_goal="task")
        reg.sleep(subagent.subagent_id)

        assert calls == []


class TestMultipleCallbacks:
    """All registered callbacks fire when a worker completes."""

    def test_all_callbacks_fire(self) -> None:
        """Every registered callback is invoked for a single completion."""
        reg = _make_registry()
        call_counts = [0, 0, 0]

        reg.register_completion_callback(lambda _s, _r: call_counts.__setitem__(0, call_counts[0] + 1))
        reg.register_completion_callback(lambda _s, _r: call_counts.__setitem__(1, call_counts[1] + 1))
        reg.register_completion_callback(lambda _s, _r: call_counts.__setitem__(2, call_counts[2] + 1))

        _register_and_complete(reg)

        assert call_counts == [1, 1, 1]

    def test_callbacks_fire_in_registration_order(self) -> None:
        """Callbacks are invoked in the order they were registered."""
        reg = _make_registry()
        order: List[int] = []

        reg.register_completion_callback(lambda _s, _r: order.append(1))
        reg.register_completion_callback(lambda _s, _r: order.append(2))
        reg.register_completion_callback(lambda _s, _r: order.append(3))

        _register_and_complete(reg)

        assert order == [1, 2, 3]

    def test_multiple_completions_fire_all_callbacks_each_time(self) -> None:
        """Each completion event fires all callbacks independently."""
        reg = _make_registry()
        calls_a: List[str] = []
        calls_b: List[str] = []

        reg.register_completion_callback(lambda sid, _r: calls_a.append(sid))
        reg.register_completion_callback(lambda sid, _r: calls_b.append(sid))

        sid1, _ = _register_and_complete(reg, task="task 1", result="result 1")
        sid2, _ = _register_and_complete(reg, task="task 2", result="result 2")

        assert calls_a == [sid1, sid2]
        assert calls_b == [sid1, sid2]


class TestCallbackErrorIsolation:
    """A failing callback must not block subsequent callbacks."""

    def test_failing_callback_does_not_prevent_later_callbacks(self) -> None:
        """If one callback raises, subsequent callbacks still run."""
        reg = _make_registry()
        later_calls: List[str] = []

        def exploding_callback(sid: str, _res: str) -> None:
            raise RuntimeError("boom!")

        reg.register_completion_callback(exploding_callback)
        reg.register_completion_callback(lambda sid, _r: later_calls.append(sid))

        sid, _ = _register_and_complete(reg)

        # The later callback still ran despite the first one exploding.
        assert later_calls == [sid]

    def test_complete_returns_normally_when_callback_fails(self) -> None:
        """registry.complete() must not propagate a callback exception."""
        reg = _make_registry()
        reg.register_completion_callback(lambda _s, _r: 1 / 0)  # ZeroDivisionError

        subagent = reg.register(project_manager="pm-alpha", task_goal="task")
        # Should NOT raise.
        result = reg.complete(subagent.subagent_id, result_summary="ok")

        assert result.status == "completed"


class TestCallbackThreadSafety:
    """Callbacks registered from multiple threads are all honoured."""

    def test_callbacks_registered_concurrently_all_fire(self) -> None:
        """Registering callbacks from different threads and completing a worker
        should invoke all of them."""
        reg = _make_registry()
        results: List[str] = []
        lock = threading.Lock()

        def make_cb(label: str):
            def cb(sid: str, _res: str) -> None:
                with lock:
                    results.append(label)
            return cb

        threads = [threading.Thread(target=reg.register_completion_callback, args=(make_cb(f"cb-{i}"),)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        _register_and_complete(reg)

        assert len(results) == 10


# ---------------------------------------------------------------------------
# WorkerBridge.setup_auto_propagation tests
# ---------------------------------------------------------------------------

class TestSetupAutoPropagation:
    """WorkerBridge.setup_auto_propagation wires registry callbacks correctly."""

    def _make_bridge(self, orchestrator=None) -> WorkerBridge:
        reg = _make_registry()
        bridge = WorkerBridge(
            worker_registry_factory=lambda: reg,
            workspace_dir=Path("/tmp"),
            chain_orchestrator=orchestrator,
        )
        return bridge

    def test_propagate_result_called_on_complete(self) -> None:
        """setup_auto_propagation causes propagate_result to fire on completion."""
        orchestrator = MagicMock()
        bridge = self._make_bridge(orchestrator)

        fake_chain = MagicMock()
        fake_chain.chain_id = "chain-001"

        bridge.setup_auto_propagation(fake_chain)

        # Spawn and complete a worker through the bridge.
        sid = bridge.spawn("pm-alpha", "some task")
        bridge.complete("pm-alpha", sid, "task done")

        orchestrator.propagate_result.assert_called_once_with(fake_chain, "task done")

    def test_no_propagation_without_orchestrator(self) -> None:
        """Without a chain_orchestrator, setup_auto_propagation is a no-op."""
        bridge = self._make_bridge(orchestrator=None)
        fake_chain = MagicMock()

        # Should not raise even without an orchestrator.
        bridge.setup_auto_propagation(fake_chain)

        sid = bridge.spawn("pm-alpha", "some task")
        bridge.complete("pm-alpha", sid, "done")
        # Nothing to assert — just verify no exception was raised.

    def test_multiple_completions_each_propagated(self) -> None:
        """Every completion triggers a separate propagate_result call."""
        orchestrator = MagicMock()
        bridge = self._make_bridge(orchestrator)
        fake_chain = MagicMock()
        fake_chain.chain_id = "chain-002"

        bridge.setup_auto_propagation(fake_chain)

        for i in range(3):
            sid = bridge.spawn("pm-alpha", f"task {i}")
            bridge.complete("pm-alpha", sid, f"result {i}")

        assert orchestrator.propagate_result.call_count == 3

    def test_propagated_result_matches_completion_result(self) -> None:
        """The result forwarded to propagate_result matches what was passed to complete()."""
        orchestrator = MagicMock()
        bridge = self._make_bridge(orchestrator)
        fake_chain = MagicMock()

        bridge.setup_auto_propagation(fake_chain)

        sid = bridge.spawn("pm-alpha", "task")
        bridge.complete("pm-alpha", sid, "the exact result string")

        _, call_kwargs = orchestrator.propagate_result.call_args
        # Could be positional args
        args, _ = orchestrator.propagate_result.call_args
        assert args[1] == "the exact result string"
