#!/usr/bin/env bash
# OAuth2 e2e scenario: Keycloak IdP + ariaops-mcp in Podman.
#
#   ./run.sh          start the stack, run the tests, leave it running
#   ./run.sh down     stop and remove the stack
#
# Stack:
#   keycloak          quay.io/keycloak/keycloak (realm 'ariaops', users alice/bob)  -> localhost:8081
#   ariaops-mcp-e2e   ariaops-mcp:latest in OAuth mode, 2 fake instances (se, de)   -> localhost:8090
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$HERE/../.." && pwd)"
NETWORK=${E2E_NETWORK:-ariaops-e2e}
KC_NAME=${E2E_KC_NAME:-keycloak}
MCP_NAME=${E2E_MCP_NAME:-ariaops-mcp-e2e}
KC_IMAGE=quay.io/keycloak/keycloak:23.0
exec 3>&2
progress() {
    if [ -t 3 ]; then printf '[%s/5] %s\n' "$1" "$2" >&3; fi
}

curl_noproxy() {
    curl --noproxy '*' "$@"
}

down() {
    podman rm -f "$KC_NAME" "$MCP_NAME" 2>/dev/null || true
    podman network rm "$NETWORK" 2>/dev/null || true
    echo "Stack removed."
}

if [[ "${1:-}" == "down" ]]; then
    down
    exit 0
fi

echo "==> Building ariaops-mcp image"
progress 1 'Build image'
podman build --format docker -t ariaops-mcp:latest -f "$REPO_ROOT/Containerfile" "$REPO_ROOT" >/dev/null
echo "    built"

echo "==> (Re)creating network and containers"
progress 2 'Start isolated stack'
down >/dev/null 2>&1 || true
podman network create "$NETWORK" >/dev/null

podman run -d --name "$KC_NAME" --network "$NETWORK" --network-alias keycloak \
    -p 8081:8080 \
    -e KEYCLOAK_ADMIN=admin -e KEYCLOAK_ADMIN_PASSWORD=admin \
    -e no_proxy='*' -e NO_PROXY='*' \
    -v "$HERE/realm-ariaops.json:/opt/keycloak/data/import/realm-ariaops.json:z" \
    "$KC_IMAGE" start-dev --import-realm >/dev/null
echo "    keycloak starting on http://localhost:8081 (admin/admin)"

echo "==> Waiting for Keycloak realm import (can take ~60s on first pull)"
progress 3 'Wait for Keycloak'
for i in $(seq 1 60); do
    if curl_noproxy -sf "http://localhost:8081/realms/ariaops/.well-known/openid-configuration" >/dev/null 2>&1; then
        echo "    realm 'ariaops' is up"
        break
    fi
    if [[ $i == 60 ]]; then
        echo "    Keycloak did not come up; logs:"
        podman logs --tail 30 "$KC_NAME"
        exit 1
    fi
    sleep 2
done

podman run -d --name "$MCP_NAME" --network "$NETWORK" \
    -p 8090:8080 \
    --env-file "$HERE/mcp.env" \
    ariaops-mcp:latest >/dev/null
echo "    ariaops-mcp starting on http://localhost:8090"

echo "==> Waiting for MCP server"
progress 4 'Wait for MCP server'
for i in $(seq 1 30); do
    # 401 means the OAuth gate is up and answering — that's "ready" here.
    code=$(curl_noproxy -s -o /dev/null -w '%{http_code}' -X POST "http://localhost:8090/" || true)
    if [[ "$code" == "401" ]]; then
        echo "    MCP server is up (unauthenticated POST -> 401)"
        break
    fi
    if [[ $i == 30 ]]; then
        echo "    MCP server did not come up; logs:"
        podman logs --tail 30 "$MCP_NAME"
        exit 1
    fi
    sleep 1
done

echo "==> Running e2e tests"
progress 5 'Run OAuth and role-routing tests'
cd "$REPO_ROOT"
env no_proxy="localhost,127.0.0.1" NO_PROXY="localhost,127.0.0.1" \
    uv run --extra dev python "$HERE/e2e_oauth.py"
status=$?

echo
echo "Stack is still running:"
echo "  Keycloak admin console:  http://localhost:8081  (admin/admin)"
echo "  MCP server (OAuth):      http://localhost:8090"
echo "  Users: alice/alicepw (ops, all instances), bob/bobpw (country, SE only)"
echo "  Tear down with: $0 down"
exit $status
