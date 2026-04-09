"""Tests for ProfileRegistry CRUD operations.

Tests are written against the spec in docs/phase1-implementation-plan.md.
The ProfileRegistry class is expected at core.registry.profile_registry.ProfileRegistry.
"""

from __future__ import annotations

import pytest

from core.registry.exceptions import (
    DuplicateProfile,
    InvalidProfileName,
    ProfileNotFound,
)
from core.registry.models import Profile, Role, Status
from core.registry.profile_registry import ProfileRegistry


class TestCreateProfile:
    """Tests for create_profile()."""

    def test_create_department_head(self, registry: ProfileRegistry) -> None:
        """Creating a department_head under the CEO should succeed.

        New non-CEO profiles start in 'onboarding' status by default.
        """
        profile = registry.create_profile(
            name="cto",
            role="department_head",
            parent="hermes",
            department="engineering",
            description="CTO",
        )
        assert profile.profile_name == "cto"
        assert profile.role == Role.DEPARTMENT_HEAD.value
        assert profile.parent_profile == "hermes"
        assert profile.department == "engineering"
        # New profiles start in onboarding status, not active
        assert profile.status == Status.ONBOARDING.value

    def test_create_project_manager(self, registry: ProfileRegistry) -> None:
        """Creating a project_manager under a dept head should succeed."""
        registry.create_profile(
            name="cto",
            role="department_head",
            parent="hermes",
            department="engineering",
        )
        profile = registry.create_profile(
            name="pm-one",
            role="project_manager",
            parent="cto",
            department="engineering",
            description="First PM",
        )
        assert profile.profile_name == "pm-one"
        assert profile.role == Role.PROJECT_MANAGER.value
        assert profile.parent_profile == "cto"

    def test_create_project_manager_under_ceo(self, registry: ProfileRegistry) -> None:
        """Creating a project_manager directly under the CEO should succeed."""
        profile = registry.create_profile(
            name="pm-direct",
            role="project_manager",
            parent="hermes",
            department="engineering",
            description="Direct PM",
        )
        assert profile.profile_name == "pm-direct"
        assert profile.role == Role.PROJECT_MANAGER.value
        assert profile.parent_profile == "hermes"

    def test_create_profile_returns_profile_dataclass(self, registry: ProfileRegistry) -> None:
        """create_profile should return a Profile instance."""
        profile = registry.create_profile(
            name="cto",
            role="department_head",
            parent="hermes",
            department="engineering",
        )
        assert isinstance(profile, Profile)

    def test_create_profile_sets_timestamps(self, registry: ProfileRegistry) -> None:
        """New profiles should have created_at and updated_at set."""
        profile = registry.create_profile(
            name="cto",
            role="department_head",
            parent="hermes",
            department="engineering",
        )
        assert profile.created_at is not None
        assert profile.updated_at is not None

    def test_create_profile_with_config_path(self, registry: ProfileRegistry) -> None:
        """config_path should be stored when provided."""
        profile = registry.create_profile(
            name="cto",
            role="department_head",
            parent="hermes",
            department="engineering",
            config_path="/etc/profiles/cto",
        )
        assert profile.config_path == "/etc/profiles/cto"

    def test_create_all_roles(self, sample_org: ProfileRegistry) -> None:
        """The sample_org fixture should create profiles across all roles."""
        ceo = sample_org.get_profile("hermes")
        assert ceo.role == Role.CEO.value

        cto = sample_org.get_profile("cto")
        assert cto.role == Role.DEPARTMENT_HEAD.value

        pm = sample_org.get_profile("pm-alpha")
        assert pm.role == Role.PROJECT_MANAGER.value


class TestGetProfile:
    """Tests for get_profile()."""

    def test_get_existing_profile(self, sample_org: ProfileRegistry) -> None:
        """get_profile should return the correct profile."""
        profile = sample_org.get_profile("cto")
        assert profile.profile_name == "cto"
        assert profile.display_name is not None

    def test_get_nonexistent_profile_raises(self, registry: ProfileRegistry) -> None:
        """get_profile should raise ProfileNotFound for unknown names."""
        with pytest.raises(ProfileNotFound):
            registry.get_profile("does-not-exist")

    def test_get_ceo_profile(self, registry: ProfileRegistry) -> None:
        """The auto-created CEO should be retrievable."""
        ceo = registry.get_profile("hermes")
        assert ceo.role == Role.CEO.value
        assert ceo.parent_profile is None


