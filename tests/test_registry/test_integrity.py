"""Tests for the hierarchy integrity checker (Task 4).

Tests ``scan_integrity()`` against various healthy and broken hierarchy states.
Some tests manipulate the DB directly (bypassing ProfileRegistry validation)
to create intentionally corrupt data.
"""

from __future__ import annotations

import sqlite3

import pytest

from core.registry.integrity import (
    RULE_ARCHIVED_WITH_ACTIVE_DEPS,
    RULE_CIRCULAR_REFERENCE,
    RULE_CONFIG_PATH_MISSING,
    RULE_DEPT_HEAD_PARENT_CEO,
    RULE_EXACTLY_ONE_CEO,
    RULE_INVALID_PROFILE_NAME,
    RULE_ORPHANED_PROFILE,
    RULE_PM_PARENT_DEPT_HEAD,
    IntegrityIssue,
    Severity,
    scan_integrity,
)
from core.registry.models import Role, Status
from core.registry.profile_registry import ProfileRegistry


# ---------------------------------------------------------------------------
# Helpers — raw SQL injection to create deliberately corrupt data
# ---------------------------------------------------------------------------


def _raw_insert(
    registry: ProfileRegistry,
    name: str,
    role: str,
    parent: str | None,
    status: str = "active",
    config_path: str | None = None,
) -> None:
    """Insert a profile row directly into the DB, bypassing all validation.

    Temporarily disables foreign-key enforcement so we can create
    intentionally corrupt data for testing.
    """
    with registry._cursor(commit=True) as cur:
        cur.execute("PRAGMA foreign_keys = OFF")
        cur.execute(
            """
            INSERT INTO profiles
                (profile_name, display_name, role, parent_profile,
                 department, status, created_at, updated_at,
                 config_path, description)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'), ?, ?)
            """,
            (name, name, role, parent, None, status, config_path, None),
        )
        cur.execute("PRAGMA foreign_keys = ON")


def _raw_update(
    registry: ProfileRegistry,
    name: str,
    **fields: str | None,
) -> None:
    """Update fields directly in the DB, bypassing validation.

    Temporarily disables foreign-key enforcement so we can create
    intentionally corrupt data for testing.
    """
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [name]
    with registry._cursor(commit=True) as cur:
        cur.execute("PRAGMA foreign_keys = OFF")
        cur.execute(
            f"UPDATE profiles SET {set_clause} WHERE profile_name = ?",
            values,
        )
        cur.execute("PRAGMA foreign_keys = ON")


def _raw_delete(registry: ProfileRegistry, name: str) -> None:
    """Hard-delete a profile row from the DB."""
    with registry._cursor(commit=True) as cur:
        cur.execute("PRAGMA foreign_keys = OFF")
        cur.execute("DELETE FROM profiles WHERE profile_name = ?", (name,))
        cur.execute("PRAGMA foreign_keys = ON")


def _issues_by_rule(
    issues: list[IntegrityIssue], rule: str
) -> list[IntegrityIssue]:
    """Filter issues to those matching a specific rule_violated."""
    return [i for i in issues if i.rule_violated == rule]


# ---------------------------------------------------------------------------
# Tests — healthy hierarchy
# ---------------------------------------------------------------------------


class TestHealthyHierarchy:
    """A well-formed hierarchy should produce zero issues."""

    def test_fresh_registry_is_clean(self, registry: ProfileRegistry) -> None:
        """A freshly initialized registry (CEO only) should have no issues."""
        issues = scan_integrity(registry)
        assert issues == []

    def test_sample_org_is_clean(self, sample_org: ProfileRegistry) -> None:
        """The standard sample org should have no issues."""
        issues = scan_integrity(sample_org)
        assert issues == []

    def test_archived_pm_is_clean(self, sample_org: ProfileRegistry) -> None:
        """Archiving a leaf PM should not create integrity issues."""
        sample_org.delete_profile("pm-alpha")
        issues = scan_integrity(sample_org)
        assert issues == []


# ---------------------------------------------------------------------------
# Tests — exactly one CEO
# ---------------------------------------------------------------------------


