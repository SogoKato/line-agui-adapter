"""Adapter utilities for converting LINE events to AG-UI requests and back."""

from __future__ import annotations

import base64
import inspect
import mimetypes
from typing import Any, NamedTuple, Protocol
from uuid import uuid4

from ag_ui.core import BinaryInputContent, TextInputContent, UserMessage
from linebot.v3.messaging import (
    AudioMessage,
    ImageMessage,
    TextMessage,
    VideoMessage,
)
from linebot.v3.messaging import (
    Message as LineMessage,
)
from linebot.v3.webhooks import (
    AudioMessageContent,
    FileMessageContent,
    ImageMessageContent,
    MessageContent,
    MessageEvent,
    TextMessageContent,
    VideoMessageContent,
)

from .client import AguiHttpClient
from .middleware import MiddlewarePipeline
from .models import AguiRequest, AguiResponse, InputContentPart, OutputContentPart


class FetchedContent(NamedTuple):
    """Fetched binary payload and its optional MIME type."""

    data: bytes
    mime_type: str | None = None


class MessageContentFetcher(Protocol):
    """Protocol for retrieving binary content for a LINE message ID."""

    async def __call__(self, message_id: str) -> bytes | FetchedContent:
        """Fetch content bytes for the given LINE message ID."""
        ...


