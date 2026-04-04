"""Unit tests for `LineAguiAdapter` public methods."""

from __future__ import annotations

import asyncio
import base64
from typing import Any, cast

from ag_ui.core import BinaryInputContent, TextInputContent
from linebot.v3.messaging import ImageMessage, TextMessage
from linebot.v3.webhooks import (
    ContentProvider,
    DeliveryContext,
    FileMessageContent,
    GroupSource,
    ImageMessageContent,
    MessageEvent,
    RoomSource,
    TextMessageContent,
    UserSource,
)
from linebot.v3.webhooks.models.event_mode import EventMode

from line_agui_adapter import (
    AguiRequest,
    AguiResponse,
    AguiResponseMessage,
    FetchedContent,
    LineAguiAdapter,
)
from line_agui_adapter.models import OutputContentPart, OutputContentSource


class _RecordingAguiClient:
    def __init__(self, response: AguiResponse) -> None:
        self.response = response
        self.requests: list[AguiRequest] = []

    async def request(self, request: AguiRequest) -> AguiResponse:
        self.requests.append(request)
        return self.response


class _RecordingContentFetcher:
    def __init__(self, content: bytes | FetchedContent) -> None:
        self.content = content
        self.calls: list[str] = []

    async def __call__(self, message_id: str) -> bytes | FetchedContent:
        self.calls.append(message_id)
        return self.content


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _make_text_event(
    text: str,
    *,
    message_id: str = "message-1",
    user_id: str = "user-1",
    reply_token: str = "reply-1",
    webhook_event_id: str = "webhook-1",
) -> MessageEvent:
    return MessageEvent(
        timestamp=1_710_000_000_000,
        mode=EventMode.ACTIVE,
        webhookEventId=webhook_event_id,
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken=reply_token,
        source=UserSource(userId=user_id),
        message=TextMessageContent(
            id=message_id,
            text=text,
            emojis=None,
            mention=None,
            quoteToken="quote-1",
            quotedMessageId=None,
            markAsReadToken=None,
        ),
    )


def _make_group_text_event(
    text: str,
    *,
    message_id: str = "group-message-1",
    group_id: str = "group-1",
    user_id: str = "user-in-group-1",
    reply_token: str = "reply-group-1",
    webhook_event_id: str = "webhook-group-1",
) -> MessageEvent:
    return MessageEvent(
        timestamp=1_710_000_000_000,
        mode=EventMode.ACTIVE,
        webhookEventId=webhook_event_id,
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken=reply_token,
        source=GroupSource(groupId=group_id, userId=user_id),
        message=TextMessageContent(
            id=message_id,
            text=text,
            emojis=None,
            mention=None,
            quoteToken="quote-group-1",
            quotedMessageId=None,
            markAsReadToken=None,
        ),
    )


def _make_room_text_event(
    text: str,
    *,
    message_id: str = "room-message-1",
    room_id: str = "room-1",
    user_id: str = "user-in-room-1",
    reply_token: str = "reply-room-1",
    webhook_event_id: str = "webhook-room-1",
) -> MessageEvent:
    return MessageEvent(
        timestamp=1_710_000_000_000,
        mode=EventMode.ACTIVE,
        webhookEventId=webhook_event_id,
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken=reply_token,
        source=RoomSource(roomId=room_id, userId=user_id),
        message=TextMessageContent(
            id=message_id,
            text=text,
            emojis=None,
            mention=None,
            quoteToken="quote-room-1",
            quotedMessageId=None,
            markAsReadToken=None,
        ),
    )


def _make_image_event(
    *,
    message_id: str = "image-1",
    user_id: str = "user-1",
    provider_type: str = "line",
    original_content_url: str | None = None,
) -> MessageEvent:
    return MessageEvent(
        timestamp=1_710_000_000_000,
        mode=EventMode.ACTIVE,
        webhookEventId="webhook-image-1",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken="reply-image-1",
        source=UserSource(userId=user_id),
        message=ImageMessageContent(
            id=message_id,
            contentProvider=ContentProvider(
                type=provider_type,
                originalContentUrl=original_content_url,
                previewImageUrl=None,
            ),
            imageSet=None,
            quoteToken="quote-image-1",
            markAsReadToken=None,
        ),
    )


