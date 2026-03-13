"""Entry point for ariaops-mcp."""

import asyncio
import json
import logging

from ariaops_mcp.config import get_settings
from ariaops_mcp.server import create_server


def main() -> None:
    s = get_settings()
    logging.basicConfig(level=s.log_level)

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
                body = json.dumps({"status": "ok"}).encode("utf-8")
                await send(
                    {
                        "type": "http.response.start",
                        "status": 200,
                        "headers": [(b"content-type", b"application/json")],
                    }
                )
                await send({"type": "http.response.body", "body": body})
                return

            await session_manager.handle_request(scope, receive, send)

        async def run_http() -> None:
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
            async with stdio_server() as (read_stream, write_stream):
                await server.run(
                    read_stream,
                    write_stream,
                    server.create_initialization_options(),
                )

        asyncio.run(run_stdio())


if __name__ == "__main__":
    main()
