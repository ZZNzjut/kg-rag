"""冒烟测试：入库样例文档 → 建图 → 问答，打印关键中间结果。

用法：python tests/smoke.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import graphstore, ingest, qa, store, vectorstore  # noqa: E402


def main() -> None:
    # 每次冒烟都重算，避免命中上一轮的 TTL 缓存
    store.cache_clear()
    for p in (ROOT / "data" / "graphs").glob("*.json"):
        p.unlink()

    samples = sorted((ROOT / "data" / "samples").glob("*"))
    print(f"== 入库 {len(samples)} 个样例文档 ==")
    for p in samples:
        if p.suffix.lower() not in {".md", ".txt", ".docx", ".xlsx", ".pdf", ".csv"}:
            continue
        try:
            meta = ingest.ingest(p)
            print(f"  · {meta['name']:<28} title={meta['title']:<20} chunks={meta['chunks']:<3} dim={meta['dim']}")
        except Exception as exc:
            print(f"  ! {p.name} 失败: {exc}")

    print("\n== 构建知识图谱 ==")
    info = ingest.build_graph()
    print(f"  nodes={info['nodes']} edges={info['edges']} "
          f"entities={info['entity_mentions']} triplets={info['triplet_mentions']} {info['elapsed_ms']}ms")

    g = graphstore.GraphStore.load()
    if g:
        stats = g.stats()
        print(f"  类型分布: {stats['types']}")
        print(f"  关系分布: {stats['relations']}")
        print(f"  核心实体: {[e['name'] for e in stats['top_entities'][:6]]}")
        comms = g.communities()
        print(f"  社区数: {len(comms)}，最大社区: {comms[list(comms)[0]][:6] if comms else []}")

    print("\n== 问答示例 ==")
    for q in ["出差住宿标准是多少？", "P1 故障多久响应？", "离职后账号什么时候删除？"]:
        r = qa.ask(q)
        print(f"\nQ: {q}")
        print(f"  模式={r['mode']} 耗时={r['latency_ms']}ms 召回={r['routes']}")
        print("  " + r["answer"].replace("\n", "\n  "))
        print(f"  引用: {[(c['n'], c['doc_title'], c['heading']) for c in r['citations']]}")

    print("\n== 向量库统计 ==", vectorstore.stats())


if __name__ == "__main__":
    main()