def _make_file_event(
    *,
    message_id: str = "file-1",
    user_id: str = "user-1",
    file_name: str = "upload",
) -> MessageEvent:
    return MessageEvent(
        timestamp=1_710_000_000_000,
        mode=EventMode.ACTIVE,
        webhookEventId="webhook-file-1",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken="reply-file-1",
        source=UserSource(userId=user_id),
        message=FileMessageContent(
            id=message_id,
            fileName=file_name,
            fileSize=12,
            markAsReadToken=None,
        ),
    )


def _assistant_response(
    content: str | list[OutputContentPart] | dict[str, Any] | None,
) -> AguiResponse:
    return AguiResponse(
        messages=[
            AguiResponseMessage(
                id="assistant-1",
                role="assistant",
                content=content,
            )
        ],
        raw={},
    )


def test_build_agui_request_from_text_event_includes_line_metadata() -> None:
    client = _RecordingAguiClient(response=_assistant_response("unused"))
    adapter = LineAguiAdapter(agui_client=cast(Any, client))

    request = _run(
        adapter.build_agui_request(
            _make_text_event("hello from line"),
            metadata={"tenant_id": "tenant-1"},
        )
    )

    assert request.thread_id == "line:user:user-1"
    assert request.run_id == "webhook-1"
    assert len(request.messages) == 1
    assert request.messages[0].content == "hello from line"
    assert request.messages[0].name == "user-1"
    assert request.forwarded_props["tenant_id"] == "tenant-1"
    assert request.forwarded_props["line"] == {
        "replyToken": "reply-1",
        "eventType": "message",
        "source": {"type": "user", "user_id": "user-1"},
        "timestamp": 1_710_000_000_000,
        "message": {"id": "message-1", "type": "text"},
    }


def test_build_agui_request_uses_explicit_conversation_id_when_given() -> None:
    client = _RecordingAguiClient(response=_assistant_response("unused"))
    adapter = LineAguiAdapter(agui_client=cast(Any, client))

    request = _run(
        adapter.build_agui_request(
            _make_text_event("hello", webhook_event_id="webhook-override"),
            conversation_id="thread-override",
        )
    )

    assert request.thread_id == "thread-override"
    assert request.run_id == "webhook-override"


def test_build_agui_request_uses_group_id_for_group_source_thread_id() -> None:
    client = _RecordingAguiClient(response=_assistant_response("unused"))
    adapter = LineAguiAdapter(agui_client=cast(Any, client))

    request = _run(adapter.build_agui_request(_make_group_text_event("hello group")))

    assert request.thread_id == "line:group:group-1"
    assert request.messages[0].name == "user-in-group-1"
    assert request.forwarded_props["line"]["source"] == {
        "type": "group",
        "user_id": "user-in-group-1",
        "group_id": "group-1",
    }


def test_build_agui_request_uses_room_id_for_room_source_thread_id() -> None:
    client = _RecordingAguiClient(response=_assistant_response("unused"))
    adapter = LineAguiAdapter(agui_client=cast(Any, client))

    request = _run(adapter.build_agui_request(_make_room_text_event("hello room")))

    assert request.thread_id == "line:room:room-1"
    assert request.messages[0].name == "user-in-room-1"
    assert request.forwarded_props["line"]["source"] == {
        "type": "room",
        "user_id": "user-in-room-1",
        "room_id": "room-1",
    }


