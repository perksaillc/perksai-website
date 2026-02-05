import http from 'node:http';

const PORT = process.env.RETELL_PROXY_PORT ? Number(process.env.RETELL_PROXY_PORT) : 3336;
const GATEWAY_HOST = process.env.GATEWAY_HOST || '127.0.0.1';
const GATEWAY_PORT = process.env.GATEWAY_PORT ? Number(process.env.GATEWAY_PORT) : 18789;
const BRIDGE_HOST = process.env.BRIDGE_HOST || '127.0.0.1';
const BRIDGE_PORT = process.env.BRIDGE_PORT ? Number(process.env.BRIDGE_PORT) : 3335;

function proxy(req, res, targetHost, targetPort) {
  const opts = {
    hostname: targetHost,
    port: targetPort,
    path: req.url,
    method: req.method,
    headers: { ...req.headers },
  };

  const upstream = http.request(opts, (up) => {
    res.writeHead(up.statusCode || 502, up.headers);
    up.pipe(res);
  });

  upstream.on('error', (err) => {
    res.writeHead(502, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ ok: false, error: 'bad_gateway', detail: String(err?.message || err) }));
  });

  req.pipe(upstream);
}

const server = http.createServer((req, res) => {
  // Route Retell sync tool-calls + Retell webhooks
  if (req.url?.startsWith('/retell/sync') || req.url?.startsWith('/retell/webhook')) {
    return proxy(req, res, BRIDGE_HOST, BRIDGE_PORT);
  }
  // Route Clawdbot hooks + everything else to Gateway (so /hooks/* keeps working)
  return proxy(req, res, GATEWAY_HOST, GATEWAY_PORT);
});

server.listen(PORT, '127.0.0.1', () => {
  console.log(`retell-reverse-proxy listening on http://127.0.0.1:${PORT}`);
  console.log(`  /retell/sync  -> ${BRIDGE_HOST}:${BRIDGE_PORT}`);
  console.log(`  /*            -> ${GATEWAY_HOST}:${GATEWAY_PORT}`);
});

process.on('SIGINT', () => server.close(() => process.exit(0)));
