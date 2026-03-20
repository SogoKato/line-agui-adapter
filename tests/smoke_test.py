"""Minimal smoke test for published distributions."""

from line_agui_adapter import AguiHttpClient, LineAguiAdapter, create_content_fetcher


def main() -> None:
    """Import the package and touch key public exports."""
    assert AguiHttpClient is not None
    assert LineAguiAdapter is not None
    assert create_content_fetcher is not None


if __name__ == "__main__":
    main()
