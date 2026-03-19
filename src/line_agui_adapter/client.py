"""HTTP client for sending AG-UI requests."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import TypeAdapter

from .event_buffer import AguiEventBuffer, parse_sse_events
from .models import AguiEvent, AguiRequest, AguiResponse

EVENTS_ADAPTER = TypeAdapter(list[AguiEvent])


class AguiHttpClient:
    """Minimal async HTTP transport for AG-UI endpoints."""

    def __init__(
        self,
        endpoint: str,
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
    ) -> None:
        """Store endpoint settings used for AG-UI requests."""
        self.endpoint = endpoint
        self.timeout = timeout
        self.headers = headers or {}

    async def request(self, agui_request: AguiRequest) -> AguiResponse:
        """POST an AG-UI request and normalize the response payload."""
        payload = agui_request.model_dump(by_alias=True, exclude_none=True)
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                self.endpoint, json=payload, headers=self.headers
            )
            response.raise_for_status()
        return self._normalize_response(response)

    def _normalize_response(self, response: httpx.Response) -> AguiResponse:
        """Normalize JSON or event-stream responses into a buffered AG-UI response."""
        content_type = response.headers.get("content-type", "")
        parsed_json: Any | None = None

        try:
            parsed_json = response.json()
        except ValueError:
            parsed_json = None

        if isinstance(parsed_json, dict) and isinstance(
            parsed_json.get("messages"), list
        ):
            return AguiResponse(messages=parsed_json["messages"], raw=parsed_json)

        raw_events = self._extract_raw_events(
            parsed_json=parsed_json,
            response_text=response.text,
            content_type=content_type,
        )
        if raw_events is None:
            raw: dict[str, Any]
            if isinstance(parsed_json, dict):
                raw = parsed_json
            else:
                raw = {
                    "body": response.text,
                    "contentType": content_type,
                }
            return AguiResponse(messages=[], raw=raw)

        events = EVENTS_ADAPTER.validate_python(raw_events)
        buffer = AguiEventBuffer()
        return buffer.buffer(
            events,
            raw={
                "events": raw_events,
                "contentType": content_type,
            },
        )

    def _extract_raw_events(
        self,
        *,
        parsed_json: Any | None,
        response_text: str,
        content_type: str,
    ) -> list[dict[str, Any]] | None:
        """Extract raw AG-UI event objects from supported transport encodings."""
        if isinstance(parsed_json, list) and self._looks_like_event_list(parsed_json):
            return parsed_json
        if isinstance(parsed_json, dict):
            events = parsed_json.get("events")
            if isinstance(events, list) and self._looks_like_event_list(events):
                return events

        if "text/event-stream" in content_type:
            return parse_sse_events(response_text)
        return None

    def _looks_like_event_list(self, value: list[Any]) -> bool:
        """Return whether the payload looks like a list of AG-UI event objects."""
        if not value:
            return True
        return all(isinstance(item, dict) and "type" in item for item in value)
