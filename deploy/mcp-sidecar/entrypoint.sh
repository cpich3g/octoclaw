#!/bin/sh
set -e

STDIO_CMD="${MCP_COMMAND} ${MCP_ARGS}"

echo "MCP Sidecar starting..."
echo "  Command: ${STDIO_CMD}"
echo "  Port: ${MCP_PORT}"
echo "  Auth: ${MCP_API_KEY:+enabled}"

if [ -n "${MCP_API_KEY:-}" ]; then
    # Run supergateway on an internal port, auth proxy on the public port
    INTERNAL_PORT=9000
    export MCP_API_KEY
    export INTERNAL_PORT
    export MCP_PORT

    # Start supergateway in background on internal port
    supergateway \
        --stdio "${STDIO_CMD}" \
        --outputTransport streamableHttp \
        --port "${INTERNAL_PORT}" \
        --streamableHttpPath "/mcp" \
        --healthEndpoint /healthz \
        --cors \
        --stateful \
        --logLevel info &

    # Wait for supergateway to be ready
    sleep 2

    # Run auth proxy on the public port
    exec node /auth-proxy.js
else
    # No auth — run supergateway directly
    exec supergateway \
        --stdio "${STDIO_CMD}" \
        --outputTransport streamableHttp \
        --port "${MCP_PORT}" \
        --streamableHttpPath "/mcp" \
        --healthEndpoint /healthz \
        --cors \
        --stateful \
        --logLevel info
fi