# LINE AG-UI Adapter

A lightweight adapter that bridges [LINE Messaging API](https://developers.line.biz/en/docs/messaging-api/) and [AG-UI](https://docs.ag-ui.com/introduction) using [line-bot-sdk-python v3](https://github.com/line/line-bot-sdk-python).

It converts LINE webhook message events into AG-UI requests, sends them to an AG-UI server, and converts the final AG-UI response back into LINE reply messages.

[日本語での紹介記事はこちら Description in Japanese](https://sogo.dev/posts/2026/03/line-agui-adapter)

[![LINE AG-UI Adapter Demo](https://img.youtube.com/vi/SygA3jxyOvg/default.jpg)](https://youtu.be/SygA3jxyOvg)

## Features

- Convert LINE inbound messages to AG-UI input
  - text
    - image / audio / video as multimodal input parts
    - file as multimodal input parts only for supported document/text/spreadsheet extensions
- Convert AG-UI responses back to LINE reply messages
  - text
  - image / audio / video when `source.type == "url"`
  - document responses as a text message containing the document URL
- Add middleware hooks before and after the AG-UI request
- Buffer the final AG-UI response before replying, which fits LINE's non-streaming reply flow

## Installation

Using `uv`:

```bash
uv add line-agui-adapter
```

Using `pip`:

```bash
pip install line-agui-adapter
```

If you want to run the FastAPI example as well:

```bash
uv add fastapi uvicorn python-dotenv
```

## Getting Started

The FastAPI example is available in [examples/fastapi/main.py](examples/fastapi/main.py).

The example assumes that:

- a LINE channel is already configured
- the webhook server is exposed over HTTPS and reachable by LINE
- an AG-UI server is already running
- the required environment variables are set

If you do not have an AG-UI server yet, you can use the sample implementation under [tests/servers](tests/servers). For example, [tests/servers/google_adk/main.py](tests/servers/google_adk/main.py) can be used as a simple AG-UI-compatible test server.

Example startup:

```bash
cp .env.example .env
uv run uvicorn examples.fastapi.main:app --port 8000
```

## FastAPI Example

```python
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
    request.forwarded_props["tenant_id"] = "example-tenant"
    request.forwarded_props["source"] = "line-fastapi-example"
    return request


def after_agui(response):
    for message in response.assistant_messages:
        if isinstance(message.content, str) and message.content:
            message.content = f"[AG-UI] {message.content}"
    return response


@app.post("/callback")
async def callback(
    request: Request, x_line_signature: str = Header(...)
) -> dict[str, bool]:
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
```

## Middleware Hooks

Middleware hooks let you modify the AG-UI request before sending it, or modify the AG-UI response before converting it back to LINE messages.

```python
async def before_hook(request):
    request.forwarded_props["tenant_id"] = "tenant-a"
    return request


def after_hook(response):
    for message in response.assistant_messages:
        if isinstance(message.content, str) and message.content:
            message.content = f"[AG-UI] {message.content}"
    return response


adapter.pipeline.add_before(before_hook)
adapter.pipeline.add_after(after_hook)
```

## Example Environment Variables

Typical variables used by the FastAPI example:

- `LINE_CHANNEL_SECRET`
- `LINE_CHANNEL_ACCESS_TOKEN`
- `AGUI_ENDPOINT`
- `AGUI_AUTH_TOKEN` (optional)
- `LINE_AGUI_FASTAPI_EXAMPLE_LOG_LEVEL` (optional)

Set `LINE_AGUI_FASTAPI_EXAMPLE_LOG_LEVEL` to a standard Python logging level such as `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL` if you want explicit logging configuration in the example app.

## Notes

- LINE replies are generated from the final AG-UI response rather than from streaming output.
- Binary output is only converted to LINE media messages when the AG-UI response provides a URL-based source.
- LINE `file` messages are only forwarded as binary input when their extension is supported by [the OpenAI file inputs guide](https://developers.openai.com/api/docs/guides/file-inputs), such as `.pdf`, `.md`, `.txt`, `.json`, `.html`, `.xml`, `.docx`, `.pptx`, `.csv`, and `.xlsx`. Unsupported extensions are reduced to the fallback text hint only.
- The included Google ADK test server in [tests/servers/google_adk/main.py](tests/servers/google_adk/main.py) disables input blob artifact replacement so image inputs can be passed to the model as actual inline data.
