#!/bin/sh
set -e

# Build the stdio command from env vars
# MCP_COMMAND: the executable (e.g., "npx")
# MCP_ARGS: space-separated args (e.g., "-y @playwright/mcp@latest --headless")
STDIO_CMD="${MCP_COMMAND} ${MCP_ARGS}"

echo "MCP Sidecar starting..."
echo "  Command: ${STDIO_CMD}"
echo "  Port: ${MCP_PORT}"

exec supergateway \
    --stdio "${STDIO_CMD}" \
    --outputTransport streamableHttp \
    --port "${MCP_PORT}" \
    --streamableHttpPath "/mcp" \
    --healthEndpoint /healthz \
    --cors \
    --logLevel info