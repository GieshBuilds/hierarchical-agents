"""ProfileRegistry — CRUD and hierarchy operations for agent profiles."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

import json

from core.registry.exceptions import (
    DuplicateProfile,
    InvalidHierarchy,
    OnboardingIncomplete,
    OnboardingRequired,
    ProfileNotFound,
    RegistryError,
)
from core.registry.models import (
    ONBOARDING_REQUIRED_FIELDS,
    OnboardingBrief,
    Profile,
    Role,
    Status,
    validate_profile_name,
)
from core.registry.schema import init_db


class ProfileRegistry:
    """Thread-safe profile registry backed by SQLite.

    Parameters
    ----------
    db_path:
        Filesystem path to the SQLite database, or ``":memory:"`` for an
        in-memory database.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        # Open / initialise the database.
        self._conn = init_db(db_path)
        # Bootstrap the hierarchy with a default CEO if one doesn't exist.
        self._ensure_ceo_exists()

    # ------------------------------------------------------------------
    # Context-manager helpers
    # ------------------------------------------------------------------

    @contextmanager
    def _cursor(self, *, commit: bool = False) -> Iterator[sqlite3.Cursor]:
        """Yield a cursor under the thread-lock.

        If *commit* is ``True`` the transaction is committed on success
        and rolled back on error.
        """
        with self._lock:
            cur = self._conn.cursor()
            try:
                yield cur
                if commit:
                    self._conn.commit()
            except Exception:
                if commit:
                    self._conn.rollback()
                raise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_profile(row: sqlite3.Row) -> Profile:
        """Convert a ``sqlite3.Row`` to a :class:`Profile`."""
        return Profile(
            profile_name=row["profile_name"],
            display_name=row["display_name"],
            role=row["role"],
            parent_profile=row["parent_profile"],
            department=row["department"],
            status=row["status"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            config_path=row["config_path"],
            description=row["description"],
        )

    def _ensure_ceo_exists(self) -> None:
        """Auto-create a default CEO profile when the registry is empty."""
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT 1 FROM profiles WHERE role = ? LIMIT 1",
                (Role.CEO.value,),
            ).fetchone()
        if row is None:
            self.create_profile(
                name="hermes",
                display_name="Hermes",
                role=Role.CEO.value,
                parent=None,
                department="executive",
                description="Default CEO profile (auto-created)",
                _skip_onboarding=True,
            )

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _validate_hierarchy(
        self,
        role: str,
        parent: str | None,
        *,
        exclude_name: str | None = None,
    ) -> None:
        """Enforce hierarchy constraints for *role* / *parent* combination.

        Parameters
        ----------
        role:
            The role of the profile being created or reassigned.
        parent:
            The ``profile_name`` of the intended parent (``None`` for CEO).
        exclude_name:
            If provided, this profile is excluded from duplicate-CEO checks
            (used during updates).
        """
        role_enum = Role(role)

        if role_enum is Role.CEO:
            if parent is not None:
                raise InvalidHierarchy("CEO must not have a parent profile")
            # Only one CEO allowed.
            with self._cursor() as cur:
                row = cur.execute(
                    "SELECT profile_name FROM profiles WHERE role = ? AND status != ?",
                    (Role.CEO.value, Status.ARCHIVED.value),
                ).fetchone()
            if row is not None and (
                exclude_name is None or row["profile_name"] != exclude_name
            ):
                raise InvalidHierarchy(
                    "Only one CEO profile is allowed — "
                    f"existing CEO is '{row['profile_name']}'"
                )
            return

        # Non-CEO profiles MUST have a parent.
        if parent is None:
            raise InvalidHierarchy(f"Role '{role}' requires a parent profile")

        # Fetch the parent profile — it must exist.
        parent_profile = self._get_profile_row(parent)
        if parent_profile is None:
            raise ProfileNotFound(parent)

    def _check_circular(self, profile_name: str, new_parent: str) -> None:
        """Raise ``InvalidHierarchy`` if assigning *new_parent* would create a cycle."""
        visited: set[str] = {profile_name}
        current: str | None = new_parent
        while current is not None:
            if current in visited:
                raise InvalidHierarchy(
                    f"Circular reference detected: assigning '{new_parent}' as "
                    f"parent of '{profile_name}' creates a cycle"
                )
            visited.add(current)
            row = self._get_profile_row(current)
            if row is None:
                break
            current = row["parent_profile"]

    def _get_profile_row(self, name: str) -> sqlite3.Row | None:
        """Return the raw DB row for *name*, or ``None``."""
        with self._cursor() as cur:
            return cur.execute(
                "SELECT * FROM profiles WHERE profile_name = ?", (name,)
            ).fetchone()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def create_profile(
        self,
        name: str,
        display_name: str | None = None,
        role: str = "department_head",
        parent: str | None = None,
        department: str | None = None,
        description: str | None = None,
        config_path: str | None = None,
        *,
        _skip_onboarding: bool = False,
    ) -> Profile:
        """Create a new profile and return it.

        Non-CEO profiles are created with ``status='onboarding'`` by default.
        They remain in that status until the parent PM submits a completed
        :class:`~core.registry.models.OnboardingBrief` via
        :meth:`submit_onboarding_brief`.

        Pass ``_skip_onboarding=True`` only for bootstrap / test scenarios
        where the onboarding gate should be bypassed (e.g. the CEO
        auto-creation or fixtures that pre-populate the org).

        Raises
        ------
        InvalidProfileName
            If *name* does not satisfy the naming convention.
        DuplicateProfile
            If a profile with *name* already exists.
        InvalidHierarchy
            If the role/parent combination violates hierarchy rules.
        """
        validate_profile_name(name)
        role_enum = Role(role)  # validates the role value

        # Default display_name to the profile name if not provided.
        if display_name is None:
            display_name = name
        if not isinstance(display_name, str) or not display_name.strip():
            raise RegistryError("display_name must be a non-empty string")

        # Check duplicate.
        if self._get_profile_row(name) is not None:
            raise DuplicateProfile(name)

        self._validate_hierarchy(role, parent)

        # CEO profiles are always active; all others start in onboarding
        # unless the caller explicitly bypasses the gate.
        if role_enum is Role.CEO or _skip_onboarding:
            initial_status = Status.ACTIVE.value
        else:
            initial_status = Status.ONBOARDING.value

        now = datetime.now(timezone.utc).isoformat()

        with self._cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT INTO profiles
                    (profile_name, display_name, role, parent_profile,
                     department, status, created_at, updated_at,
                     config_path, description)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    name,
                    display_name,
                    role,
                    parent,
                    department,
                    initial_status,
                    now,
                    now,
                    config_path,
                    description,
                ),
            )

        return self.get_profile(name)

    def get_profile(self, name: str) -> Profile:
        """Return the profile for *name*.

        Raises
        ------
        ProfileNotFound
            If no profile with *name* exists.
        """
        row = self._get_profile_row(name)
        if row is None:
            raise ProfileNotFound(name)
        return self._row_to_profile(row)

    # ------------------------------------------------------------------
    # Onboarding brief management
    # ------------------------------------------------------------------

    def submit_onboarding_brief(
        self,
        profile_name: str,
        parent_pm: str,
        role_definition: str,
        scope: str,
        success_criteria: str,
        handoff_protocol: str,
        discovery_answers: str = "",
        dependencies: str = "",
        first_task: str = "",
        extra: dict | None = None,
    ) -> OnboardingBrief:
        """Submit a completed onboarding brief for *profile_name*.

        Validates that all required fields are non-empty, persists the brief,
        and transitions the profile from ``onboarding`` → ``active``.

        Parameters
        ----------
        profile_name:
            The profile being onboarded.
        parent_pm:
            The profile submitting the brief (should be the parent PM/DH/CEO).
        role_definition:
            Clear statement of what this agent does.
        scope:
            What is in scope and, importantly, what is NOT in scope.
        success_criteria:
            Measurable definition of success for this agent.
        handoff_protocol:
            How completed work is returned upstream (format, channel, etc).
        discovery_answers:
            Free-form Q&A from the discovery interview.
        dependencies:
            Other profiles / systems this agent depends on.
        first_task:
            Concrete first task to confirm the agent is ready.
        extra:
            Any additional key-value context to store with the brief.

        Raises
        ------
        ProfileNotFound
            If *profile_name* does not exist.
        OnboardingIncomplete
            If any required field is empty.
        RegistryError
            If the profile is not in ``onboarding`` status (already active,
            archived, etc.).
        """
        existing = self._get_profile_row(profile_name)
        if existing is None:
            raise ProfileNotFound(profile_name)

        if existing["status"] not in (
            Status.ONBOARDING.value,
            Status.ACTIVE.value,  # allow re-submitting a brief for an active profile
        ):
            raise RegistryError(
                f"Profile '{profile_name}' has status '{existing['status']}'; "
                "onboarding brief can only be submitted for onboarding or active profiles."
            )

        # Validate required fields.
        field_values = {
            "role_definition": role_definition,
            "scope": scope,
            "success_criteria": success_criteria,
            "handoff_protocol": handoff_protocol,
        }
        missing = [k for k, v in field_values.items() if not v or not v.strip()]
        if missing:
            raise OnboardingIncomplete(profile_name, missing)

        now = datetime.now(timezone.utc).isoformat()
        extra_json = json.dumps(extra or {})

        with self._cursor(commit=True) as cur:
            cur.execute(
                """
                INSERT INTO onboarding_briefs
                    (profile_name, parent_pm, role_definition, scope,
                     success_criteria, handoff_protocol,
                     discovery_answers, dependencies, first_task,
                     submitted_at, extra_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(profile_name) DO UPDATE SET
                    parent_pm         = excluded.parent_pm,
                    role_definition   = excluded.role_definition,
                    scope             = excluded.scope,
                    success_criteria  = excluded.success_criteria,
                    handoff_protocol  = excluded.handoff_protocol,
                    discovery_answers = excluded.discovery_answers,
                    dependencies      = excluded.dependencies,
                    first_task        = excluded.first_task,
                    submitted_at      = excluded.submitted_at,
                    extra_json        = excluded.extra_json
                """,
                (
                    profile_name,
                    parent_pm,
                    role_definition.strip(),
                    scope.strip(),
                    success_criteria.strip(),
                    handoff_protocol.strip(),
                    (discovery_answers or "").strip(),
                    (dependencies or "").strip(),
                    (first_task or "").strip(),
                    now,
                    extra_json,
                ),
            )

        # Activate the profile if it was in onboarding.
        if existing["status"] == Status.ONBOARDING.value:
            self.update_profile(profile_name, status=Status.ACTIVE.value)

        return self.get_onboarding_brief(profile_name)

    def get_onboarding_brief(self, profile_name: str) -> OnboardingBrief:
        """Return the onboarding brief for *profile_name*.

        Raises
        ------
        ProfileNotFound
            If no brief exists for the given profile.
        """
        with self._cursor() as cur:
            row = cur.execute(
                "SELECT * FROM onboarding_briefs WHERE profile_name = ?",
                (profile_name,),
            ).fetchone()

        if row is None:
            raise ProfileNotFound(
                f"{profile_name} (no onboarding brief on record)"
            )

        return OnboardingBrief(
            profile_name=row["profile_name"],
            parent_pm=row["parent_pm"],
            role_definition=row["role_definition"],
            scope=row["scope"],
            success_criteria=row["success_criteria"],
            handoff_protocol=row["handoff_protocol"],
            discovery_answers=row["discovery_answers"],
            dependencies=row["dependencies"],
            first_task=row["first_task"],
            submitted_at=datetime.fromisoformat(row["submitted_at"]),
            extra=json.loads(row["extra_json"] or "{}"),
        )

    def list_onboarding_pending(self) -> list[Profile]:
        """Return all profiles currently in ``onboarding`` status."""
        return self.list_profiles(status=Status.ONBOARDING.value)

    def assert_profile_active(self, profile_name: str) -> Profile:
        """Return the profile if it is ``active``; raise otherwise.

        Raises
        ------
        ProfileNotFound
            If the profile does not exist.
        OnboardingRequired
            If the profile is still in ``onboarding`` status.
        RegistryError
            If the profile is ``suspended`` or ``archived``.
        """
        profile = self.get_profile(profile_name)
        if profile.status == Status.ONBOARDING.value:
            raise OnboardingRequired(profile_name)
        if profile.status != Status.ACTIVE.value:
            raise RegistryError(
                f"Profile '{profile_name}' has status '{profile.status}' "
                "and cannot perform active operations."
            )
        return profile

    def update_profile(self, name: str, **fields: Any) -> Profile:
        """Update mutable fields on an existing profile and return the result.

        Updatable fields: ``display_name``, ``department``, ``description``,
        ``config_path``, ``role``, ``parent_profile``, ``status``.

        Raises
        ------
        ProfileNotFound
            If *name* does not exist.
        InvalidHierarchy
            If changing role/parent would violate hierarchy rules.
        """
        existing = self._get_profile_row(name)
        if existing is None:
            raise ProfileNotFound(name)

        allowed = {
            "display_name",
            "department",
            "description",
            "config_path",
            "role",
            "parent_profile",
            "status",
        }
        to_update = {k: v for k, v in fields.items() if k in allowed}
        if not to_update:
            return self._row_to_profile(existing)

        # Validate display_name if being updated.
        if "display_name" in to_update:
            dn = to_update["display_name"]
            if not isinstance(dn, str) or not dn.strip():
                raise RegistryError("display_name must be a non-empty string")

        # Validate status if being updated.
        if "status" in to_update:
            Status(to_update["status"])  # raises ValueError for invalid

        # If role or parent_profile is changing, re-validate hierarchy.
        new_role = to_update.get("role", existing["role"])
        new_parent = to_update.get("parent_profile", existing["parent_profile"])
        if "role" in to_update or "parent_profile" in to_update:
            Role(new_role)  # validate
            self._validate_hierarchy(new_role, new_parent, exclude_name=name)
            if new_parent is not None:
                self._check_circular(name, new_parent)

        to_update["updated_at"] = datetime.now(timezone.utc).isoformat()

        set_clause = ", ".join(f"{col} = ?" for col in to_update)
        values = list(to_update.values()) + [name]

        with self._cursor(commit=True) as cur:
            cur.execute(
                f"UPDATE profiles SET {set_clause} WHERE profile_name = ?",
                values,
            )

        return self.get_profile(name)

    def delete_profile(self, name: str) -> None:
        """Soft-delete (archive) a profile.

        Raises
        ------
        ProfileNotFound
            If *name* does not exist.
        InvalidHierarchy
            If the profile has active dependents that haven't been
            reassigned or archived.
        """
        existing = self._get_profile_row(name)
        if existing is None:
            raise ProfileNotFound(name)

        # Check for active dependents.
        with self._cursor() as cur:
            deps = cur.execute(
                "SELECT profile_name FROM profiles "
                "WHERE parent_profile = ? AND status != ?",
                (name, Status.ARCHIVED.value),
            ).fetchall()
        if deps:
            dep_names = [d["profile_name"] for d in deps]
            raise InvalidHierarchy(
                f"Cannot archive '{name}': has active dependents — "
                f"{dep_names}"
            )

        self.update_profile(name, status=Status.ARCHIVED.value)

    def list_profiles(
        self,
        role: str | None = None,
        department: str | None = None,
        status: str | None = None,
    ) -> list[Profile]:
        """Return profiles matching the optional filters."""
        clauses: list[str] = []
        params: list[str] = []

        if role is not None:
            clauses.append("role = ?")
            params.append(role)
        if department is not None:
            clauses.append("department = ?")
            params.append(department)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)

        sql = "SELECT * FROM profiles"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY profile_name"

        with self._cursor() as cur:
            rows = cur.execute(sql, params).fetchall()
        return [self._row_to_profile(r) for r in rows]

    # ------------------------------------------------------------------
    # Hierarchy operations
    # ------------------------------------------------------------------

    def list_reports(self, profile_name: str) -> list[Profile]:
        """Return the direct reports of *profile_name*."""
        # Ensure the profile exists.
        self.get_profile(profile_name)

        with self._cursor() as cur:
            rows = cur.execute(
                "SELECT * FROM profiles WHERE parent_profile = ? AND status != ? "
                "ORDER BY profile_name",
                (profile_name, Status.ARCHIVED.value),
            ).fetchall()
        return [self._row_to_profile(r) for r in rows]

    def get_chain_of_command(self, profile_name: str) -> list[Profile]:
        """Return the chain from *profile_name* up to the CEO (inclusive)."""
        # Ensure the starting profile exists.
        self.get_profile(profile_name)

        chain: list[Profile] = []
        visited: set[str] = set()
        current: str | None = profile_name

        while current is not None:
            if current in visited:
                break  # safety: don't loop on corrupted data
            visited.add(current)
            row = self._get_profile_row(current)
            if row is None:
                break
            chain.append(self._row_to_profile(row))
            current = row["parent_profile"]

        return chain

    def reassign(self, profile_name: str, new_parent: str) -> Profile:
        """Move *profile_name* under *new_parent*, validating the hierarchy.

        Raises
        ------
        InvalidHierarchy
            If the new parent's role is inappropriate or would create a cycle,
            or if the profile is archived.
        """
        existing = self._get_profile_row(profile_name)
        if existing is None:
            raise ProfileNotFound(profile_name)

        if existing["status"] == Status.ARCHIVED.value:
            raise InvalidHierarchy(
                f"Cannot reassign archived profile '{profile_name}'"
            )

        role = existing["role"]
        self._validate_hierarchy(role, new_parent, exclude_name=profile_name)
        self._check_circular(profile_name, new_parent)

        return self.update_profile(profile_name, parent_profile=new_parent)

    def get_org_tree(self, root: str | None = None) -> dict[str, Any]:
        """Return a nested dict representing the org chart.

        Each node has the form::

            {
                "name": "ceo",
                "display_name": "CEO",
                "role": "ceo",
                "status": "active",
                "children": [...]
            }

        Parameters
        ----------
        root:
            Start the tree at this profile.  Defaults to the CEO.
        """
        # Build a lookup and children map from the full profile list.
        all_profiles = self.list_profiles()
        by_name: dict[str, Profile] = {p.profile_name: p for p in all_profiles}
        children_map: dict[str | None, list[Profile]] = {}
        for p in all_profiles:
            children_map.setdefault(p.parent_profile, []).append(p)

        def _build(node_name: str) -> dict[str, Any]:
            p = by_name[node_name]
            kids = sorted(
                children_map.get(node_name, []),
                key=lambda c: c.profile_name,
            )
            return {
                "profile_name": p.profile_name,
                "display_name": p.display_name,
                "role": p.role,
                "status": p.status,
                "children": [_build(c.profile_name) for c in kids],
            }

        if root is not None:
            if root not in by_name:
                raise ProfileNotFound(root)
            return _build(root)

        # Find the CEO (root of the tree).
        ceo_profiles = [p for p in all_profiles if p.role == Role.CEO.value]
        if not ceo_profiles:
            return {"profile_name": None, "children": []}
        return _build(ceo_profiles[0].profile_name)

    # ------------------------------------------------------------------
    # Status helpers
    # ------------------------------------------------------------------

    def suspend(self, profile_name: str) -> Profile:
        """Set a profile's status to ``suspended``."""
        return self.update_profile(profile_name, status=Status.SUSPENDED.value)

    def activate(self, profile_name: str) -> Profile:
        """Set a profile's status to ``active``."""
        return self.update_profile(profile_name, status=Status.ACTIVE.value)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()
