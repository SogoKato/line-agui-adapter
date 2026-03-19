# line-agui-adapter
AG-UI Client adapter for LINE Messaging API (@line/line-bot-sdk-python)

`line-bot-sdk-python v3` のハンドラ体験を維持しつつ、

- LINE Webhook Event を AG-UI リクエストに変換
- AG-UI 応答を LINE reply message 形式に変換
- AG-UI 呼び出しの前後に任意処理（middleware hook）を挿入

するための最小アダプタです。

## インストール

```bash
uv add line-agui-adapter
```

## 対応メッセージ

- 受信（LINE → AG-UI）
  - text
  - image / audio / video / file（AG-UI multimodal part に変換）
- 返信（AG-UI → LINE）
  - text
  - image / audio / video（`source.type == "url"` の場合）
  - document は URL をテキストで返却

LINE 側の制約に合わせ、ストリーミングは扱わず最終レスポンスをバッファして返信します。

## examples

- [examples/fastapi/main.py](examples/fastapi/main.py)
	- LINE webhook を受けて AG-UI に転送し、`before` / `after` hook 付きで返信する FastAPI 例
- [examples/fastapi/.env.example](examples/fastapi/.env.example)
	- examples 用の環境変数サンプル

### examples の起動例

設定値は [examples/fastapi/.env.example](examples/fastapi/.env.example) をコピーして利用できます。
AG-UI サーバは別途起動済みである前提です。

```bash
uv add fastapi uvicorn
cp examples/fastapi/.env.example .env
AGUI_ENDPOINT=http://127.0.0.1:8001/run \
LINE_CHANNEL_SECRET=... \
LINE_CHANNEL_ACCESS_TOKEN=... \
uv run uvicorn examples.fastapi.main:app --reload --port 8000
```

## FastAPI での最小利用例

```python
from typing import cast

from linebot.v3 import WebhookParser, WebhookPayload
from linebot.v3.messaging import ApiClient, MessagingApiBlob
from linebot.v3.webhooks import MessageEvent

from line_agui_adapter import AguiHttpClient, LineAguiAdapter, create_content_fetcher


parser = WebhookParser(channel_secret="YOUR_CHANNEL_SECRET")
payload = cast(WebhookPayload, parser.parse(body, x_line_signature, as_payload=True))

with ApiClient(configuration) as api_client:
	blob_api = MessagingApiBlob(api_client)
	adapter = LineAguiAdapter(
		agui_client=AguiHttpClient(endpoint="https://your-agui-server.example.com/run"),
		content_fetcher=create_content_fetcher(blob_api),
	)

	for event in payload.events or []:
		if not isinstance(event, MessageEvent):
			continue

		messages = await adapter.handle_event(event)
```

完全な FastAPI webhook 実装は [examples/fastapi/main.py](examples/fastapi/main.py) を参照してください。

## Middleware hook

AG-UI 呼び出し前後に任意ロジックを挟めます。

```python
async def before_hook(req):
	req.forwarded_props["tenant_id"] = "tenant-a"
	return req


def after_hook(res):
	for message in res.assistant_messages:
		if isinstance(message.content, str) and message.content:
			message.content = f"[AG-UI] {message.content}"
	return res


adapter.pipeline.add_before(before_hook)
adapter.pipeline.add_after(after_hook)
```