def test_build_agui_request_embeds_fetched_media_for_line_hosted_content() -> None:
    fetcher = _RecordingContentFetcher(FetchedContent(b"png-bytes", "image/png"))
    adapter = LineAguiAdapter(
        agui_client=cast(Any, _RecordingAguiClient(response=_assistant_response(None))),
        content_fetcher=fetcher,
    )

    request = _run(adapter.build_agui_request(_make_image_event()))

    assert fetcher.calls == ["image-1"]
    assert isinstance(request.messages[0].content, list)
    content = request.messages[0].content
    assert len(content) == 2
    assert isinstance(content[0], TextInputContent)
    assert content[0].text == "[LINE image message]"
    assert isinstance(content[1], BinaryInputContent)
    assert content[1].mime_type == "image/png"
    assert content[1].data == base64.b64encode(b"png-bytes").decode("ascii")


def test_build_agui_request_falls_back_from_generic_fetched_mime_type() -> None:
    fetcher = _RecordingContentFetcher(
        FetchedContent(b"image-bytes", "application/octet-stream")
    )
    adapter = LineAguiAdapter(
        agui_client=cast(Any, _RecordingAguiClient(response=_assistant_response(None))),
        content_fetcher=fetcher,
    )

    request = _run(adapter.build_agui_request(_make_image_event()))

    assert isinstance(request.messages[0].content, list)
    content = request.messages[0].content
    assert len(content) == 2
    assert isinstance(content[1], BinaryInputContent)
    assert content[1].mime_type == "image/jpeg"
    assert content[1].data == base64.b64encode(b"image-bytes").decode("ascii")


def test_build_agui_request_skips_file_without_extension_without_fetching() -> None:
    fetcher = _RecordingContentFetcher(
        FetchedContent(b"file-bytes", "application/octet-stream")
    )
    adapter = LineAguiAdapter(
        agui_client=cast(Any, _RecordingAguiClient(response=_assistant_response(None))),
        content_fetcher=fetcher,
    )

    request = _run(adapter.build_agui_request(_make_file_event()))

    assert fetcher.calls == []
    assert isinstance(request.messages[0].content, list)
    content = request.messages[0].content
    assert len(content) == 1
    assert isinstance(content[0], TextInputContent)
    assert content[0].text == "[LINE file message: upload]"


def test_build_agui_request_embeds_supported_markdown_file() -> None:
    fetcher = _RecordingContentFetcher(
        FetchedContent(b"# title\nbody\n", "application/octet-stream")
    )
    adapter = LineAguiAdapter(
        agui_client=cast(Any, _RecordingAguiClient(response=_assistant_response(None))),
        content_fetcher=fetcher,
    )

    request = _run(adapter.build_agui_request(_make_file_event(file_name="note.md")))

    assert fetcher.calls == ["file-1"]
    assert isinstance(request.messages[0].content, list)
    content = request.messages[0].content
    assert len(content) == 2
    assert isinstance(content[1], BinaryInputContent)
    assert content[1].mime_type == "text/markdown"
    assert content[1].filename == "note.md"


def test_build_agui_request_skips_unsupported_file_extension_without_fetching() -> None:
    fetcher = _RecordingContentFetcher(FetchedContent(b"zip-bytes", "application/zip"))
    adapter = LineAguiAdapter(
        agui_client=cast(Any, _RecordingAguiClient(response=_assistant_response(None))),
        content_fetcher=fetcher,
    )

    request = _run(
        adapter.build_agui_request(_make_file_event(file_name="archive.zip"))
    )

    assert fetcher.calls == []
    assert isinstance(request.messages[0].content, list)
    content = request.messages[0].content
    assert len(content) == 1
    assert isinstance(content[0], TextInputContent)
    assert content[0].text == "[LINE file message: archive.zip]"


def test_build_agui_request_uses_external_media_url_without_fetching() -> None:
    fetcher = _RecordingContentFetcher(b"should-not-be-used")
    adapter = LineAguiAdapter(
        agui_client=cast(Any, _RecordingAguiClient(response=_assistant_response(None))),
        content_fetcher=fetcher,
    )

    request = _run(
        adapter.build_agui_request(
            _make_image_event(
                provider_type="external",
                original_content_url="https://example.com/image.png",
            )
        )
    )

    assert fetcher.calls == []
    assert isinstance(request.messages[0].content, list)
    content = request.messages[0].content
    assert len(content) == 2
    assert isinstance(content[1], BinaryInputContent)
    assert content[1].url == "https://example.com/image.png"
    assert content[1].mime_type == "image/png"


