"""标准库 HTTP 服务（零依赖兜底实现）。"""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlparse

from . import api


class Handler(BaseHTTPRequestHandler):
    server_version = "KGRAG/1.0"
    protocol_version = "HTTP/1.1"

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        query = dict(parse_qsl(parsed.query))
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        try:
            # email.message 保留原始大小写（X-Filename），api 层按小写键取值
            headers = {str(k).lower(): v for k, v in self.headers.items()}
            status, payload, ctype = api.dispatch(method, parsed.path, query, headers, body)
        except Exception as exc:  # 不应发生：api 内部已兜底
            status, payload, ctype = 500, json.dumps(
                {"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False).encode("utf-8"), "application/json"
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        try:
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def do_DELETE(self):
        self._handle("DELETE")

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET,POST,DELETE,OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Filename")
        self.end_headers()

    def log_message(self, fmt, *args):
        if args and str(args[1]).startswith(("5", "4")):
            print(f"[http] {self.address_string()} {fmt % args}")


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.daemon_threads = True
    print(f"\n  KGRAG 已启动 → http://{host}:{port}")
    print("  提示：上传文档 → 建图 → 问答；数据落在 data/ 目录\n")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        httpd.server_close()