class TestUpdateProfile:
    """Tests for update_profile()."""

    def test_update_description(self, sample_org: ProfileRegistry) -> None:
        """Updating the description field should persist."""
        sample_org.update_profile("cto", description="Updated CTO description")
        profile = sample_org.get_profile("cto")
        assert profile.description == "Updated CTO description"

    def test_update_display_name(self, sample_org: ProfileRegistry) -> None:
        """Updating display_name should persist."""
        sample_org.update_profile("cto", display_name="Chief Tech Officer")
        profile = sample_org.get_profile("cto")
        assert profile.display_name == "Chief Tech Officer"

    def test_update_department(self, sample_org: ProfileRegistry) -> None:
        """Updating department should persist."""
        sample_org.update_profile("cto", department="technology")
        profile = sample_org.get_profile("cto")
        assert profile.department == "technology"

    def test_update_nonexistent_raises(self, registry: ProfileRegistry) -> None:
        """Updating a non-existent profile should raise ProfileNotFound."""
        with pytest.raises(ProfileNotFound):
            registry.update_profile("ghost", description="boo")

    def test_update_sets_updated_at(self, sample_org: ProfileRegistry) -> None:
        """update_profile should refresh the updated_at timestamp."""
        before = sample_org.get_profile("cto")
        sample_org.update_profile("cto", description="new desc")
        after = sample_org.get_profile("cto")
        # updated_at should be >= the original (may be equal if very fast)
        assert after.updated_at >= before.updated_at


class TestDeleteProfile:
    """Tests for delete_profile() — soft delete (archive)."""

    def test_soft_delete_sets_archived(self, registry: ProfileRegistry) -> None:
        """delete_profile should set status to 'archived'."""
        registry.create_profile(
            name="cto",
            role="department_head",
            parent="hermes",
            department="engineering",
        )
        registry.delete_profile("cto")
        profile = registry.get_profile("cto")
        assert profile.status == Status.ARCHIVED.value

    def test_delete_nonexistent_raises(self, registry: ProfileRegistry) -> None:
        """Deleting a non-existent profile should raise ProfileNotFound."""
        with pytest.raises(ProfileNotFound):
            registry.delete_profile("ghost")

    def test_archived_profile_still_retrievable(self, registry: ProfileRegistry) -> None:
        """Archived profiles should still be returned by get_profile."""
        registry.create_profile(
            name="cto",
            role="department_head",
            parent="hermes",
            department="engineering",
        )
        registry.delete_profile("cto")
        profile = registry.get_profile("cto")
        assert profile.profile_name == "cto"


class TestListProfiles:
    """Tests for list_profiles() with filtering."""

    def test_list_all(self, sample_org: ProfileRegistry) -> None:
        """list_profiles() with no filters returns all profiles."""
        profiles = sample_org.list_profiles()
        # 1 CEO + 2 dept heads + 3 PMs = 6
        assert len(profiles) == 6

    def test_filter_by_role(self, sample_org: ProfileRegistry) -> None:
        """list_profiles(role='project_manager') returns only PMs."""
        pms = sample_org.list_profiles(role="project_manager")
        assert len(pms) == 3
        assert all(p.role == Role.PROJECT_MANAGER.value for p in pms)

    def test_filter_by_department(self, sample_org: ProfileRegistry) -> None:
        """list_profiles(department='engineering') returns engineering profiles."""
        eng = sample_org.list_profiles(department="engineering")
        # CTO + pm-alpha + pm-beta = 3
        assert len(eng) == 3

    def test_filter_by_status(self, sample_org: ProfileRegistry) -> None:
        """list_profiles(status='active') returns only active profiles."""
        active = sample_org.list_profiles(status="active")
        assert len(active) == 6

    def test_filter_by_status_after_archive(self, sample_org: ProfileRegistry) -> None:
        """After archiving a profile, it should not appear in active filter."""
        sample_org.delete_profile("pm-gamma")
        active = sample_org.list_profiles(status="active")
        names = [p.profile_name for p in active]
        assert "pm-gamma" not in names

    def test_filter_combined(self, sample_org: ProfileRegistry) -> None:
        """Multiple filters should be ANDed together."""
        eng_pms = sample_org.list_profiles(
            role="project_manager", department="engineering"
        )
        assert len(eng_pms) == 2

    def test_list_returns_profile_instances(self, sample_org: ProfileRegistry) -> None:
        """list_profiles should return a list of Profile instances."""
        profiles = sample_org.list_profiles()
        assert all(isinstance(p, Profile) for p in profiles)

    def test_list_empty_result(self, sample_org: ProfileRegistry) -> None:
        """Filtering for a non-existent department returns empty list."""
        result = sample_org.list_profiles(department="nonexistent")
        assert result == []

    def test_filter_by_role_ceo(self, sample_org: ProfileRegistry) -> None:
        """Filtering by role='ceo' should return exactly one profile."""
        ceos = sample_org.list_profiles(role="ceo")
        assert len(ceos) == 1
        assert ceos[0].profile_name == "hermes"

    def test_filter_by_role_department_head(self, sample_org: ProfileRegistry) -> None:
        """Filtering by role='department_head' returns the dept heads."""
        heads = sample_org.list_profiles(role="department_head")
        assert len(heads) == 2


