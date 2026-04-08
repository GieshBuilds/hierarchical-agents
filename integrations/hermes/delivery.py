#!/usr/bin/env python3
"""DeliveryDispatcher — watches the delivery queue and dispatches results.

Scans ``~/.hermes/hierarchy/delivery/`` for undelivered TASK_RESPONSE files
written by the hermes gateway when results reach the root of the hierarchy.
For each undelivered result, calls a configurable delivery hook, then marks
the file as delivered.

Delivery hooks are pluggable — callers provide a callable that receives a
dict with the result payload and returns True on success.  Built-in hooks:

- ``stdout_hook``  — prints a formatted summary to stdout (default)
- ``command_hook`` — pipes JSON to a shell command's stdin
- ``webhook_hook`` — POSTs JSON to an HTTP endpoint
- ``file_hook``    — appends JSON lines to a file

Usage::

    # One-shot check (for cron)
    dispatcher = DeliveryDispatcher()
    dispatcher.process()

    # With a custom hook
    dispatcher = DeliveryDispatcher(hook=my_telegram_hook)
    dispatcher.process()

    # Continuous daemon
    dispatcher = DeliveryDispatcher(hook=webhook_hook("https://..."))
    dispatcher.run(interval=120)

    # CLI
    python3 -m integrations.hermes.delivery [--hook stdout|command:...|webhook:...|file:...] [--daemon --interval 120]

Stdlib only — no external dependencies.
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)

HIERARCHY_DIR = Path.home() / ".hermes" / "hierarchy"
DELIVERY_DIR = HIERARCHY_DIR / "delivery"

# Type alias for delivery hooks
DeliveryHook = Callable[[Dict[str, Any]], bool]


# ---------------------------------------------------------------------------
# Built-in hooks
# ---------------------------------------------------------------------------

def stdout_hook(item: Dict[str, Any]) -> bool:
    """Print a formatted summary to stdout."""
    from_profile = item.get("from_profile", "unknown")
    forwarded_by = item.get("forwarded_by")
    task = item.get("task", "")[:200]
    result = item.get("result", "")
    error = item.get("error", "")
    ts = item.get("timestamp", "")

    origin = f"{from_profile} (via {forwarded_by})" if forwarded_by else from_profile

    print(f"\n{'=' * 60}")
    print(f"  From: {origin}")
    print(f"  Time: {ts}")
    if task:
        print(f"  Task: {task}")
    print(f"{'=' * 60}")
    if error:
        print(f"  ERROR: {error}")
    elif result:
        # Truncate very long results for terminal display
        display = result if len(result) <= 2000 else result[:2000] + "\n... (truncated)"
        print(display)
    print(f"{'=' * 60}\n")
    return True


def command_hook(command: str) -> DeliveryHook:
    """Return a hook that pipes the JSON payload to a shell command's stdin."""
    def _hook(item: Dict[str, Any]) -> bool:
        try:
            proc = subprocess.run(
                command,
                shell=True,
                input=json.dumps(item),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode != 0:
                logger.warning(
                    "Delivery command failed (rc=%d): %s",
                    proc.returncode,
                    proc.stderr[:200],
                )
                return False
            return True
        except Exception as exc:
            logger.warning("Delivery command error: %s", exc)
            return False
    return _hook


def webhook_hook(url: str) -> DeliveryHook:
    """Return a hook that POSTs JSON to an HTTP endpoint."""
    def _hook(item: Dict[str, Any]) -> bool:
        try:
            data = json.dumps(item).encode("utf-8")
            req = Request(
                url,
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlopen(req, timeout=15) as resp:
                if resp.status >= 400:
                    logger.warning("Webhook returned %d", resp.status)
                    return False
            return True
        except Exception as exc:
            logger.warning("Webhook error: %s", exc)
            return False
    return _hook


def file_hook(path: str) -> DeliveryHook:
    """Return a hook that appends JSON lines to a file."""
    def _hook(item: Dict[str, Any]) -> bool:
        try:
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(item) + "\n")
            return True
        except Exception as exc:
            logger.warning("File delivery error: %s", exc)
            return False
    return _hook


def make_telegram_hook(bot_token: str, chat_id: str) -> Callable[[str], bool]:
    """Return a hook that sends a text message via the Telegram Bot API.

    This is a lightweight delivery hook — no agent session, no model
    invocation, just a direct HTTPS POST to api.telegram.org.

    Parameters
    ----------
    bot_token : str
        Telegram Bot API token (from BotFather).
    chat_id : str
        Target chat ID to deliver messages to.

    Returns
    -------
    callable
        ``(text: str) -> bool`` — sends the text and returns True on success.
    """
    api_url = f"https://api.telegram.org/bot{bot_token}/sendMessage"

    def _hook(text: str) -> bool:
        # Telegram has a 4096-char limit per message; chunk if needed
        chunks = []
        while text:
            if len(text) <= 4096:
                chunks.append(text)
                break
            # Split at last newline before limit
            split_at = text.rfind("\n", 0, 4096)
            if split_at <= 0:
                split_at = 4096
            chunks.append(text[:split_at])
            text = text[split_at:].lstrip("\n")

        for chunk in chunks:
            payload = json.dumps({
                "chat_id": chat_id,
                "text": chunk,
            }).encode("utf-8")
            req = Request(
                api_url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urlopen(req, timeout=15) as resp:
                    if resp.status >= 400:
                        logger.warning("Telegram API returned %d", resp.status)
                        return False
            except URLError as exc:
                logger.warning("Telegram delivery error: %s", exc)
                return False
        return True

    return _hook


def make_telegram_hook_from_env() -> Optional[Callable[[str], bool]]:
    """Create a Telegram delivery hook from environment variables.

    Reads ``TELEGRAM_BOT_TOKEN`` and ``TELEGRAM_HOME_CHANNEL`` from the
    environment (or from ``~/.hermes/.env`` as a fallback).

    Returns None if the required variables are not set.
    """
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_HOME_CHANNEL", "")

    # Fallback: read from ~/.hermes/.env
    if not token or not chat_id:
        env_path = Path.home() / ".hermes" / ".env"
        if env_path.exists():
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key, val = key.strip(), val.strip()
                    if key == "TELEGRAM_BOT_TOKEN" and not token:
                        token = val
                    elif key == "TELEGRAM_HOME_CHANNEL" and not chat_id:
                        chat_id = val
            except Exception:
                pass

    if not token or not chat_id:
        return None

    return make_telegram_hook(token, chat_id)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

class DeliveryDispatcher:
    """Watches the delivery queue and dispatches results via a hook.

    Parameters
    ----------
    delivery_dir : Path | None
        Override the delivery directory (default: ``~/.hermes/hierarchy/delivery/``).
    hook : DeliveryHook | None
        Callable that receives a result dict and returns True on success.
        Defaults to ``stdout_hook``.
    """

    def __init__(
        self,
        delivery_dir: Optional[Path] = None,
        hook: Optional[DeliveryHook] = None,
    ) -> None:
        self._dir = delivery_dir or DELIVERY_DIR
        self._hook = hook or stdout_hook

    def get_pending(self) -> List[Path]:
        """Return undelivered files sorted by name (chronological)."""
        if not self._dir.exists():
            return []
        pending = []
        for f in sorted(self._dir.iterdir()):
            if not f.suffix == ".json":
                continue
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                if not data.get("delivered", False):
                    pending.append(f)
            except (json.JSONDecodeError, OSError):
                continue
        return pending

    def process(self) -> int:
        """Process all undelivered items. Returns count of delivered items."""
        pending = self.get_pending()
        if not pending:
            logger.debug("No pending deliveries")
            return 0

        delivered = 0
        for filepath in pending:
            try:
                data = json.loads(filepath.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to read %s: %s", filepath.name, exc)
                continue

            try:
                success = self._hook(data)
            except Exception as exc:
                logger.warning("Hook failed for %s: %s", filepath.name, exc)
                success = False

            if success:
                data["delivered"] = True
                data["delivered_at"] = datetime.now(timezone.utc).isoformat()
                try:
                    filepath.write_text(
                        json.dumps(data, indent=2), encoding="utf-8"
                    )
                except OSError as exc:
                    logger.warning("Failed to mark %s delivered: %s", filepath.name, exc)
                delivered += 1
                logger.info("Delivered %s", filepath.name)
            else:
                logger.warning("Delivery failed for %s, will retry", filepath.name)

        return delivered

    def run(self, interval: float = 120) -> None:
        """Run continuously, checking every ``interval`` seconds.

        Handles SIGINT/SIGTERM for clean shutdown.
        """
        stop = False

        def _handle_signal(signum: int, frame: Any) -> None:
            nonlocal stop
            stop = True
            logger.info("Received signal %d, stopping…", signum)

        signal.signal(signal.SIGINT, _handle_signal)
        signal.signal(signal.SIGTERM, _handle_signal)

        logger.info(
            "Delivery dispatcher started (dir=%s, interval=%.0fs)",
            self._dir,
            interval,
        )

        while not stop:
            try:
                count = self.process()
                if count:
                    logger.info("Delivered %d item(s)", count)
            except Exception as exc:
                logger.error("Dispatch cycle error: %s", exc)

            # Sleep in short increments so signals are handled promptly
            end_time = time.monotonic() + interval
            while not stop and time.monotonic() < end_time:
                time.sleep(min(2.0, end_time - time.monotonic()))

        logger.info("Delivery dispatcher stopped")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _parse_hook_spec(spec: str) -> DeliveryHook:
    """Parse a hook specification string into a DeliveryHook."""
    if spec == "stdout":
        return stdout_hook
    if spec.startswith("command:"):
        return command_hook(spec[8:])
    if spec.startswith("webhook:"):
        return webhook_hook(spec[8:])
    if spec.startswith("file:"):
        return file_hook(spec[5:])
    raise ValueError(
        f"Unknown hook spec: {spec!r}. "
        "Use: stdout, command:<cmd>, webhook:<url>, file:<path>"
    )


def main(argv: Optional[List[str]] = None) -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Dispatch completed hierarchy results to the owner.",
    )
    parser.add_argument(
        "--hook",
        default="stdout",
        help=(
            "Delivery hook. Options: stdout (default), command:<cmd>, "
            "webhook:<url>, file:<path>"
        ),
    )
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run continuously instead of one-shot.",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=120,
        help="Seconds between checks in daemon mode (default: 120).",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Override delivery directory.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    hook = _parse_hook_spec(args.hook)
    dispatcher = DeliveryDispatcher(delivery_dir=args.dir, hook=hook)

    if args.daemon:
        dispatcher.run(interval=args.interval)
    else:
        count = dispatcher.process()
        if count:
            logger.info("Delivered %d item(s)", count)


if __name__ == "__main__":
    main()
