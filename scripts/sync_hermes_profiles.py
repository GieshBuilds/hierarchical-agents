#!/usr/bin/env python3
"""Sync existing Hermes profiles into the hierarchy registry.

Run this once after installing hierarchical-agents to bring your existing
Hermes profiles into the registry. Re-running it is safe — profiles that
already exist are skipped.

Usage
-----
    python scripts/sync_hermes_profiles.py

    # Show the org chart after sync
    python scripts/sync_hermes_profiles.py --show-chart

Environment Variables
---------------------
    HERMES_PROFILES_DIR   Path to your Hermes profiles  (default: ~/.hermes/profiles/)
    HERMES_DB_BASE_DIR    Path to hierarchy databases    (default: ~/.hermes/hierarchy/)

Role Guessing
-------------
ProfileBridge reads each profile's SOUL.md and guesses a role based on
keywords (ceo, director, project manager, pm, etc.). All non-CEO profiles
default to parent='hermes'. Use the CLI to reassign parents after sync:

    python -m core reassign-parent --name <profile> --parent <new_parent>
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Allow running from the repo root without installing the package.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.registry.profile_registry import ProfileRegistry
from core.registry.org_chart import render_org_chart
from integrations.hermes.config import HermesConfig
from integrations.hermes.profile_bridge import ProfileBridge


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Hermes profiles into the hierarchy registry")
    parser.add_argument("--show-chart", action="store_true", help="Print org chart after sync")
    args = parser.parse_args()

    config = HermesConfig.from_env()

    if not config.profiles_dir.is_dir():
        print(f"ERROR: Hermes profiles directory not found: {config.profiles_dir}")
        print("Make sure Hermes is installed and you have at least one profile.")
        sys.exit(1)

    # Ensure hierarchy DB directory exists
    config.db_base_dir.mkdir(parents=True, exist_ok=True)
    registry_db = config.db_base_dir / "registry.db"

    print(f"Profiles dir : {config.profiles_dir}")
    print(f"Registry db  : {registry_db}")
    print()

    registry = ProfileRegistry(str(registry_db))
    bridge = ProfileBridge(registry=registry, config=config)

    # Discover what's there
    discovered = bridge.discover_profiles()
    if not discovered:
        print("No profile directories found in", config.profiles_dir)
        sys.exit(0)

    print(f"Found {len(discovered)} profile(s): {', '.join(discovered)}")
    print("Syncing...")

    report = bridge.sync_to_registry()

    print()
    if report.added:
        print(f"  Added   ({len(report.added)}): {', '.join(report.added)}")
    if report.skipped:
        print(f"  Skipped ({len(report.skipped)}): {', '.join(report.skipped)}")
    if report.errors:
        print(f"  Errors  ({len(report.errors)}):")
        for err in report.errors:
            print(f"    - {err}")

    print()
    print("Done. All non-CEO profiles default to parent='hermes'.")
    print("To reassign parents:")
    print("  python -m core reassign-parent --name <profile> --parent <new_parent>")

    if args.show_chart:
        print()
        print(render_org_chart(registry))


if __name__ == "__main__":
    main()
