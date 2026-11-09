// A tiny conference schedule service. Replace it with your own app: keep
// listening on $PORT and keep /healthz and /readyz, and the manifests in
// deploy/ keep working.
const http = require('node:http');

const port = Number(process.env.PORT || 8080);
const team = process.env.TEAM_NAME || 'your-team';
const version = process.env.APP_VERSION || 'dev';

const sessions = [
  { id: 1, time: '10:00', room: 'Main', title: 'Kickoff and sponsor talks' },
  { id: 2, time: '11:00', room: 'Everywhere', title: 'Build' },
  { id: 3, time: '14:00', room: 'Main', title: 'Panel' },
  { id: 4, time: '17:30', room: 'Main', title: 'Submissions close' },
];

const server = http.createServer((req, res) => {
  let url;
  try {
    url = new URL(req.url, 'http://localhost');
  } catch {
    res.writeHead(400, { 'content-type': 'text/plain' });
    return res.end('bad request\n');
  }
  if (url.pathname === '/healthz' || url.pathname === '/readyz') {
    res.writeHead(200, { 'content-type': 'text/plain' });
    return res.end('ok\n');
  }
  if (url.pathname === '/api/sessions') {
    res.writeHead(200, { 'content-type': 'application/json' });
    return res.end(JSON.stringify({ version, sessions }));
  }
  if (url.pathname === '/') {
    res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
    const rows = sessions.map((s) => `<li>${s.time} · ${s.room} · ${s.title}</li>`).join('');
    return res.end(`<!doctype html><title>${team}</title><h1>${team}</h1><p>version ${version}</p><ul>${rows}</ul>`);
  }
  res.writeHead(404, { 'content-type': 'text/plain' });
  res.end('not found\n');
});

server.listen(port, () => console.log(`listening on :${port}, version ${version}`));

// Exit cleanly when Kubernetes stops the pod, so rollouts don't drop requests.
process.on('SIGTERM', () => server.close(() => process.exit(0)));
