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
    SG_PID=$!

    # Wait for supergateway to be ready
    sleep 2

    # Run auth proxy in background
    node /auth-proxy.js &
    PROXY_PID=$!

    # If either process exits, kill the other and exit (so ACA restarts the container)
    trap "kill $SG_PID $PROXY_PID 2>/dev/null; exit 1" TERM INT
    wait -n $SG_PID $PROXY_PID 2>/dev/null
    echo "Process exited, shutting down..."
    kill $SG_PID $PROXY_PID 2>/dev/null
    exit 1
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