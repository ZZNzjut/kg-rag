"""HTTP 接口层（纯逻辑，不绑定框架）。

server_stdlib.py 和 server_fastapi.py 都只是把请求转成
dispatch(method, path, query, headers, body) 然后回写响应，
这样无论环境里有没有 FastAPI，行为完全一致。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import unquote

from . import config, graphstore, ingest, qa, store, vectorstore

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"

_SAFE_NAME = re.compile(r"[^\w\u4e00-\u9fa5.\-（）()]+")


def _json(status: int, payload) -> Tuple[int, bytes, str]:
    return status, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8"


def _file_response(path: Path) -> Tuple[int, bytes, str]:
    if not path.exists() or not path.is_file():
        return _json(404, {"error": f"not found: {path.name}"})
    ctype = {
        ".html": "text/html; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".json": "application/json; charset=utf-8",
        ".svg": "image/svg+xml",
        ".png": "image/png",
    }.get(path.suffix.lower(), "application/octet-stream")
    return 200, path.read_bytes(), ctype


def _safe_upload_name(name: str) -> str:
    name = unquote(name or "upload.txt").replace("\\", "/").split("/")[-1]
    name = _SAFE_NAME.sub("_", name).strip("._") or "upload.txt"
    if not Path(name).suffix:
        name += ".txt"
    return name[:120]


def handle_upload(filename: str, body: bytes) -> Tuple[int, bytes, str]:
    if not body:
        return _json(400, {"error": "文件内容为空"})
    name = _safe_upload_name(filename)
    ext = Path(name).suffix.lower()
    if ext not in config.SUPPORTED_EXT:
        return _json(400, {"error": f"不支持的扩展名 {ext}，支持：{sorted(config.SUPPORTED_EXT)}"})
    target = config.UPLOAD_DIR / name
    if target.exists():
        stem, suffix = target.stem, target.suffix
        i = 2
        while target.exists():
            target = config.UPLOAD_DIR / f"{stem}_{i}{suffix}"
            i += 1
        name = target.name
    target.write_bytes(body)
    try:
        meta = ingest.ingest(target, display_name=name)
    except Exception as exc:
        target.unlink(missing_ok=True)
        return _json(400, {"error": str(exc)})
    return _json(200, {"ok": True, "file": meta})


def dispatch(method: str, path: str, query: Dict[str, str],
             headers: Optional[Dict[str, str]] = None,
             body: bytes = b"") -> Tuple[int, bytes, str]:
    headers = headers or {}
    method = method.upper()
    path = path.rstrip("/") or "/"

    try:
        # ---------- 静态页面 ----------
        if method == "GET" and (path == "/" or path == "/index.html"):
            return _file_response(WEB_DIR / "index.html")
        if method == "GET" and path.startswith("/static/"):
            return _file_response(WEB_DIR / path[len("/static/"):])

        # ---------- 健康检查 / 总览 ----------
        if method == "GET" and path == "/api/health":
            return _json(200, {
                "ok": True,
                "llm": config.llm_enabled(),
                "embed": "remote" if (config.EMBED_BASE_URL and config.EMBED_API_KEY) else "local_tfidf",
                "files": len(store.list_files()),
                "vectors": vectorstore.stats(),
                "db": store.stats(),
            })

        if method == "GET" and path == "/api/status":
            files = store.list_files()
            g = graphstore.GraphStore.load(query.get("graph_id", "default"))
            return _json(200, {
                "files": len(files),
                "chunks": sum(int(f.get("chunks") or 0) for f in files),
                "nodes": len(g.nodes) if g else 0,
                "llm": config.llm_enabled(),
            })

        if method == "GET" and path == "/api/files":
            return _json(200, {"files": store.list_files(), "vectors": vectorstore.stats()})

        # ---------- 上传 ----------
        if method == "POST" and path == "/api/upload":
            filename = headers.get("x-filename") or query.get("name", "")
            return handle_upload(filename, body)

        # ---------- 建图 ----------
        if method == "POST" and path in ("/api/build", "/api/rebuild"):
            payload = json.loads(body.decode("utf-8") or "{}")
            if payload.get("reset"):
                for f in vectorstore.files():
                    vectorstore.delete(f["file_id"])
                for p in config.GRAPH_DIR.glob("*.json"):
                    p.unlink(missing_ok=True)
                for f in store.list_files():
                    store.delete_file(f["file_id"])
                store.clear_history()
                store.cache_clear()
                ingest.rebuild_all()
            result = ingest.build_graph(
                file_id=payload.get("file_id") or None,
                use_llm=bool(payload.get("use_llm")),
            )
            g = graphstore.GraphStore.load("default")
            sub = g.subgraph(limit=200) if g else {"nodes": [], "edges": [], "communities": []}
            st = g.stats() if g else {}
            return _json(200, {
                "ok": True,
                "nodes": sub["nodes"],
                "edges": sub["edges"],
                "communities": sub["communities"],
                "stats": {k: st.get(k) for k in ("nodes", "edges", "built_at")},
                "type_distribution": st.get("types", {}),
                "relation_distribution": st.get("relations", {}),
                "top_entities": st.get("top_entities", []),
                "edge_count": st.get("edges", 0),
                "elapsed_ms": result.get("elapsed_ms"),
                "chunks": result.get("chunks"),
                "cached": result.get("cached", False),
            })

        # ---------- 图谱 ----------
        if method == "GET" and path == "/api/graph":
            gid = query.get("graph_id", "default")
            g = graphstore.GraphStore.load(gid)
            if not g:
                return _json(404, {"error": "图谱尚未构建，请先点「构建图谱」"})
            limit = int(query.get("limit", "120"))
            sub = g.subgraph(limit=limit)
            return _json(200, {**sub, "stats": g.stats()})

        if method == "GET" and path == "/api/graph/stats":
            g = graphstore.GraphStore.load(query.get("graph_id", "default"))
            return _json(200, {"stats": g.stats() if g else None})

        # ---------- 问答 ----------
        if method == "POST" and path in ("/api/ask", "/api/qa"):
            payload = json.loads(body.decode("utf-8") or "{}")
            question = (payload.get("question") or "").strip()
            if not question:
                return _json(400, {"error": "question 不能为空"})
            result = qa.ask(
                question,
                file_id=payload.get("file_id") or None,
                use_graph=bool(payload.get("use_graph", True)),
                top_k=int(payload.get("top_k") or config.TOP_K),
            )
            return _json(200, result)

        if method == "GET" and path in ("/api/history", "/api/qa/history"):
            limit = int(query.get("limit", "20"))
            return _json(200, {"history": store.list_history(limit)})

        if method == "DELETE" and path in ("/api/history", "/api/qa/history"):
            store.clear_history()
            return _json(200, {"ok": True})

        return _json(404, {"error": f"未知接口: {method} {path}"})
    except json.JSONDecodeError:
        return _json(400, {"error": "请求体不是合法 JSON"})
    except KeyError as exc:
        return _json(400, {"error": f"缺少字段: {exc}"})
    except Exception as exc:  # 兜底：把异常类型带回去，便于前端排查
        return _json(500, {"error": f"{type(exc).__name__}: {exc}"})