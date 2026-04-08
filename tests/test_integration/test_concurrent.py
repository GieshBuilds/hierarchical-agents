"""Concurrent integration tests for ChainOrchestrator.

Verifies thread-safety of chain creation, delegation, worker spawning,
and result propagation under parallel workloads.

Note: The ProfileRegistry and SubagentRegistry use SQLite with default
check_same_thread=True. For true cross-thread concurrent tests, we
replace their internal connection with one that has check_same_thread=False.
The ChainOrchestrator's internal Lock and each subsystem's Lock ensure
correctness.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest

from core.integration.orchestrator import ChainOrchestrator
from core.integration.delegation import ChainStatus, DelegationChain, HopStatus
from core.integration.exceptions import (
    ChainAlreadyComplete,
    ChainNotFound,
    CircularDelegation,
    InvalidDelegation,
)
from core.ipc.models import MessagePriority, MessageType
from core.ipc.message_bus import MessageBus
from core.registry.profile_registry import ProfileRegistry
from core.registry.schema import init_db as init_registry_db
from core.workers.subagent_registry import SubagentRegistry
from core.workers.schema import init_subagent_db


# ---------------------------------------------------------------------------
# Helpers — enable cross-thread access on an existing connection
# ---------------------------------------------------------------------------


def _reopen_connection_thread_safe(conn: sqlite3.Connection, db_path: str) -> sqlite3.Connection:
    """Re-open a SQLite connection with check_same_thread=False.

    Closes the original connection and returns a new one with the same
    row_factory and pragmas.
    """
    conn.close()
    new_conn = sqlite3.connect(db_path, check_same_thread=False)
    new_conn.row_factory = sqlite3.Row
    new_conn.execute("PRAGMA journal_mode = WAL;")
    new_conn.execute("PRAGMA foreign_keys = ON;")
    return new_conn


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_dir(tmp_path):
    """Return a temp directory for database files."""
    return str(tmp_path)


@pytest.fixture
def registry(tmp_dir):
    """ProfileRegistry with CEO (hermes) → CTO, CMO → PMs hierarchy.

    Re-opens connection with check_same_thread=False for concurrent use.
    """
    db_path = os.path.join(tmp_dir, "registry.db")
    reg = ProfileRegistry(db_path)
    reg.create_profile(
        "cto", display_name="CTO", role="department_head",
        parent="hermes", department="engineering",
    )
    reg.create_profile(
        "cmo", display_name="CMO", role="department_head",
        parent="hermes", department="marketing",
    )
    reg.create_profile(
        "pm-alpha", display_name="PM Alpha", role="project_manager",
        parent="cto",
    )
    reg.create_profile(
        "pm-beta", display_name="PM Beta", role="project_manager",
        parent="cto",
    )
    reg.create_profile(
        "pm-mktg", display_name="PM Marketing", role="project_manager",
        parent="cmo",
    )
    # Re-open with check_same_thread=False for concurrent access
    reg._conn = _reopen_connection_thread_safe(reg._conn, db_path)
    return reg


@pytest.fixture
def bus(tmp_dir):
    """MessageBus without profile registry (already uses check_same_thread=False)."""
    db_path = os.path.join(tmp_dir, "bus.db")
    return MessageBus(db_path)


@pytest.fixture
def worker_registries(tmp_dir):
    """Factory that creates per-PM SubagentRegistry instances with thread-safe SQLite.

    Uses in-memory databases with check_same_thread=False to avoid
    cross-thread SQLite limitations in concurrent tests.
    """
    _cache: dict[str, SubagentRegistry] = {}

    def factory(pm_name: str) -> SubagentRegistry:
        if pm_name not in _cache:
            # Use :memory: so all ops go through a single thread-safe connection
            wreg = SubagentRegistry(":memory:")
            # Replace the in-memory connection with a thread-safe one
            old_conn = wreg._connections.get("_memory")
            if old_conn:
                # Create a file-backed DB that supports cross-thread access
                db_path = os.path.join(tmp_dir, f"workers-{pm_name}.db")
                new_conn = sqlite3.connect(db_path, check_same_thread=False)
                new_conn.row_factory = sqlite3.Row
                new_conn.execute("PRAGMA journal_mode = WAL;")
                new_conn.execute("PRAGMA foreign_keys = ON;")
                # Initialize schema on the new connection
                from core.workers.schema import (
                    CREATE_SCHEMA_VERSION_TABLE,
                    CREATE_SUBAGENTS_TABLE,
                    CREATE_INDEXES,
                    SCHEMA_VERSION,
                )
                new_conn.execute(CREATE_SCHEMA_VERSION_TABLE)
                new_conn.execute(CREATE_SUBAGENTS_TABLE)
                for idx_sql in CREATE_INDEXES:
                    new_conn.execute(idx_sql)
                row = new_conn.execute(
                    "SELECT MAX(version) AS v FROM schema_version"
                ).fetchone()
                if row[0] is None:
                    new_conn.execute(
                        "INSERT INTO schema_version (version) VALUES (?);",
                        (SCHEMA_VERSION,),
                    )
                new_conn.commit()
                old_conn.close()
                wreg._connections["_memory"] = new_conn
            _cache[pm_name] = wreg
        return _cache[pm_name]

    return factory


@pytest.fixture
def orchestrator(registry, bus, worker_registries):
    """ChainOrchestrator wired to all subsystems."""
    return ChainOrchestrator(
        registry=registry,
        bus=bus,
        worker_registry_factory=worker_registries,
    )


# ===========================================================================
# Concurrent Chain Creation
# ===========================================================================


class TestConcurrentChainCreation:
    """Verify thread-safety of chain creation under concurrent load."""

    def test_concurrent_create_10_chains(self, orchestrator):
        """Create 10 chains concurrently — all should succeed with unique IDs."""
        num_chains = 10
        results: list[DelegationChain] = []
        errors: list[Exception] = []

        def create_chain(i: int) -> DelegationChain:
            return orchestrator.create_chain(f"Concurrent task {i}", "hermes")

        with ThreadPoolExecutor(max_workers=num_chains) as pool:
            futures = [pool.submit(create_chain, i) for i in range(num_chains)]
            for future in as_completed(futures):
                try:
                    results.append(future.result())
                except Exception as exc:
                    errors.append(exc)

        assert len(errors) == 0, f"Chain creation errors: {errors}"
        assert len(results) == num_chains
        chain_ids = {c.chain_id for c in results}
        assert len(chain_ids) == num_chains

        all_chains = orchestrator.list_chains()
        assert len(all_chains) == num_chains

    def test_concurrent_create_20_chains(self, orchestrator):
        """Stress test: 20 chains created simultaneously."""
        num_chains = 20
        results: list[DelegationChain] = []
        barrier = threading.Barrier(num_chains)

        def create_chain(i: int) -> DelegationChain:
            barrier.wait()
            return orchestrator.create_chain(f"Stress task {i}", "hermes")

        with ThreadPoolExecutor(max_workers=num_chains) as pool:
            futures = [pool.submit(create_chain, i) for i in range(num_chains)]
            for future in as_completed(futures):
                results.append(future.result())

        assert len(results) == num_chains
        assert len({c.chain_id for c in results}) == num_chains

    def test_concurrent_create_and_list(self, orchestrator):
        """Create chains and list them concurrently — no crashes."""
        num_chains = 10

        def create_chain(i: int):
            orchestrator.create_chain(f"Task {i}", "hermes")

        def list_chains():
            return orchestrator.list_chains()

        with ThreadPoolExecutor(max_workers=20) as pool:
            futures = []
            for i in range(num_chains):
                futures.append(pool.submit(create_chain, i))
                futures.append(pool.submit(list_chains))
            for f in as_completed(futures):
                f.result()

        final = orchestrator.list_chains()
        assert len(final) == num_chains


# ===========================================================================
# Concurrent Delegation
# ===========================================================================


class TestConcurrentDelegation:
    """Verify thread-safety of delegating tasks through the hierarchy."""

    def test_concurrent_delegate_10_chains(self, orchestrator):
        """Create 10 chains and delegate each concurrently."""
        num = 10
        chains = [
            orchestrator.create_chain(f"Task {i}", "hermes")
            for i in range(num)
        ]
        errors: list[Exception] = []

        def delegate_chain(chain: DelegationChain):
            orchestrator.delegate(chain, "hermes", "cto")

        with ThreadPoolExecutor(max_workers=num) as pool:
            futures = [pool.submit(delegate_chain, c) for c in chains]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as exc:
                    errors.append(exc)

        assert len(errors) == 0, f"Delegation errors: {errors}"
        for chain in chains:
            assert chain.status == ChainStatus.ACTIVE
            assert len(chain.hops) == 1
            assert chain.hops[0].to_profile == "cto"

    def test_concurrent_delegate_down_chain_to_different_pms(self, orchestrator):
        """Delegate to pm-alpha and pm-beta concurrently from different chains."""
        chain_alpha = orchestrator.create_chain("Alpha task", "hermes")
        chain_beta = orchestrator.create_chain("Beta task", "hermes")
        errors: list[Exception] = []

        def delegate_alpha():
            orchestrator.delegate_down_chain(chain_alpha, "pm-alpha")

        def delegate_beta():
            orchestrator.delegate_down_chain(chain_beta, "pm-beta")

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(delegate_alpha), pool.submit(delegate_beta)]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as exc:
                    errors.append(exc)

        assert len(errors) == 0, f"Errors: {errors}"
        assert chain_alpha.status == ChainStatus.ACTIVE
        assert chain_beta.status == ChainStatus.ACTIVE
        assert len(chain_alpha.hops) == 2
        assert len(chain_beta.hops) == 2

    def test_concurrent_delegate_cross_department(self, orchestrator):
        """Simultaneously delegate to engineering and marketing PMs."""
        pairs = []
        targets = ["pm-alpha", "pm-beta", "pm-mktg"]
        for i, target in enumerate(targets):
            pairs.append(
                (orchestrator.create_chain(f"Task for {target}", "hermes"), target)
            )
        errors: list[Exception] = []

        def delegate(pair):
            chain, target = pair
            orchestrator.delegate_down_chain(chain, target)

        with ThreadPoolExecutor(max_workers=len(targets)) as pool:
            futures = [pool.submit(delegate, pair) for pair in pairs]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as exc:
                    errors.append(exc)

        assert len(errors) == 0, f"Delegation errors: {errors}"
        for chain, target in pairs:
            assert chain.status == ChainStatus.ACTIVE


# ===========================================================================
# Concurrent Worker Spawning
# ===========================================================================


class TestConcurrentWorkerSpawning:
    """Verify concurrent worker spawning from different PMs."""

    def test_concurrent_spawn_workers_same_pm(self, orchestrator, worker_registries):
        """Spawn 5 workers under pm-alpha concurrently."""
        chain = orchestrator.create_chain("Big feature", "hermes")
        orchestrator.delegate_down_chain(chain, "pm-alpha")
        # Pre-create registry for thread-safe access
        worker_registries("pm-alpha")

        num_workers = 5
        worker_ids: list[str] = []
        errors: list[Exception] = []

        def spawn(i: int):
            return orchestrator.spawn_worker(chain, "pm-alpha", f"Sub-task {i}")

        with ThreadPoolExecutor(max_workers=num_workers) as pool:
            futures = [pool.submit(spawn, i) for i in range(num_workers)]
            for f in as_completed(futures):
                try:
                    worker_ids.append(f.result())
                except Exception as exc:
                    errors.append(exc)

        assert len(errors) == 0, f"Spawn errors: {errors}"
        assert len(worker_ids) == num_workers
        assert len(set(worker_ids)) == num_workers
        assert len(chain.workers) == num_workers

    def test_concurrent_spawn_workers_different_pms(self, orchestrator, worker_registries):
        """Spawn workers under pm-alpha and pm-beta simultaneously."""
        chain_a = orchestrator.create_chain("Alpha work", "hermes")
        orchestrator.delegate_down_chain(chain_a, "pm-alpha")
        chain_b = orchestrator.create_chain("Beta work", "hermes")
        orchestrator.delegate_down_chain(chain_b, "pm-beta")
        # Pre-create registries
        worker_registries("pm-alpha")
        worker_registries("pm-beta")

        errors: list[Exception] = []
        results: list[str] = []

        def spawn_alpha(i: int):
            return orchestrator.spawn_worker(chain_a, "pm-alpha", f"Alpha-{i}")

        def spawn_beta(i: int):
            return orchestrator.spawn_worker(chain_b, "pm-beta", f"Beta-{i}")

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = (
                [pool.submit(spawn_alpha, i) for i in range(5)]
                + [pool.submit(spawn_beta, i) for i in range(5)]
            )
            for f in as_completed(futures):
                try:
                    results.append(f.result())
                except Exception as exc:
                    errors.append(exc)

        assert len(errors) == 0, f"Spawn errors: {errors}"
        assert len(results) == 10
        assert len(chain_a.workers) == 5
        assert len(chain_b.workers) == 5


# ===========================================================================
# Concurrent Result Propagation
# ===========================================================================


class TestConcurrentResultPropagation:
    """Verify concurrent result propagation from multiple chains."""

    def test_concurrent_propagate_results(self, orchestrator, bus):
        """Propagate results for 10 independent chains concurrently."""
        num = 10
        chains: list[DelegationChain] = []
        for i in range(num):
            c = orchestrator.create_chain(f"Task {i}", "hermes")
            orchestrator.delegate(c, "hermes", "cto")
            chains.append(c)

        errors: list[Exception] = []

        def propagate(chain: DelegationChain, idx: int):
            orchestrator.propagate_result(chain, f"Result {idx}")

        with ThreadPoolExecutor(max_workers=num) as pool:
            futures = [
                pool.submit(propagate, c, i) for i, c in enumerate(chains)
            ]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as exc:
                    errors.append(exc)

        assert len(errors) == 0, f"Propagation errors: {errors}"
        for c in chains:
            assert c.status == ChainStatus.COMPLETED

        ceo_msgs = bus.poll("hermes", message_type=MessageType.TASK_RESPONSE)
        assert len(ceo_msgs) == num

    def test_concurrent_fail_chains(self, orchestrator, bus):
        """Fail multiple chains concurrently."""
        num = 5
        chains: list[DelegationChain] = []
        for i in range(num):
            c = orchestrator.create_chain(f"Failing task {i}", "hermes")
            orchestrator.delegate(c, "hermes", "cto")
            chains.append(c)

        errors: list[Exception] = []

        def fail(chain: DelegationChain, idx: int):
            orchestrator.fail_chain(chain, f"Error {idx}")

        with ThreadPoolExecutor(max_workers=num) as pool:
            futures = [pool.submit(fail, c, i) for i, c in enumerate(chains)]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as exc:
                    errors.append(exc)

        assert len(errors) == 0, f"Fail errors: {errors}"
        for c in chains:
            assert c.status == ChainStatus.FAILED

    def test_concurrent_mixed_complete_and_fail(self, orchestrator, bus):
        """Some chains complete while others fail — no interference."""
        complete_chains = []
        fail_chains = []
        for i in range(5):
            c = orchestrator.create_chain(f"Complete task {i}", "hermes")
            orchestrator.delegate(c, "hermes", "cto")
            complete_chains.append(c)
        for i in range(5):
            c = orchestrator.create_chain(f"Fail task {i}", "hermes")
            orchestrator.delegate(c, "hermes", "cto")
            fail_chains.append(c)

        errors: list[Exception] = []

        def do_complete(chain, idx):
            orchestrator.propagate_result(chain, f"Success {idx}")

        def do_fail(chain, idx):
            orchestrator.fail_chain(chain, f"Failed {idx}")

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = (
                [pool.submit(do_complete, c, i) for i, c in enumerate(complete_chains)]
                + [pool.submit(do_fail, c, i) for i, c in enumerate(fail_chains)]
            )
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as exc:
                    errors.append(exc)

        assert len(errors) == 0, f"Errors: {errors}"
        for c in complete_chains:
            assert c.status == ChainStatus.COMPLETED
        for c in fail_chains:
            assert c.status == ChainStatus.FAILED


# ===========================================================================
# End-to-End Concurrent Scenarios
# ===========================================================================


class TestConcurrentEndToEnd:
    """Full concurrent end-to-end scenarios."""

    def test_full_concurrent_lifecycle(self, orchestrator, bus, worker_registries):
        """10 full lifecycles (create → delegate → spawn → complete → propagate)
        running concurrently."""
        num = 10
        pms = ["pm-alpha", "pm-beta"]
        errors: list[Exception] = []
        completed_chains: list[DelegationChain] = []

        # Pre-create worker registries
        for pm in pms:
            worker_registries(pm)

        def full_lifecycle(i: int):
            pm = pms[i % len(pms)]
            chain = orchestrator.create_chain(f"Lifecycle {i}", "hermes")
            orchestrator.delegate_down_chain(chain, pm)
            sid = orchestrator.spawn_worker(chain, pm, f"Work item {i}")
            orchestrator.complete_worker(chain, pm, sid, f"Done {i}")
            orchestrator.propagate_result(chain, f"Result {i}")
            return chain

        with ThreadPoolExecutor(max_workers=num) as pool:
            futures = [pool.submit(full_lifecycle, i) for i in range(num)]
            for f in as_completed(futures):
                try:
                    completed_chains.append(f.result())
                except Exception as exc:
                    errors.append(exc)

        assert len(errors) == 0, f"Lifecycle errors: {errors}"
        assert len(completed_chains) == num
        for c in completed_chains:
            assert c.status == ChainStatus.COMPLETED

        all_chains = orchestrator.list_chains()
        assert len(all_chains) == num

    def test_concurrent_get_chain_during_mutations(self, orchestrator):
        """Get chains while they are being delegated and completed."""
        chains: list[DelegationChain] = []
        for i in range(5):
            c = orchestrator.create_chain(f"Task {i}", "hermes")
            chains.append(c)

        errors: list[Exception] = []

        def delegate_and_complete(chain):
            orchestrator.delegate(chain, "hermes", "cto")
            orchestrator.propagate_result(chain, "Done")

        def get_chain(chain_id):
            try:
                return orchestrator.get_chain(chain_id)
            except ChainNotFound:
                return None

        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = []
            for c in chains:
                futures.append(pool.submit(delegate_and_complete, c))
                futures.append(pool.submit(get_chain, c.chain_id))
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as exc:
                    errors.append(exc)

        assert len(errors) == 0, f"Errors: {errors}"
        for c in chains:
            assert c.status == ChainStatus.COMPLETED

    def test_concurrent_list_chains_with_filters(self, orchestrator):
        """Create chains concurrently, then filter by status and originator."""
        num = 12
        chains: list[DelegationChain] = []

        def create_and_maybe_delegate(i: int):
            c = orchestrator.create_chain(f"Task {i}", "hermes")
            if i % 2 == 0:
                orchestrator.delegate(c, "hermes", "cto")
            return c

        with ThreadPoolExecutor(max_workers=num) as pool:
            futures = [pool.submit(create_and_maybe_delegate, i) for i in range(num)]
            for f in as_completed(futures):
                chains.append(f.result())

        pending = orchestrator.list_chains(status=ChainStatus.PENDING)
        active = orchestrator.list_chains(status=ChainStatus.ACTIVE)

        assert len(pending) == 6
        assert len(active) == 6
