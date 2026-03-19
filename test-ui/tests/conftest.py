"""
Test configuration for test-ui/app.py.

Adds the test-ui directory and repo src to sys.path so `app` is importable,
and provides shared fixtures.
"""

import sys
from pathlib import Path

# Make sure test-ui/app.py and src/ariaops_mcp are importable
TEST_UI_DIR = Path(__file__).parent.parent
REPO_ROOT = TEST_UI_DIR.parent
REPO_SRC = REPO_ROOT / "src"

for p in (str(TEST_UI_DIR), str(REPO_SRC)):
    if p not in sys.path:
        sys.path.insert(0, p)

import app  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def reset_token_cache():
    """Reset the in-memory token cache and LITELLM_TOKEN env var between tests."""
    app._token_cache.update({"value": None, "exp": 0})
    app._GLOBAL_SESSION_STATE["llm_token"].update({"value": None, "exp": 0})
    app._GLOBAL_SESSION_STATE["ariaops"].update(
        {"ready": False, "tools": [], "handlers": {}, "client": None, "settings": None}
    )
    app._ariaops_ready = False
    app._mcp_tools = []
    app._mcp_handlers = {}
    yield
    app._token_cache.update({"value": None, "exp": 0})
    app._GLOBAL_SESSION_STATE["llm_token"].update({"value": None, "exp": 0})
    app._GLOBAL_SESSION_STATE["ariaops"].update(
        {"ready": False, "tools": [], "handlers": {}, "client": None, "settings": None}
    )
    app._ariaops_ready = False
    app._mcp_tools = []
    app._mcp_handlers = {}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Remove LLM/proxy env vars so tests start from a clean slate."""
    for var in (
        "LITELLM_TOKEN",
        "HTTP_PROXY",
        "http_proxy",
        "HTTPS_PROXY",
        "https_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        monkeypatch.delenv(var, raising=False)
