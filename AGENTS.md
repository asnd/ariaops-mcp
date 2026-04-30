# Agent Instructions for ariaops-mcp

## Commands

```bash
pip install -e ".[dev]"                  # dev setup (includes ruff, pyright, pytest, respx)
python -m ariaops_mcp                    # run server (stdio)
ARIAOPS_TRANSPORT=http ARIAOPS_PORT=8080 python -m ariaops_mcp  # run server (HTTP)
```

Verification (run in this order):

```bash
ruff check src/ tests/
pyright src/
pytest -ra --tb=short -v --maxfail=1
```

CI also runs: `pytest --cov=src/ariaops_mcp --cov-report=term-missing --durations=10`

## MCP Client STDIO Config

```json
{
  "mcpServers": {
    "ariaops": {
      "command": "python",
      "args": ["-m", "ariaops_mcp"],
      "env": {
        "ARIAOPS_HOST": "vrops.example.com",
        "ARIAOPS_USERNAME": "admin",
        "ARIAOPS_PASSWORD": "secret",
        "ARIAOPS_AUTH_SOURCE": "local",
        "ARIAOPS_VERIFY_SSL": "true"
      }
    }
  }
}
```

## Required Env Vars

- `ARIAOPS_HOST` — hostname only, **no `https://` prefix** (validated, will error with scheme)
- `ARIAOPS_USERNAME`, `ARIAOPS_PASSWORD` — required
- `ARIAOPS_AUTH_SOURCE` — default `local`
- `ARIAOPS_ENABLE_WRITE_OPERATIONS` — default `false`; must be `true` for mutating tools
- `ARIAOPS_LOG_LEVEL` — default `INFO`

Full reference: `.env.example`

## Gotchas

- **`ARIAOPS_ENABLE_WRITE_OPERATIONS` is evaluated at import time.** Tool registry is built at `server.py:28` via module-level `_build_registry()`. The env var must be set before the process starts — changing it at runtime has no effect.
- **`get_settings()` is `@lru_cache`.** Once Settings is instantiated, env var changes are ignored until `get_settings.cache_clear()` is called. Tests do this in `conftest.py`.
- **Client is a module-level singleton** (`client.py:189` `_client`). Tests must set `client_module._client = None` to reset between tests (autouse fixture in conftest).
- **Auth header format is `vRealizeOpsToken`**, not Bearer.
- **`conftest.py` uses `os.environ.setdefault`** to set test env vars at module level because `server.py` triggers `Settings()` during import/collection.
- **`asyncio_mode = "auto"`** in pytest config — no `@pytest.mark.asyncio` needed.
- **Python 3.11+ required.**
- **Container: use `podman build --format docker`** (not plain `podman build`) when HEALTHCHECK is needed.

## Testing

- HTTP mocking via `respx` — no live Aria Ops instance needed
- Tests expect mock host `vrops.test.local`
- `mock_env` fixture in conftest sets standard test env vars (host, user, pass, `VERIFY_SSL=false`)
- `TOKEN_RESPONSE` dict in conftest provides a reusable auth token fixture
