"""Tests for IPC framework integration interfaces."""
from __future__ import annotations

from typing import Optional

import pytest

from core.ipc.interface import MessageHandler, MessageRouter, ProfileActivator
from core.ipc.models import Message


# -- Mock implementations --


class ConcreteHandler:
    def handle_message(self, message: Message) -> Optional[Message]:
        return None


class ConcreteActivator:
    def __init__(self):
        self._active: set[str] = set()

    def activate_profile(self, profile_name: str) -> bool:
        self._active.add(profile_name)
        return True

    def deactivate_profile(self, profile_name: str) -> bool:
        self._active.discard(profile_name)
        return True

    def is_active(self, profile_name: str) -> bool:
        return profile_name in self._active


class ConcreteRouter:
    def route_message(self, message: Message) -> bool:
        return True

    def can_route(self, to_profile: str) -> bool:
        return True


class NotAHandler:
    def do_something(self):
        pass


class NotAnActivator:
    def start(self):
        pass


class NotARouter:
    def forward(self):
        pass


# -- Tests --


class TestMessageHandler:
    def test_isinstance_check(self):
        handler = ConcreteHandler()
        assert isinstance(handler, MessageHandler)

    def test_not_handler(self):
        obj = NotAHandler()
        assert not isinstance(obj, MessageHandler)

    def test_handle_returns_none(self):
        handler = ConcreteHandler()
        msg = Message(from_profile="ceo", to_profile="cto")
        result = handler.handle_message(msg)
        assert result is None

    def test_handle_returns_response(self):
        class RespondingHandler:
            def handle_message(self, message: Message) -> Optional[Message]:
                return Message(
                    from_profile=message.to_profile,
                    to_profile=message.from_profile,
                    payload={"response": "done"},
                )

        handler = RespondingHandler()
        assert isinstance(handler, MessageHandler)
        msg = Message(from_profile="ceo", to_profile="cto")
        resp = handler.handle_message(msg)
        assert resp is not None
        assert resp.from_profile == "cto"


class TestProfileActivator:
    def test_isinstance_check(self):
        activator = ConcreteActivator()
        assert isinstance(activator, ProfileActivator)

    def test_not_activator(self):
        obj = NotAnActivator()
        assert not isinstance(obj, ProfileActivator)

    def test_activate_deactivate_cycle(self):
        activator = ConcreteActivator()
        assert activator.is_active("cto") is False
        activator.activate_profile("cto")
        assert activator.is_active("cto") is True
        activator.deactivate_profile("cto")
        assert activator.is_active("cto") is False


class TestMessageRouter:
    def test_isinstance_check(self):
        router = ConcreteRouter()
        assert isinstance(router, MessageRouter)

    def test_not_router(self):
        obj = NotARouter()
        assert not isinstance(obj, MessageRouter)

    def test_route_message(self):
        router = ConcreteRouter()
        msg = Message(from_profile="ceo", to_profile="cto")
        assert router.route_message(msg) is True

    def test_can_route(self):
        router = ConcreteRouter()
        assert router.can_route("cto") is True


class TestProtocolsAreRuntimeCheckable:
    def test_all_protocols_runtime_checkable(self):
        """All IPC protocols should be runtime checkable."""
        # The real check is that isinstance works without errors
        handler = ConcreteHandler()
        activator = ConcreteActivator()
        router = ConcreteRouter()
        assert isinstance(handler, MessageHandler)
        assert isinstance(activator, ProfileActivator)
        assert isinstance(router, MessageRouter)
