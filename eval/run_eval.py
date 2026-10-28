"""离线评测：检索命中率 / MRR / 引用准确率 / 答案要点覆盖率 / 延迟。

用法：
    python eval/run_eval.py            # 生成 eval/report.md 并在终端打印摘要
    python eval/run_eval.py --no-graph # 对照：关闭实体关系路

为什么要评测：RAG 项目最容易“看起来能用”。这里用 20 条带标准答案的问题
做小规模回归，数字不好看就说明召回或排序有问题，而不是靠感觉。
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core import config, qa, store  # noqa: E402

GOLD = [
    {"q": "年假有几天？", "doc": "01_员工手册.md", "kw": ["年假", "15"]},
    {"q": "病假需要提供什么证明？", "doc": "01_员工手册.md", "kw": ["病假", "证明"]},
    {"q": "加班可以调休吗？", "doc": "01_员工手册.md", "kw": ["调休"]},
    {"q": "试用期多久、转正怎么走？", "doc": "01_员工手册.md", "kw": ["试用期"]},
    {"q": "出差住宿一线城市每晚多少钱？", "doc": "02_费用报销制度.md", "kw": ["600"]},
    {"q": "报销发票最晚什么时候提交？", "doc": "02_费用报销制度.md", "kw": ["发票"]},
    {"q": "差旅补贴标准是多少？", "doc": "02_费用报销制度.md", "kw": ["补贴"]},
    {"q": "报销审批流程要走几步？", "doc": "02_费用报销制度.md", "kw": ["审批"]},
    {"q": "P1 故障多久响应？", "doc": "03_IT运维服务规范.md", "kw": ["15 分钟", "15分钟"]},
    {"q": "生产变更窗口是什么时间？", "doc": "03_IT运维服务规范.md", "kw": ["变更"]},
    {"q": "机房多久巡检一次？", "doc": "03_IT运维服务规范.md", "kw": ["巡检"]},
    {"q": "数据分为几个安全等级？", "doc": "04_信息安全与数据分级规范.md", "kw": ["等级", "级"]},
    {"q": "密码多长时间更换一次？", "doc": "04_信息安全与数据分级规范.md", "kw": ["密码"]},
    {"q": "离职后账号多久删除？", "doc": "04_信息安全与数据分级规范.md", "kw": ["删除", "禁用"]},
    {"q": "客户 P1 故障的响应和恢复时限？", "doc": "05_客户服务SLA.md", "kw": ["P1", "响应"]},
    {"q": "工单如何升级？", "doc": "05_客户服务SLA.md", "kw": ["升级"]},
    {"q": "采购金额多大需要三家比价？", "doc": "06_采购与供应商管理.md", "kw": ["比价", "三家"]},
    {"q": "供应商准入需要哪些材料？", "doc": "06_采购与供应商管理.md", "kw": ["供应商", "材料"]},
    {"q": "采购合同谁审批？", "doc": "06_采购与供应商管理.md", "kw": ["审批", "合同"]},
    {"q": "公司有哪些福利？", "doc": "01_员工手册.md", "kw": ["福利"]},
]


def doc_index() -> dict:
    """文件显示名 → file_id，用于把检索结果映射回标准答案。"""
    return {f["name"]: f["file_id"] for f in store.list_files()}


def run(use_graph: bool, top_k: int = 6) -> dict:
    files = doc_index()
    if not files:
        raise SystemExit("没有已入库文档，先运行 python tests/smoke.py 或启动服务上传样例")

    rows, lat = [], []
    for item in GOLD:
        file_id = files.get(item["doc"])
        if not file_id:
            rows.append({**item, "error": "文档未入库"})
            continue
        t0 = time.perf_counter()
        res = qa.ask(item["q"], use_graph=use_graph, top_k=top_k)
        ms = (time.perf_counter() - t0) * 1000
        lat.append(ms)

        cites = res["citations"]
        docs = [c["doc_id"] for c in cites]
        rank = next((i + 1 for i, d in enumerate(docs) if d == file_id), None)
        answer = res.get("answer", "")
        kw_hit = sum(1 for k in item["kw"] if k in answer)
        rows.append({
            **item,
            "mode": res["mode"],
            "hit1": rank == 1,
            "hit3": rank is not None and rank <= 3,
            "mrr": (1.0 / rank) if rank else 0.0,
            "kw_hit": kw_hit,
            "route_entity": res["routes"].get("entity", 0),
            "latency": round(ms, 1),
        })

    ok = [r for r in rows if "error" not in r]
    n = len(ok) or 1
    return {
        "use_graph": use_graph,
        "n": len(ok),
        "hit1": sum(1 for r in ok if r["hit1"]) / n,
        "hit3": sum(1 for r in ok if r["hit3"]) / n,
        "mrr": sum(r["mrr"] for r in ok) / n,
        "kw_cov": sum(1 for r in ok if r["kw_hit"] > 0) / n,
        "kw_full": sum(1 for r in ok if r["kw_hit"] == len(r["kw"])) / n,
        "entity_fired": sum(1 for r in ok if r["route_entity"] > 0) / n,
        "p50": statistics.median(lat) if lat else 0,
        "avg": statistics.mean(lat) if lat else 0,
        "rows": rows,
    }


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-graph", action="store_true", help="关闭实体关系路做对照")
    ap.add_argument("--top-k", type=int, default=6)
    args = ap.parse_args()

    print(f"== 评测：{len(GOLD)} 题，top_k={args.top_k} ==")
    main_res = run(not args.no_graph, args.top_k)
    ctrl = run(False, args.top_k) if not args.no_graph else None

    def show(tag, r):
        print(f"[{tag}] 命中@1={pct(r['hit1'])} 命中@3={pct(r['hit3'])} "
              f"MRR={r['mrr']:.3f} 要点覆盖={pct(r['kw_cov'])} "
              f"全要点={pct(r['kw_full'])} 实体路触发={pct(r['entity_fired'])} "
              f"p50={r['p50']:.0f}ms")

    show("graph" if not args.no_graph else "no-graph", main_res)
    if ctrl:
        show("no-graph", ctrl)

    lines = [
        "# RAG 离线评测报告",
        "",
        f"- 题量：{main_res['n']}（人工构造，覆盖 6 份文档）",
        f"- top_k：{args.top_k}",
        f"- 生成时间：{time.strftime('%Y-%m-%d %H:%M')}",
        "",
        "| 指标 | 四路召回（默认） | 关闭实体关系路 |",
        "|---|---|---|",
    ]
    rows_tbl = [
        ("引用命中@1", pct(main_res["hit1"]), pct(ctrl["hit1"]) if ctrl else "-"),
        ("引用命中@3", pct(main_res["hit3"]), pct(ctrl["hit3"]) if ctrl else "-"),
        ("MRR", f"{main_res['mrr']:.3f}", f"{ctrl['mrr']:.3f}" if ctrl else "-"),
        ("答案要点覆盖", pct(main_res["kw_cov"]), pct(ctrl["kw_cov"]) if ctrl else "-"),
        ("全要点覆盖", pct(main_res["kw_full"]), pct(ctrl["kw_full"]) if ctrl else "-"),
    ]
    lines += [f"| {a} | {b} | {c} |" for a, b, c in rows_tbl]
    if ctrl:
        lines.append(f"| p50 延迟 | {main_res['p50']:.0f} ms | {ctrl['p50']:.0f} ms |")
    lines += [
        "",
        "> 说明：这是 6 份样例文档、20 道人工题的小规模回归，用来发现流水线退化，",
        "> 不能当成线上效果数字；题目与文档同源，绝对分数会偏高。",
        "",
        "## 逐题结果（默认配置）",
        "",
        "| # | 问题 | 期望文档 | 命中@3 | MRR | 要点命中 | 实体路条数 | 延迟 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for i, r in enumerate(main_res["rows"], 1):
        if "error" in r:
            lines.append(f"| {i} | {r['q']} | {r['doc']} | - | - | - | - | - |")
            continue
        lines.append(
            f"| {i} | {r['q']} | {r['doc']} | {'是' if r['hit3'] else '否'} | "
            f"{r['mrr']:.2f} | {r['kw_hit']}/{len(r['kw'])} | {r['route_entity']} | {r['latency']}ms |"
        )
    out = ROOT / "eval" / "report.md"
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"报告已写入：{out}")


if __name__ == "__main__":
    main()