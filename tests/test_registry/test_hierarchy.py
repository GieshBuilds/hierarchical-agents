"""Tests for hierarchy operations: chain of command, list reports, org tree.

Tests are written against the spec in docs/phase1-implementation-plan.md.
"""

from __future__ import annotations

import pytest

from core.registry.exceptions import ProfileNotFound
from core.registry.models import Profile, Role, Status
from core.registry.profile_registry import ProfileRegistry


class TestCEOAutoCreated:
    """On first init the registry should auto-create a CEO profile."""

    def test_ceo_exists_on_init(self, registry: ProfileRegistry) -> None:
        """A fresh registry should contain exactly one CEO profile."""
        ceo = registry.get_profile("hermes")
        assert ceo.role == Role.CEO.value
        assert ceo.status == Status.ACTIVE.value

    def test_ceo_has_no_parent(self, registry: ProfileRegistry) -> None:
        """The CEO profile should have parent_profile=None."""
        ceo = registry.get_profile("hermes")
        assert ceo.parent_profile is None

    def test_only_one_ceo_after_init(self, registry: ProfileRegistry) -> None:
        """list_profiles(role='ceo') should return exactly one entry."""
        ceos = registry.list_profiles(role="ceo")
        assert len(ceos) == 1


class TestChainOfCommand:
    """Tests for get_chain_of_command()."""

    def test_ceo_chain_is_self(self, registry: ProfileRegistry) -> None:
        """Chain of command for the CEO should contain only the CEO."""
        chain = registry.get_chain_of_command("hermes")
        assert len(chain) == 1
        assert chain[0].profile_name == "hermes"

    def test_dept_head_chain(self, sample_org: ProfileRegistry) -> None:
        """Chain for a dept head should be [dept_head, CEO]."""
        chain = sample_org.get_chain_of_command("cto")
        names = [p.profile_name for p in chain]
        assert names == ["cto", "hermes"]

    def test_pm_chain(self, sample_org: ProfileRegistry) -> None:
        """Chain for a PM should be [PM, dept_head, CEO]."""
        chain = sample_org.get_chain_of_command("pm-alpha")
        names = [p.profile_name for p in chain]
        assert names == ["pm-alpha", "cto", "hermes"]

    def test_chain_ends_at_ceo(self, sample_org: ProfileRegistry) -> None:
        """Every chain of command should end with the CEO."""
        chain = sample_org.get_chain_of_command("pm-gamma")
        assert chain[-1].role == Role.CEO.value

    def test_chain_nonexistent_raises(self, registry: ProfileRegistry) -> None:
        """get_chain_of_command for a non-existent profile should raise."""
        with pytest.raises(ProfileNotFound):
            registry.get_chain_of_command("ghost")


class TestListReports:
    """Tests for list_reports()."""

    def test_ceo_direct_reports(self, sample_org: ProfileRegistry) -> None:
        """CEO should have two dept heads as direct reports."""
        reports = sample_org.list_reports("hermes")
        names = sorted(p.profile_name for p in reports)
        assert names == ["cmo", "cto"]

    def test_dept_head_direct_reports(self, sample_org: ProfileRegistry) -> None:
        """CTO should have two PMs as direct reports."""
        reports = sample_org.list_reports("cto")
        names = sorted(p.profile_name for p in reports)
        assert names == ["pm-alpha", "pm-beta"]

    def test_dept_head_single_report(self, sample_org: ProfileRegistry) -> None:
        """CMO should have one PM as direct report."""
        reports = sample_org.list_reports("cmo")
        assert len(reports) == 1
        assert reports[0].profile_name == "pm-gamma"

    def test_pm_has_no_reports(self, sample_org: ProfileRegistry) -> None:
        """PMs should have no direct reports."""
        reports = sample_org.list_reports("pm-alpha")
        assert reports == []

    def test_list_reports_nonexistent_raises(self, registry: ProfileRegistry) -> None:
        """list_reports for a non-existent profile should raise."""
        with pytest.raises(ProfileNotFound):
            registry.list_reports("ghost")

    def test_list_reports_returns_profiles(self, sample_org: ProfileRegistry) -> None:
        """list_reports should return a list of Profile instances."""
        reports = sample_org.list_reports("hermes")
        assert all(isinstance(r, Profile) for r in reports)


class TestOrgTree:
    """Tests for get_org_tree()."""

    def test_full_org_tree(self, sample_org: ProfileRegistry) -> None:
        """get_org_tree() should return a nested structure rooted at CEO."""
        tree = sample_org.get_org_tree()
        # Root should be the CEO
        assert tree["profile_name"] == "hermes"
        assert tree["role"] == Role.CEO.value

    def test_org_tree_has_children(self, sample_org: ProfileRegistry) -> None:
        """The tree should contain children at each level."""
        tree = sample_org.get_org_tree()
        # CEO should have 2 children
        assert "children" in tree
        assert len(tree["children"]) == 2

    def test_org_tree_nested_structure(self, sample_org: ProfileRegistry) -> None:
        """Dept heads in the tree should have PM children."""
        tree = sample_org.get_org_tree()
        children = tree["children"]
        # Find the CTO subtree
        cto_tree = next(c for c in children if c["profile_name"] == "cto")
        assert len(cto_tree["children"]) == 2

    def test_org_tree_leaf_nodes(self, sample_org: ProfileRegistry) -> None:
        """PMs should be leaf nodes with empty children lists."""
        tree = sample_org.get_org_tree()
        children = tree["children"]
        cto_tree = next(c for c in children if c["profile_name"] == "cto")
        for pm in cto_tree["children"]:
            assert pm["children"] == [] or "children" not in pm or pm.get("children") == []

    def test_org_tree_from_subtree(self, sample_org: ProfileRegistry) -> None:
        """get_org_tree(root='cto') should return a subtree rooted at CTO."""
        tree = sample_org.get_org_tree(root="cto")
        assert tree["profile_name"] == "cto"
        assert len(tree["children"]) == 2

    def test_org_tree_single_node(self, sample_org: ProfileRegistry) -> None:
        """get_org_tree(root='pm-alpha') should return a leaf node."""
        tree = sample_org.get_org_tree(root="pm-alpha")
        assert tree["profile_name"] == "pm-alpha"
        assert tree.get("children", []) == []

    def test_org_tree_nonexistent_root_raises(self, registry: ProfileRegistry) -> None:
        """get_org_tree with an invalid root should raise ProfileNotFound."""
        with pytest.raises(ProfileNotFound):
            registry.get_org_tree(root="ghost")
