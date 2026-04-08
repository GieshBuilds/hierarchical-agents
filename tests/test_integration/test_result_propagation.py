"""Tests for result propagation — collect_worker_result and HermesWorkerManager.

Covers:
- ResultCollector.collect_worker_result() stores results on the chain
- Multiple results accumulate correctly
- Overwrite semantics for the same subagent_id
- DelegationChain.worker_results persists across to_dict / from_dict round-trip
- HermesWorkerManager.on_worker_complete() updates the registry and calls collect
"""
from __future__ import annotations

from unittest.mock import MagicMock, call

import pytest

from core.integration.delegation import DelegationChain
from core.integration.result_propagation import ResultCollector
from core.workers.subagent_registry import SubagentRegistry
from integrations.hermes.worker_manager import HermesWorkerManager, WorkerResultData


# ======================================================================
# Helpers / fixtures
# ======================================================================


def _make_collector() -> ResultCollector:
    """Return a ResultCollector with mocked bus and protocol.

    The bus and protocol are not exercised by collect_worker_result(),
    but the constructor requires them.
    """
    return ResultCollector(bus=MagicMock(), protocol=MagicMock())


def _make_chain(**kwargs) -> DelegationChain:
    """Return a DelegationChain with sensible defaults."""
    return DelegationChain(
        task_description=kwargs.get("task_description", "test task"),
        originator=kwargs.get("originator", "hermes"),
    )


# ======================================================================
# TestCollectWorkerResult
# ======================================================================


class TestCollectWorkerResult:
    """Tests for ResultCollector.collect_worker_result()."""

    def test_stores_result_on_chain(self):
        """Result is stored under the correct subagent_id key."""
        collector = _make_collector()
        chain = _make_chain()

        collector.collect_worker_result(chain, "sa-001", "Task finished OK")

        assert chain.worker_results["sa-001"] == "Task finished OK"

    def test_chain_starts_with_empty_worker_results(self):
        """A fresh DelegationChain has an empty worker_results dict."""
        chain = _make_chain()
        assert chain.worker_results == {}

    def test_multiple_results_accumulate(self):
        """Results for distinct subagent IDs all coexist on the chain."""
        collector = _make_collector()
        chain = _make_chain()

        collector.collect_worker_result(chain, "sa-001", "result A")
        collector.collect_worker_result(chain, "sa-002", "result B")
        collector.collect_worker_result(chain, "sa-003", "result C")

        assert chain.worker_results == {
            "sa-001": "result A",
            "sa-002": "result B",
            "sa-003": "result C",
        }

    def test_overwrite_same_subagent(self):
        """Recording a result for the same subagent_id replaces the old value."""
        collector = _make_collector()
        chain = _make_chain()

        collector.collect_worker_result(chain, "sa-001", "first result")
        collector.collect_worker_result(chain, "sa-001", "second result")

        assert chain.worker_results["sa-001"] == "second result"
        assert len(chain.worker_results) == 1

    def test_result_does_not_alter_workers_list(self):
        """collect_worker_result() must not modify chain.workers."""
        collector = _make_collector()
        chain = _make_chain()
        chain.add_worker("sa-001")

        collector.collect_worker_result(chain, "sa-001", "done")

        assert chain.workers == ["sa-001"]

    def test_result_does_not_change_chain_status(self):
        """collect_worker_result() must not alter the chain status."""
        from core.integration.delegation import ChainStatus

        collector = _make_collector()
        chain = _make_chain()

        collector.collect_worker_result(chain, "sa-001", "done")

        assert chain.status == ChainStatus.PENDING

    def test_empty_result_string(self):
        """An empty result string is stored without error."""
        collector = _make_collector()
        chain = _make_chain()

        collector.collect_worker_result(chain, "sa-001", "")

        assert chain.worker_results["sa-001"] == ""

    def test_returns_false_when_no_auto_propagate(self):
        """collect_worker_result() returns False when auto_propagate=False (default)."""
        collector = _make_collector()
        chain = _make_chain()

        rv = collector.collect_worker_result(chain, "sa-001", "done")

        assert rv is False


