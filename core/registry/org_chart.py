"""Org chart text visualization for the Profile Registry.

Renders the organizational hierarchy as a tree using Unicode box-drawing
characters.  Stdlib only — no external dependencies.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from core.registry.profile_registry import ProfileRegistry


def render_org_chart(
    registry: ProfileRegistry,
    root: str | None = None,
    show_status: bool = True,
    active_only: bool = False,
) -> str:
    """Render the org chart as a Unicode tree string.

    Parameters
    ----------
    registry:
        A :class:`ProfileRegistry` instance to read profiles from.
    root:
        Start the tree at this profile name.  ``None`` starts from the CEO.
    show_status:
        If ``True`` (default), append ``[status]`` after each entry.
    active_only:
        If ``True``, hide profiles whose status is ``suspended`` or
        ``archived``.

    Returns
    -------
    str
        A multi-line string with the tree visualization.

    Example output::

        Hermes (CEO) [active]
        ├── CTO (department_head) [active]
        │   ├── PM: Backend (project_manager) [active]
        │   └── PM: Frontend (project_manager) [active]
        ├── CMO (department_head) [active]
        │   └── PM: Social Media (project_manager) [active]
        └── COO (department_head) [suspended]
    """
    tree = registry.get_org_tree(root)

    if tree.get("profile_name") is None:
        return "(empty org chart)"

    lines: list[str] = []
    _render_node(tree, lines, prefix="", is_last=True, is_root=True,
                 show_status=show_status, active_only=active_only)
    return "\n".join(lines)


def _format_label(node: dict[str, Any], show_status: bool) -> str:
    """Build the display label for a single node."""
    label = f"{node['display_name']} ({node['role']})"
    if show_status:
        label += f" [{node['status']}]"
    return label


def _render_node(
    node: dict[str, Any],
    lines: list[str],
    prefix: str,
    is_last: bool,
    is_root: bool,
    show_status: bool,
    active_only: bool,
) -> None:
    """Recursively render *node* and its children into *lines*.

    Parameters
    ----------
    node:
        A dict from :meth:`ProfileRegistry.get_org_tree` with keys
        ``display_name``, ``role``, ``status``, ``children``.
    lines:
        Accumulator list of output lines.
    prefix:
        The indentation string built up by parent calls.
    is_last:
        Whether this node is the last sibling (determines └── vs ├──).
    is_root:
        ``True`` only for the very first (root) call — no connector.
    show_status:
        Forward from :func:`render_org_chart`.
    active_only:
        Forward from :func:`render_org_chart`.
    """
    # Filter this node out if active_only is set.
    if active_only and node["status"] in ("suspended", "archived"):
        return

    # Build connector.
    if is_root:
        connector = ""
    elif is_last:
        connector = "└── "
    else:
        connector = "├── "

    label = _format_label(node, show_status)
    lines.append(f"{prefix}{connector}{label}")

    # Determine children to render (possibly filtered).
    children = node.get("children", [])
    if active_only:
        children = [c for c in children
                    if c["status"] not in ("suspended", "archived")]

    # Build the prefix for child lines.
    if is_root:
        child_prefix = prefix
    elif is_last:
        child_prefix = prefix + "    "
    else:
        child_prefix = prefix + "│   "

    for i, child in enumerate(children):
        child_is_last = (i == len(children) - 1)
        _render_node(
            child,
            lines,
            prefix=child_prefix,
            is_last=child_is_last,
            is_root=False,
            show_status=show_status,
            active_only=active_only,
        )
