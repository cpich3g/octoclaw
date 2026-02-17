# MCP Sidecar — wraps a stdio MCP server as Streamable HTTP via supergateway
# Usage: docker run -e MCP_COMMAND="npx" -e MCP_ARGS="-y @playwright/mcp@latest" -p 8000:8000 mcp-sidecar
FROM node:22-slim

# System deps for Playwright (chromium) and Azure MCP Server (.NET)
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash curl ca-certificates \
    libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 \
    libcups2 libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 \
    libasound2 libatspi2.0-0 libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

# Pre-install supergateway and common MCP servers globally
RUN npm install -g supergateway@latest \
    @playwright/mcp@latest \
    @azure/mcp@latest \
    @modelcontextprotocol/server-sequential-thinking@latest \
    alpaca-mcp@latest \
    @coingecko/coingecko-mcp@latest \
    @iflow-mcp/panth1823-f1-mcp-server@latest

# Install Playwright Chromium (from the globally installed @playwright/mcp)
RUN cd "$(npm root -g)/@playwright/mcp" \
    && npx playwright install --with-deps chromium

# Environment variables for runtime configuration
ENV MCP_COMMAND="npx"
ENV MCP_ARGS=""
ENV MCP_PORT=8000
ENV MCP_ENV=""

EXPOSE 8000

# Health check via supergateway's health endpoint
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:${MCP_PORT}/healthz || exit 1

# Entrypoint and auth proxy
COPY auth-proxy.js /auth-proxy.js
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/bin/bash", "/entrypoint.sh"]
