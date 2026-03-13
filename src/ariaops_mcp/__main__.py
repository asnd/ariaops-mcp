"""Entry point for ariaops-mcp."""

import asyncio
import logging

from ariaops_mcp.config import settings
from ariaops_mcp.server import create_server


def main() -> None:
    logging.basicConfig(level=settings.log_level)

    server = create_server()

    if settings.transport == "http":
        import uvicorn
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

        session_manager = StreamableHTTPSessionManager(
            app=server,
            event_store=None,
            json_response=False,
            stateless=True,
        )

        async def run_http() -> None:
            async with session_manager.run():
                config = uvicorn.Config(
                    session_manager.handle_request,
                    host="0.0.0.0",
                    port=settings.port,
                    log_level=settings.log_level.lower(),
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
