"""Message cleanup — TTL expiry and archival for the IPC bus.

Handles bulk operations for expiring and archiving stale messages.
Operates directly on the SQLite connection for efficiency.

Stdlib only — no external dependencies.
"""
from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timezone

from core.ipc.message_bus import MessageBus
from core.ipc.models import MessageStatus


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


class MessageCleanup:
    """Cleanup operations for the IPC message bus.

    Parameters
    ----------
    bus : MessageBus
        The message bus to clean up.
    """

    def __init__(self, bus: MessageBus) -> None:
        self._bus = bus
        self._conn = bus._conn
        self._lock = bus._lock

    def expire_messages(self) -> int:
        """Mark expired messages as expired.

        Scans for messages where expires_at < now and status is
        'pending' or 'delivered', and sets them to 'expired'.

        Returns
        -------
        int
            Number of messages expired.
        """
        now = _now_iso()
        with self._lock:
            cursor = self._conn.cursor()
            try:
                cursor.execute(
                    """
                    UPDATE messages SET status = ?
                    WHERE expires_at IS NOT NULL
                      AND expires_at < ?
                      AND status IN (?, ?)
                    """,
                    (
                        MessageStatus.EXPIRED.value,
                        now,
                        MessageStatus.PENDING.value,
                        MessageStatus.DELIVERED.value,
                    ),
                )
                count = cursor.rowcount
                self._conn.commit()
                return count
            except Exception:
                self._conn.rollback()
                raise

    def archive_expired(self) -> int:
        """Move expired messages to the archive table.

        Copies expired messages to message_archive with archived_at timestamp,
        then deletes them from messages.

        Returns
        -------
        int
            Number of messages archived.
        """
        now = _now_iso()
        with self._lock:
            cursor = self._conn.cursor()
            try:
                # Copy to archive
                cursor.execute(
                    """
                    INSERT INTO message_archive (
                        message_id, from_profile, to_profile, message_type,
                        payload, correlation_id, priority, status,
                        created_at, expires_at, archived_at
                    )
                    SELECT
                        message_id, from_profile, to_profile, message_type,
                        payload, correlation_id, priority, status,
                        created_at, expires_at, ?
                    FROM messages
                    WHERE status = ?
                    """,
                    (now, MessageStatus.EXPIRED.value),
                )
                count = cursor.rowcount

                # Delete from messages
                cursor.execute(
                    "DELETE FROM messages WHERE status = ?",
                    (MessageStatus.EXPIRED.value,),
                )

                self._conn.commit()
                return count
            except Exception:
                self._conn.rollback()
                raise

    def cleanup(self) -> dict[str, int]:
        """Run full cleanup: expire then archive.

        Returns
        -------
        dict[str, int]
            {'expired': N, 'archived': M}
        """
        expired = self.expire_messages()
        archived = self.archive_expired()
        return {"expired": expired, "archived": archived}

    def get_archived_count(self) -> int:
        """Get the number of archived messages.

        Returns
        -------
        int
            Number of messages in the archive table.
        """
        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM message_archive")
            row = cursor.fetchone()
            return row[0] if row else 0

    def get_archived_messages(
        self,
        *,
        correlation_id: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """Retrieve archived messages.

        Parameters
        ----------
        correlation_id : str | None
            Optional filter by correlation ID.
        limit : int
            Maximum results. Default: 50.

        Returns
        -------
        list[dict]
            Archived message records as dicts.
        """
        conditions = []
        params: list = []

        if correlation_id is not None:
            conditions.append("correlation_id = ?")
            params.append(correlation_id)

        where = " AND ".join(conditions) if conditions else "1=1"

        with self._lock:
            cursor = self._conn.cursor()
            cursor.execute(
                f"SELECT * FROM message_archive WHERE {where} ORDER BY archived_at DESC LIMIT ?",
                params + [limit],
            )
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
