const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const file = path.resolve(__dirname, '..', 'index.html');
const server = http.createServer((req, res) => {
  if (req.url.split('?')[0] === '/favicon.ico') { res.writeHead(204); res.end(); return; }
  res.writeHead(200, {'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store'});
  fs.createReadStream(file).pipe(res);
});
server.listen(4174, '127.0.0.1', () => {
  http.get('http://127.0.0.1:4174', res => {
    console.log('Local: http://127.0.0.1:4174 — HTTP ' + res.statusCode);
    res.resume();
  });
});
