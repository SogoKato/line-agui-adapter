"""Hook pipeline types for processing requests and responses around AG-UI."""

from __future__ import annotations

from typing import Awaitable, Callable

from .models import AguiRequest, AguiResponse

BeforeAguiHook = Callable[[AguiRequest], AguiRequest | Awaitable[AguiRequest]]
AfterAguiHook = Callable[[AguiResponse], AguiResponse | Awaitable[AguiResponse]]


class MiddlewarePipeline:
    """Container for pre- and post-processing hooks around AG-UI calls."""

    def __init__(
        self,
        before_agui: list[BeforeAguiHook] | None = None,
        after_agui: list[AfterAguiHook] | None = None,
    ) -> None:
        """Initialize hook lists for before and after AG-UI processing."""
        self.before_agui = before_agui or []
        self.after_agui = after_agui or []

    def add_before(self, hook: BeforeAguiHook) -> None:
        """Register a hook to run before the AG-UI request is sent."""
        self.before_agui.append(hook)

    def add_after(self, hook: AfterAguiHook) -> None:
        """Register a hook to run after the AG-UI response is received."""
        self.after_agui.append(hook)
