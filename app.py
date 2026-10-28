#!/usr/bin/env python
"""启动入口：python app.py [--port 8765]

优先用 FastAPI（若已安装），否则回退标准库 HTTP 服务；两者共用同一套业务代码。
"""
from __future__ import annotations

import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(description="企业知识库图谱生成与 RAG 问答系统")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--backend", choices=["auto", "fastapi", "stdlib"], default="auto")
    args = parser.parse_args()

    backend = args.backend
    if backend == "auto":
        try:
            import fastapi  # noqa: F401
            import uvicorn  # noqa: F401
            backend = "fastapi"
        except ImportError:
            backend = "stdlib"

    if backend == "fastapi":
        from core.server_fastapi import serve
    else:
        from core.server_stdlib import serve

    print(f"  启动模式：{backend}")
    serve(host=args.host, port=args.port)


if __name__ == "__main__":
    sys.exit(main())