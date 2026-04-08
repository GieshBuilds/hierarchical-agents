"""Tests for IPC exception classes."""

from __future__ import annotations

import pytest
from core.ipc.exceptions import (
    IPCError,
    MessageNotFound,
    InvalidRecipient,
    InvalidMessageType,
    MessageExpired,
    MessageBusError,
    DeliveryError,
)


class TestIPCError:
    def test_base_exception(self):
        err = IPCError("test error")
        assert str(err) == "test error"
        assert isinstance(err, Exception)

    def test_all_subclasses_inherit(self):
        for cls in [MessageNotFound, InvalidRecipient, InvalidMessageType,
                    MessageExpired, MessageBusError, DeliveryError]:
            assert issubclass(cls, IPCError)


class TestMessageNotFound:
    def test_message(self):
        err = MessageNotFound("msg-abc123")
        assert "msg-abc123" in str(err)
        assert err.message_id == "msg-abc123"


class TestInvalidRecipient:
    def test_without_reason(self):
        err = InvalidRecipient("bad-profile")
        assert "bad-profile" in str(err)
        assert err.profile_name == "bad-profile"
        assert err.reason == ""

    def test_with_reason(self):
        err = InvalidRecipient("bad-profile", "does not exist")
        assert "does not exist" in str(err)
        assert err.reason == "does not exist"


class TestInvalidMessageType:
    def test_message(self):
        err = InvalidMessageType("invalid_type")
        assert "invalid_type" in str(err)
        assert err.message_type == "invalid_type"


class TestMessageExpired:
    def test_message(self):
        err = MessageExpired("msg-abc123")
        assert "msg-abc123" in str(err)
        assert err.message_id == "msg-abc123"


class TestMessageBusError:
    def test_message(self):
        err = MessageBusError("bus down")
        assert str(err) == "bus down"


class TestDeliveryError:
    def test_without_reason(self):
        err = DeliveryError("msg-abc123")
        assert "msg-abc123" in str(err)
        assert err.message_id == "msg-abc123"
        assert err.reason == ""

    def test_with_reason(self):
        err = DeliveryError("msg-abc123", "recipient offline")
        assert "recipient offline" in str(err)
