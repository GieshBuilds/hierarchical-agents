"""Tests for memory exception classes."""

from __future__ import annotations

import pytest
from core.memory.exceptions import (
    ScopedMemoryError,
    MemoryEntryNotFound,
    KnowledgeEntryNotFound,
    MemoryBudgetExceeded,
    InvalidTierTransition,
    InvalidMemoryScope,
    ContextInjectionError,
    GarbageCollectionError,
    MemoryStoreError,
)


# ---------------------------------------------------------------------------
# Base exception
# ---------------------------------------------------------------------------


class TestScopedMemoryError:
    """Tests for the ScopedMemoryError base exception."""

    def test_is_exception(self) -> None:
        err = ScopedMemoryError("test error")
        assert isinstance(err, Exception)

    def test_message(self) -> None:
        err = ScopedMemoryError("test error")
        assert str(err) == "test error"

    def test_can_be_raised(self) -> None:
        with pytest.raises(ScopedMemoryError):
            raise ScopedMemoryError("boom")


# ---------------------------------------------------------------------------
# MemoryEntryNotFound
# ---------------------------------------------------------------------------


class TestMemoryEntryNotFound:
    """Tests for the MemoryEntryNotFound exception."""

    def test_message_format(self) -> None:
        err = MemoryEntryNotFound("mem-abc12345")
        assert "mem-abc12345" in str(err)
        assert "Memory entry not found" in str(err)

    def test_entry_id_attribute(self) -> None:
        err = MemoryEntryNotFound("mem-abc12345")
        assert err.entry_id == "mem-abc12345"

    def test_is_scoped_memory_error(self) -> None:
        err = MemoryEntryNotFound("mem-abc12345")
        assert isinstance(err, ScopedMemoryError)


# ---------------------------------------------------------------------------
# KnowledgeEntryNotFound
# ---------------------------------------------------------------------------


class TestKnowledgeEntryNotFound:
    """Tests for the KnowledgeEntryNotFound exception."""

    def test_message_format(self) -> None:
        err = KnowledgeEntryNotFound("kb-abc12345")
        assert "kb-abc12345" in str(err)
        assert "Knowledge entry not found" in str(err)

    def test_entry_id_attribute(self) -> None:
        err = KnowledgeEntryNotFound("kb-abc12345")
        assert err.entry_id == "kb-abc12345"

    def test_is_scoped_memory_error(self) -> None:
        err = KnowledgeEntryNotFound("kb-abc12345")
        assert isinstance(err, ScopedMemoryError)


# ---------------------------------------------------------------------------
# MemoryBudgetExceeded
# ---------------------------------------------------------------------------


class TestMemoryBudgetExceeded:
    """Tests for the MemoryBudgetExceeded exception."""

    def test_message_format(self) -> None:
        err = MemoryBudgetExceeded("ceo", "entries", 1001, 1000)
        msg = str(err)
        assert "ceo" in msg
        assert "entries" in msg
        assert "1001" in msg
        assert "1000" in msg

    def test_profile_name_attribute(self) -> None:
        err = MemoryBudgetExceeded("ceo", "entries", 1001, 1000)
        assert err.profile_name == "ceo"

    def test_budget_type_attribute(self) -> None:
        err = MemoryBudgetExceeded("ceo", "bytes", 11000000, 10485760)
        assert err.budget_type == "bytes"

    def test_current_attribute(self) -> None:
        err = MemoryBudgetExceeded("ceo", "entries", 1001, 1000)
        assert err.current == 1001

    def test_limit_attribute(self) -> None:
        err = MemoryBudgetExceeded("ceo", "entries", 1001, 1000)
        assert err.limit == 1000

    def test_is_scoped_memory_error(self) -> None:
        err = MemoryBudgetExceeded("ceo", "entries", 1001, 1000)
        assert isinstance(err, ScopedMemoryError)


# ---------------------------------------------------------------------------
# InvalidTierTransition
# ---------------------------------------------------------------------------


class TestInvalidTierTransition:
    """Tests for the InvalidTierTransition exception."""

    def test_message_format(self) -> None:
        err = InvalidTierTransition("cold", "hot")
        msg = str(err)
        assert "cold" in msg
        assert "hot" in msg
        assert "Invalid tier transition" in msg

    def test_from_tier_attribute(self) -> None:
        err = InvalidTierTransition("cold", "hot")
        assert err.from_tier == "cold"

    def test_to_tier_attribute(self) -> None:
        err = InvalidTierTransition("cold", "hot")
        assert err.to_tier == "hot"

    def test_is_scoped_memory_error(self) -> None:
        err = InvalidTierTransition("cold", "hot")
        assert isinstance(err, ScopedMemoryError)


# ---------------------------------------------------------------------------
# InvalidMemoryScope
# ---------------------------------------------------------------------------


