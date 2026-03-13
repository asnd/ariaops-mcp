# Build stage
FROM python:3.11-slim AS builder

WORKDIR /build
COPY pyproject.toml .
COPY README.md .
COPY src/ src/
RUN pip install --no-cache-dir build && python -m build --wheel

# Runtime stage
FROM python:3.11-slim

LABEL org.opencontainers.image.title="ariaops-mcp"
LABEL org.opencontainers.image.description="Read-only MCP server for VMware Aria Operations"
LABEL org.opencontainers.image.source="https://github.com/asnd/ariaops-mcp"

RUN useradd --system --no-create-home --uid 1001 mcpuser

WORKDIR /app
COPY --from=builder /build/dist/*.whl .
RUN pip install --no-cache-dir *.whl && rm *.whl

USER mcpuser

ENV ARIAOPS_TRANSPORT=http
ENV ARIAOPS_PORT=8080
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

ENTRYPOINT ["python", "-m", "ariaops_mcp"]
