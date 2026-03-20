"""Integration tests across `LineAguiAdapter` and `AguiHttpClient`."""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest
from ag_ui.core import (
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
)
from linebot.v3.messaging import TextMessage
from linebot.v3.webhooks import (
    DeliveryContext,
    MessageEvent,
    TextMessageContent,
    UserSource,
)
from linebot.v3.webhooks.models.event_mode import EventMode

from line_agui_adapter import AguiHttpClient, LineAguiAdapter


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


@dataclass
class _QueuedResponse:
    body: bytes
    status: int = 200
    headers: dict[str, str] = field(default_factory=dict)


class _IntegrationServer:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._responses: list[_QueuedResponse] = []
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), self._make_handler())
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._httpd.server_port}"

    def enqueue_json(self, payload: dict[str, Any]) -> None:
        self._responses.append(
            _QueuedResponse(
                body=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
        )

    def enqueue_sse(self, events: list[dict[str, Any]]) -> None:
        body = "".join(
            f"data: {json.dumps(event)}\n\n" for event in events
        ) + "data: [DONE]\n\n"
        self._responses.append(
            _QueuedResponse(
                body=body.encode("utf-8"),
                headers={"Content-Type": "text/event-stream"},
            )
        )

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                body_length = int(self.headers.get("Content-Length", "0"))
                raw_body = self.rfile.read(body_length)
                parsed_json = json.loads(raw_body.decode("utf-8"))
                server.requests.append(
                    {
                        "path": self.path,
                        "headers": dict(self.headers.items()),
                        "json": parsed_json,
                    }
                )

                if not server._responses:
                    self.send_response(500)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(b"No queued response")
                    return

                response = server._responses.pop(0)
                self.send_response(response.status)
                for name, value in response.headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(response.body)))
                self.end_headers()
                self.wfile.write(response.body)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

        return Handler


@pytest.fixture
def integration_server() -> Any:
    server = _IntegrationServer()
    try:
        yield server
    finally:
        server.close()


@pytest.mark.integration
def test_handle_event_posts_expected_request_and_returns_json_reply(
    integration_server: _IntegrationServer,
) -> None:
    integration_server.enqueue_json(
        {
            "messages": [
                {
                    "id": "assistant-1",
                    "role": "assistant",
                    "content": "Hello from AG-UI",
                }
            ]
        }
    )
    adapter = LineAguiAdapter(
        agui_client=AguiHttpClient(
            endpoint=f"{integration_server.base_url}/runs",
            headers={"Authorization": "Bearer integration-token"},
        )
    )

    messages = _run(adapter.handle_event(_make_text_event("hello from line")))

    assert len(messages) == 1
    assert isinstance(messages[0], TextMessage)
    assert messages[0].text == "Hello from AG-UI"

    assert len(integration_server.requests) == 1
    request = integration_server.requests[0]
    assert request["path"] == "/runs"
    assert request["headers"]["Authorization"] == "Bearer integration-token"
    assert request["json"]["threadId"] == "line:user:user-1"
    assert request["json"]["runId"] == "webhook-1"
    assert request["json"]["messages"][0]["content"] == "hello from line"
    assert request["json"]["forwardedProps"]["line"]["replyToken"] == "reply-1"


@pytest.mark.integration
def test_handle_event_buffers_sse_response_and_returns_text_reply(
    integration_server: _IntegrationServer,
) -> None:
    integration_server.enqueue_sse(
        [
            TextMessageStartEvent(
                message_id="assistant-1",
                role="assistant",
            ).model_dump(mode="json", by_alias=True, exclude_none=True),
            TextMessageContentEvent(
                message_id="assistant-1",
                delta="Hello",
            ).model_dump(mode="json", by_alias=True, exclude_none=True),
            TextMessageContentEvent(
                message_id="assistant-1",
                delta=" from stream",
            ).model_dump(mode="json", by_alias=True, exclude_none=True),
            TextMessageEndEvent(
                message_id="assistant-1",
            ).model_dump(mode="json", by_alias=True, exclude_none=True),
        ]
    )
    adapter = LineAguiAdapter(
        agui_client=AguiHttpClient(endpoint=f"{integration_server.base_url}/stream")
    )

    messages = _run(adapter.handle_event(_make_text_event("stream please")))

    assert len(messages) == 1
    assert isinstance(messages[0], TextMessage)
    assert messages[0].text == "Hello from stream"
    assert integration_server.requests[0]["path"] == "/stream"