class TestExactlyOneCEO:
    """Rule: exactly one non-archived CEO must exist."""

    def test_no_ceo_detected(self, registry: ProfileRegistry) -> None:
        """If the CEO is removed, scan should report an error."""
        _raw_delete(registry, "hermes")
        issues = scan_integrity(registry)
        ceo_issues = _issues_by_rule(issues, RULE_EXACTLY_ONE_CEO)
        assert len(ceo_issues) == 1
        assert ceo_issues[0].severity == Severity.ERROR.value
        assert "No active CEO" in ceo_issues[0].message

    def test_multiple_ceos_detected(self, registry: ProfileRegistry) -> None:
        """If two CEOs exist, scan should report an error."""
        # Bypass validation to insert a second CEO
        _raw_insert(registry, "second-ceo", Role.CEO.value, None)
        issues = scan_integrity(registry)
        ceo_issues = _issues_by_rule(issues, RULE_EXACTLY_ONE_CEO)
        assert len(ceo_issues) == 1
        assert ceo_issues[0].severity == Severity.ERROR.value
        assert "Multiple CEO" in ceo_issues[0].message

    def test_archived_ceo_not_counted(self, registry: ProfileRegistry) -> None:
        """An archived CEO should not count toward the one-CEO rule."""
        # Create a second CEO, then archive the original
        _raw_insert(registry, "new-ceo", Role.CEO.value, None)
        _raw_update(registry, "hermes", status=Status.ARCHIVED.value)
        issues = scan_integrity(registry)
        ceo_issues = _issues_by_rule(issues, RULE_EXACTLY_ONE_CEO)
        assert len(ceo_issues) == 0


# ---------------------------------------------------------------------------
# Tests — dept heads must parent to CEO
# ---------------------------------------------------------------------------


class TestDeptHeadParentCEO:
    """Rule: all non-archived department heads must report to the CEO."""

    def test_dept_head_parenting_to_pm(self, sample_org: ProfileRegistry) -> None:
        """A dept head whose parent is a PM should trigger an error."""
        _raw_update(sample_org, "cto", parent_profile="pm-alpha")
        issues = scan_integrity(sample_org)
        dh_issues = _issues_by_rule(issues, RULE_DEPT_HEAD_PARENT_CEO)
        assert len(dh_issues) >= 1
        names = [i.profile_name for i in dh_issues]
        assert "cto" in names

    def test_dept_head_no_parent(self, sample_org: ProfileRegistry) -> None:
        """A dept head with no parent should trigger an error."""
        _raw_update(sample_org, "cto", parent_profile=None)
        issues = scan_integrity(sample_org)
        dh_issues = _issues_by_rule(issues, RULE_DEPT_HEAD_PARENT_CEO)
        assert any(i.profile_name == "cto" for i in dh_issues)

    def test_archived_dept_head_ignored(self, sample_org: ProfileRegistry) -> None:
        """Archived dept heads are not checked for parent rules."""
        sample_org.delete_profile("pm-alpha")
        sample_org.delete_profile("pm-beta")
        sample_org.delete_profile("cto")
        # The archived CTO has no issue (it's archived)
        issues = scan_integrity(sample_org)
        dh_issues = _issues_by_rule(issues, RULE_DEPT_HEAD_PARENT_CEO)
        assert not any(i.profile_name == "cto" for i in dh_issues)


# ---------------------------------------------------------------------------
# Tests — PMs must parent to dept head
# ---------------------------------------------------------------------------


class TestPMParentDeptHead:
    """Rule: all non-archived PMs must report to the CEO or a department head."""

    def test_pm_parenting_to_ceo(self, sample_org: ProfileRegistry) -> None:
        """A PM whose parent is the CEO is valid — no error expected."""
        _raw_update(sample_org, "pm-alpha", parent_profile="hermes")
        issues = scan_integrity(sample_org)
        pm_issues = _issues_by_rule(issues, RULE_PM_PARENT_DEPT_HEAD)
        assert not any(i.profile_name == "pm-alpha" for i in pm_issues)

    def test_pm_parenting_to_pm(self, sample_org: ProfileRegistry) -> None:
        """A PM whose parent is another PM should trigger an error."""
        _raw_update(sample_org, "pm-alpha", parent_profile="pm-beta")
        issues = scan_integrity(sample_org)
        pm_issues = _issues_by_rule(issues, RULE_PM_PARENT_DEPT_HEAD)
        assert any(i.profile_name == "pm-alpha" for i in pm_issues)

    def test_pm_no_parent(self, sample_org: ProfileRegistry) -> None:
        """A PM with no parent should trigger an error."""
        _raw_update(sample_org, "pm-alpha", parent_profile=None)
        issues = scan_integrity(sample_org)
        pm_issues = _issues_by_rule(issues, RULE_PM_PARENT_DEPT_HEAD)
        assert any(i.profile_name == "pm-alpha" for i in pm_issues)


# ---------------------------------------------------------------------------
# Tests — orphaned profiles
# ---------------------------------------------------------------------------


