"""Adapter utilities for converting LINE events to AG-UI requests and back."""

from __future__ import annotations

import base64
import inspect
import logging
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

logger = logging.getLogger(__name__)

LINE_MAX_TEXT_LENGTH = 5000


def _debug(message: str, *args: Any) -> None:
    logger.debug("[LineAguiAdapter] " + message, *args)


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
        _debug(
            "Initialized LineAguiAdapter: "
            "content_fetcher=%s before_hooks=%d after_hooks=%d",
            self.content_fetcher is not None,
            len(self.pipeline.before_agui),
            len(self.pipeline.after_agui),
        )

    async def handle_event(
        self,
        event: MessageEvent,
        *,
        conversation_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> list[LineMessage]:
        """Send a LINE event to AG-UI and convert the response into LINE messages."""
        _debug(
            "Handling LINE event: "
            "type=%s message_type=%s reply_token=%s conversation_id=%s "
            "metadata_keys=%s",
            event.type,
            getattr(event.message, "type", None),
            bool(event.reply_token),
            conversation_id,
            sorted(metadata.keys()) if metadata else [],
        )
        agui_request = await self.build_agui_request(
            event,
            conversation_id=conversation_id,
            metadata=metadata,
        )

        agui_request = await self._apply_before_hooks(agui_request)
        _debug(
            "Sending AG-UI request: thread_id=%s run_id=%s messages=%d",
            agui_request.thread_id,
            agui_request.run_id,
            len(agui_request.messages),
        )
        agui_response = await self.agui_client.request(agui_request)
        _debug(
            "Received AG-UI response: assistant_messages=%d",
            len(agui_response.assistant_messages),
        )
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
        _debug(
            "Converted LINE content to AG-UI payload: message_type=%s content_kind=%s",
            getattr(event.message, "type", None),
            type(content).__name__,
        )
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

        thread_id = resolved_thread_id or self._fallback_thread_id(event)
        run_id = self._event_run_id(event)
        _debug(
            "Built AG-UI request: thread_id=%s run_id=%s source=%s",
            thread_id,
            run_id,
            self._source_dict(event),
        )

        return AguiRequest(
            thread_id=thread_id,
            run_id=run_id,
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
            _debug("LINE text message detected: length=%d", len(message.text or ""))
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
            _debug(
                "Unsupported LINE message type for AG-UI conversion: type=%s",
                getattr(message, "type", None),
            )
            return ""

        parts: list[InputContentPart] = []
        text_hint = self._fallback_text_for_non_text_message(message)
        if text_hint:
            parts.append(TextInputContent(text=text_hint))

        media_part = await self._to_media_part(message)
        if media_part is not None:
            parts.append(media_part)

        if not parts:
            _debug(
                "No AG-UI parts generated for non-text message: type=%s",
                message.type,
            )
            return ""

        _debug(
            "Built AG-UI parts for non-text message: type=%s parts=%d",
            message.type,
            len(parts),
        )
        return parts

    async def _to_media_part(
        self,
        message: ImageMessageContent
        | AudioMessageContent
        | VideoMessageContent
        | FileMessageContent,
    ) -> BinaryInputContent | None:
        message_type = message.type
        _debug("Converting media message: type=%s id=%s", message_type, message.id)

        if not isinstance(message, FileMessageContent) and self._provider_is_external(
            message
        ):
            url = self._external_content_url(message)
            if not url:
                _debug(
                    "External content provider URL was missing: type=%s id=%s",
                    message_type,
                    message.id,
                )
                return None

            _debug(
                "Using external media URL: type=%s id=%s url=%s",
                message_type,
                message.id,
                url,
            )
            return BinaryInputContent(
                mime_type=self._guess_mime_type(message_type, message, url=url),
                url=url,
                filename=self._message_filename(message),
            )

        if self.content_fetcher is None:
            _debug(
                "Content fetcher is not configured; "
                "media payload will be skipped: type=%s id=%s",
                message_type,
                message.id,
            )
            return None

        message_id = message.id
        if not message_id:
            _debug("Media message ID was missing: type=%s", message_type)
            return None

        fetched_content = self._normalize_fetched_content(
            await self.content_fetcher(str(message_id))
        )
        _debug(
            "Fetched media content: type=%s id=%s bytes=%d mime_type=%s",
            message_type,
            message_id,
            len(fetched_content.data),
            fetched_content.mime_type,
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
            _debug("Running before hook: %s", self._hook_name(hook))
            maybe_value = hook(request)
            if inspect.isawaitable(maybe_value):
                request = await maybe_value
            else:
                request = maybe_value
        return request

    async def _apply_after_hooks(self, response: AguiResponse) -> AguiResponse:
        for hook in reversed(self.pipeline.after_agui):
            _debug("Running after hook: %s", self._hook_name(hook))
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

        _debug(
            "Converted AG-UI response to LINE messages: count=%d", len(line_messages)
        )
        return line_messages

    def _append_line_messages_from_content(
        self,
        line_messages: list[LineMessage],
        content: str | list[OutputContentPart] | dict[str, Any] | None,
    ) -> None:
        if isinstance(content, str) and content.strip():
            self._append_text_messages(line_messages, content)
            return

        if not isinstance(content, list):
            return

        for part in content:
            if part.type == "text" and part.text:
                stripped_text = part.text.strip()
                if not stripped_text:
                    continue
                self._append_text_messages(line_messages, stripped_text)
                continue

            line_message = self._line_message_from_output_part(part)
            if line_message is not None:
                line_messages.append(line_message)

    def _line_message_from_output_part(
        self, part: OutputContentPart
    ) -> LineMessage | None:
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
            return self._build_text_message(f"Document: {url}")
        return None

    def _append_text_messages(
        self, line_messages: list[LineMessage], text: str
    ) -> None:
        for chunk in self._split_text(text):
            line_messages.append(self._build_text_message(chunk))

    def _build_text_message(self, text: str) -> TextMessage:
        return TextMessage(text=text, quickReply=None, quoteToken=None)

    def _split_text(self, text: str) -> list[str]:
        if len(text) <= LINE_MAX_TEXT_LENGTH:
            return [text]

        chunks: list[str] = []
        remaining = text
        while len(remaining) > LINE_MAX_TEXT_LENGTH:
            split_at = self._best_split_index(remaining)
            chunks.append(remaining[:split_at])
            remaining = remaining[split_at:]

        if remaining:
            chunks.append(remaining)

        _debug(
            "Split long LINE text message: original_length=%d chunks=%d",
            len(text),
            len(chunks),
        )
        return chunks

    def _best_split_index(self, text: str) -> int:
        window = text[:LINE_MAX_TEXT_LENGTH]
        for separator in ("\n", "。", " "):
            split_at = window.rfind(separator)
            if split_at > 0:
                # Include the separator in the previous chunk so the next chunk
                # does not start with punctuation/whitespace.
                return split_at + 1
        return LINE_MAX_TEXT_LENGTH

    def _hook_name(self, hook: Any) -> str:
        return getattr(hook, "__name__", hook.__class__.__name__)

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