class TestDuplicateRejected:
    """Duplicate profile names should be rejected."""

    def test_duplicate_name_raises(self, registry: ProfileRegistry) -> None:
        """Creating two profiles with the same name should raise DuplicateProfile."""
        registry.create_profile(
            name="cto",
            role="department_head",
            parent="hermes",
            department="engineering",
        )
        with pytest.raises(DuplicateProfile):
            registry.create_profile(
                name="cto",
                role="department_head",
                parent="hermes",
                department="engineering",
            )


class TestInvalidNameRejected:
    """Profile names violating naming rules should be rejected."""

    def test_empty_name(self, registry: ProfileRegistry) -> None:
        with pytest.raises(InvalidProfileName):
            registry.create_profile(
                name="",
                role="department_head",
                parent="hermes",
                department="engineering",
            )

    def test_uppercase_name(self, registry: ProfileRegistry) -> None:
        with pytest.raises(InvalidProfileName):
            registry.create_profile(
                name="CTO",
                role="department_head",
                parent="hermes",
                department="engineering",
            )

    def test_name_with_spaces(self, registry: ProfileRegistry) -> None:
        with pytest.raises(InvalidProfileName):
            registry.create_profile(
                name="my profile",
                role="department_head",
                parent="hermes",
                department="engineering",
            )

    def test_name_starting_with_number(self, registry: ProfileRegistry) -> None:
        with pytest.raises(InvalidProfileName):
            registry.create_profile(
                name="1cto",
                role="department_head",
                parent="hermes",
                department="engineering",
            )

    def test_name_starting_with_hyphen(self, registry: ProfileRegistry) -> None:
        with pytest.raises(InvalidProfileName):
            registry.create_profile(
                name="-cto",
                role="department_head",
                parent="hermes",
                department="engineering",
            )

    def test_name_too_long(self, registry: ProfileRegistry) -> None:
        with pytest.raises(InvalidProfileName):
            registry.create_profile(
                name="a" * 65,
                role="department_head",
                parent="hermes",
                department="engineering",
            )

    def test_name_with_special_chars(self, registry: ProfileRegistry) -> None:
        with pytest.raises(InvalidProfileName):
            registry.create_profile(
                name="cto@company",
                role="department_head",
                parent="hermes",
                department="engineering",
            )

    def test_name_with_underscores(self, registry: ProfileRegistry) -> None:
        """Underscores are not allowed in profile names (only hyphens)."""
        with pytest.raises(InvalidProfileName):
            registry.create_profile(
                name="cto_dept",
                role="department_head",
                parent="hermes",
                department="engineering",
            )


class TestSuspendActivate:
    """Tests for suspend() and activate() operations."""

    def test_suspend_profile(self, sample_org: ProfileRegistry) -> None:
        """suspend() should set status to 'suspended'."""
        sample_org.suspend("cto")
        profile = sample_org.get_profile("cto")
        assert profile.status == Status.SUSPENDED.value

    def test_activate_suspended_profile(self, sample_org: ProfileRegistry) -> None:
        """activate() should restore status to 'active'."""
        sample_org.suspend("cto")
        sample_org.activate("cto")
        profile = sample_org.get_profile("cto")
        assert profile.status == Status.ACTIVE.value

    def test_suspend_nonexistent_raises(self, registry: ProfileRegistry) -> None:
        with pytest.raises(ProfileNotFound):
            registry.suspend("ghost")

    def test_activate_nonexistent_raises(self, registry: ProfileRegistry) -> None:
        with pytest.raises(ProfileNotFound):
            registry.activate("ghost")
