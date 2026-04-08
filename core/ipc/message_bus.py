"""Message Bus — core IPC message storage and retrieval.

Single shared SQLite database for inter-profile communication.
Thread-safe operations with priority queue ordering.

Stdlib only — no external dependencies.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Generator, Optional

from core.ipc.exceptions import (
    InvalidRecipient,
    MessageBusError,
    MessageExpired,
    MessageNotFound,
)
from core.ipc.models import (
    DEFAULT_TTL,
    Message,
    MessagePriority,
    MessageStatus,
    MessageType,
    VALID_STATUS_TRANSITIONS,
    generate_message_id,
)
from core.ipc.schema import init_ipc_db


def _now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _row_to_message(row: sqlite3.Row) -> Message:
    """Convert a SQLite Row to a Message dataclass."""
    return Message(
        message_id=row["message_id"],
        from_profile=row["from_profile"],
        to_profile=row["to_profile"],
        message_type=MessageType(row["message_type"]),
        payload=Message.payload_from_json(row["payload"]),
        correlation_id=row["correlation_id"],
        priority=MessagePriority(row["priority"]),
        status=MessageStatus(row["status"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        expires_at=(
            datetime.fromisoformat(row["expires_at"])
            if row["expires_at"]
            else None
        ),
    )


# Priority sort mapping: urgent=0 (first), normal=1, low=2
_PRIORITY_SORT = {
    "urgent": 0,
    "normal": 1,
    "low": 2,
}


class MessageBus:
    """IPC message bus for inter-profile communication.

    Single shared SQLite database backing asynchronous message passing
    between profiles in the hierarchy.

    Parameters
    ----------
    db_path : str
        Path to the bus.db file, or ':memory:' for testing.
    profile_registry : object | None
        Optional ProfileRegistry for recipient validation (duck-typed).
        When provided, ``send()`` validates that the recipient profile exists.
    default_ttl : timedelta | None
        Default time-to-live for messages. None means messages never expire.
        Default: 24 hours.
    """

    def __init__(
        self,
        db_path: str = ":memory:",
        profile_registry: object | None = None,
        default_ttl: timedelta | None = DEFAULT_TTL,
    ) -> None:
        self._db_path = db_path
        self._profile_registry = profile_registry
        self._default_ttl = default_ttl
        self._lock = threading.Lock()
        self._conn = init_ipc_db(db_path)

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    @contextmanager
    def _cursor(
        self,
        *,
        commit: bool = False,
    ) -> Generator[sqlite3.Cursor, None, None]:
        """Yield a cursor under the bus lock.

        Parameters
        ----------
        commit : bool
            If True, commit on successful exit. Rollback on exception.
        """
        with self._lock:
            cursor = self._conn.cursor()
            try:
                yield cursor
                if commit:
                    self._conn.commit()
            except Exception:
                self._conn.rollback()
                raise

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    # ------------------------------------------------------------------
    # Recipient validation
    # ------------------------------------------------------------------

    def _validate_recipient(self, profile_name: str) -> None:
        """Validate that the recipient profile exists.

        Only performed when a profile_registry was supplied.

        Parameters
        ----------
        profile_name : str
            The recipient profile name.

        Raises
        ------
        InvalidRecipient
            If the profile does not exist in the registry.
        """
        if self._profile_registry is None:
            return

        try:
            self._profile_registry.get(profile_name)  # type: ignore[union-attr]
        except Exception:
            raise InvalidRecipient(profile_name, "profile not found in registry")

    # ------------------------------------------------------------------
    # Core operations
    # ------------------------------------------------------------------

    def send(
        self,
        from_profile: str,
        to_profile: str,
        message_type: MessageType,
        payload: dict | None = None,
        correlation_id: str | None = None,
        priority: MessagePriority = MessagePriority.NORMAL,
        ttl: timedelta | None = ...,  # type: ignore[assignment]
    ) -> str:
        """Send a message to a profile.

        Parameters
        ----------
        from_profile : str
            Sender profile name.
        to_profile : str
            Recipient profile name.
        message_type : MessageType
            Type of message.
        payload : dict | None
            JSON-serializable message content. Defaults to empty dict.
        correlation_id : str | None
            Optional correlation ID for request/response linking.
        priority : MessagePriority
            Message priority. Default: normal.
        ttl : timedelta | None
            Time-to-live. Use ``...`` (default) for the bus default TTL.
            Use ``None`` to never expire.

        Returns
        -------
        str
            The message_id of the sent message.

        Raises
        ------
        InvalidRecipient
            If recipient validation is enabled and the profile doesn't exist.
        MessageBusError
            If the send operation fails.
        """
        self._validate_recipient(to_profile)

        if payload is None:
            payload = {}

        message_id = generate_message_id()
        now = datetime.now(timezone.utc)

        # Resolve TTL: sentinel (...) = use bus default, None = no expiry
        effective_ttl = self._default_ttl if ttl is ... else ttl
        expires_at = (now + effective_ttl) if effective_ttl is not None else None

        with self._cursor(commit=True) as cursor:
            try:
                cursor.execute(
                    """
                    INSERT INTO messages (
                        message_id, from_profile, to_profile, message_type,
                        payload, correlation_id, priority, status,
                        created_at, expires_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        message_id,
                        from_profile,
                        to_profile,
                        message_type.value if isinstance(message_type, MessageType) else message_type,
                        Message(payload=payload).payload_as_json(),
                        correlation_id,
                        priority.value if isinstance(priority, MessagePriority) else priority,
                        MessageStatus.PENDING.value,
                        now.isoformat(),
                        expires_at.isoformat() if expires_at else None,
                    ),
                )
            except sqlite3.Error as e:
                raise MessageBusError(f"Failed to send message: {e}") from e

        return message_id

    def get(self, message_id: str) -> Message:
        """Retrieve a single message by ID.

        Parameters
        ----------
        message_id : str
            The message ID to look up.

        Returns
        -------
        Message
            The message.

        Raises
        ------
        MessageNotFound
            If no message with the given ID exists.
        """
        with self._cursor() as cursor:
            cursor.execute(
                "SELECT * FROM messages WHERE message_id = ?",
                (message_id,),
            )
            row = cursor.fetchone()

        if row is None:
            raise MessageNotFound(message_id)

        return _row_to_message(row)

    def poll(
        self,
        profile_name: str,
        *,
        limit: int = 50,
        message_type: MessageType | None = None,
        include_expired: bool = False,
    ) -> list[Message]:
        """Poll for pending messages for a profile.

        Returns messages ordered by priority (urgent first) then by
        creation time (oldest first).

        Parameters
        ----------
        profile_name : str
            The recipient profile to poll for.
        limit : int
            Maximum number of messages to return. Default: 50.
        message_type : MessageType | None
            Optional filter by message type.
        include_expired : bool
            If False (default), exclude messages past their expires_at.

        Returns
        -------
        list[Message]
            Pending messages, priority-ordered.
        """
        conditions = ["to_profile = ?", "status = ?"]
        params: list = [profile_name, MessageStatus.PENDING.value]

        if message_type is not None:
            conditions.append("message_type = ?")
            params.append(
                message_type.value
                if isinstance(message_type, MessageType)
                else message_type
            )

        if not include_expired:
            conditions.append("(expires_at IS NULL OR expires_at > ?)")
            params.append(_now_iso())

        where_clause = " AND ".join(conditions)

        # Sort: priority (urgent=0 first), then created_at ASC (FIFO)
        query = f"""
            SELECT * FROM messages
            WHERE {where_clause}
            ORDER BY
                CASE priority
                    WHEN 'urgent' THEN 0
                    WHEN 'normal' THEN 1
                    WHEN 'low' THEN 2
                END ASC,
                created_at ASC
            LIMIT ?
        """
        params.append(limit)

        with self._cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        return [_row_to_message(row) for row in rows]

    def acknowledge(self, message_id: str) -> Message:
        """Mark a message as delivered.

        Parameters
        ----------
        message_id : str
            The message to acknowledge.

        Returns
        -------
        Message
            The updated message.

        Raises
        ------
        MessageNotFound
            If the message doesn't exist.
        MessageExpired
            If the message has expired.
        MessageBusError
            If the status transition is invalid.
        """
        return self._transition_status(message_id, MessageStatus.DELIVERED)

    def mark_read(self, message_id: str) -> Message:
        """Mark a message as read.

        Parameters
        ----------
        message_id : str
            The message to mark as read.

        Returns
        -------
        Message
            The updated message.

        Raises
        ------
        MessageNotFound
            If the message doesn't exist.
        MessageExpired
            If the message has expired.
        MessageBusError
            If the status transition is invalid.
        """
        return self._transition_status(message_id, MessageStatus.READ)

    def get_by_correlation(self, correlation_id: str) -> list[Message]:
        """Find all messages sharing a correlation ID.

        Parameters
        ----------
        correlation_id : str
            The correlation ID to search for.

        Returns
        -------
        list[Message]
            Messages with the given correlation_id, ordered by created_at.
        """
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT * FROM messages
                WHERE correlation_id = ?
                ORDER BY created_at ASC
                """,
                (correlation_id,),
            )
            rows = cursor.fetchall()

        return [_row_to_message(row) for row in rows]

    def get_pending_count(self, profile_name: str) -> int:
        """Count pending messages for a profile.

        Parameters
        ----------
        profile_name : str
            The recipient profile.

        Returns
        -------
        int
            Number of pending, non-expired messages.
        """
        with self._cursor() as cursor:
            cursor.execute(
                """
                SELECT COUNT(*) FROM messages
                WHERE to_profile = ?
                  AND status = ?
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (profile_name, MessageStatus.PENDING.value, _now_iso()),
            )
            row = cursor.fetchone()

        return row[0] if row else 0

    def list_messages(
        self,
        *,
        profile_name: str | None = None,
        status: MessageStatus | None = None,
        message_type: MessageType | None = None,
        direction: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Message]:
        """List messages with optional filters.

        Parameters
        ----------
        profile_name : str | None
            Filter by profile (as sender or recipient based on direction).
        status : MessageStatus | None
            Filter by message status.
        message_type : MessageType | None
            Filter by message type.
        direction : str | None
            'sent' to filter by from_profile, 'received' to filter by to_profile.
            None means filter on either.
        limit : int
            Maximum results. Default: 50.
        offset : int
            Skip first N results. Default: 0.

        Returns
        -------
        list[Message]
            Matching messages, ordered by created_at DESC.
        """
        conditions: list[str] = []
        params: list = []

        if profile_name is not None:
            if direction == "sent":
                conditions.append("from_profile = ?")
                params.append(profile_name)
            elif direction == "received":
                conditions.append("to_profile = ?")
                params.append(profile_name)
            else:
                conditions.append("(from_profile = ? OR to_profile = ?)")
                params.extend([profile_name, profile_name])

        if status is not None:
            conditions.append("status = ?")
            params.append(
                status.value if isinstance(status, MessageStatus) else status
            )

        if message_type is not None:
            conditions.append("message_type = ?")
            params.append(
                message_type.value
                if isinstance(message_type, MessageType)
                else message_type
            )

        where_clause = " AND ".join(conditions) if conditions else "1=1"

        query = f"""
            SELECT * FROM messages
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        with self._cursor() as cursor:
            cursor.execute(query, params)
            rows = cursor.fetchall()

        return [_row_to_message(row) for row in rows]

    def delete(self, message_id: str) -> bool:
        """Hard-delete a message.

        Parameters
        ----------
        message_id : str
            The message to delete.

        Returns
        -------
        bool
            True if the message was deleted.

        Raises
        ------
        MessageNotFound
            If the message doesn't exist.
        """
        # Verify existence first
        self.get(message_id)

        with self._cursor(commit=True) as cursor:
            cursor.execute(
                "DELETE FROM messages WHERE message_id = ?",
                (message_id,),
            )
            return cursor.rowcount > 0

    def get_stats(self) -> dict:
        """Get message bus statistics.

        Returns
        -------
        dict
            Statistics including counts by status, by type, and by profile.
        """
        stats: dict = {
            "total": 0,
            "by_status": {},
            "by_type": {},
            "by_profile": {},
            "archived": 0,
        }

        with self._cursor() as cursor:
            # Total active messages
            cursor.execute("SELECT COUNT(*) FROM messages")
            stats["total"] = cursor.fetchone()[0]

            # By status
            cursor.execute(
                "SELECT status, COUNT(*) FROM messages GROUP BY status"
            )
            stats["by_status"] = {row[0]: row[1] for row in cursor.fetchall()}

            # By type
            cursor.execute(
                "SELECT message_type, COUNT(*) FROM messages GROUP BY message_type"
            )
            stats["by_type"] = {row[0]: row[1] for row in cursor.fetchall()}

            # By recipient profile (pending count)
            cursor.execute(
                """
                SELECT to_profile, COUNT(*) FROM messages
                WHERE status = 'pending'
                GROUP BY to_profile
                """
            )
            stats["by_profile"] = {row[0]: row[1] for row in cursor.fetchall()}

            # Archived count
            cursor.execute("SELECT COUNT(*) FROM message_archive")
            stats["archived"] = cursor.fetchone()[0]

        return stats

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _transition_status(
        self,
        message_id: str,
        new_status: MessageStatus,
    ) -> Message:
        """Transition a message to a new status.

        Parameters
        ----------
        message_id : str
            The message to update.
        new_status : MessageStatus
            The target status.

        Returns
        -------
        Message
            The updated message.

        Raises
        ------
        MessageNotFound
            If the message doesn't exist.
        MessageExpired
            If the message has expired.
        MessageBusError
            If the transition is invalid.
        """
        msg = self.get(message_id)

        # Check expiry
        if msg.is_expired() and new_status != MessageStatus.EXPIRED:
            raise MessageExpired(message_id)

        # Validate transition
        if not msg.can_transition_to(new_status):
            raise MessageBusError(
                f"Invalid status transition: {msg.status.value} -> {new_status.value} "
                f"for message {message_id}"
            )

        with self._cursor(commit=True) as cursor:
            cursor.execute(
                "UPDATE messages SET status = ? WHERE message_id = ?",
                (new_status.value, message_id),
            )

        return self.get(message_id)