# ======================================================================
# TestDelegationChainWorkerResultsPersistence
# ======================================================================


class TestDelegationChainWorkerResultsPersistence:
    """Tests for DelegationChain.worker_results round-trip serialization."""

    def test_to_dict_includes_worker_results(self):
        """to_dict() must serialize worker_results."""
        chain = _make_chain()
        chain.worker_results["sa-x"] = "some result"

        d = chain.to_dict()

        assert "worker_results" in d
        assert d["worker_results"] == {"sa-x": "some result"}

    def test_from_dict_restores_worker_results(self):
        """from_dict() must restore worker_results from serialized data."""
        chain = _make_chain()
        chain.worker_results["sa-x"] = "some result"
        chain.worker_results["sa-y"] = "another"

        restored = DelegationChain.from_dict(chain.to_dict())

        assert restored.worker_results == {"sa-x": "some result", "sa-y": "another"}

    def test_from_dict_missing_key_defaults_to_empty(self):
        """from_dict() must default worker_results to {} if key absent (backwards compat)."""
        chain = _make_chain()
        d = chain.to_dict()
        del d["worker_results"]

        restored = DelegationChain.from_dict(d)

        assert restored.worker_results == {}


# ======================================================================
# TestHermesWorkerManager
# ======================================================================


class TestHermesWorkerManager:
    """Tests for HermesWorkerManager.on_worker_complete()."""

    @pytest.fixture
    def registry(self) -> SubagentRegistry:
        """In-memory SubagentRegistry for testing."""
        return SubagentRegistry(base_path=":memory:")

    @pytest.fixture
    def chain(self) -> DelegationChain:
        """A fresh DelegationChain."""
        return _make_chain()

    @pytest.fixture
    def collector(self) -> ResultCollector:
        """ResultCollector with mocked dependencies."""
        return _make_collector()

    @pytest.fixture
    def manager(
        self,
        registry: SubagentRegistry,
        collector: ResultCollector,
        chain: DelegationChain,
    ) -> HermesWorkerManager:
        """HermesWorkerManager wired to test fixtures."""
        return HermesWorkerManager(
            registry=registry,
            result_collector=collector,
            chain=chain,
            pm_profile="pm",
        )

    def _register_running_worker(
        self, registry: SubagentRegistry, pm: str = "pm"
    ) -> str:
        """Register a worker in RUNNING state and return its subagent_id."""
        sa = registry.register(project_manager=pm, task_goal="test task")
        return sa.subagent_id

    # ------------------------------------------------------------------
    # Registry updates
    # ------------------------------------------------------------------

    def test_on_worker_complete_marks_registry_completed(
        self, manager: HermesWorkerManager, registry: SubagentRegistry
    ):
        """on_worker_complete() must transition the registry entry to 'completed'."""
        sa_id = self._register_running_worker(registry)
        result = WorkerResultData(summary="All done")

        manager.on_worker_complete(sa_id, result)

        updated = registry.get(sa_id)
        assert updated.status == "completed"

    def test_on_worker_complete_stores_result_summary_in_registry(
        self, manager: HermesWorkerManager, registry: SubagentRegistry
    ):
        """on_worker_complete() must persist result_summary in registry."""
        sa_id = self._register_running_worker(registry)
        result = WorkerResultData(summary="Finished the refactor")

        manager.on_worker_complete(sa_id, result)

        updated = registry.get(sa_id)
        assert updated.result_summary == "Finished the refactor"

    # ------------------------------------------------------------------
    # Chain bookkeeping
    # ------------------------------------------------------------------

    def test_on_worker_complete_records_result_on_chain(
        self,
        manager: HermesWorkerManager,
        registry: SubagentRegistry,
        chain: DelegationChain,
    ):
        """on_worker_complete() must store result in chain.worker_results."""
        sa_id = self._register_running_worker(registry)
        result = WorkerResultData(summary="deployment done")

        manager.on_worker_complete(sa_id, result)

        assert chain.worker_results[sa_id] == "deployment done"

    def test_on_worker_complete_calls_collect_with_correct_args(
        self, registry: SubagentRegistry, chain: DelegationChain
    ):
        """collect_worker_result() is called with (chain, subagent_id, summary)."""
        mock_collector = MagicMock(spec=ResultCollector)
        mgr = HermesWorkerManager(
            registry=registry,
            result_collector=mock_collector,
            chain=chain,
            pm_profile="pm",
        )

        sa_id = self._register_running_worker(registry)
        result = WorkerResultData(summary="tests passed")

        mgr.on_worker_complete(sa_id, result)

        mock_collector.collect_worker_result.assert_called_once_with(
            chain, sa_id, "tests passed"
        )

    def test_on_worker_complete_calls_registry_complete_before_collect(
        self, chain: DelegationChain
    ):
        """registry.complete() is called before result_collector.collect_worker_result().

        This ordering guarantees that persistent state is updated before the
        in-memory chain bookkeeping, reducing the risk of stale in-memory state
        after a crash.
        """
        call_order: list[str] = []

        mock_registry = MagicMock(spec=SubagentRegistry)
        mock_registry.complete.side_effect = lambda *a, **kw: call_order.append(
            "registry.complete"
        )

        mock_collector = MagicMock(spec=ResultCollector)
        mock_collector.collect_worker_result.side_effect = (
            lambda *a, **kw: call_order.append("collect_worker_result")
        )

        mgr = HermesWorkerManager(
            registry=mock_registry,
            result_collector=mock_collector,
            chain=chain,
            pm_profile="pm",
        )

        mgr.on_worker_complete("sa-999", WorkerResultData(summary="ok"))

        assert call_order == ["registry.complete", "collect_worker_result"]

    # ------------------------------------------------------------------
    # Multiple workers
    # ------------------------------------------------------------------

    def test_two_workers_both_recorded_on_chain(
        self,
        manager: HermesWorkerManager,
        registry: SubagentRegistry,
        chain: DelegationChain,
    ):
        """Multiple workers completing in sequence all appear in chain.worker_results."""
        sa1 = self._register_running_worker(registry)
        sa2 = self._register_running_worker(registry)

        manager.on_worker_complete(sa1, WorkerResultData(summary="worker 1 done"))
        manager.on_worker_complete(sa2, WorkerResultData(summary="worker 2 done"))

        assert chain.worker_results[sa1] == "worker 1 done"
        assert chain.worker_results[sa2] == "worker 2 done"
        assert len(chain.worker_results) == 2


