"""四路召回 + 加权 rank 融合（核心检索层）。

四路：
1. vector   —— 向量余弦（等价 Chroma 语义检索）
2. bm25     —— 稀疏关键词（自实现 BM25）
3. entity   —— 实体关系路：问题里的实体 → 图谱邻居 → 命中这些实体的块
4. position —— 位置路：文档首尾块（摘要/结论/总则通常在这里）

融合：score += (RECALL_K - rank) * weight（线性 rank 融合，非 RRF），
同分时按 chunk 文本长度降序，与参考实现一致。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from . import config, store, vectorstore
from .bm25 import bm25_cache
from .extractor import extract_entities
from .graphstore import GraphStore
from .text import tokenize


def _all_chunks(file_id: Optional[str]) -> List[Dict]:
    datas = []
    if file_id:
        d = vectorstore.load(file_id)
        if d:
            datas.append(d)
    else:
        for meta in vectorstore.files():
            d = vectorstore.load(meta["file_id"])
            if d:
                datas.append(d)
    chunks: List[Dict] = []
    for d in datas:
        for c in d["chunks"]:
            c = dict(c)
            c["doc_title"] = d.get("title", c.get("doc_title", ""))
            chunks.append(c)
    return chunks


def _vector_recall(file_id: Optional[str], question: str, k: int) -> List[Dict]:
    return [c for c, _s in vectorstore.search(file_id, question, k)]


def _bm25_recall(chunks: List[Dict], question: str, k: int) -> List[Dict]:
    if not chunks:
        return []
    key = "|".join(c["chunk_id"] for c in chunks[:2000])
    idx = bm25_cache.get_or_build(key, [c["text"] for c in chunks])
    top = idx.top_k(question, k)
    if top:
        return [chunks[i] for i, _s in top]
    # 兜底：无 token 命中时取前几块，保证融合层有数据（对齐参考实现）
    return chunks[: max(1, min(k, len(chunks)))]


def _entity_recall(question: str, seed_chunks: List[Dict], k: int) -> List[Dict]:
    """图谱路：问题直接点名的实体 + 种子块里出现的实体 → 邻居块扩展。

    只靠“问题里出现实体名”太窄（自然提问很少报实体名），所以这里以
    向量/BM25 已召回的块为种子，反查这些块提到的高频实体，再把实体的
    其他块和 1 跳邻居块补回来——这才是图谱对 RAG 真正有用的地方。
    """
    g = GraphStore.load()
    if not g or not g.nodes:
        return []

    weights: Dict[str, float] = {}
    by_id_name: Dict[str, List[str]] = {}
    for nname, node in g.nodes.items():
        for cid in node.get("chunk_ids", []):
            by_id_name.setdefault(cid, []).append(nname)

    # 1) 问题里直接出现的实体（权重最高）
    names = [e["name"] for e in extract_entities(question)]
    for nname in g.nodes:
        if len(nname) >= 2 and nname in question and nname not in names:
            names.append(nname)
    for name in names:
        node = g.find(name)
        if node:
            for cid in node.get("chunk_ids", []):
                weights[cid] = weights.get(cid, 0.0) + 3.0

    # 2) 种子块里的实体 → 该实体的其他块 + 1 跳邻居块
    seed_ids = {c["chunk_id"] for c in seed_chunks}
    seed_ents: Dict[str, float] = {}
    for cid in seed_ids:
        for nname in by_id_name.get(cid, []):
            node = g.nodes[nname]
            seed_ents[nname] = seed_ents.get(nname, 0.0) + 1.0 + 0.15 * len(node.get("chunk_ids", []))
    for nname, _w in sorted(seed_ents.items(), key=lambda kv: -kv[1])[:8]:
        node = g.nodes.get(nname)
        if not node:
            continue
        for cid in node.get("chunk_ids", []):
            if cid not in seed_ids:  # 种子块本身已由向量/BM25 覆盖
                weights[cid] = weights.get(cid, 0.0) + 2.0
        for _rel, nbrs in g.neighbors(nname, hops=1).items():
            for nb in nbrs:
                for cid in (g.nodes.get(nb) or {}).get("chunk_ids", []):
                    weights[cid] = weights.get(cid, 0.0) + 1.0

    if not weights:
        return []
    all_chunks = _all_chunks(None)
    by_id = {c["chunk_id"]: c for c in all_chunks}
    out = [by_id[cid] for cid in weights if cid in by_id]
    out.sort(key=lambda c: (-weights[c["chunk_id"]], -len(c["text"])))
    return out[:k]


def _position_recall(chunks: List[Dict], k: int) -> List[Dict]:
    """文档级首尾块（按文件分组，各取头尾）。"""
    if not chunks:
        return []
    groups: Dict[str, List[Dict]] = {}
    for c in chunks:
        groups.setdefault(c["doc_id"], []).append(c)
    picked: List[Dict] = []
    per_doc = max(1, k // max(1, len(groups)))
    for doc_chunks in groups.values():
        doc_chunks.sort(key=lambda c: c.get("index_in_doc", 0))
        head = per_doc // 2 if per_doc >= 2 else 1
        tail = max(0, per_doc - head)
        picked.extend(doc_chunks[:head])
        if tail:
            picked.extend(doc_chunks[-tail:])
    return picked[:k]


def fuse(routes: Dict[str, List[Dict]], k: int = config.TOP_K) -> List[Tuple[Dict, Dict]]:
    """线性 rank 融合：每路按名次线性衰减再加权。"""
    scores: Dict[str, float] = {}
    chunk_map: Dict[str, Dict] = {}
    detail: Dict[str, Dict[str, float]] = {}
    for name, docs in routes.items():
        w = config.ROUTE_WEIGHTS.get(name, 1.0)
        for rank, c in enumerate(docs):
            cid = c["chunk_id"]
            chunk_map[cid] = c
            add = (len(docs) - rank) * w
            scores[cid] = scores.get(cid, 0.0) + add
            detail.setdefault(cid, {})[name] = round(add, 3)
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], -len(chunk_map[kv[0]]["text"])))
    # 分数闸门：只保留与第一名差距不大的块，避免位置路把无关文档顶进上下文
    top_score = ordered[0][1] if ordered else 0.0
    floor = top_score * 0.35
    out = []
    for cid, sc in ordered:
        if sc < floor and out:
            break
        out.append((chunk_map[cid], {"score": round(sc, 3), "routes": detail.get(cid, {})}))
        if len(out) >= k:
            break
    return out


def retrieve(question: str, file_id: Optional[str] = None, top_k: int = config.TOP_K,
             recall_k: int = config.RECALL_K, use_graph: bool = True) -> Dict:
    chunks = _all_chunks(file_id)
    vector_hits = _vector_recall(file_id, question, recall_k)
    bm25_hits = _bm25_recall(chunks, question, recall_k)
    routes: Dict[str, List[Dict]] = {
        "vector": vector_hits,
        "bm25": bm25_hits,
        "position": _position_recall(chunks, recall_k),
    }
    if use_graph:
        seeds = {c["chunk_id"]: c for c in vector_hits + bm25_hits}
        routes["entity"] = _entity_recall(question, list(seeds.values()), recall_k)
    fused = fuse(routes, top_k)
    return {
        "chunks": [c for c, _m in fused],
        "scores": {c["chunk_id"]: m for c, m in fused},
        "route_counts": {k: len(v) for k, v in routes.items()},
        "candidates": sum(len(v) for v in routes.values()),
    }