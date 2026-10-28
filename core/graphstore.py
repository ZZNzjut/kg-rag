"""知识图谱存储与查询：节点/边累积、社区划分（标签传播）、子图导出。

对应参考实现里 Neo4j 承担的角色。这里用 JSON 持久化 + 内存索引，
接口保持「建图 → 查邻居 → 子图 → 社区」四件事，方便日后替换成 Neo4j。
"""
from __future__ import annotations

import json
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from . import config


def _path(graph_id: str) -> Path:
    return config.GRAPH_DIR / f"{graph_id}.json"


def _node_id(name: str) -> str:
    return name


_TYPE_PRIORITY = {"ORG": 0, "DOC": 1, "ROLE": 2, "POLICY": 3, "SYSTEM": 4, "DEPT": 5}


class GraphStore:
    def __init__(self, graph_id: str = "default"):
        self.graph_id = graph_id
        self.nodes: Dict[str, Dict] = {}
        self.edges: Dict[Tuple[str, str, str], Dict] = {}
        self.built_at = 0

    # ---------- 构建 ----------
    def add(self, entities: List[Dict], triplets: List[Dict], chunk: Dict) -> None:
        doc_id = chunk["doc_id"]
        chunk_id = chunk["chunk_id"]
        for e in entities:
            name = e.get("name")
            if not name:
                continue
            node = self.nodes.setdefault(name, {
                "id": name, "name": name, "type": e.get("type", "ORG"),
                "mentions": 0, "chunk_ids": [], "doc_ids": [],
            })
            node["mentions"] += int(e.get("mentions", 1) or 1)
            if chunk_id not in node["chunk_ids"]:
                node["chunk_ids"].append(chunk_id)
            if doc_id not in node["doc_ids"]:
                node["doc_ids"].append(doc_id)
            # 类型取更具体的（DEPT > SYSTEM > POLICY > ROLE > DOC > ORG）
            new_type = e.get("type", "ORG")
            if _TYPE_PRIORITY.get(new_type, 0) > _TYPE_PRIORITY.get(node["type"], 0):
                node["type"] = new_type

        for t in triplets:
            h, r, tl = t.get("head"), t.get("relation", "相关"), t.get("tail")
            if not h or not tl or h == tl:
                continue
            if h not in self.nodes or tl not in self.nodes:
                # 三元组两端必须都是已抽出的节点，避免脏边
                if h not in self.nodes:
                    self.nodes[h] = {"id": h, "name": h, "type": "ORG", "mentions": 1,
                                     "chunk_ids": [chunk_id], "doc_ids": [doc_id]}
                if tl not in self.nodes:
                    self.nodes[tl] = {"id": tl, "name": tl, "type": "ORG", "mentions": 1,
                                      "chunk_ids": [chunk_id], "doc_ids": [doc_id]}
            key = (h, tl, r)
            edge = self.edges.setdefault(key, {"src": h, "dst": tl, "rel": r, "weight": 0,
                                               "chunk_ids": [], "evidence": []})
            edge["weight"] += 1
            if chunk_id not in edge["chunk_ids"]:
                edge["chunk_ids"].append(chunk_id)
            sent = t.get("sentence") or ""
            if sent and sent not in edge["evidence"] and len(edge["evidence"]) < 3:
                edge["evidence"].append(sent)

    # 同块共现边：以「本块提及最多的实体」为锚点做星型连接，
        # 避免完全贪心两两连边把图拉成毛球
        local = [e["name"] for e in entities if e.get("name") in self.nodes][:8]
        if len(local) >= 2:
            hub = max(local, key=lambda n: len(self.nodes[n]["chunk_ids"]))
            for other in local:
                if other == hub:
                    continue
                key = (hub, other, "共现")
                if key not in self.edges:
                    self.edges[key] = {"src": hub, "dst": other, "rel": "共现", "weight": 1,
                                       "chunk_ids": [chunk_id], "evidence": []}

    # ---------- 持久化 ----------
    def save(self) -> Dict:
        self.built_at = int(time.time())
        payload = {
            "graph_id": self.graph_id,
            "built_at": self.built_at,
            "nodes": list(self.nodes.values()),
            "edges": [dict(k=list(k), **v) for k, v in self.edges.items()],
        }
        _path(self.graph_id).write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return {"graph_id": self.graph_id, "nodes": len(self.nodes), "edges": len(self.edges)}

    @classmethod
    def load(cls, graph_id: str = "default") -> Optional["GraphStore"]:
        p = _path(graph_id)
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        g = cls(graph_id)
        g.built_at = data.get("built_at", 0)
        for n in data.get("nodes", []):
            g.nodes[n["name"]] = n
        for e in data.get("edges", []):
            key = (e["src"], e["dst"], e["rel"])
            g.edges[key] = {"src": e["src"], "dst": e["dst"], "rel": e["rel"],
                            "weight": e.get("weight", 1), "chunk_ids": e.get("chunk_ids", []),
                            "evidence": e.get("evidence", [])}
        return g

    def stats(self) -> Dict:
        types = Counter(n.get("type", "ORG") for n in self.nodes.values())
        rels = Counter(e.get("rel", "相关") for e in self.edges.values())
        top = sorted(self.nodes.values(), key=lambda n: -len(n.get("chunk_ids", [])))[:10]
        return {
            "graph_id": self.graph_id,
            "nodes": len(self.nodes),
            "edges": len(self.edges),
            "types": dict(types),
            "relations": dict(rels),
            "top_entities": [{"name": n["name"], "type": n["type"], "chunks": len(n["chunk_ids"])} for n in top],
            "built_at": self.built_at,
        }

    # ---------- 查询 ----------
    def find(self, name: str) -> Optional[Dict]:
        if name in self.nodes:
            return self.nodes[name]
        # 模糊：包含关系取最长匹配
        cands = [n for n in self.nodes if name and (name in n or n in name)]
        if not cands:
            return None
        cands.sort(key=len, reverse=True)
        return self.nodes[cands[0]]

    def neighbors(self, name: str, hops: int = 1) -> Dict[str, Set[str]]:
        """返回 {关系: {邻居名}}，hops 控制扩散层数。"""
        out: Dict[str, Set[str]] = defaultdict(set)
        frontier = {name}
        seen = {name}
        for _ in range(max(1, hops)):
            nxt = set()
            for (src, dst, rel) in self.edges:
                if src in frontier and dst not in seen:
                    out[rel].add(dst)
                    nxt.add(dst)
                elif dst in frontier and src not in seen:
                    out[rel].add(src)
                    nxt.add(src)
            seen |= nxt
            frontier = nxt
            if not frontier:
                break
        return dict(out)

    def chunk_ids_for(self, names: List[str]) -> Set[str]:
        ids: Set[str] = set()
        for n in names:
            node = self.find(n)
            if node:
                ids.update(node.get("chunk_ids", []))
        return ids

    def communities(self, max_iter: int = 6) -> Dict[str, List[str]]:
        """标签传播社区发现（LPA 简化版）。"""
        if not self.nodes:
            return {}
        adj: Dict[str, Set[str]] = defaultdict(set)
        for (src, dst, _rel) in self.edges:
            adj[src].add(dst)
            adj[dst].add(src)
        labels = {n: n for n in self.nodes}
        for _ in range(max_iter):
            changed = False
            for n in self.nodes:
                if not adj[n]:
                    continue
                cnt = Counter(labels[m] for m in adj[n])
                best = max(cnt.items(), key=lambda kv: (kv[1], kv[0] == labels[n]))[0]
                if best != labels[n]:
                    labels[n] = best
                    changed = True
            if not changed:
                break
        groups: Dict[str, List[str]] = defaultdict(list)
        for n, lab in labels.items():
            groups[lab].append(n)
        return {k: sorted(v) for k, v in sorted(groups.items(), key=lambda kv: -len(kv[1])) if len(v) > 1}

    def subgraph(self, names: Optional[List[str]] = None, hops: int = 2, limit: int = 120) -> Dict:
        """导出可视化子图：节点 + 边 + 社区 + 度数。"""
        if names:
            keep: Set[str] = set()
            for n in names:
                node = self.find(n)
                if not node:
                    continue
                keep.add(node["name"])
                for rel, nbrs in self.neighbors(node["name"], hops=hops).items():
                    keep |= nbrs
            keep = set(sorted(keep)[:limit])
        else:
            deg = Counter()
            for (src, dst, _r) in self.edges:
                deg[src] += 1
                deg[dst] += 1
            keep = {n for n, _ in deg.most_common(limit)}

        nodes = [{
            "id": n, "name": n, "type": self.nodes[n].get("type", "ORG"),
            "degree": sum(1 for (s, d, _r) in self.edges if s == n or d == n),
            "chunks": len(self.nodes[n].get("chunk_ids", [])),
            "chunk_count": len(self.nodes[n].get("chunk_ids", [])),
            "chunk_ids": list(self.nodes[n].get("chunk_ids", [])),
            "evidence": [],
        } for n in keep if n in self.nodes]
        edges = [{
            "source": s, "target": d, "rel": r, "weight": e["weight"],
            "evidence": (e.get("evidence") or [""])[0],
        } for (s, d, r), e in self.edges.items() if s in keep and d in keep]
        comm = self.communities()
        return {"nodes": nodes, "edges": edges,
                "communities": [{"label": k, "members": v} for k, v in comm.items()],
                "built_at": self.built_at}