def test_handle_event_sends_request_through_hooks_and_returns_reply_messages() -> None:
    client = _RecordingAguiClient(response=_assistant_response("hello back"))
    adapter = LineAguiAdapter(agui_client=cast(Any, client))

    def before(request: AguiRequest) -> AguiRequest:
        request.forwarded_props["tenant_id"] = "tenant-42"
        return request

    async def after(response: AguiResponse) -> AguiResponse:
        for message in response.assistant_messages:
            if isinstance(message.content, str):
                message.content = f"[after] {message.content}"
        return response

    adapter.pipeline.add_before(before)
    adapter.pipeline.add_after(after)

    messages = _run(adapter.handle_event(_make_text_event("hello")))

    assert len(client.requests) == 1
    assert client.requests[0].forwarded_props["tenant_id"] == "tenant-42"
    assert len(messages) == 1
    assert isinstance(messages[0], TextMessage)
    assert cast(TextMessage, messages[0]).text == "[after] hello back"


def test_to_line_messages_returns_fallback_text_when_no_assistant_output_exists() -> (
    None
):
    adapter = LineAguiAdapter(
        agui_client=cast(Any, _RecordingAguiClient(response=_assistant_response(None)))
    )

    messages = adapter.to_line_messages(AguiResponse(messages=[], raw={}))

    assert len(messages) == 1
    assert isinstance(messages[0], TextMessage)
    assert cast(TextMessage, messages[0]).text == "対応できる応答がありませんでした。"


def test_to_line_messages_converts_multimodal_parts() -> None:
    adapter = LineAguiAdapter(
        agui_client=cast(Any, _RecordingAguiClient(response=_assistant_response(None)))
    )

    messages = adapter.to_line_messages(
        _assistant_response(
            [
                OutputContentPart(
                    type="image",
                    source=OutputContentSource(
                        type="url",
                        value="https://example.com/image.png",
                    ),
                ),
                OutputContentPart(
                    type="document",
                    source=OutputContentSource(
                        type="url",
                        value="https://example.com/file.pdf",
                    ),
                ),
            ]
        )
    )

    assert len(messages) == 2
    assert isinstance(messages[0], ImageMessage)
    assert isinstance(messages[1], TextMessage)
    assert (
        cast(TextMessage, messages[1]).text == "Document: https://example.com/file.pdf"
    )


def test_to_line_messages_splits_long_string_content_into_multiple_text_messages() -> (
    None
):
    adapter = LineAguiAdapter(
        agui_client=cast(Any, _RecordingAguiClient(response=_assistant_response(None)))
    )

    response = AguiResponse(
        messages=[
            AguiResponseMessage(
                id="assistant-1",
                role="assistant",
                content="a" * 5001,
            )
        ],
        raw={},
    )

    messages = adapter.to_line_messages(response)

    assert len(messages) == 2
    assert isinstance(messages[0], TextMessage)
    assert isinstance(messages[1], TextMessage)
    assert cast(TextMessage, messages[0]).text == "a" * 5000
    assert cast(TextMessage, messages[1]).text == "a"


def test_to_line_messages_splits_long_text_output_parts_into_multiple_text_messages() -> (
    None
):
    adapter = LineAguiAdapter(
        agui_client=cast(Any, _RecordingAguiClient(response=_assistant_response(None)))
    )

    response = AguiResponse(
        messages=[
            AguiResponseMessage(
                id="assistant-1",
                role="assistant",
                content=[OutputContentPart(type="text", text="b" * 10001)],
            )
        ],
        raw={},
    )

    messages = adapter.to_line_messages(response)

    assert len(messages) == 3
    assert all(isinstance(message, TextMessage) for message in messages)
    assert [len(cast(TextMessage, message).text) for message in messages] == [
        5000,
        5000,
        1,
    ]
