"""Minimal FastAPI example for receiving LINE webhooks and calling AG-UI."""

from __future__ import annotations

import logging
import os
from typing import cast

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request
from linebot.v3 import WebhookParser, WebhookPayload
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    MessagingApiBlob,
    ReplyMessageRequest,
    ShowLoadingAnimationRequest,
)
from linebot.v3.webhooks import MessageEvent

from line_agui_adapter import AguiHttpClient, LineAguiAdapter, create_content_fetcher

load_dotenv()

LOG_LEVEL = os.environ.get("LINE_AGUI_FASTAPI_EXAMPLE_LOG_LEVEL", "").upper()

if LOG_LEVEL:
    resolved_log_level = getattr(logging, LOG_LEVEL, None)
    if isinstance(resolved_log_level, int):
        logging.basicConfig(level=resolved_log_level)
        logging.getLogger().setLevel(resolved_log_level)

app = FastAPI()
parser = WebhookParser(channel_secret=os.environ["LINE_CHANNEL_SECRET"])
configuration = Configuration(access_token=os.environ["LINE_CHANNEL_ACCESS_TOKEN"])
agui_client = AguiHttpClient(
    endpoint=os.environ["AGUI_ENDPOINT"],
    headers=(
        {"Authorization": f"Bearer {os.environ['AGUI_AUTH_TOKEN']}"}
        if os.environ.get("AGUI_AUTH_TOKEN")
        else {}
    ),
)


async def before_agui(request):
    """Attach example metadata before the AG-UI request is sent."""
    request.forwarded_props["tenant_id"] = "example-tenant"
    request.forwarded_props["source"] = "line-fastapi-example"
    return request


def after_agui(response):
    """Prefix assistant text so response rewriting is easy to spot."""
    for message in response.assistant_messages:
        if isinstance(message.content, str) and message.content:
            message.content = f"[AG-UI] {message.content}"
    return response


@app.post("/callback")
async def callback(
    request: Request, x_line_signature: str = Header(...)
) -> dict[str, bool]:
    """Parse a LINE webhook request, forward message events, and reply."""
    body = (await request.body()).decode("utf-8")

    try:
        payload = cast(
            WebhookPayload, parser.parse(body, x_line_signature, as_payload=True)
        )
    except InvalidSignatureError as exc:
        raise HTTPException(status_code=400, detail="invalid signature") from exc

    with ApiClient(configuration) as api_client:
        line_api = MessagingApi(api_client)
        blob_api = MessagingApiBlob(api_client)
        adapter = LineAguiAdapter(
            agui_client=agui_client,
            content_fetcher=create_content_fetcher(blob_api),
        )
        adapter.pipeline.add_before(before_agui)
        adapter.pipeline.add_after(after_agui)

        for event in payload.events or []:
            if not isinstance(event, MessageEvent):
                continue
            if event.mode == "standby" or not event.reply_token:
                continue

            user_id = getattr(event.source, "user_id", None)
            if user_id:
                line_api.show_loading_animation(
                    ShowLoadingAnimationRequest(
                        chatId=user_id,
                        loadingSeconds=60,
                    )
                )

            messages = await adapter.handle_event(event)
            line_api.reply_message(
                ReplyMessageRequest(
                    replyToken=event.reply_token,
                    messages=messages,
                    notificationDisabled=False,
                )
            )

    return {"ok": True}
