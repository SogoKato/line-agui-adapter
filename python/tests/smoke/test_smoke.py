"""Pytest smoke tests for public package exports."""

from line_agui_adapter import AguiHttpClient, LineAguiAdapter, create_content_fetcher


def test_public_exports_are_importable() -> None:
    assert AguiHttpClient is not None
    assert LineAguiAdapter is not None
    assert create_content_fetcher is not None