class TestOrphanedProfiles:
    """Rule: no profile should reference a parent that doesn't exist."""

    def test_orphaned_profile_detected(self, sample_org: ProfileRegistry) -> None:
        """A profile pointing to a non-existent parent should be flagged."""
        _raw_update(sample_org, "pm-alpha", parent_profile="ghost-parent")
        issues = scan_integrity(sample_org)
        orphan_issues = _issues_by_rule(issues, RULE_ORPHANED_PROFILE)
        assert any(i.profile_name == "pm-alpha" for i in orphan_issues)

    def test_ceo_null_parent_not_orphaned(self, registry: ProfileRegistry) -> None:
        """The CEO with parent=None is NOT orphaned."""
        issues = scan_integrity(registry)
        orphan_issues = _issues_by_rule(issues, RULE_ORPHANED_PROFILE)
        assert len(orphan_issues) == 0


# ---------------------------------------------------------------------------
# Tests — circular references
# ---------------------------------------------------------------------------


class TestCircularReferences:
    """Rule: no profile may be its own ancestor."""

    def test_self_loop_detected(self, registry: ProfileRegistry) -> None:
        """A profile pointing to itself should trigger a circular ref error."""
        registry.create_profile(
            name="cto", role="department_head", parent="hermes"
        )
        _raw_update(registry, "cto", parent_profile="cto")
        issues = scan_integrity(registry)
        circ_issues = _issues_by_rule(issues, RULE_CIRCULAR_REFERENCE)
        assert len(circ_issues) >= 1

    def test_two_node_cycle_detected(self, sample_org: ProfileRegistry) -> None:
        """A two-node cycle (A -> B -> A) should be detected."""
        _raw_update(sample_org, "cto", parent_profile="pm-alpha")
        _raw_update(sample_org, "pm-alpha", parent_profile="cto")
        issues = scan_integrity(sample_org)
        circ_issues = _issues_by_rule(issues, RULE_CIRCULAR_REFERENCE)
        assert len(circ_issues) >= 1

    def test_no_circular_in_valid_hierarchy(self, sample_org: ProfileRegistry) -> None:
        """A valid hierarchy should have no circular references."""
        issues = scan_integrity(sample_org)
        circ_issues = _issues_by_rule(issues, RULE_CIRCULAR_REFERENCE)
        assert len(circ_issues) == 0


# ---------------------------------------------------------------------------
# Tests — archived with active dependents
# ---------------------------------------------------------------------------


class TestArchivedWithActiveDependents:
    """Rule: an archived profile must not have active (non-archived) dependents."""

    def test_archived_with_active_child(self, sample_org: ProfileRegistry) -> None:
        """Archiving a dept head while a PM is still active should be flagged."""
        # Bypass validation to archive the dept head directly
        _raw_update(sample_org, "cto", status=Status.ARCHIVED.value)
        issues = scan_integrity(sample_org)
        dep_issues = _issues_by_rule(issues, RULE_ARCHIVED_WITH_ACTIVE_DEPS)
        assert any(i.profile_name == "cto" for i in dep_issues)

    def test_archived_with_all_children_archived(self, sample_org: ProfileRegistry) -> None:
        """If all children are also archived, no issue should be reported."""
        sample_org.delete_profile("pm-alpha")
        sample_org.delete_profile("pm-beta")
        sample_org.delete_profile("cto")
        issues = scan_integrity(sample_org)
        dep_issues = _issues_by_rule(issues, RULE_ARCHIVED_WITH_ACTIVE_DEPS)
        assert not any(i.profile_name == "cto" for i in dep_issues)


# ---------------------------------------------------------------------------
# Tests — profile name validation
# ---------------------------------------------------------------------------


class TestProfileNameValidation:
    """Rule: all profile names must match the naming convention."""

    def test_invalid_name_detected(self, registry: ProfileRegistry) -> None:
        """A profile with an uppercase name should be flagged."""
        _raw_insert(registry, "BAD-NAME", Role.DEPARTMENT_HEAD.value, "hermes")
        issues = scan_integrity(registry)
        name_issues = _issues_by_rule(issues, RULE_INVALID_PROFILE_NAME)
        assert any(i.profile_name == "BAD-NAME" for i in name_issues)

    def test_valid_names_pass(self, sample_org: ProfileRegistry) -> None:
        """Standard valid names should not trigger name issues."""
        issues = scan_integrity(sample_org)
        name_issues = _issues_by_rule(issues, RULE_INVALID_PROFILE_NAME)
        assert len(name_issues) == 0

    def test_name_with_special_chars(self, registry: ProfileRegistry) -> None:
        """A name with special characters should be flagged."""
        _raw_insert(registry, "bad@name!", Role.DEPARTMENT_HEAD.value, "hermes")
        issues = scan_integrity(registry)
        name_issues = _issues_by_rule(issues, RULE_INVALID_PROFILE_NAME)
        assert any(i.profile_name == "bad@name!" for i in name_issues)


# ---------------------------------------------------------------------------
# Tests — config path existence
# ---------------------------------------------------------------------------


