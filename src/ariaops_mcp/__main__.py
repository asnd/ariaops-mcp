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

from ariaops_mcp.client import close_all, get_client
from ariaops_mcp.config import Settings, get_settings
from ariaops_mcp.http_auth import JWTTokenVerifier
from ariaops_mcp.logging_config import configure_logging
from ariaops_mcp.server import create_server


async def _health_check(_request: Request) -> JSONResponse:
    settings = get_settings()
    instances = settings.resolved_instances()
    results: list[dict[str, Any]] = []
    overall_ok = True
    for inst in instances:
        client = get_client(inst.id)
        entry: dict[str, Any] = {"id": inst.id, "circuit_breaker": client.circuit_breaker.state.value}
        try:
            await client.get("/versions/current")
            entry["status"] = "ok"
        except Exception as e:
            entry["status"] = "degraded"
            entry["detail"] = str(e)
            overall_ok = False
        results.append(entry)

    payload: dict[str, Any] = {"status": "ok" if overall_ok else "degraded", "instances": results}
    # Preserve the legacy single-instance shape for existing probes.
    if len(results) == 1:
        payload["circuit_breaker"] = results[0]["circuit_breaker"]
        if results[0]["status"] != "ok" and "detail" in results[0]:
            payload["detail"] = results[0]["detail"]
    return JSONResponse(payload, status_code=200 if overall_ok else 503)


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

    if s.effective_auth_mode == "oauth":
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
    elif s.effective_auth_mode == "ldap":
        from ariaops_mcp.ldap_auth import (
            BasicLDAPAuthBackend,
            BasicRequireAuthMiddleware,
            LDAPAuthenticator,
        )

        authenticator = LDAPAuthenticator.from_settings(s)
        middleware = [
            Middleware(AuthenticationMiddleware, backend=BasicLDAPAuthBackend(authenticator)),
            Middleware(AuthContextMiddleware),
        ]
        # OAuth scope requirements do not apply here: the LDAP backend issues
        # tokens without scopes, so passing http_oauth_required_scopes would
        # 403 every request. Authorization is enforced by principal claims.
        mcp_endpoint = BasicRequireAuthMiddleware(streamable_http_app)

    routes.append(Route("/", endpoint=mcp_endpoint))

    @asynccontextmanager
    async def lifespan(_app: Starlette):
        async with session_manager.run():
            try:
                yield
            finally:
                await close_all()

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
                await close_all()

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
            try:
                async with stdio_server() as (read_stream, write_stream):
                    await server.run(
                        read_stream,
                        write_stream,
                        server.create_initialization_options(),
                    )
            finally:
                await close_all()

        asyncio.run(run_stdio())


if __name__ == "__main__":
    main()
