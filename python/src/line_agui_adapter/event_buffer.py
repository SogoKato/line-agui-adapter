"""Minimal buffering for AG-UI event streams."""

from __future__ import annotations

import json
from typing import Any, cast

from ag_ui.core import (
    ActivityMessage,
    AssistantMessage,
    MessagesSnapshotEvent,
    ReasoningMessage,
    RunErrorEvent,
    TextMessageChunkEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallResultEvent,
    ToolMessage,
)

from .models import AguiEvent, AguiResponse, AguiResponseMessage, Role


class AguiEventBuffer:
    """Buffer only the AG-UI events needed for LINE replies."""

    def __init__(self) -> None:
        """Initialize message storage for a single buffered response."""
        self._messages: dict[str, AguiResponseMessage] = {}
        self._message_order: list[str] = []
        self._current_text_message_id: str | None = None
        self._run_error: dict[str, Any] | None = None

    def buffer(self, events: list[AguiEvent], *, raw: dict[str, Any]) -> AguiResponse:
        """Fold a list of events into normalized response messages."""
        for event in events:
            self._apply_event(event)

        response_raw = dict(raw)
        if self._run_error is not None:
            response_raw["runError"] = self._run_error

        return AguiResponse(
            messages=[self._messages[message_id] for message_id in self._message_order],
            events=events,
            raw=response_raw,
        )

    def _apply_event(self, event: AguiEvent) -> None:
        if isinstance(event, MessagesSnapshotEvent):
            self._replace_from_snapshot(event)
            return
        if isinstance(event, TextMessageStartEvent):
            self._current_text_message_id = event.message_id
            self._ensure_text_message(
                event.message_id,
                role=event.role,
                name=event.name,
            )
            return
        if isinstance(event, TextMessageContentEvent):
            self._append_text(event.message_id, event.delta)
            self._current_text_message_id = event.message_id
            return
        if isinstance(event, TextMessageEndEvent):
            if self._current_text_message_id == event.message_id:
                self._current_text_message_id = None
            return
        if isinstance(event, TextMessageChunkEvent):
            self._apply_text_chunk(event)
            return
        if isinstance(event, ToolCallResultEvent):
            self._upsert_message(
                AguiResponseMessage(
                    id=event.message_id,
                    role=event.role or "tool",
                    content=event.content,
                    tool_call_id=event.tool_call_id,
                )
            )
            return
        if isinstance(event, RunErrorEvent):
            self._run_error = event.model_dump(by_alias=True, exclude_none=True)

    def _replace_from_snapshot(self, event: MessagesSnapshotEvent) -> None:
        self._messages = {}
        self._message_order = []
        for message in event.messages:
            self._upsert_message(_from_core_message(message))

    def _apply_text_chunk(self, event: TextMessageChunkEvent) -> None:
        message_id = event.message_id or self._current_text_message_id
        if message_id is None:
            return

        self._ensure_text_message(
            message_id,
            role=event.role or "assistant",
            name=event.name,
        )
        if event.delta:
            self._append_text(message_id, event.delta)
        self._current_text_message_id = message_id

    def _ensure_text_message(
        self,
        message_id: str,
        *,
        role: str,
        name: str | None,
    ) -> AguiResponseMessage:
        existing = self._messages.get(message_id)
        if existing is not None:
            if existing.name is None and name is not None:
                existing.name = name
            return existing

        message = AguiResponseMessage(
            id=message_id,
            role=cast(Role, role),
            content="",
            name=name,
        )
        self._upsert_message(message)
        return message

    def _append_text(self, message_id: str, delta: str) -> None:
        message = self._ensure_text_message(message_id, role="assistant", name=None)
        existing = message.content
        if isinstance(existing, str):
            message.content = existing + delta
            return
        message.content = delta

    def _upsert_message(self, message: AguiResponseMessage) -> None:
        if message.id not in self._messages:
            self._message_order.append(message.id)
        self._messages[message.id] = message


def parse_sse_events(payload: str) -> list[dict[str, Any]]:
    """Parse an SSE payload into a list of raw JSON event objects."""
    raw_events: list[dict[str, Any]] = []
    for block in payload.split("\n\n"):
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith(":") or not line:
                continue
            if not line.startswith("data:"):
                continue
            data_lines.append(line[5:].lstrip())

        if not data_lines:
            continue

        data = "\n".join(data_lines).strip()
        if not data or data == "[DONE]":
            continue

        parsed = json.loads(data)
        if isinstance(parsed, list):
            raw_events.extend(item for item in parsed if isinstance(item, dict))
        elif isinstance(parsed, dict):
            raw_events.append(parsed)
    return raw_events


def _from_core_message(message: Any) -> AguiResponseMessage:
    if isinstance(message, AssistantMessage):
        return AguiResponseMessage(
            id=message.id,
            role=message.role,
            content=message.content,
            name=message.name,
            tool_calls=[
                tool_call.model_dump(by_alias=True, exclude_none=True)
                for tool_call in message.tool_calls or []
            ]
            or None,
            encrypted_content=message.encrypted_value,
        )
    if isinstance(message, ToolMessage):
        return AguiResponseMessage(
            id=message.id,
            role=message.role,
            content=message.content,
            tool_call_id=message.tool_call_id,
            error=message.error,
            encrypted_content=message.encrypted_value,
        )
    if isinstance(message, ReasoningMessage):
        return AguiResponseMessage(
            id=message.id,
            role=message.role,
            content=message.content,
            encrypted_content=message.encrypted_value,
        )
    if isinstance(message, ActivityMessage):
        return AguiResponseMessage(
            id=message.id,
            role=message.role,
            content=message.content,
            activity_type=message.activity_type,
        )

    payload = message.model_dump(by_alias=False, exclude_none=True)
    return AguiResponseMessage(
        id=payload["id"],
        role=payload["role"],
        content=payload.get("content"),
        name=payload.get("name"),
        tool_calls=payload.get("tool_calls"),
        tool_call_id=payload.get("tool_call_id"),
        error=payload.get("error"),
        activity_type=payload.get("activity_type"),
        encrypted_content=payload.get("encrypted_value"),
    )
