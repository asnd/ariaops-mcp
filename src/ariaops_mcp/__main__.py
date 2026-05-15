"""Entry point for ariaops-mcp."""

import asyncio
import signal
from contextlib import asynccontextmanager
from typing import Any

from mcp.server import Server
from mcp.server.auth.middleware.auth_context import AuthContextMiddleware
from mcp.server.auth.middleware.bearer_auth import BearerAuthBackend, RequireAuthMiddleware
from mcp.server.auth.routes import build_resource_metadata_url, create_protected_resource_routes
from mcp.server.fastmcp.server import StreamableHTTPASGIApp
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.authentication import AuthenticationMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ariaops_mcp.client import get_client, get_client_pool
from ariaops_mcp.config import Settings, get_settings
from ariaops_mcp.http_auth import JWTTokenVerifier
from ariaops_mcp.logging_config import configure_logging
from ariaops_mcp.server import create_server


async def _health_check(_request: Request) -> JSONResponse:
    try:
        client = get_client()
        cb_state = client.circuit_breaker.state.value
        await client.get("/versions/current")
        return JSONResponse({"status": "ok", "circuit_breaker": cb_state})
    except Exception as e:
        return JSONResponse({"status": "degraded", "detail": str(e)}, status_code=503)


def create_http_app(
    *,
    server: Server[Any, Any] | None = None,
    settings: Settings | None = None,
    session_manager: Any | None = None,
) -> Starlette:
    s = settings or get_settings()
    server = server or create_server()

    if session_manager is None:
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

        session_manager = StreamableHTTPSessionManager(
            app=server,
            event_store=None,
            json_response=False,
            stateless=True,
        )

    streamable_http_app = StreamableHTTPASGIApp(session_manager)
    routes: list[Route] = [Route("/health", endpoint=_health_check, methods=["GET"])]
    middleware: list[Middleware] = []
    mcp_endpoint: Any = streamable_http_app

    if s.http_oauth_enabled:
        issuer_url = s.http_oauth_issuer_url
        resource_server_url = s.http_oauth_resource_server_url
        if issuer_url is None or resource_server_url is None:
            raise RuntimeError(
                "OAuth is enabled but issuer/resource-server URLs are missing — "
                "this should have been caught by Settings validation."
            )
        verifier = JWTTokenVerifier(s)
        middleware = [
            Middleware(AuthenticationMiddleware, backend=BearerAuthBackend(verifier)),
            Middleware(AuthContextMiddleware),
        ]
        resource_metadata_url = build_resource_metadata_url(resource_server_url)
        routes.extend(
            create_protected_resource_routes(
                resource_url=resource_server_url,
                authorization_servers=[issuer_url],
                scopes_supported=s.http_oauth_required_scopes,
            )
        )
        mcp_endpoint = RequireAuthMiddleware(
            streamable_http_app,
            s.http_oauth_required_scopes,
            resource_metadata_url,
        )

    routes.append(Route("/", endpoint=mcp_endpoint))

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        async with session_manager.run():
            try:
                yield
            finally:
                await get_client_pool().shutdown()
                await get_client().close()

    return Starlette(routes=routes, middleware=middleware, lifespan=lifespan)


def main() -> None:
    s = get_settings()
    configure_logging(level=s.log_level, fmt=s.log_format)

    server = create_server()

    if s.transport == "http":
        import uvicorn

        async def run_http() -> None:
            loop = asyncio.get_event_loop()
            app = create_http_app(server=server, settings=s)

            async def shutdown() -> None:
                await get_client_pool().shutdown()
                await get_client().close()

            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))

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
            from ariaops_mcp.client import get_client, get_client_pool

            try:
                async with stdio_server() as (read_stream, write_stream):
                    await server.run(
                        read_stream,
                        write_stream,
                        server.create_initialization_options(),
                    )
            finally:
                await get_client_pool().shutdown()
                await get_client().close()

        asyncio.run(run_stdio())


if __name__ == "__main__":
    main()