class TestConfigPathExistence:
    """Rule: config paths, when set, should exist on disk (warning only)."""

    def test_missing_config_path_is_warning(self, registry: ProfileRegistry) -> None:
        """A non-existent config path should produce a warning, not an error."""
        registry.create_profile(
            name="cto",
            role="department_head",
            parent="hermes",
            config_path="/nonexistent/path/to/config",
        )
        issues = scan_integrity(registry)
        path_issues = _issues_by_rule(issues, RULE_CONFIG_PATH_MISSING)
        assert len(path_issues) == 1
        assert path_issues[0].severity == Severity.WARNING.value

    def test_no_config_path_no_warning(self, registry: ProfileRegistry) -> None:
        """A profile with no config_path should not trigger a warning."""
        registry.create_profile(
            name="cto",
            role="department_head",
            parent="hermes",
        )
        issues = scan_integrity(registry)
        path_issues = _issues_by_rule(issues, RULE_CONFIG_PATH_MISSING)
        assert len(path_issues) == 0

    def test_existing_config_path_no_warning(
        self, registry: ProfileRegistry, tmp_path
    ) -> None:
        """A config path that exists on disk should not trigger a warning."""
        config_dir = tmp_path / "profile-config"
        config_dir.mkdir()
        registry.create_profile(
            name="cto",
            role="department_head",
            parent="hermes",
            config_path=str(config_dir),
        )
        issues = scan_integrity(registry)
        path_issues = _issues_by_rule(issues, RULE_CONFIG_PATH_MISSING)
        assert len(path_issues) == 0


# ---------------------------------------------------------------------------
# Tests — issue sorting and structure
# ---------------------------------------------------------------------------


class TestIssueSorting:
    """Issues should be sorted by severity (errors first) then profile name."""

    def test_errors_before_warnings(self, registry: ProfileRegistry) -> None:
        """Errors should appear before warnings in the results."""
        # Create a profile with a missing config path (warning) and a bad name (error)
        _raw_insert(
            registry, "BAD", Role.DEPARTMENT_HEAD.value, "hermes",
            config_path="/nonexistent",
        )
        issues = scan_integrity(registry)
        assert len(issues) >= 2
        # Find the first warning and first error
        first_error_idx = next(
            (i for i, iss in enumerate(issues)
             if iss.severity == Severity.ERROR.value),
            None,
        )
        last_error_idx = None
        for i, iss in enumerate(issues):
            if iss.severity == Severity.ERROR.value:
                last_error_idx = i
        first_warning_idx = next(
            (i for i, iss in enumerate(issues)
             if iss.severity == Severity.WARNING.value),
            None,
        )
        if first_warning_idx is not None and last_error_idx is not None:
            assert last_error_idx < first_warning_idx

    def test_integrity_issue_dataclass_fields(self) -> None:
        """IntegrityIssue should have the expected fields."""
        issue = IntegrityIssue(
            severity="error",
            profile_name="test",
            message="test message",
            rule_violated="test_rule",
        )
        assert issue.severity == "error"
        assert issue.profile_name == "test"
        assert issue.message == "test message"
        assert issue.rule_violated == "test_rule"

    def test_integrity_issue_is_frozen(self) -> None:
        """IntegrityIssue should be immutable (frozen dataclass)."""
        issue = IntegrityIssue(
            severity="error",
            profile_name="test",
            message="test message",
            rule_violated="test_rule",
        )
        with pytest.raises(AttributeError):
            issue.severity = "warning"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Tests — multiple issues at once
# ---------------------------------------------------------------------------


class TestMultipleIssues:
    """The scanner should detect multiple issues in a single scan."""

    def test_multiple_issues_detected(self, registry: ProfileRegistry) -> None:
        """A badly corrupted DB should produce multiple issues."""
        # Insert profiles with various problems
        _raw_insert(registry, "BAD-HEAD", Role.DEPARTMENT_HEAD.value, "ghost")
        # Insert a real dept head so "bad-pm" can have it as parent-of-parent,
        # then insert bad-pm with another PM as parent (invalid — PMs can only
        # report to CEO or dept head).
        _raw_insert(registry, "good-head", Role.DEPARTMENT_HEAD.value, "hermes")
        _raw_insert(registry, "anchor-pm", Role.PROJECT_MANAGER.value, "good-head")
        _raw_insert(
            registry, "stale-pm", Role.PROJECT_MANAGER.value, "anchor-pm",
            config_path="/does/not/exist",
        )
        issues = scan_integrity(registry)
        # Should have at least: invalid name, orphaned profile, PM wrong parent,
        # config path missing
        rules_found = {i.rule_violated for i in issues}
        assert RULE_INVALID_PROFILE_NAME in rules_found
        assert RULE_ORPHANED_PROFILE in rules_found
        assert RULE_PM_PARENT_DEPT_HEAD in rules_found
        assert RULE_CONFIG_PATH_MISSING in rules_found
