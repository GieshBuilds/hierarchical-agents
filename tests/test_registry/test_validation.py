"""Tests for hierarchy validation and integrity rules.

From the spec (Task 4 in phase1-implementation-plan.md):
- Only one CEO allowed
- Dept heads must parent to CEO
- PMs must parent to a dept head
- Cannot archive profile with active dependents
- No circular references
- Invalid role transitions rejected
"""

from __future__ import annotations

import pytest

from core.registry.exceptions import (
    DuplicateProfile,
    InvalidHierarchy,
    RegistryError,
)
from core.registry.models import Role, Status
from core.registry.profile_registry import ProfileRegistry


class TestOnlyOneCEO:
    """Only one CEO profile should be allowed in the registry."""

    def test_cannot_create_second_ceo(self, registry: ProfileRegistry) -> None:
        """Attempting to create a second CEO should raise InvalidHierarchy."""
        # The first CEO ('hermes') is auto-created.
        with pytest.raises(InvalidHierarchy):
            registry.create_profile(
                name="second-ceo",
                role="ceo",
                parent=None,
                department=None,
            )

    def test_cannot_create_ceo_with_parent(self, registry: ProfileRegistry) -> None:
        """A CEO should not have a parent profile."""
        with pytest.raises(InvalidHierarchy):
            registry.create_profile(
                name="second-ceo",
                role="ceo",
                parent="hermes",
                department=None,
            )


class TestDeptHeadParentRules:
    """Department heads must have parent_profile pointing to the CEO."""

    def test_dept_head_must_parent_to_ceo(self, registry: ProfileRegistry) -> None:
        """Creating a dept head with CEO as parent should succeed."""
        profile = registry.create_profile(
            name="cto",
            role="department_head",
            parent="hermes",
            department="engineering",
        )
        assert profile.parent_profile == "hermes"

    def test_dept_head_can_parent_to_any_profile(self, sample_org: ProfileRegistry) -> None:
        """Dept heads can parent to any existing profile (flexible hierarchy)."""
        # Dept head under another dept head
        sub = sample_org.create_profile(
            name="sub-cto",
            role="department_head",
            parent="cto",
            department="engineering",
        )
        assert sub.parent_profile == "cto"

    def test_dept_head_cannot_have_no_parent(self, registry: ProfileRegistry) -> None:
        """A dept head must have a parent (the CEO)."""
        with pytest.raises(InvalidHierarchy):
            registry.create_profile(
                name="orphan-head",
                role="department_head",
                parent=None,
                department="engineering",
            )

    def test_dept_head_nonexistent_parent_raises(self, registry: ProfileRegistry) -> None:
        """A dept head referencing a non-existent parent should fail."""
        with pytest.raises((InvalidHierarchy, RegistryError)):
            registry.create_profile(
                name="cto",
                role="department_head",
                parent="nonexistent",
                department="engineering",
            )


class TestPMParentRules:
    """Project managers must have a parent profile."""

    def test_pm_can_parent_to_dept_head(self, sample_org: ProfileRegistry) -> None:
        """Creating a PM with a dept head as parent should succeed."""
        pm = sample_org.get_profile("pm-alpha")
        assert pm.parent_profile == "cto"

    def test_pm_can_parent_to_ceo(self, registry: ProfileRegistry) -> None:
        """A PM can report directly to the CEO."""
        pm = registry.create_profile(
            name="pm-direct",
            role="project_manager",
            parent="hermes",
            department="engineering",
        )
        assert pm.parent_profile == "hermes"

    def test_pm_can_parent_to_pm(self, sample_org: ProfileRegistry) -> None:
        """A PM can have another PM as parent (flexible hierarchy)."""
        pm = sample_org.create_profile(
            name="pm-under-pm",
            role="project_manager",
            parent="pm-alpha",
            department="engineering",
        )
        assert pm.parent_profile == "pm-alpha"

    def test_pm_cannot_have_no_parent(self, registry: ProfileRegistry) -> None:
        """A PM must have a parent."""
        with pytest.raises(InvalidHierarchy):
            registry.create_profile(
                name="orphan-pm",
                role="project_manager",
                parent=None,
                department="engineering",
            )


class TestArchiveWithDependents:
    """Cannot archive/delete a profile that has active dependents."""

    def test_cannot_archive_ceo_with_active_reports(self, sample_org: ProfileRegistry) -> None:
        """Archiving the CEO when dept heads are active should fail."""
        with pytest.raises(InvalidHierarchy):
            sample_org.delete_profile("hermes")

    def test_cannot_archive_dept_head_with_active_pms(self, sample_org: ProfileRegistry) -> None:
        """Archiving a dept head with active PMs should fail."""
        with pytest.raises(InvalidHierarchy):
            sample_org.delete_profile("cto")

    def test_can_archive_leaf_pm(self, sample_org: ProfileRegistry) -> None:
        """Archiving a PM with no reports should succeed."""
        sample_org.delete_profile("pm-alpha")
        profile = sample_org.get_profile("pm-alpha")
        assert profile.status == Status.ARCHIVED.value

    def test_can_archive_dept_head_after_pms_archived(self, sample_org: ProfileRegistry) -> None:
        """After archiving all PMs under a dept head, archiving it should succeed."""
        sample_org.delete_profile("pm-gamma")
        sample_org.delete_profile("cmo")
        profile = sample_org.get_profile("cmo")
        assert profile.status == Status.ARCHIVED.value

    def test_cannot_archive_dept_head_with_mixed_reports(
        self, sample_org: ProfileRegistry
    ) -> None:
        """If one PM is archived but another is active, dept head can't be archived."""
        sample_org.delete_profile("pm-alpha")
        with pytest.raises(InvalidHierarchy):
            sample_org.delete_profile("cto")


class TestNoCircularReferences:
    """The hierarchy must never allow circular references."""

    def test_profile_cannot_be_own_parent(self, registry: ProfileRegistry) -> None:
        """A profile cannot be its own parent."""
        with pytest.raises((InvalidHierarchy, RegistryError)):
            registry.create_profile(
                name="loop",
                role="department_head",
                parent="loop",
                department="engineering",
            )

    def test_no_indirect_circular_via_reassign(self, sample_org: ProfileRegistry) -> None:
        """Reassigning to create an indirect cycle should fail.

        e.g., if we could reassign CTO to report to pm-alpha (who reports to CTO),
        that would create a cycle.
        """
        with pytest.raises(InvalidHierarchy):
            sample_org.reassign("cto", new_parent="pm-alpha")


class TestInvalidRoleTransitions:
    """Profiles should not be updatable to invalid roles that break the hierarchy."""

    def test_cannot_change_ceo_role(self, registry: ProfileRegistry) -> None:
        """The CEO's role should not be changeable."""
        with pytest.raises((InvalidHierarchy, RegistryError)):
            registry.update_profile("hermes", role="department_head")

    def test_cannot_promote_pm_to_ceo(self, sample_org: ProfileRegistry) -> None:
        """A PM should not be promotable to CEO (would create second CEO)."""
        with pytest.raises((InvalidHierarchy, RegistryError)):
            sample_org.update_profile("pm-alpha", role="ceo")
