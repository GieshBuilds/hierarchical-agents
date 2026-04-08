"""HermesProfileActivator — activates profiles by launching gateway subprocesses.

Implements the ``ProfileActivator`` protocol from ``core.ipc.interface``.
When a message is routed to an inactive profile, the activator launches
``hierarchy_gateway.py start <profile>`` as a detached background process
so the gateway outlives the calling process.

Stdlib only — no external dependencies.
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Default path to the gateway script
_DEFAULT_GATEWAY_SCRIPT = Path.home() / ".hermes" / "hierarchy" / "hierarchy_gateway.py"


class HermesProfileActivator:
    """Activates profiles by launching gateway processes on demand.

    Implements the ``ProfileActivator`` protocol from ``core.ipc.interface``.

    When ``activate_profile`` is called for a profile that is not yet active,
    a ``hierarchy_gateway.py start <profile>`` subprocess is launched in the
    background. The subprocess is fully detached so it survives after the
    calling process (e.g. hermes agent) exits.

    Parameters
    ----------
    config : object
        Hermes-specific configuration (HermesConfig or similar).
    gateway_factory : callable, optional
        A callable ``(profile_name: str) -> GatewayHook`` for in-process
        gateway creation. Used only in tests. When ``None`` (default),
        gateways are launched as subprocesses.
    gateway_script : Path, optional
        Path to ``hierarchy_gateway.py``. Defaults to
        ``~/.hermes/hierarchy/hierarchy_gateway.py``.
    """

    def __init__(
        self,
        config: Any,
        gateway_factory: Optional[Callable[[str], Any]] = None,
        gateway_script: Optional[Path] = None,
    ) -> None:
        self._config = config
        self._gateway_factory = gateway_factory
        self._gateway_script = gateway_script or _DEFAULT_GATEWAY_SCRIPT
        self._lock = threading.Lock()
        # Maps profile_name -> subprocess.Popen or GatewayHook or True (stub)
        self._active_gateways: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # ProfileActivator protocol
    # ------------------------------------------------------------------

    def activate_profile(self, profile_name: str) -> bool:
        """Activate a profile by launching a gateway process.

        If the profile already has an active gateway, this is a no-op.

        In production (no ``gateway_factory``), launches
        ``hierarchy_gateway.py start <profile>`` as a detached subprocess
        that survives after this process exits.

        In test mode (``gateway_factory`` provided), creates and starts
        a GatewayHook in-process.

        Parameters
        ----------
        profile_name : str
            The profile to activate.

        Returns
        -------
        bool
            True if activation succeeded.
        """
        with self._lock:
            if profile_name in self._active_gateways:
                # Check if still alive
                existing = self._active_gateways[profile_name]
                if isinstance(existing, subprocess.Popen):
                    if existing.poll() is None:
                        return True  # Still running
                    else:
                        # Process died, remove stale entry
                        del self._active_gateways[profile_name]
                elif hasattr(existing, "is_running"):
                    if existing.is_running:
                        return True
                    else:
                        del self._active_gateways[profile_name]
                else:
                    return True  # Stub mode

            # --- In-process mode (tests) ---
            if self._gateway_factory is not None:
                try:
                    gateway = self._gateway_factory(profile_name)
                    gateway.start()
                    self._active_gateways[profile_name] = gateway
                    logger.info(
                        "Activated profile '%s' — in-process gateway started",
                        profile_name,
                    )
                    return True
                except Exception as exc:
                    logger.error(
                        "Failed to activate profile '%s': %s",
                        profile_name,
                        exc,
                    )
                    return False

            # --- Subprocess mode (production) ---
            return self._launch_gateway_subprocess(profile_name)

    def _is_gateway_running(self, profile_name: str) -> bool:
        """Check if a gateway process is already running for this profile.

        Uses PID files in the logs directory to track gateways across
        process restarts.
        """
        pid_file = self._gateway_script.parent / "logs" / f"gateway-{profile_name}.pid"
        if not pid_file.exists():
            return False
        try:
            pid = int(pid_file.read_text().strip())
            # Check if process is alive
            os.kill(pid, 0)
            return True
        except (ValueError, ProcessLookupError, OSError):
            # PID file is stale — clean it up
            pid_file.unlink(missing_ok=True)
            return False

    def _launch_gateway_subprocess(self, profile_name: str) -> bool:
        """Launch hierarchy_gateway.py as a detached background process.

        Checks for an existing gateway via PID file first to prevent
        duplicates across process restarts. The subprocess is fully
        detached so it survives after the calling process exits.
        """
        # Check if already running from a previous process
        if self._is_gateway_running(profile_name):
            logger.info(
                "Gateway for '%s' already running (found PID file), skipping launch",
                profile_name,
            )
            self._active_gateways[profile_name] = True  # Mark as active
            return True

        script = self._gateway_script
        if not script.exists():
            logger.error(
                "Gateway script not found at %s — cannot activate '%s'",
                script,
                profile_name,
            )
            return False

        # Log and PID files
        logs_dir = script.parent / "logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / f"gateway-{profile_name}.log"
        pid_file = logs_dir / f"gateway-{profile_name}.pid"

        try:
            cmd = [sys.executable, str(script), "start", profile_name]

            with open(log_file, "a") as log_fh:
                proc = subprocess.Popen(
                    cmd,
                    stdout=log_fh,
                    stderr=log_fh,
                    stdin=subprocess.DEVNULL,
                    start_new_session=True,  # Detach from parent
                    env={**os.environ},
                )

            # Write PID file for cross-process dedup
            pid_file.write_text(str(proc.pid))

            self._active_gateways[profile_name] = proc
            logger.info(
                "Activated profile '%s' — gateway subprocess pid=%d, log=%s",
                profile_name,
                proc.pid,
                log_file,
            )
            return True

        except Exception as exc:
            logger.error(
                "Failed to launch gateway for '%s': %s",
                profile_name,
                exc,
            )
            return False

    def deactivate_profile(self, profile_name: str) -> bool:
        """Deactivate a profile by stopping its gateway.

        Parameters
        ----------
        profile_name : str
            The profile to deactivate.

        Returns
        -------
        bool
            True if deactivation succeeded.
        """
        with self._lock:
            gateway = self._active_gateways.pop(profile_name, None)
            if gateway is None:
                return True

            if isinstance(gateway, subprocess.Popen):
                try:
                    os.killpg(os.getpgid(gateway.pid), signal.SIGTERM)
                    gateway.wait(timeout=5)
                except (ProcessLookupError, OSError):
                    pass  # Already dead
                except subprocess.TimeoutExpired:
                    os.killpg(os.getpgid(gateway.pid), signal.SIGKILL)
                # Clean up PID file
                pid_file = self._gateway_script.parent / "logs" / f"gateway-{profile_name}.pid"
                pid_file.unlink(missing_ok=True)
                logger.info("Deactivated profile '%s' — subprocess stopped", profile_name)
            elif hasattr(gateway, "close"):
                try:
                    gateway.close()
                except Exception as exc:
                    logger.warning("Error stopping gateway for '%s': %s", profile_name, exc)
                logger.info("Deactivated profile '%s' — gateway stopped", profile_name)

            return True

    def is_active(self, profile_name: str) -> bool:
        """Check if a profile currently has an active gateway.

        Parameters
        ----------
        profile_name : str
            The profile to check.

        Returns
        -------
        bool
            True if the profile has a running gateway.
        """
        with self._lock:
            gateway = self._active_gateways.get(profile_name)
            if gateway is None:
                return False

            if isinstance(gateway, subprocess.Popen):
                if gateway.poll() is not None:
                    del self._active_gateways[profile_name]
                    return False
                return True

            if hasattr(gateway, "is_running"):
                if not gateway.is_running:
                    del self._active_gateways[profile_name]
                    return False

            return True

    def is_profile_active(self, profile_name: str) -> bool:
        """Convenience alias for :meth:`is_active`."""
        return self.is_active(profile_name)

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def get_active_profiles(self) -> list[str]:
        """Return a list of all currently active profile names."""
        with self._lock:
            return list(self._active_gateways.keys())

    def get_gateway(self, profile_name: str) -> Optional[Any]:
        """Return the gateway instance for a profile, or None."""
        with self._lock:
            gw = self._active_gateways.get(profile_name)
            if gw is True:
                return None
            return gw

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def shutdown(self) -> None:
        """Stop all active gateways and clear state."""
        with self._lock:
            for name in list(self._active_gateways.keys()):
                gateway = self._active_gateways.pop(name, None)
                if isinstance(gateway, subprocess.Popen):
                    try:
                        os.killpg(os.getpgid(gateway.pid), signal.SIGTERM)
                        gateway.wait(timeout=5)
                    except (ProcessLookupError, OSError, subprocess.TimeoutExpired):
                        pass
                elif hasattr(gateway, "close"):
                    try:
                        gateway.close()
                    except Exception:
                        pass
            self._active_gateways.clear()
            logger.info("ProfileActivator shut down — all gateways stopped")