class TestInvalidMemoryScope:
    """Tests for the InvalidMemoryScope exception."""

    def test_message_format(self) -> None:
        err = InvalidMemoryScope("strategic", "worker")
        msg = str(err)
        assert "strategic" in msg
        assert "worker" in msg

    def test_scope_attribute(self) -> None:
        err = InvalidMemoryScope("strategic", "worker")
        assert err.scope == "strategic"

    def test_role_attribute(self) -> None:
        err = InvalidMemoryScope("strategic", "worker")
        assert err.role == "worker"

    def test_is_scoped_memory_error(self) -> None:
        err = InvalidMemoryScope("strategic", "worker")
        assert isinstance(err, ScopedMemoryError)


# ---------------------------------------------------------------------------
# ContextInjectionError
# ---------------------------------------------------------------------------


class TestContextInjectionError:
    """Tests for the ContextInjectionError exception."""

    def test_message_format(self) -> None:
        err = ContextInjectionError("ceo", "budget exceeded")
        msg = str(err)
        assert "ceo" in msg
        assert "budget exceeded" in msg
        assert "Context injection failed" in msg

    def test_profile_name_attribute(self) -> None:
        err = ContextInjectionError("ceo", "timeout")
        assert err.profile_name == "ceo"

    def test_reason_attribute(self) -> None:
        err = ContextInjectionError("ceo", "timeout")
        assert err.reason == "timeout"

    def test_is_scoped_memory_error(self) -> None:
        err = ContextInjectionError("ceo", "timeout")
        assert isinstance(err, ScopedMemoryError)


# ---------------------------------------------------------------------------
# GarbageCollectionError
# ---------------------------------------------------------------------------


class TestGarbageCollectionError:
    """Tests for the GarbageCollectionError exception."""

    def test_message_format(self) -> None:
        err = GarbageCollectionError("database locked")
        msg = str(err)
        assert "database locked" in msg
        assert "Garbage collection failed" in msg

    def test_reason_attribute(self) -> None:
        err = GarbageCollectionError("database locked")
        assert err.reason == "database locked"

    def test_is_scoped_memory_error(self) -> None:
        err = GarbageCollectionError("database locked")
        assert isinstance(err, ScopedMemoryError)


# ---------------------------------------------------------------------------
# MemoryStoreError
# ---------------------------------------------------------------------------


class TestMemoryStoreError:
    """Tests for the MemoryStoreError exception."""

    def test_message_format(self) -> None:
        err = MemoryStoreError("disk full")
        assert str(err) == "disk full"

    def test_is_scoped_memory_error(self) -> None:
        err = MemoryStoreError("disk full")
        assert isinstance(err, ScopedMemoryError)

    def test_can_be_raised_and_caught(self) -> None:
        with pytest.raises(MemoryStoreError):
            raise MemoryStoreError("write failed")


# ---------------------------------------------------------------------------
# Exception hierarchy
# ---------------------------------------------------------------------------


class TestExceptionHierarchy:
    """Tests that all exceptions are subclasses of ScopedMemoryError."""

    def test_all_subclasses(self) -> None:
        exception_classes = [
            MemoryEntryNotFound,
            KnowledgeEntryNotFound,
            MemoryBudgetExceeded,
            InvalidTierTransition,
            InvalidMemoryScope,
            ContextInjectionError,
            GarbageCollectionError,
            MemoryStoreError,
        ]
        for cls in exception_classes:
            assert issubclass(cls, ScopedMemoryError), (
                f"{cls.__name__} is not a subclass of ScopedMemoryError"
            )

    def test_all_are_exceptions(self) -> None:
        exception_classes = [
            ScopedMemoryError,
            MemoryEntryNotFound,
            KnowledgeEntryNotFound,
            MemoryBudgetExceeded,
            InvalidTierTransition,
            InvalidMemoryScope,
            ContextInjectionError,
            GarbageCollectionError,
            MemoryStoreError,
        ]
        for cls in exception_classes:
            assert issubclass(cls, Exception), (
                f"{cls.__name__} is not a subclass of Exception"
            )

    def test_catch_base_catches_all(self) -> None:
        """Catching ScopedMemoryError should catch all subtypes."""
        with pytest.raises(ScopedMemoryError):
            raise MemoryEntryNotFound("mem-test1234")

        with pytest.raises(ScopedMemoryError):
            raise KnowledgeEntryNotFound("kb-test1234")

        with pytest.raises(ScopedMemoryError):
            raise MemoryBudgetExceeded("ceo", "entries", 1001, 1000)

        with pytest.raises(ScopedMemoryError):
            raise InvalidTierTransition("cold", "hot")

        with pytest.raises(ScopedMemoryError):
            raise InvalidMemoryScope("strategic", "worker")

        with pytest.raises(ScopedMemoryError):
            raise ContextInjectionError("ceo", "reason")

        with pytest.raises(ScopedMemoryError):
            raise GarbageCollectionError("reason")

        with pytest.raises(ScopedMemoryError):
            raise MemoryStoreError("reason")
