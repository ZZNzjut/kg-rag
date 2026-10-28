"""种子脚本：用 data/samples 下的文档重建一套可演示的数据。

    python scripts/seed.py            # 清空 vectors/graphs/uploads 后重新入库并建图
    python scripts/seed.py --keep     # 保留已有数据，只补齐 samples 里缺的文件

重建只依赖 uploads/ 目录（ingest 会把源文件复制进去），所以网页上点
「重置并重建」也能得到同样结果。
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config, graphstore, ingest, store, vectorstore  # noqa: E402


def _wipe() -> None:
    for d in (config.VECTOR_DIR, config.GRAPH_DIR, config.UPLOAD_DIR):
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True, exist_ok=True)
    if config.DB_PATH.exists():
        config.DB_PATH.unlink()
    store.init_db()


def main() -> int:
    ap = argparse.ArgumentParser(description="重建 KG-RAG 演示数据")
    ap.add_argument("--keep", action="store_true", help="保留现有向量/图谱/上传文件")
    args = ap.parse_args()

    if not args.keep:
        _wipe()
    else:
        store.init_db()

    samples = sorted(config.SAMPLES_DIR.glob("*")) if config.SAMPLES_DIR.exists() else []
    samples = [p for p in samples if p.suffix.lower() in config.SUPPORTED_EXT]
    if not samples:
        print(f"没有找到样例文档：{config.SAMPLES_DIR}")
        return 1

    for p in samples:
        try:
            meta = ingest.ingest(p)
            print(f"  入库 {meta['name']}  → {meta['chunks']} 块 / {meta['chars']} 字")
        except Exception as exc:  # noqa: BLE001 - 演示脚本，失败要看得见
            print(f"  失败 {p.name}: {exc}")

    g = ingest.build_graph()
    gr = graphstore.GraphStore.load()
    n_comm = len(gr.communities()) if gr else 0
    print(f"  建图完成：{g.get('nodes', 0)} 实体 / {g.get('edges', 0)} 关系 / "
          f"{n_comm} 社区，用时 {g.get('elapsed_ms', 0)} ms")
    st = vectorstore.stats()
    print(f"  当前知识库：{st.get('files', 0)} 文件 / {st.get('chunks', 0)} 块")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())