# ======================================================================
# TestWorkerResultData
# ======================================================================


class TestWorkerResultData:
    """Tests for the WorkerResultData dataclass."""

    def test_summary_required(self):
        """WorkerResultData requires at least a summary."""
        r = WorkerResultData(summary="hello")
        assert r.summary == "hello"

    def test_default_artifacts_empty(self):
        """artifacts defaults to empty list."""
        r = WorkerResultData(summary="x")
        assert r.artifacts == []

    def test_default_token_cost_zero(self):
        """token_cost defaults to 0."""
        r = WorkerResultData(summary="x")
        assert r.token_cost == 0

    def test_default_session_history_empty(self):
        """session_history defaults to empty list."""
        r = WorkerResultData(summary="x")
        assert r.session_history == []

    def test_all_fields_settable(self):
        """All fields can be set explicitly."""
        history = [{"role": "user", "content": "hi"}]
        r = WorkerResultData(
            summary="done",
            artifacts=["file.py"],
            token_cost=1234,
            session_history=history,
        )
        assert r.summary == "done"
        assert r.artifacts == ["file.py"]
        assert r.token_cost == 1234
        assert r.session_history == history

    def test_satisfies_worker_result_protocol(self):
        """WorkerResultData satisfies the WorkerResult protocol via runtime check."""
        from core.workers.interface import WorkerResult

        r = WorkerResultData(summary="check")
        assert isinstance(r, WorkerResult)
