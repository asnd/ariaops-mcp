"""Entry point for ariaops-mcp."""

import asyncio
import json
import signal

from ariaops_mcp.config import get_settings
from ariaops_mcp.logging_config import configure_logging
from ariaops_mcp.server import create_server


def main() -> None:
    s = get_settings()
    configure_logging(level=s.log_level, fmt=s.log_format)

    server = create_server()

    if s.transport == "http":
        import uvicorn
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

        session_manager = StreamableHTTPSessionManager(
            app=server,
            event_store=None,
            json_response=False,
            stateless=True,
        )

        async def app(scope, receive, send):
            if scope["type"] == "http" and scope.get("method") == "GET" and scope.get("path") == "/health":
                try:
                    from ariaops_mcp.client import get_client

                    client = get_client()
                    cb_state = client.circuit_breaker.state.value
                    await client.get("/versions/current")
                    body = json.dumps({"status": "ok", "circuit_breaker": cb_state}).encode()
                    status = 200
                except Exception as e:
                    body = json.dumps({"status": "degraded", "detail": str(e)}).encode()
                    status = 503
                await send(
                    {
                        "type": "http.response.start",
                        "status": status,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return

            await session_manager.handle_request(scope, receive, send)

        async def run_http() -> None:
            from ariaops_mcp.client import get_client

            loop = asyncio.get_event_loop()

            async def shutdown() -> None:
                await get_client().close()

            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))

            async with session_manager.run():
                config = uvicorn.Config(
                    app,
                    host="0.0.0.0",
                    port=s.port,
                    log_level=s.log_level.lower(),
                )
                uvicorn_server = uvicorn.Server(config)
                await uvicorn_server.serve()

        asyncio.run(run_http())
    else:
        from mcp.server.stdio import stdio_server

        async def run_stdio() -> None:
            from ariaops_mcp.client import get_client

            try:
                async with stdio_server() as (read_stream, write_stream):
                    await server.run(
                        read_stream,
                        write_stream,
                        server.create_initialization_options(),
                    )
            finally:
                await get_client().close()

        asyncio.run(run_stdio())


if __name__ == "__main__":
    main()
