"""End-to-end tests against the Google ADK sample server."""

from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from dotenv import load_dotenv
from linebot.v3.messaging import TextMessage
from linebot.v3.webhooks import (
    DeliveryContext,
    MessageEvent,
    TextMessageContent,
    UserSource,
)
from linebot.v3.webhooks.models.event_mode import EventMode

from line_agui_adapter import AguiHttpClient, LineAguiAdapter


PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _wait_for_tcp_port(host: str, port: int, *, timeout: float, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output = process.stdout.read() if process.stdout is not None else ""
            raise RuntimeError(f"Google ADK test server exited early.\n{output}")
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex((host, port)) == 0:
                return
        time.sleep(0.1)

    raise TimeoutError(f"Timed out waiting for {host}:{port} to accept connections")


def _make_text_event(text: str) -> MessageEvent:
    return MessageEvent(
        timestamp=1_710_000_000_000,
        mode=EventMode.ACTIVE,
        webhookEventId="webhook-e2e-1",
        deliveryContext=DeliveryContext(isRedelivery=False),
        replyToken="reply-e2e-1",
        source=UserSource(userId="user-e2e-1"),
        message=TextMessageContent(
            id="message-e2e-1",
            text=text,
            emojis=None,
            mention=None,
            quoteToken="quote-e2e-1",
            quotedMessageId=None,
            markAsReadToken=None,
        ),
    )


@pytest.fixture
def google_adk_server() -> Any:
    if not os.environ.get("GOOGLE_API_KEY"):
        pytest.skip("requires GOOGLE_API_KEY to run the Google ADK E2E test")

    port = _find_free_port()
    env = dict(os.environ)
    env.setdefault("PYTHONPATH", str(PROJECT_ROOT))
    command = [
        sys.executable,
        "-m",
        "uvicorn",
        "tests.servers.google_adk.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    process = subprocess.Popen(
        command,
        cwd=PROJECT_ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )

    try:
        _wait_for_tcp_port("127.0.0.1", port, timeout=20.0, process=process)
        yield f"http://127.0.0.1:{port}"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.mark.e2e
def test_google_adk_server_returns_line_reply_for_greeting(
    google_adk_server: str,
) -> None:
    adapter = LineAguiAdapter(agui_client=AguiHttpClient(endpoint=f"{google_adk_server}/"))

    messages = _run(adapter.handle_event(_make_text_event("hello")))

    assert messages
    assert isinstance(messages[0], TextMessage)
    assert messages[0].text
    assert "hello" in messages[0].text.lower()
