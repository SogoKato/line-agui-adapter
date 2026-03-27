"""Helpers for bridging LINE SDK content APIs into adapter callables."""

from __future__ import annotations

from typing import Any

from .adapter import FetchedContent


def create_content_fetcher(blob_api: Any):
    """Create an async fetcher backed by `MessagingApiBlob`."""

    async def fetch(message_id: str) -> FetchedContent:
        api_response = blob_api.get_message_content_with_http_info(message_id)
        content_type = (
            api_response.headers.get("content-type") if api_response.headers else None
        )
        return FetchedContent(
            data=bytes(api_response.data or b""),
            mime_type=content_type,
        )

    return fetch
