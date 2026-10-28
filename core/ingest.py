"""入库流水线：上传 → 解析 → 切块 → 向量化 → （可选）抽实体建图。

和参考实现的差异：这里把「向量入库」和「图谱构建」拆成两个入口，
因为建图要跑实体抽取，文档多时比较慢，前端可以先看到切块再点建图。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Dict, List, Optional

from . import config, extractor, graphstore, store, vectorstore
from .parsers import parse_file
from .splitter import split_document
from .text import stable_hash


def ingest(path: Path, display_name: Optional[str] = None) -> Dict:
    """解析 + 切块 + 向量入库，返回文件信息。

    源文件不在 uploads/ 里时先复制进去：这样「重建」只依赖 uploads/ 目录，
    种子脚本和 API 上传走同一条路径，/api/build?reset 才真的可复现。
    """
    path = Path(path)
    if path.resolve().parent != config.UPLOAD_DIR.resolve():
        target = config.UPLOAD_DIR / path.name
        i = 2
        while target.exists() and target.read_bytes() != path.read_bytes():
            target = config.UPLOAD_DIR / f"{path.stem}_{i}{path.suffix}"
            i += 1
        if not target.exists():
            config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
            target.write_bytes(path.read_bytes())
        path = target

    parsed = parse_file(path)
    name = display_name or parsed["name"]
    file_id = stable_hash(name + str(parsed["chars"]))
    chunks = split_document(file_id, parsed["title"], parsed["text"])
    if not chunks:
        raise ValueError("文档切块后为空，请检查内容")

    info = vectorstore.build_and_save(file_id, parsed["title"], chunks)
    meta = {
        "file_id": file_id,
        "title": parsed["title"],
        "ext": parsed["ext"],
        "name": name,
        "size": path.stat().st_size,
        "chunks": info["chunks"],
        "chars": parsed["chars"],
    }
    store.upsert_file(meta)
    meta["dim"] = info["dim"]
    return meta


def _chunks_of(file_id: Optional[str]) -> List[Dict]:
    datas = []
    if file_id:
        d = vectorstore.load(file_id)
        if d:
            datas.append(d)
    else:
        for f in vectorstore.files():
            d = vectorstore.load(f["file_id"])
            if d:
                datas.append(d)
    out: List[Dict] = []
    for d in datas:
        for c in d["chunks"]:
            c = dict(c)
            c["doc_title"] = d.get("title", "")
            out.append(c)
    return out


def build_graph(file_id: Optional[str] = None, use_llm: bool = False,
                graph_id: str = "default") -> Dict:
    """对指定文件（或全部）抽实体关系并建图。"""
    chunks = _chunks_of(file_id)
    if not chunks:
        raise ValueError("没有可建图的文档，请先上传")
    cache_key = f"graph:{graph_id}:{file_id or 'all'}:{use_llm}:{len(chunks)}"
    cached = store.cache_get(cache_key)
    if cached:
        return {**cached, "cached": True}

    g = graphstore.GraphStore(graph_id)
    t0 = time.perf_counter()
    total_ent, total_tri = 0, 0
    for c in chunks:
        text = f"{c.get('prefix', '')}\n{c['text']}"
        result = extractor.extract(text, use_llm=use_llm)
        g.add(result["entities"], result["triplets"], c)
        total_ent += len(result["entities"])
        total_tri += len(result["triplets"])
    saved = g.save()
    stats = g.stats()
    out = {
        **saved,
        **stats,
        "chunks": len(chunks),
        "entity_mentions": total_ent,
        "triplet_mentions": total_tri,
        "use_llm": use_llm,
        "elapsed_ms": round((time.perf_counter() - t0) * 1000, 1),
    }
    store.cache_set(cache_key, out, ttl=config.CACHE_TTL["graph"])
    return out


def rebuild_all() -> Dict:
    """清空向量与图，按 uploads 目录重新入库（用于评测/演示一键复现）。"""
    uploads = sorted(config.UPLOAD_DIR.glob("*"))
    results = []
    for p in uploads:
        if p.suffix.lower() in config.SUPPORTED_EXT:
            try:
                results.append(ingest(p))
            except Exception as exc:
                results.append({"name": p.name, "error": str(exc)})
    g = build_graph()
    return {"files": results, "graph": g}