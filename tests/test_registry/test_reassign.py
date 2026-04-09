"""Tests for the reassign() operation.

Reassign moves a profile to a new parent in the hierarchy, with full
validation of role compatibility and circular reference prevention.
"""

from __future__ import annotations

import pytest

from core.registry.exceptions import (
    InvalidHierarchy,
    ProfileNotFound,
    RegistryError,
)
from core.registry.models import Profile, Role
from core.registry.profile_registry import ProfileRegistry


class TestReassignSuccess:
    """Valid reassignment scenarios."""

    def test_move_pm_to_different_dept_head(self, sample_org: ProfileRegistry) -> None:
        """Moving a PM from one dept head to another should succeed."""
        sample_org.reassign("pm-alpha", new_parent="cmo")
        profile = sample_org.get_profile("pm-alpha")
        assert profile.parent_profile == "cmo"

    def test_reassigned_pm_appears_in_new_parent_reports(
        self, sample_org: ProfileRegistry
    ) -> None:
        """After reassignment, the PM should appear in the new parent's reports."""
        sample_org.reassign("pm-alpha", new_parent="cmo")
        reports = sample_org.list_reports("cmo")
        names = [p.profile_name for p in reports]
        assert "pm-alpha" in names

    def test_reassigned_pm_removed_from_old_parent_reports(
        self, sample_org: ProfileRegistry
    ) -> None:
        """After reassignment, the PM should not appear in the old parent's reports."""
        sample_org.reassign("pm-alpha", new_parent="cmo")
        reports = sample_org.list_reports("cto")
        names = [p.profile_name for p in reports]
        assert "pm-alpha" not in names

    def test_reassign_updates_chain_of_command(
        self, sample_org: ProfileRegistry
    ) -> None:
        """After reassignment, the chain of command should reflect the new parent."""
        sample_org.reassign("pm-alpha", new_parent="cmo")
        chain = sample_org.get_chain_of_command("pm-alpha")
        names = [p.profile_name for p in chain]
        assert names == ["pm-alpha", "cmo", "hermes"]

    def test_reassign_dept_head_remains_valid(self, sample_org: ProfileRegistry) -> None:
        """A dept head can only report to CEO, so reassigning to another CEO is a no-op
        or should remain valid if there's only one CEO."""
        # Dept heads must report to CEO. Reassigning to the same CEO should be fine.
        sample_org.reassign("cto", new_parent="hermes")
        profile = sample_org.get_profile("cto")
        assert profile.parent_profile == "hermes"


class TestReassignInvalidParent:
    """Reassignment to an invalid parent role should be rejected."""

    def test_cannot_reassign_pm_to_pm(self, sample_org: ProfileRegistry) -> None:
        """A PM cannot be reassigned to report to another PM."""
        with pytest.raises(InvalidHierarchy):
            sample_org.reassign("pm-alpha", new_parent="pm-beta")

    def test_can_reassign_pm_to_ceo(self, sample_org: ProfileRegistry) -> None:
        """A PM may be reassigned to report directly to the CEO."""
        sample_org.reassign("pm-alpha", new_parent="hermes")
        profile = sample_org.get_profile("pm-alpha")
        assert profile.parent_profile == "hermes"

    def test_reassign_pm_to_ceo_updates_chain_of_command(
        self, sample_org: ProfileRegistry
    ) -> None:
        """PM -> CEO reassignment should update the chain of command."""
        sample_org.reassign("pm-alpha", new_parent="hermes")
        chain = sample_org.get_chain_of_command("pm-alpha")
        names = [p.profile_name for p in chain]
        assert names == ["pm-alpha", "hermes"]

    def test_cannot_reassign_dept_head_to_pm(self, sample_org: ProfileRegistry) -> None:
        """A dept head cannot be reassigned to report to a PM."""
        with pytest.raises(InvalidHierarchy):
            sample_org.reassign("cto", new_parent="pm-alpha")

    def test_cannot_reassign_dept_head_to_dept_head(
        self, sample_org: ProfileRegistry
    ) -> None:
        """A dept head cannot report to another dept head."""
        with pytest.raises(InvalidHierarchy):
            sample_org.reassign("cto", new_parent="cmo")

    def test_cannot_reassign_ceo(self, sample_org: ProfileRegistry) -> None:
        """The CEO has no parent and should not be reassignable."""
        with pytest.raises(InvalidHierarchy):
            sample_org.reassign("hermes", new_parent="cto")

    def test_reassign_nonexistent_profile_raises(
        self, registry: ProfileRegistry
    ) -> None:
        """Reassigning a non-existent profile should raise ProfileNotFound."""
        with pytest.raises(ProfileNotFound):
            registry.reassign("ghost", new_parent="hermes")

    def test_reassign_to_nonexistent_parent_raises(
        self, sample_org: ProfileRegistry
    ) -> None:
        """Reassigning to a non-existent parent should raise."""
        with pytest.raises((ProfileNotFound, InvalidHierarchy, RegistryError)):
            sample_org.reassign("pm-alpha", new_parent="nonexistent")


class TestReassignCircular:
    """Reassignment must not create circular references."""

    def test_cannot_reassign_to_self(self, sample_org: ProfileRegistry) -> None:
        """A profile cannot be reassigned to itself."""
        with pytest.raises(InvalidHierarchy):
            sample_org.reassign("cto", new_parent="cto")

    def test_cannot_create_cycle_via_reassign(
        self, sample_org: ProfileRegistry
    ) -> None:
        """Reassigning a dept head to report to its own PM child should fail."""
        with pytest.raises(InvalidHierarchy):
            sample_org.reassign("cto", new_parent="pm-alpha")

    def test_cannot_create_indirect_cycle(self, sample_org: ProfileRegistry) -> None:
        """Even indirect cycles via multiple levels should be prevented.

        If we had a deeper hierarchy, reassigning an ancestor to a descendant
        should always be blocked.
        """
        # pm-alpha -> cto -> hermes
        # Trying to make hermes report to pm-alpha should fail
        with pytest.raises(InvalidHierarchy):
            sample_org.reassign("hermes", new_parent="pm-alpha")


class TestReassignEdgeCases:
    """Edge cases for reassignment."""

    def test_reassign_to_same_parent_is_noop(
        self, sample_org: ProfileRegistry
    ) -> None:
        """Reassigning to the current parent should succeed without error."""
        sample_org.reassign("pm-alpha", new_parent="cto")
        profile = sample_org.get_profile("pm-alpha")
        assert profile.parent_profile == "cto"

    def test_reassign_archived_profile_raises(
        self, sample_org: ProfileRegistry
    ) -> None:
        """Reassigning an archived profile should raise an error."""
        sample_org.delete_profile("pm-alpha")
        with pytest.raises((InvalidHierarchy, RegistryError)):
            sample_org.reassign("pm-alpha", new_parent="cmo")
