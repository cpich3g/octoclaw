#!/bin/bash

STDIO_CMD="${MCP_COMMAND} ${MCP_ARGS}"

echo "MCP Sidecar starting..."
echo "  Command: ${STDIO_CMD}"
echo "  Mode: ${MCP_MODE:-stdio}"
echo "  Port: ${MCP_PORT}"
echo "  Auth: ${MCP_API_KEY:+enabled}"

# HTTP mode: the MCP server is already HTTP-native, just proxy through auth
if [ "${MCP_MODE}" = "http" ]; then
    HTTP_PORT="${MCP_HTTP_PORT:-3000}"
    export INTERNAL_PORT="${HTTP_PORT}"
    export MCP_API_KEY
    export MCP_PORT

    # Start the HTTP MCP server in background
    ${MCP_COMMAND} ${MCP_ARGS} 2>&1 &
    SERVER_PID=$!
    sleep 3

    if ! kill -0 $SERVER_PID 2>/dev/null; then
        echo "ERROR: MCP server exited prematurely"
        exit 1
    fi
    echo "MCP HTTP server running (PID ${SERVER_PID}) on port ${HTTP_PORT}"

    # Run auth proxy in background
    node /auth-proxy.js 2>&1 &
    PROXY_PID=$!
    sleep 1

    if ! kill -0 $PROXY_PID 2>/dev/null; then
        echo "ERROR: auth proxy exited prematurely"
        exit 1
    fi
    echo "auth proxy running (PID ${PROXY_PID})"

    trap "kill $SERVER_PID $PROXY_PID 2>/dev/null; exit 1" TERM INT
    wait -n $SERVER_PID $PROXY_PID
    echo "A process exited, shutting down..."
    kill $SERVER_PID $PROXY_PID 2>/dev/null
    exit 1
fi

# stdio mode (default): wrap with supergateway
if [ -n "${MCP_API_KEY:-}" ]; then
    INTERNAL_PORT=9000
    export MCP_API_KEY
    export INTERNAL_PORT
    export MCP_PORT

    supergateway \
        --stdio "${STDIO_CMD}" \
        --outputTransport streamableHttp \
        --port "${INTERNAL_PORT}" \
        --streamableHttpPath "/mcp" \
        --healthEndpoint /healthz \
        --cors \
        --stateful \
        --sessionTimeout 300 \
        --logLevel info 2>&1 &
    SG_PID=$!

    sleep 3

    if ! kill -0 $SG_PID 2>/dev/null; then
        echo "ERROR: supergateway exited prematurely"
        exit 1
    fi
    echo "supergateway running (PID ${SG_PID})"

    node /auth-proxy.js 2>&1 &
    PROXY_PID=$!
    sleep 1

    if ! kill -0 $PROXY_PID 2>/dev/null; then
        echo "ERROR: auth proxy exited prematurely"
        exit 1
    fi
    echo "auth proxy running (PID ${PROXY_PID})"

    trap "kill $SG_PID $PROXY_PID 2>/dev/null; exit 1" TERM INT
    wait -n $SG_PID $PROXY_PID
    echo "A process exited, shutting down..."
    kill $SG_PID $PROXY_PID 2>/dev/null
    exit 1
else
    exec supergateway \
        --stdio "${STDIO_CMD}" \
        --outputTransport streamableHttp \
        --port "${MCP_PORT}" \
        --streamableHttpPath "/mcp" \
        --healthEndpoint /healthz \
        --cors \
        --stateful \
        --sessionTimeout 300 \
        --logLevel info
fi