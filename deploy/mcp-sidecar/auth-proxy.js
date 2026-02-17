// Auth proxy for MCP sidecar — validates Bearer token, proxies to supergateway
const http = require("http");

const API_KEY = process.env.MCP_API_KEY;
const INTERNAL_PORT = process.env.INTERNAL_PORT || 9000;
const PUBLIC_PORT = process.env.MCP_PORT || 8000;

const server = http.createServer((req, res) => {
  // Health endpoint — probes backend, no auth required
  if (req.url === "/healthz") {
    const healthPath = process.env.MCP_MODE === "http" ? "/health" : "/healthz";
    const probe = http.get(
      `http://127.0.0.1:${INTERNAL_PORT}${healthPath}`,
      { timeout: 3000 },
      (probeRes) => {
        res.writeHead(probeRes.statusCode, { "Content-Type": "text/plain" });
        res.end(probeRes.statusCode === 200 ? "ok" : "backend unhealthy");
      }
    );
    probe.on("error", () => {
      res.writeHead(503, { "Content-Type": "text/plain" });
      res.end("backend unavailable");
    });
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
