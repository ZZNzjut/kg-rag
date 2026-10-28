"""可选：FastAPI 适配层（环境里装了 fastapi 时自动启用，行为与标准库版一致）。"""
from __future__ import annotations

import json
from typing import Dict

from fastapi import FastAPI, Request
from fastapi.responses import Response

from . import api

app = FastAPI(title="KGRAG", description="企业知识库图谱生成与 RAG 问答（复现版）", version="1.0.0")


def _q(request: Request) -> Dict[str, str]:
    return {k: v for k, v in request.query_params.items()}


def _resp(result):
    status, payload, ctype = result
    return Response(content=payload, status_code=status, media_type=ctype)


@app.get("/")
def index():
    return _resp(api.dispatch("GET", "/", {}, {}))


@app.get("/static/{path:path}")
def static(path: str):
    return _resp(api.dispatch("GET", f"/static/{path}", {}, {}))


@app.api_route("/api/{path:path}", methods=["GET", "POST", "DELETE", "OPTIONS"])
async def proxy(path: str, request: Request):
    body = await request.body() if request.method in ("POST", "PUT") else b""
    return _resp(api.dispatch(request.method, f"/api/{path}", _q(request), dict(request.headers), body))


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    print(f"\n  KGRAG (FastAPI) 已启动 → http://{host}:{port}/docs\n")
    uvicorn.run(app, host=host, port=port, log_level="warning")