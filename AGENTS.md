# Agent Instructions for ariaops-mcp

## Quick Start

- **Install**: `pip install -e .`
- **Dev install**: `pip install -e ".[dev]"`
- **Run (stdio)**: `python -m ariaops_mcp`
- **Run (HTTP)**: `ARIAOPS_TRANSPORT=http ARIAOPS_PORT=8080 python -m ariaops_mcp`

## Configuration

Copy `.env.example` to `.env` and set:
- `ARIAOPS_HOST` (required)
- `ARIAOPS_USERNAME` (required)
- `ARIAOPS_PASSWORD` (required)
- `ARIAOPS_AUTH_SOURCE` (default: `local`)
- `ARIAOPS_VERIFY_SSL` (default: `true`)
- `ARIAOPS_TRANSPORT` (default: `stdio`)
- `ARIAOPS_PORT` (default: `8080`)
- `ARIAOPS_ENABLE_WRITE_OPERATIONS` (default: `false`)

## Verification Commands

Run these in order:

```bash
ruff check src/ tests/
pyright src/
pytest -ra --tb=short -v --maxfail=1
```

## Testing

- Tests use `respx` for HTTP mocking
- Fixtures reset client singleton and settings cache
- Set env vars via `mock_env` fixture or manually:
  ```python
  monkeypatch.setenv("ARIAOPS_HOST", "vrops.test.local")
  ```

## Project Structure

- `src/ariaops_mcp/`: Main package
  - `server.py`: MCP server entry point
  - `client.py`: AriaOps REST client
  - `config.py`: Settings via Pydantic
  - `tools/`: MCP tool implementations
- `tests/`: Test suite with `pytest`

## Key Behaviors

- Transport modes: `stdio` (default) or `http`
- Write operations require explicit enable: `ARIAOPS_ENABLE_WRITE_OPERATIONS=true`
- HTTP mode includes `/health` endpoint
- Client singleton cached per module; reset in tests

## Gotchas

- Python 3.11+ required
- Podman used for container builds (not Docker)
- Use `podman build --format docker` for health checks
- Tests expect mock AriaOps API at `vrops.test.local`