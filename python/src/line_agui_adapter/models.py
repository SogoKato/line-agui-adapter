"""Shared AG-UI request, event, and normalized response types for the adapter."""

from __future__ import annotations

from typing import Any, Literal, TypeAlias

from ag_ui.core import (
    BinaryInputContent,
    Event,
    Message,
    RunAgentInput,
    TextInputContent,
    UserMessage,
)
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

Role: TypeAlias = Literal[
    "user",
    "assistant",
    "system",
    "tool",
    "developer",
    "activity",
    "reasoning",
]

AguiMessage: TypeAlias = Message
AguiEvent: TypeAlias = Event
AguiRequest = RunAgentInput
AguiUserMessage = UserMessage
InputContentPart: TypeAlias = TextInputContent | BinaryInputContent


class AguiResponseBaseModel(BaseModel):
    """Base model for normalized AG-UI response payloads."""

    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=to_camel,
        extra="allow",
    )


class OutputContentSource(AguiResponseBaseModel):
    """Reference to a binary output represented as inline data or a URL."""

    type: Literal["data", "url"]
    value: str
    mime_type: str | None = None


class OutputContentPart(AguiResponseBaseModel):
    """Single multimodal content part in an assistant response."""

    type: Literal["text", "image", "audio", "video", "document"]
    text: str | None = None
    source: OutputContentSource | None = None
    metadata: dict[str, Any] | None = None


class AguiResponseMessage(AguiResponseBaseModel):
    """Normalized final message reconstructed from AG-UI responses or events."""

    id: str
    role: Role
    content: str | list[OutputContentPart] | dict[str, Any] | None = None
    name: str | None = None
    tool_calls: list[dict[str, Any]] | None = None
    tool_call_id: str | None = None
    error: str | None = None
    activity_type: str | None = None
    encrypted_content: str | None = None


class AguiResponse(AguiResponseBaseModel):
    """Normalized AG-UI response with folded messages and original events."""

    messages: list[AguiResponseMessage]
    raw: dict[str, Any]
    events: list[AguiEvent] = Field(default_factory=list)
    state: dict[str, Any] | None = None

    @property
    def assistant_messages(self) -> list[AguiResponseMessage]:
        """Return only assistant-role messages from the response."""
        return [message for message in self.messages if message.role == "assistant"]
