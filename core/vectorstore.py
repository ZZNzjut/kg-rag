"""向量库：每个文件一个 collection（JSON 持久化），余弦 top-k 检索。

对应参考实现里的 Chroma collection + _simple_vector_store 降级路径，
这里统一成「JSON 持久化 + 内存余弦」，保证没有外部服务也能复现。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import config
from .embed import LocalEmbedder, RemoteEmbedder, cosine, make_embedder
from .text import tokenize


def _path(file_id: str) -> Path:
    return config.VECTOR_DIR / f"{file_id}.json"


def build_and_save(file_id: str, title: str, chunks: List[Dict]) -> Dict:
    texts = [f"{c['prefix']}\n{c['text']}" for c in chunks]
    embedder = make_embedder(texts)
    vecs = [embedder.transform(t) for t in texts]
    payload = {
        "file_id": file_id,
        "title": title,
        "created_at": int(time.time()),
        "embedder": embedder.to_dict(),
        "chunks": [
            {**c, "embed_text": t, "vec": v} for c, t, v in zip(chunks, texts, vecs)
        ],
    }
    _path(file_id).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return {"file_id": file_id, "chunks": len(chunks), "dim": len(vecs[0]) if vecs else 0}


def load(file_id: str) -> Optional[Dict]:
    p = _path(file_id)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def delete(file_id: str) -> bool:
    p = _path(file_id)
    if p.exists():
        p.unlink()
        return True
    return False


def _embedder_from(data: Dict):
    meta = data.get("embedder", {})
    if meta.get("kind") == "remote" and config.EMBED_BASE_URL and config.EMBED_API_KEY:
        return RemoteEmbedder(config.EMBED_BASE_URL, config.EMBED_API_KEY, meta.get("model", config.EMBED_MODEL))
    if meta.get("kind") == "remote":
        # 远程 embedding 不可用：用同语料重建一个本地 embedder，保证仍可检索
        return LocalEmbedder().fit([tokenize(c["embed_text"]) for c in data["chunks"]])
    return LocalEmbedder.from_dict(meta)


def search(file_id: Optional[str], question: str, k: int = config.RECALL_K) -> List[Tuple[Dict, float]]:
    """在单文件或全库上做余弦检索，返回 [(chunk, score)]。"""
    datas: List[Dict] = []
    if file_id:
        d = load(file_id)
        if d:
            datas.append(d)
    else:
        for p in sorted(config.VECTOR_DIR.glob("*.json")):
            try:
                datas.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue
    results: List[Tuple[Dict, float]] = []
    for data in datas:
        emb = _embedder_from(data)
        qv = emb.transform(question)
        for c in data["chunks"]:
            s = cosine(qv, c.get("vec") or [])
            if s > 0:
                results.append((c, s))
    results.sort(key=lambda x: -x[1])
    return results[:k]


def files() -> List[Dict]:
    out = []
    for p in sorted(config.VECTOR_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({
                "file_id": d["file_id"],
                "title": d.get("title", ""),
                "chunks": len(d.get("chunks", [])),
                "created_at": d.get("created_at", 0),
            })
        except Exception:
            continue
    return out


def stats() -> Dict:
    fs = files()
    chunks = sum(f["chunks"] for f in fs)
    size = sum(p.stat().st_size for p in config.VECTOR_DIR.glob("*.json"))
    return {"files": len(fs), "chunks": chunks, "bytes": size}