class LineAguiAdapter:
    """Convert LINE `MessageEvent` objects into AG-UI requests and LINE replies."""

    def __init__(
        self,
        agui_client: AguiHttpClient,
        content_fetcher: MessageContentFetcher | None = None,
        pipeline: MiddlewarePipeline | None = None,
    ) -> None:
        """Initialize the adapter with transport, content fetcher, and hooks."""
        self.agui_client = agui_client
        self.content_fetcher = content_fetcher
        self.pipeline = pipeline or MiddlewarePipeline()

    async def handle_event(
        self,
        event: MessageEvent,
        *,
        conversation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> list[LineMessage]:
        """Send a LINE event to AG-UI and convert the response into LINE messages."""
        agui_request = await self.build_agui_request(
            event,
            conversation_id=conversation_id,
            metadata=metadata,
        )

        agui_request = await self._apply_before_hooks(agui_request)
        agui_response = await self.agui_client.request(agui_request)
        agui_response = await self._apply_after_hooks(agui_response)

        return self.to_line_messages(agui_response)

    async def build_agui_request(
        self,
        event: MessageEvent,
        *,
        conversation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AguiRequest:
        """Build an AG-UI request object from a LINE message event."""
        content = await self._line_message_to_agui_content(event)
        user_message = UserMessage(
            id=self._event_message_id(event),
            content=content,
            name=self._source_id(event),
        )

        request_metadata = {
            "line": {
                "replyToken": event.reply_token,
                "eventType": event.type,
                "source": self._source_dict(event),
                "timestamp": event.timestamp,
                "message": self._message_request_metadata(event.message),
            }
        }
        if metadata:
            request_metadata.update(metadata)

        resolved_thread_id = (
            self._conversation_id_from_source(event)
            if conversation_id is None
            else conversation_id
        )

        return AguiRequest(
            thread_id=resolved_thread_id or self._fallback_thread_id(event),
            run_id=self._event_run_id(event),
            state={},
            messages=[user_message],
            tools=[],
            context=[],
            forwarded_props=request_metadata,
        )

    async def _line_message_to_agui_content(
        self, event: MessageEvent
    ) -> str | list[InputContentPart]:
        message = event.message
        if isinstance(message, TextMessageContent):
            return message.text or ""
        if not isinstance(
            message,
            (
                ImageMessageContent,
                AudioMessageContent,
                VideoMessageContent,
                FileMessageContent,
            ),
        ):
            return ""

        parts: list[InputContentPart] = []
        text_hint = self._fallback_text_for_non_text_message(message)
        if text_hint:
            parts.append(TextInputContent(text=text_hint))

        media_part = await self._to_media_part(message)
        if media_part is not None:
            parts.append(media_part)

        if not parts:
            return ""
        return parts

    async def _to_media_part(
        self,
        message: ImageMessageContent
        | AudioMessageContent
        | VideoMessageContent
        | FileMessageContent,
    ) -> BinaryInputContent | None:
        message_type = message.type

        if not isinstance(message, FileMessageContent) and self._provider_is_external(
            message
        ):
            url = self._external_content_url(message)
            if not url:
                return None
            return BinaryInputContent(
                mime_type=self._guess_mime_type(message_type, message, url=url),
                url=url,
                filename=self._message_filename(message),
            )

        if self.content_fetcher is None:
            return None

        message_id = message.id
        if not message_id:
            return None

        fetched_content = self._normalize_fetched_content(
            await self.content_fetcher(str(message_id))
        )
        encoded = base64.b64encode(fetched_content.data).decode("ascii")
        return BinaryInputContent(
            mime_type=self._guess_mime_type(
                message_type,
                message,
                explicit_mime_type=fetched_content.mime_type,
            ),
            data=encoded,
            filename=self._message_filename(message),
        )

    async def _apply_before_hooks(self, request: AguiRequest) -> AguiRequest:
        for hook in self.pipeline.before_agui:
            maybe_value = hook(request)
            if inspect.isawaitable(maybe_value):
                request = await maybe_value
            else:
                request = maybe_value
        return request

    async def _apply_after_hooks(self, response: AguiResponse) -> AguiResponse:
        for hook in reversed(self.pipeline.after_agui):
            maybe_value = hook(response)
            if inspect.isawaitable(maybe_value):
                response = await maybe_value
            else:
                response = maybe_value
        return response

    def to_line_messages(self, response: AguiResponse) -> list[LineMessage]:
        """Convert assistant messages in an AG-UI response into LINE reply messages."""
        line_messages: list[LineMessage] = []
        for message in response.assistant_messages:
            self._append_line_messages_from_content(line_messages, message.content)

        if not line_messages:
            line_messages.append(
                TextMessage(
                    text="対応できる応答がありませんでした。",
                    quickReply=None,
                    quoteToken=None,
                )
            )

        return line_messages

    def _append_line_messages_from_content(
        self,
        line_messages: list[LineMessage],
        content: str | list[OutputContentPart] | dict[str, Any] | None,
    ) -> None:
        if isinstance(content, str) and content.strip():
            line_messages.append(
                TextMessage(text=content, quickReply=None, quoteToken=None)
            )
            return

        if not isinstance(content, list):
            return

        for part in content:
            line_message = self._line_message_from_output_part(part)
            if line_message is not None:
                line_messages.append(line_message)

    def _line_message_from_output_part(
        self, part: OutputContentPart
    ) -> LineMessage | None:
        if part.type == "text" and part.text:
            return TextMessage(text=part.text, quickReply=None, quoteToken=None)

        source = part.source
        if source is None or source.type != "url":
            return None

        url = source.value
        if not isinstance(url, str) or not url:
            return None

        if part.type == "image":
            return ImageMessage(
                originalContentUrl=url,
                previewImageUrl=url,
                quickReply=None,
            )
        if part.type == "video":
            return VideoMessage(
                originalContentUrl=url,
                previewImageUrl=url,
                trackingId=None,
                quickReply=None,
            )
        if part.type == "audio":
            return AudioMessage(
                originalContentUrl=url,
                duration=self._extract_duration(part),
                quickReply=None,
            )
        if part.type == "document":
            return TextMessage(
                text=f"Document: {url}",
                quickReply=None,
                quoteToken=None,
            )
        return None

    def _source_id(self, event: MessageEvent) -> str | None:
        source = event.source
        for key in ("user_id", "group_id", "room_id"):
            value = getattr(source, key, None)
            if isinstance(value, str) and value:
                return value
        return None

    def _event_message_id(self, event: MessageEvent) -> str:
        message_id = event.message.id
        if isinstance(message_id, str) and message_id:
            return message_id
        return uuid4().hex

    def _event_run_id(self, event: MessageEvent) -> str:
        webhook_event_id = getattr(event, "webhook_event_id", None)
        if isinstance(webhook_event_id, str) and webhook_event_id:
            return webhook_event_id
        return uuid4().hex

    def _fallback_thread_id(self, event: MessageEvent) -> str:
        return f"line:{self._event_message_id(event)}"

    def _source_dict(self, event: MessageEvent) -> dict[str, Any] | None:
        source = event.source
        result: dict[str, Any] = {"type": getattr(source, "type", None)}
        for key in ("user_id", "group_id", "room_id"):
            value = getattr(source, key, None)
            if value is not None:
                result[key] = value
        return result

    def _conversation_id_from_source(self, event: MessageEvent) -> str | None:
        source = event.source
        source_type = getattr(source, "type", "unknown")
        for key in ("user_id", "group_id", "room_id"):
            value = getattr(source, key, None)
            if isinstance(value, str) and value:
                return f"line:{source_type}:{value}"
        return None

    def _provider_is_external(
        self,
        message: ImageMessageContent | AudioMessageContent | VideoMessageContent,
    ) -> bool:
        return message.content_provider.type == "external"

    def _external_content_url(
        self,
        message: ImageMessageContent | AudioMessageContent | VideoMessageContent,
    ) -> str | None:
        return getattr(message.content_provider, "original_content_url", None)

    def _message_request_metadata(
        self,
        message: MessageContent,
    ) -> dict[str, Any]:
        metadata: dict[str, Any] = {
            "id": message.id,
            "type": message.type,
        }
        duration = getattr(message, "duration", None)
        if duration is not None:
            metadata["duration"] = duration
        file_name = getattr(message, "file_name", None)
        if file_name is not None:
            metadata["fileName"] = file_name
        file_size = getattr(message, "file_size", None)
        if file_size is not None:
            metadata["fileSize"] = file_size
        return metadata

    def _message_filename(
        self,
        message: ImageMessageContent
        | AudioMessageContent
        | VideoMessageContent
        | FileMessageContent,
    ) -> str | None:
        file_name = getattr(message, "file_name", None)
        if isinstance(file_name, str) and file_name:
            return file_name
        return None

    def _normalize_fetched_content(
        self, content: bytes | FetchedContent
    ) -> FetchedContent:
        if isinstance(content, FetchedContent):
            return content
        return FetchedContent(data=content)

    def _guess_mime_type(
        self,
        message_type: str,
        message: ImageMessageContent
        | AudioMessageContent
        | VideoMessageContent
        | FileMessageContent,
        *,
        explicit_mime_type: str | None = None,
        url: str | None = None,
    ) -> str:
        if explicit_mime_type:
            return explicit_mime_type

        if url:
            guessed_from_url, _ = mimetypes.guess_type(url)
            if guessed_from_url:
                return guessed_from_url

        file_name = getattr(message, "file_name", None)
        if file_name:
            guessed_from_name, _ = mimetypes.guess_type(file_name)
            if guessed_from_name:
                return guessed_from_name

        if message_type == "image":
            return "image/jpeg"
        if message_type == "audio":
            return "audio/mpeg"
        if message_type == "video":
            return "video/mp4"
        return "application/octet-stream"

    def _fallback_text_for_non_text_message(
        self,
        message: ImageMessageContent
        | AudioMessageContent
        | VideoMessageContent
        | FileMessageContent,
    ) -> str | None:
        message_type = message.type
        if message_type == "image":
            return "[LINE image message]"
        if message_type == "audio":
            return "[LINE audio message]"
        if message_type == "video":
            return "[LINE video message]"
        if message_type == "file":
            name = getattr(message, "file_name", None)
            if name:
                return f"[LINE file message: {name}]"
            return "[LINE file message]"
        return None

    def _extract_duration(self, part: OutputContentPart) -> int:
        metadata = part.metadata
        if isinstance(metadata, dict):
            duration = metadata.get("duration")
            if isinstance(duration, int):
                return duration
        return 1000
