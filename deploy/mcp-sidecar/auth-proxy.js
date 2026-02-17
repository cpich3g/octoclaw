// Auth proxy for MCP sidecar — validates Bearer token, proxies to supergateway
const http = require("http");

const API_KEY = process.env.MCP_API_KEY;
const INTERNAL_PORT = process.env.INTERNAL_PORT || 9000;
const PUBLIC_PORT = process.env.MCP_PORT || 8000;

const server = http.createServer((req, res) => {
  // Health endpoint — no auth required
  if (req.url === "/healthz") {
    res.writeHead(200, { "Content-Type": "text/plain" });
    res.end("ok");
    return;
  }

  // Validate Bearer token
  const auth = req.headers.authorization || "";
  if (auth !== `Bearer ${API_KEY}`) {
    res.writeHead(401, { "Content-Type": "application/json" });
    res.end(JSON.stringify({ error: "Unauthorized" }));
    return;
  }

  // Proxy to supergateway
  const proxyReq = http.request(
    {
      hostname: "127.0.0.1",
      port: INTERNAL_PORT,
      path: req.url,
      method: req.method,
      headers: req.headers,
    },
    (proxyRes) => {
      res.writeHead(proxyRes.statusCode, proxyRes.headers);
      proxyRes.pipe(res, { end: true });
    }
  );

  // No timeout — SSE connections are long-lived
  proxyReq.setTimeout(0);

  proxyReq.on("error", (err) => {
    if (!res.headersSent) {
      res.writeHead(502, { "Content-Type": "application/json" });
      res.end(JSON.stringify({ error: "Backend unavailable" }));
    }
  });

  req.pipe(proxyReq, { end: true });
});

// No server timeout for SSE streaming
server.timeout = 0;
server.keepAliveTimeout = 0;

server.listen(PUBLIC_PORT, () => {
  console.log(`Auth proxy listening on :${PUBLIC_PORT} → :${INTERNAL_PORT}`);
});
