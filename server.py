#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
机器人资讯本地服务器
用法：python3 server.py
访问：http://localhost:8080
局域网分享：http://<你的IP>:8080
"""

import json
import subprocess
import sys
import socket
from datetime import datetime
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

BASE_DIR   = Path(__file__).parent
PORT       = 8080
NEWS_CACHE = BASE_DIR / 'news_cache.json'
SCRAPER    = BASE_DIR / 'scraper.py'
HTML_FILE  = BASE_DIR / 'index.html'
DOCS_DIR   = BASE_DIR / 'docs'


class Handler(BaseHTTPRequestHandler):

    # ── 路由 ────────────────────────────────────────────────
    def do_GET(self):
        p = urlparse(self.path).path
        if p in ('/', '/index.html'):
            self._serve_file(HTML_FILE, 'text/html; charset=utf-8')
        elif p == '/api/news':
            self._serve_news()
        elif p == '/api/docs':
            self._serve_docs()
        elif p.startswith('/docs/') and p.endswith('.md'):
            name = p.split('/')[-1]
            self._serve_file(DOCS_DIR / name, 'text/plain; charset=utf-8')
        else:
            self.send_error(404)

    def do_POST(self):
        p = urlparse(self.path).path
        if p == '/api/fetch':
            self._run_fetch()
        elif p == '/api/publish':
            self._run_publish()
        else:
            self.send_error(404)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors(); self.end_headers()

    # ── 处理函数 ─────────────────────────────────────────────
    def _serve_file(self, path: Path, ctype: str):
        if not path.exists():
            self.send_error(404, f'File not found: {path.name}')
            return
        data = path.read_bytes()
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', len(data))
        self._cors(); self.end_headers()
        self.wfile.write(data)

    def _serve_news(self):
        if NEWS_CACHE.exists():
            raw = NEWS_CACHE.read_bytes()
        else:
            raw = b'{"items":[],"last_updated":null,"new_count":0,"total":0}'
        self._json(raw)

    def _serve_docs(self):
        DOCS_DIR.mkdir(exist_ok=True)
        files = sorted(DOCS_DIR.glob('*.md'), reverse=True)
        self._json(json.dumps([f.name for f in files]).encode())

    def _run_fetch(self):
        try:
            proc = subprocess.run(
                [sys.executable, str(SCRAPER)],
                capture_output=True, text=True,
                timeout=120, cwd=str(BASE_DIR)
            )
            if NEWS_CACHE.exists():
                nd = json.loads(NEWS_CACHE.read_text('utf-8'))
            else:
                nd = {'new_count': 0, 'total': 0}

            resp = {
                'ok':        proc.returncode == 0,
                'new_count': nd.get('new_count', 0),
                'total':     nd.get('total', 0),
                'log':       (proc.stdout or '')[-3000:],
                'err':       (proc.stderr or '')[-800:],
            }
        except subprocess.TimeoutExpired:
            resp = {'ok': False, 'new_count': 0, 'total': 0,
                    'log': '', 'err': '采集超时（>120秒）'}
        except Exception as e:
            resp = {'ok': False, 'new_count': 0, 'total': 0,
                    'log': '', 'err': str(e)}

        self._json(json.dumps(resp, ensure_ascii=False).encode('utf-8'))

    def _run_publish(self):
        script = BASE_DIR / 'publish.sh'
        if not script.exists():
            self._json(json.dumps({
                'ok': False, 'err': 'publish.sh 不存在，请先按说明配置 Git 仓库'
            }, ensure_ascii=False).encode('utf-8'))
            return
        try:
            proc = subprocess.run(
                ['bash', str(script)],
                capture_output=True, text=True,
                timeout=60, cwd=str(BASE_DIR)
            )
            resp = {
                'ok':  proc.returncode == 0,
                'log': (proc.stdout or '')[-2000:],
                'err': (proc.stderr or '')[-800:],
            }
        except Exception as e:
            resp = {'ok': False, 'log': '', 'err': str(e)}
        self._json(json.dumps(resp, ensure_ascii=False).encode('utf-8'))

    # ── 工具函数 ─────────────────────────────────────────────
    def _cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def _json(self, data: bytes):
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', len(data))
        self._cors(); self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):
        ts = datetime.now().strftime('%H:%M:%S')
        print(f'  [{ts}] {fmt % args}')


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return '127.0.0.1'


if __name__ == '__main__':
    ip = get_local_ip()
    srv = HTTPServer(('0.0.0.0', PORT), Handler)
    print(f'\n{"="*52}')
    print(f'  🤖 机器人资讯助手  已启动')
    print(f'{"="*52}')
    print(f'  本机访问:   http://localhost:{PORT}')
    print(f'  局域网分享: http://{ip}:{PORT}')
    print(f'  停止服务:   Ctrl+C')
    print(f'{"="*52}\n')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\n服务已停止。')
